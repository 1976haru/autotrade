import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.core.rate_limiter import SlidingWindowRateLimiter


PAPER_HOST = "https://openapivts.koreainvestment.com:29443"
LIVE_HOST  = "https://openapi.koreainvestment.com:9443"

_TOKEN_REFRESH_MARGIN = timedelta(seconds=60)
# EGW00133 = "접근토큰 발급 1분당 1회" — 계좌(앱키) 단위 레이트리밋. 재발급 폭격 방지.
_TOKEN_RATELIMIT_CODE = "EGW00133"
_TOKEN_RATELIMIT_BACKOFF = timedelta(seconds=60)

_log = logging.getLogger("autotrade.kis_token")


class KisAuthError(RuntimeError):
    pass


class KisTokenRateLimitedError(KisAuthError):
    """토큰 발급이 레이트리밋(EGW00133)으로 보호 창 안에 있어 *KIS 를 호출하지 않고*
    즉시 실패. 호출자는 마지막 정상값(stale)으로 폴백하거나 잠시 후 재시도."""


class KisApiError(RuntimeError):
    pass


# ── 토큰 디스크 캐시 (이 모듈 단일 reader/writer) ──────────────────────────────
#   %APPDATA%\Autotrade\kis_token_cache.json. access_token + 만료시각만 저장
#   (app_secret 미저장). 재기동 시 유효하면 재사용해 발급 자체를 회피.
def _token_cache_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "Autotrade" / "kis_token_cache.json"


def _token_cache_key(app_key: str, is_paper: bool) -> str:
    h = hashlib.sha256((app_key or "").encode("utf-8")).hexdigest()[:16]
    return f"{h}_{'paper' if is_paper else 'live'}"


def _load_cached_token(app_key: str, is_paper: bool) -> tuple[str, datetime] | None:
    try:
        path = _token_cache_path()
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        entry = (raw or {}).get(_token_cache_key(app_key, is_paper))
        if not entry:
            return None
        tok = entry.get("access_token")
        exp = entry.get("expires_at")
        if not tok or not exp:
            return None
        dt = datetime.fromisoformat(str(exp))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return tok, dt
    except Exception as exc:  # noqa: BLE001 — 손상은 폴백(정상 재발급), 절대 raise 안 함.
        _log.warning("[kis_token] 토큰 캐시 읽기 실패 — 재발급 폴백: %s", exc)
        return None


def _save_cached_token(app_key: str, is_paper: bool, token: str, expires_at: datetime) -> None:
    try:
        path = _token_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                raw = {}
        raw[_token_cache_key(app_key, is_paper)] = {
            "access_token": token, "expires_at": expires_at.isoformat(),
        }
        path.write_text(json.dumps(raw), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _log.warning("[kis_token] 토큰 캐시 저장 실패(무시): %s", exc)


class KisClient:
    """Token-managing async HTTP client for KIS REST API.

    SHADOW-mode read paths are wired: `get_price` (quote) and `inquire_balance`
    (cash + positions in one call). Order placement is intentionally absent —
    that lives behind PermissionGate in a separate PR and is never AI-executed.

    Tests inject a custom httpx transport via the `transport` kwarg.
    """

    def __init__(
        self,
        app_key:    str,
        app_secret: str,
        is_paper:   bool = True,
        transport:  httpx.AsyncBaseTransport | None = None,
        timeout:    float = 10.0,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ):
        if not app_key or not app_secret:
            raise KisAuthError("KIS app_key and app_secret are required")
        self.app_key    = app_key
        self.app_secret = app_secret
        self.is_paper   = is_paper
        self.base_url   = PAPER_HOST if is_paper else LIVE_HOST
        self._transport = transport
        self._timeout   = timeout
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._rate_limiter = rate_limiter
        # T1: 발급 직렬화 락 + EGW00133 백오프 창(이 시각 전엔 KIS 미호출).
        self._token_lock = asyncio.Lock()
        self._token_retry_not_before: datetime | None = None

    async def _throttle(self) -> None:
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire()

    def _client(self) -> httpx.AsyncClient:
        kwargs = {"base_url": self.base_url, "timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    def _token_valid(self, now: datetime) -> bool:
        return bool(self._token and self._token_expires_at and now < self._token_expires_at)

    async def _ensure_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token_valid(now):
            return self._token

        # T1⒜: 발급을 락으로 직렬화 — 동시/연속 콜드스타트 호출이 발급을 *1회만* 트리거.
        async with self._token_lock:
            now = datetime.now(timezone.utc)
            if self._token_valid(now):
                return self._token

            # T1⒞: 디스크 캐시에 유효 토큰이 있으면 재사용(발급 자체 회피, 재기동 포함).
            cached = _load_cached_token(self.app_key, self.is_paper)
            if cached and now < cached[1]:
                self._token, self._token_expires_at = cached
                return self._token

            # T1⒝: 레이트리밋 보호 창 안이면 KIS 를 때리지 않고 즉시 실패(폭격 차단).
            if self._token_retry_not_before and now < self._token_retry_not_before:
                raise KisTokenRateLimitedError(
                    f"KIS token issuance backing off until {self._token_retry_not_before.isoformat()} (EGW00133)"
                )

            await self._throttle()
            async with self._client() as client:
                r = await client.post(
                    "/oauth2/tokenP",
                    json={
                        "grant_type": "client_credentials",
                        "appkey":     self.app_key,
                        "appsecret":  self.app_secret,
                    },
                )
            if r.status_code != 200:
                body = r.text[:200]
                if _TOKEN_RATELIMIT_CODE in body or r.status_code == 403:
                    # 발급 1분당 1회 초과 → 60초 백오프 창 기록(그 전 호출은 KIS 무호출).
                    self._token_retry_not_before = now + _TOKEN_RATELIMIT_BACKOFF
                    raise KisTokenRateLimitedError(
                        f"KIS token rate-limited ({_TOKEN_RATELIMIT_CODE}); backing off 60s"
                    )
                raise KisAuthError(f"KIS token endpoint returned {r.status_code}: {body}")
            data = r.json()
            token = data.get("access_token")
            if not token:
                raise KisAuthError(f"KIS token response missing access_token: {data}")
            self._token = token
            expires_in = int(data.get("expires_in", 86400))
            self._token_expires_at = now + timedelta(seconds=expires_in) - _TOKEN_REFRESH_MARGIN
            self._token_retry_not_before = None  # 성공 → 백오프 해제
            _save_cached_token(self.app_key, self.is_paper, token, self._token_expires_at)
            return token

    async def get_price(self, symbol: str) -> dict:
        """Returns raw JSON from KIS quote endpoint. Caller extracts fields."""
        token = await self._ensure_token()
        await self._throttle()
        async with self._client() as client:
            r = await client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD":         symbol,
                },
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey":        self.app_key,
                    "appsecret":     self.app_secret,
                    "tr_id":         "FHKST01010100",
                    "custtype":      "P",
                },
            )
        if r.status_code != 200:
            raise KisApiError(f"KIS quote endpoint returned {r.status_code}: {r.text[:200]}")
        return r.json()

    async def inquire_index_price(self, index_code: str) -> dict:
        """국내 업종(지수) 현재가 — read-only. KOSPI='0001', KOSDAQ='1001'.

        공유 rate limiter 경유(_throttle). 주문 0건. output 에 bstp_nmix_prpr(지수),
        bstp_nmix_prdy_ctrt(전일대비율%) 등.
        """
        token = await self._ensure_token()
        await self._throttle()
        async with self._client() as client:
            r = await client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-index-price",
                params={
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD":         index_code,
                },
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey":        self.app_key,
                    "appsecret":     self.app_secret,
                    "tr_id":         "FHPUP02100000",
                    "custtype":      "P",
                },
            )
        if r.status_code != 200:
            raise KisApiError(f"KIS index endpoint returned {r.status_code}: {r.text[:200]}")
        return r.json()

    async def inquire_overseas_index(self, *, excd: str, symb: str) -> dict:
        """해외 지수 현재가 — read-only, 공유 rate limiter 경유. 주문 0건.

        B1: 밤사이 미국 시세(S&P500/나스닥/다우/SOX)용. ★모의(paper) 호스트에서
        해외지수 조회가 막힐 수 있음(국내 FHPUP02100000 전례) — 호출자(market_briefing)
        가 per-instrument graceful-fail 처리.
        """
        token = await self._ensure_token()
        await self._throttle()
        async with self._client() as client:
            r = await client.get(
                "/uapi/overseas-price/v1/quotations/inquire-index-price",
                params={"FID_COND_MRKT_DIV_CODE": "N", "FID_INPUT_ISCD": symb, "FID_INPUT_ISCD_2": excd},
                headers={
                    "authorization": f"Bearer {token}", "appkey": self.app_key,
                    "appsecret": self.app_secret, "tr_id": "FHKST03030100", "custtype": "P",
                },
            )
        if r.status_code != 200:
            raise KisApiError(f"KIS overseas index endpoint returned {r.status_code}: {r.text[:200]}")
        return r.json()

    async def inquire_time_dailychartprice(
        self,
        symbol: str,
        *,
        date: str,
        hour: str = "153000",
        include_past: bool = True,
    ) -> dict:
        """주식일별분봉조회 [국내주식-213] — read-only 분봉 시세 조회.

        GET /uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice
        TR ID: FHKST03010230. **주문 API 가 아니다** — 시세 조회 전용.

        한 호출은 `date` 의 `hour`(HHMMSS) 기준 과거 방향으로 분봉 묶음을 반환한다.
        하루 전체를 덮으려면 caller 가 hour 를 뒤로 옮겨가며 여러 번 호출한다.
        Returns raw JSON (output1 요약 + output2 분봉 배열). Caller 가 매핑한다.
        """
        token = await self._ensure_token()
        await self._throttle()
        async with self._client() as client:
            r = await client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD":         symbol,
                    "FID_INPUT_HOUR_1":       hour,
                    "FID_INPUT_DATE_1":       date,
                    "FID_PW_DATA_INCU_YN":    "Y" if include_past else "N",
                    "FID_FAKE_TICK_INCU_YN":  "N",
                },
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey":        self.app_key,
                    "appsecret":     self.app_secret,
                    "tr_id":         "FHKST03010230",
                    "custtype":      "P",
                },
            )
        if r.status_code != 200:
            raise KisApiError(
                f"KIS time-dailychartprice endpoint returned {r.status_code}: {r.text[:200]}")
        return r.json()

    def _balance_tr_id(self) -> str:
        return "VTTC8434R" if self.is_paper else "TTTC8434R"

    def _daily_ccld_tr_id(self) -> str:
        # 주식일별주문체결조회 TR id. 구형 VTTC8001R/TTTC8001R 은 KIS 서버에서
        # rt_cd=0 + "조회할 내역이 없습니다" 빈 결과만 반환한다 (2026-06-05 실측:
        # 모의 주문이 체결되어 잔고는 바뀌어도 VTTC8001R 로는 0건). KIS 가 NXT
        # 거래소 도입과 함께 TR id 를 개편한 현행 코드는 VTTC0081R(모의) /
        # TTTC0081R(실전, 모두 3개월 이내). 신형 TR 로 동일 계좌/파라미터를
        # 조회하면 체결 row 가 정상 반환된다 — 이 값이 틀리면 fill_poller →
        # get_order_status 가 영영 FILLED 를 못 보고 filled_quantity=0 에 고정.
        return "VTTC0081R" if self.is_paper else "TTTC0081R"

    def _order_tr_id(self, *, is_buy: bool) -> str:
        if self.is_paper:
            return "VTTC0802U" if is_buy else "VTTC0801U"
        return "TTTC0802U" if is_buy else "TTTC0801U"

    async def inquire_balance(self, cano: str, prdt_cd: str) -> dict:
        """Single-call balance + positions endpoint.

        Returns raw JSON with `output1` (positions) and `output2` (cash, equity).
        cano/prdt_cd are the 8-digit / 2-digit halves of the KIS account number.
        """
        token = await self._ensure_token()
        await self._throttle()
        async with self._client() as client:
            r = await client.get(
                "/uapi/domestic-stock/v1/trading/inquire-balance",
                params={
                    "CANO":                   cano,
                    "ACNT_PRDT_CD":           prdt_cd,
                    "AFHR_FLPR_YN":           "N",
                    "OFL_YN":                 "",
                    "INQR_DVSN":              "02",
                    "UNPR_DVSN":              "01",
                    "FUND_STTL_ICLD_YN":      "N",
                    "FNCG_AMT_AUTO_RDPT_YN":  "N",
                    "PRCS_DVSN":              "00",
                    "CTX_AREA_FK100":         "",
                    "CTX_AREA_NK100":         "",
                },
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey":        self.app_key,
                    "appsecret":     self.app_secret,
                    "tr_id":         self._balance_tr_id(),
                    "custtype":      "P",
                },
            )
        if r.status_code != 200:
            raise KisApiError(f"KIS balance endpoint returned {r.status_code}: {r.text[:200]}")
        return r.json()

    async def inquire_daily_ccld(
        self,
        cano:    str,
        prdt_cd: str,
        *,
        start_date: str | None = None,
        end_date:   str | None = None,
    ) -> dict:
        """Daily order/fill inquiry. Returns today's orders by default.

        KIS does not support lookup-by-id directly — callers fetch the day's
        list and filter client-side by ODNO.
        """
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        token = await self._ensure_token()
        await self._throttle()
        async with self._client() as client:
            r = await client.get(
                "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                params={
                    "CANO":            cano,
                    "ACNT_PRDT_CD":    prdt_cd,
                    "INQR_STRT_DT":    start_date or today,
                    "INQR_END_DT":     end_date or today,
                    "SLL_BUY_DVSN_CD": "00",
                    "INQR_DVSN":       "00",
                    "PDNO":            "",
                    "CCLD_DVSN":       "00",
                    "ORD_GNO_BRNO":    "",
                    "ODNO":            "",
                    "INQR_DVSN_3":     "00",
                    "INQR_DVSN_1":     "",
                    "CTX_AREA_FK100":  "",
                    "CTX_AREA_NK100":  "",
                },
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey":        self.app_key,
                    "appsecret":     self.app_secret,
                    "tr_id":         self._daily_ccld_tr_id(),
                    "custtype":      "P",
                },
            )
        if r.status_code != 200:
            raise KisApiError(f"KIS daily-ccld endpoint returned {r.status_code}: {r.text[:200]}")
        return r.json()

    async def place_order(
        self,
        cano:       str,
        prdt_cd:    str,
        symbol:     str,
        *,
        is_buy:     bool,
        quantity:   int,
        order_type: str,        # "market" or "limit"
        limit_price: int | None = None,
    ) -> dict:
        """Submit a stock order to the KIS order-cash endpoint.

        Returns raw JSON. tr_id selects buy/sell + paper/live combination.
        Caller maps the response (rt_cd / msg1 / output.ODNO) to OrderResult.
        """
        if order_type == "limit" and not limit_price:
            raise ValueError("limit orders require a limit_price")
        ord_dvsn = "01" if order_type == "market" else "00"
        ord_unpr = "0" if order_type == "market" else str(limit_price)

        token = await self._ensure_token()
        await self._throttle()
        async with self._client() as client:
            r = await client.post(
                "/uapi/domestic-stock/v1/trading/order-cash",
                json={
                    "CANO":         cano,
                    "ACNT_PRDT_CD": prdt_cd,
                    "PDNO":         symbol,
                    "ORD_DVSN":     ord_dvsn,
                    "ORD_QTY":      str(quantity),
                    "ORD_UNPR":     ord_unpr,
                },
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey":        self.app_key,
                    "appsecret":     self.app_secret,
                    "tr_id":         self._order_tr_id(is_buy=is_buy),
                    "custtype":      "P",
                },
            )
        if r.status_code != 200:
            raise KisApiError(f"KIS order-cash endpoint returned {r.status_code}: {r.text[:200]}")
        return r.json()
