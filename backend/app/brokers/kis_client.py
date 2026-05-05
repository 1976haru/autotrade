from datetime import datetime, timedelta, timezone

import httpx


PAPER_HOST = "https://openapivts.koreainvestment.com:29443"
LIVE_HOST  = "https://openapi.koreainvestment.com:9443"

_TOKEN_REFRESH_MARGIN = timedelta(seconds=60)


class KisAuthError(RuntimeError):
    pass


class KisApiError(RuntimeError):
    pass


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

    def _client(self) -> httpx.AsyncClient:
        kwargs = {"base_url": self.base_url, "timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _ensure_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token and self._token_expires_at and now < self._token_expires_at:
            return self._token

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
            raise KisAuthError(f"KIS token endpoint returned {r.status_code}: {r.text[:200]}")
        data = r.json()
        token = data.get("access_token")
        if not token:
            raise KisAuthError(f"KIS token response missing access_token: {data}")
        self._token = token
        expires_in = int(data.get("expires_in", 86400))
        self._token_expires_at = now + timedelta(seconds=expires_in) - _TOKEN_REFRESH_MARGIN
        return token

    async def get_price(self, symbol: str) -> dict:
        """Returns raw JSON from KIS quote endpoint. Caller extracts fields."""
        token = await self._ensure_token()
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

    def _balance_tr_id(self) -> str:
        return "VTTC8434R" if self.is_paper else "TTTC8434R"

    def _daily_ccld_tr_id(self) -> str:
        return "VTTC8001R" if self.is_paper else "TTTC8001R"

    async def inquire_balance(self, cano: str, prdt_cd: str) -> dict:
        """Single-call balance + positions endpoint.

        Returns raw JSON with `output1` (positions) and `output2` (cash, equity).
        cano/prdt_cd are the 8-digit / 2-digit halves of the KIS account number.
        """
        token = await self._ensure_token()
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
