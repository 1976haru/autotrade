import time
from collections.abc import Callable
from datetime import datetime, timezone

from app.brokers.base import (
    Balance,
    BrokerAdapter,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    Position,
    Quote,
)
from app.brokers.kis_client import KisApiError, KisClient
from app.core.config import get_settings


_STUB_MESSAGE = (
    "Not yet wired in SHADOW mode. cancel_order is a write operation that lands "
    "with LIVE_MANUAL_APPROVAL routing in a follow-up PR. Real LIVE order "
    "routing is gated by RiskManager + PermissionGate and is never AI-executed "
    "(CLAUDE.md)."
)


def _kis_side_to_order_side(code: str) -> OrderSide:
    """KIS sll_buy_dvsn_cd: 01 = sell, 02 = buy."""
    return OrderSide.SELL if code == "01" else OrderSide.BUY


def _kis_order_response_to_result(raw: dict, order: "OrderRequest") -> OrderResult:
    """Map KIS order-cash response to OrderResult.

    KIS only acknowledges receipt synchronously (rt_cd='0' + ODNO). It does
    not return fill state — that requires a follow-up inquire-daily-ccld
    call. We surface the order as RECEIVED on success, REJECTED on failure.
    """
    rt_cd = raw.get("rt_cd")
    msg = raw.get("msg1", "") or ""
    msg_cd = raw.get("msg_cd", "") or ""
    output = raw.get("output") or {}
    odno = output.get("ODNO") or ""
    org_no = str(output.get("KRX_FWDG_ORD_ORGNO") or "") or None

    # Preserve the KIS message code (msg_cd) alongside msg1 so a rejection records
    # the *exact* KIS error code, e.g. "[40570000] 모의투자 주문이 불가한 계좌입니다".
    # Previously only msg1 was kept, so the diagnostic code was lost — making it
    # impossible to tell (account-not-registered vs contest-not-supported vs token)
    # apart for a KIS support ticket. (2026-06-04)
    full_msg = f"[{msg_cd}] {msg}" if msg_cd else msg

    if rt_cd != "0":
        return OrderResult(
            order_id=odno,
            status=OrderStatus.REJECTED,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            message=full_msg,
        )

    return OrderResult(
        order_id=odno,
        status=OrderStatus.RECEIVED,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        message=full_msg,
        broker_org_no=org_no,
    )


def _int(v) -> int:
    try:
        return int(float(str(v).strip() or "0"))
    except (TypeError, ValueError):
        return 0


def parse_ccld_row(row: dict) -> dict:
    """KIS 일별주문체결 1행 → 잔량 포함 dict (EOD 대조용, 순수 함수).

    remaining = rmn_qty(있으면) 또는 ord_qty − tot_ccld_qty − cncl_cfrm_qty.
    취소 주문 *자체* 행(cncl_yn=Y)은 잔량 0 — 원주문 행의 잔량이 진실이다.
    """
    ord_qty = _int(row.get("ord_qty"))
    filled = _int(row.get("tot_ccld_qty"))
    canceled = (row.get("cncl_yn") or "").upper() == "Y"
    if canceled:
        remaining = 0
    elif row.get("rmn_qty") not in (None, ""):
        remaining = max(0, _int(row.get("rmn_qty")))
    else:
        remaining = max(0, ord_qty - filled - _int(row.get("cncl_cfrm_qty")))
    return {
        "odno":      str(row.get("odno") or ""),
        "symbol":    str(row.get("pdno") or ""),
        "side":      _kis_side_to_order_side(row.get("sll_buy_dvsn_cd", "02")).value,
        "ord_qty":   ord_qty,
        "filled":    filled,
        "remaining": remaining,
        "canceled":  canceled,
        "ord_tmd":   str(row.get("ord_tmd") or ""),
        # 정정/취소용 KRX 전송 주문조직번호(주문채번지점번호) + 원주문번호(취소행이면).
        "ord_gno_brno": str(row.get("ord_gno_brno") or ""),
        "orgn_odno":    str(row.get("orgn_odno") or ""),
        "cncl_cfrm_qty": _int(row.get("cncl_cfrm_qty")),
    }


def _row_to_order_result(row: dict, order_id: str) -> OrderResult:
    ord_qty = int(row.get("ord_qty", "0") or "0")
    filled  = int(row.get("tot_ccld_qty", "0") or "0")
    canceled = (row.get("cncl_yn") or "").upper() == "Y"

    if canceled:
        status = OrderStatus.CANCELED
    elif filled >= ord_qty and ord_qty > 0:
        status = OrderStatus.FILLED
    elif 0 < filled < ord_qty:
        status = OrderStatus.PARTIALLY_FILLED
    else:
        status = OrderStatus.RECEIVED

    avg_fill_str = row.get("avg_prvs") or row.get("ccld_avg_prvs") or ""
    avg_fill = int(float(avg_fill_str)) if avg_fill_str.strip() else None

    return OrderResult(
        order_id=order_id,
        status=status,
        symbol=row.get("pdno", ""),
        side=_kis_side_to_order_side(row.get("sll_buy_dvsn_cd", "02")),
        quantity=ord_qty,
        filled_quantity=filled,
        avg_fill_price=avg_fill,
        message=row.get("ord_dvsn_name", "") or "",
    )


class KisBrokerAdapter(BrokerAdapter):
    """한국투자증권(KIS) 브로커 어댑터.

    현재 단계 (PAPER + LIVE_SHADOW read-only):
    - `get_price` — 실 API 호출 (quote)
    - `get_balance` / `get_positions` — KIS inquire-balance 한 번 호출에서 분리
    - `get_order_status` — KIS inquire-daily-ccld로 오늘자 주문 조회 후 ODNO 필터
    - `place_order` — `is_paper=True`(KIS 모의투자)일 때만 실 라우팅. False면 명시적 거부.
      LIVE 라우팅(`is_paper=False`)은 LIVE_MANUAL_APPROVAL PR에서 별도 wire.
    - `cancel_order` — write op, NotImplementedError 유지 (LIVE_MANUAL_APPROVAL PR)

    실 라이브 주문 라우팅은 RiskManager → PermissionGate → OrderExecutor를
    거치는 별도 PR에서만 다룬다.
    """

    def __init__(
        self,
        *,
        app_key:    str | None = None,
        app_secret: str | None = None,
        account_no: str | None = None,
        is_paper:   bool | None = None,
        client:     KisClient | None = None,
        quote_cache_ttl_seconds:   float | None = None,
        balance_cache_ttl_seconds: float | None = None,
        clock: Callable[[], float] | None = None,
    ):
        settings = get_settings()
        self.app_key    = app_key    if app_key    is not None else settings.kis_app_key
        self.app_secret = app_secret if app_secret is not None else settings.kis_app_secret
        self.account_no = account_no if account_no is not None else settings.kis_account_no
        self.is_paper   = is_paper   if is_paper   is not None else settings.kis_is_paper
        self._client = client

        # Short-TTL read-only caches (EGW00201 mitigation, 2026-06-02). A scan
        # tick re-reads balance per symbol/order and re-quotes the same symbol
        # across scan + route_order; caching collapses those duplicate calls.
        # Monotonic clock injectable for deterministic tests.
        self._clock: Callable[[], float] = clock or time.monotonic
        self._quote_cache_ttl = float(
            quote_cache_ttl_seconds if quote_cache_ttl_seconds is not None
            else getattr(settings, "kis_quote_cache_ttl_seconds", 0.0)
        )
        self._balance_cache_ttl = float(
            balance_cache_ttl_seconds if balance_cache_ttl_seconds is not None
            else getattr(settings, "kis_balance_cache_ttl_seconds", 0.0)
        )
        self._quote_cache: dict[str, tuple[float, Quote]] = {}
        self._balance_cache: tuple[float, dict] | None = None

    def has_credentials(self) -> bool:
        return bool(self.app_key and self.app_secret and self.account_no)

    @property
    def client(self) -> KisClient:
        if self._client is None:
            if not self.app_key or not self.app_secret:
                raise RuntimeError(
                    "KIS credentials are not configured; set KIS_APP_KEY / KIS_APP_SECRET"
                )
            # 계좌 단위 *공유* limiter 주입 — 이 lazy 경로가 무제한 client 를
            # 만들어 EGW00201 폭주에 기여하던 구멍을 막는다(deps 경로와 동일 limiter).
            from app.core.rate_limiter import get_kis_rate_limiter
            self._client = KisClient(
                self.app_key, self.app_secret, self.is_paper,
                rate_limiter=get_kis_rate_limiter(),
            )
        return self._client

    async def get_price(self, symbol: str) -> Quote:
        if self._quote_cache_ttl > 0:
            hit = self._quote_cache.get(symbol)
            if hit is not None and (self._clock() - hit[0]) < self._quote_cache_ttl:
                # Return the *same* Quote, preserving its original fetch
                # timestamp — a cached quote honestly reports its true age, so
                # RiskManager's stale check still sees real staleness.
                return hit[1]
        raw = await self.client.get_price(symbol)
        output = raw.get("output") or {}
        price_str = output.get("stck_prpr")
        if price_str is None:
            raise KisApiError(f"KIS quote response missing output.stck_prpr: {raw}")
        quote = Quote(
            symbol=symbol,
            price=int(price_str),
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="kis",
        )
        if self._quote_cache_ttl > 0:
            self._quote_cache[symbol] = (self._clock(), quote)
        return quote

    async def _cached_inquire_balance(self) -> dict:
        """inquire-balance with a short TTL cache shared by get_balance and
        get_positions. Balance is effectively constant within a scan tick, so a
        few-second TTL collapses many identical calls into one. Invalidated on
        place_order so post-fill reads are fresh."""
        cano, prdt = self._split_account()
        if self._balance_cache_ttl > 0 and self._balance_cache is not None:
            ts, cached = self._balance_cache
            if (self._clock() - ts) < self._balance_cache_ttl:
                return cached
        raw = await self.client.inquire_balance(cano, prdt)
        if self._balance_cache_ttl > 0:
            self._balance_cache = (self._clock(), raw)
        return raw

    def _split_account(self) -> tuple[str, str]:
        if not self.account_no or len(self.account_no) < 10:
            raise RuntimeError("KIS account number must be at least 10 chars (8 + 2 split)")
        return self.account_no[:-2], self.account_no[-2:]

    async def get_balance(self) -> Balance:
        raw = await self._cached_inquire_balance()
        output2 = (raw.get("output2") or [{}])[0]
        cash   = int(output2.get("dnca_tot_amt", "0"))
        equity = int(output2.get("tot_evlu_amt", "0"))
        return Balance(cash=cash, equity=equity, buying_power=cash, currency="KRW")

    async def get_orderable_cash(self, *, symbol: str, price: int) -> int:
        """Read KIS's symbol/price-specific cash orderability; fail closed."""
        cano, prdt = self._split_account()
        output = await self.client.inquire_orderable_cash(
            cano, prdt, symbol=symbol, price=int(price),
        )
        raw = output.get("ord_psbl_cash")
        no_credit = output.get("nrcvb_buy_amt")
        if raw in (None, ""):
            raise KisApiError("KIS orderable-cash response missing ord_psbl_cash")
        cash = int(raw)
        if no_credit not in (None, ""):
            cash = min(cash, int(no_credit))
        return cash

    async def get_orderable_buy_capacity(self, *, symbol: str, price: int) -> dict[str, int]:
        """Read market-order capacity using KIS no-credit amount and quantity."""
        cano, prdt = self._split_account()
        output = await self.client.inquire_orderable_cash(
            cano, prdt, symbol=symbol, price=int(price), order_division="01",
        )
        fields = {
            "orderable_cash_krw": "ord_psbl_cash",
            "no_credit_buy_amount_krw": "nrcvb_buy_amt",
            "no_credit_buy_quantity": "nrcvb_buy_qty",
            "calculation_price_krw": "psbl_qty_calc_unpr",
        }
        parsed: dict[str, int] = {}
        for target, source in fields.items():
            value = output.get(source)
            if value in (None, ""):
                raise KisApiError(f"KIS orderable-cash response missing {source}")
            try:
                parsed[target] = int(value)
            except (TypeError, ValueError) as exc:
                raise KisApiError(f"KIS orderable-cash response invalid {source}") from exc
            if parsed[target] < 0:
                raise KisApiError(f"KIS orderable-cash response invalid {source}")
        if parsed["calculation_price_krw"] <= 0:
            raise KisApiError("KIS orderable-cash response invalid psbl_qty_calc_unpr")
        return parsed

    async def get_positions(self) -> list[Position]:
        raw = await self._cached_inquire_balance()
        positions: list[Position] = []
        for item in raw.get("output1") or []:
            qty = int(item.get("hldg_qty", "0") or "0")
            if qty <= 0:
                continue
            # ord_psbl_qty(주문가능수량) — 미결제분 등으로 hldg_qty 보다 작을 수 있다.
            #   부재/파싱불가 시 None → 호출자는 보유수량으로 간주.
            _ordp_raw = item.get("ord_psbl_qty")
            try:
                ordp = int(_ordp_raw) if _ordp_raw not in (None, "") else None
            except (TypeError, ValueError):
                ordp = None
            positions.append(Position(
                symbol=item.get("pdno", ""),
                quantity=qty,
                avg_price=int(float(item.get("pchs_avg_pric", "0") or "0")),
                market_price=int(item.get("prpr", "0") or "0"),
                name=(item.get("prdt_name") or "").strip() or None,  # V4: KIS 종목명
                sellable_quantity=ordp,
            ))
        return positions

    async def place_order(self, order: OrderRequest) -> OrderResult:
        if not self.is_paper:
            raise NotImplementedError(
                "KIS live (real-money) place_order is not implemented. Live "
                "order routing requires LIVE_MANUAL_APPROVAL flow with "
                "PermissionGate approval, in a follow-up PR. Set "
                "KIS_IS_PAPER=true to use the KIS paper server."
            )
        cano, prdt = self._split_account()
        is_buy = order.side == OrderSide.BUY
        order_type = "market" if order.order_type.value == "MARKET" else "limit"
        # V3: 기본 legacy 는 V3 이전과 *완전히 동일한* 호출(api_format 인자 없음) — 검증된
        #   운영 경로 무변경. "current" 를 명시 opt-in 한 경우에만 신형 형식 인자를 넘긴다.
        api_format = str(getattr(get_settings(), "kis_paper_order_api_format", "legacy")
                         or "legacy").strip().lower()
        extra = {} if api_format == "legacy" else {"api_format": api_format}
        try:
            raw = await self.client.place_order(
                cano, prdt, order.symbol,
                is_buy=is_buy,
                quantity=order.quantity,
                order_type=order_type,
                limit_price=order.limit_price,
                **extra,
            )
        except KisApiError as e:
            raise e
        # A submitted order changes cash/positions — drop the balance cache so
        # the next read reflects the order instead of a pre-order snapshot.
        self._balance_cache = None
        return _kis_order_response_to_result(raw, order)

    async def cancel_order(self, order_id: str, *, org_no: str | None = None,
                           symbol: str = "", side: OrderSide = OrderSide.BUY) -> OrderResult:
        """미체결 잔량 전부 취소 — **KIS 모의투자 전용**.

        실전(is_paper=False)은 NotImplementedError 유지 (LIVE 취소 경로 미개방).
        호출자는 OrderExecutor.cancel 뿐(단일 진입점 #40). 응답 rt_cd=0 은 *취소 접수*
        일 뿐 — 실제 취소/직전 체결 수량은 일별체결(잔량)·잔고로 대조해야 한다.
        org_no(KRX_FWDG_ORD_ORGNO) 는 일별체결 행의 ord_gno_brno 를 사용한다.
        """
        if not self.is_paper:
            raise NotImplementedError(_STUB_MESSAGE)
        if not org_no:
            return OrderResult(order_id="", status=OrderStatus.REJECTED, symbol=symbol,
                               side=side, quantity=0,
                               message="CANCEL_ORG_NO_UNKNOWN: KRX_FWDG_ORD_ORGNO 미상")
        cano, prdt = self._split_account()
        raw = await self.client.cancel_order_paper(cano, prdt, org_no=org_no,
                                                   orig_odno=order_id)
        self._balance_cache = None
        rt_cd = raw.get("rt_cd")
        msg = f"[{raw.get('msg_cd', '')}] {raw.get('msg1', '')}".strip()
        new_odno = str((raw.get("output") or {}).get("ODNO") or "")
        return OrderResult(
            order_id=new_odno, symbol=symbol, side=side, quantity=0,
            status=OrderStatus.RECEIVED if rt_cd == "0" else OrderStatus.REJECTED,
            message=msg,
        )

    async def list_today_orders(self, *, kst_date: str) -> list[dict]:
        """오늘(KST) 주문 목록 — read-only, 미체결 잔량 포함 (EOD 청산 대조용).

        KIS 의 *정정취소가능주문조회*(inquire-psbl-rvsecncl, TTTC0084R)는 모의투자
        TR 이 없어 사용하지 않는다. 대신 이미 쓰고 있는 일별주문체결조회(VTTC0081R)
        전 페이지에서 잔량을 계산한다. 반환: [{odno, symbol, side, ord_qty, filled,
        remaining, canceled, ord_tmd}] — 주문 write 0건.
        """
        cano, prdt = self._split_account()
        raw_rows = await self.client.inquire_daily_ccld_all(cano, prdt, kst_date=kst_date)
        return [parse_ccld_row(r) for r in raw_rows]

    async def get_order_status(self, order_id: str) -> OrderResult:
        cano, prdt = self._split_account()
        raw = await self.client.inquire_daily_ccld(cano, prdt)
        for row in (raw.get("output1") or []):
            if row.get("odno") == order_id:
                return _row_to_order_result(row, order_id)
        # The order carries an ODNO, so KIS *accepted* it at submission time
        # (place_order returns REJECTED at submission for rt_cd != '0' and never
        # reaches here). Having no conclusion row in today's daily-ccld means the
        # order is "submitted / accepted, not yet concluded" — NOT rejected.
        #
        # Returning REJECTED here was a bug: the fill poller would clobber a
        # successfully-submitted order's RECEIVED status to REJECTED on the next
        # tick. Observed 2026-06-02 — 6 paper BUYs were accepted (msg "모의투자
        # 매수주문이 완료 되었습니다") yet recorded broker_status=REJECTED. Surface
        # RECEIVED (an open status) so the order stays a poll candidate until it
        # actually fills or shows a real conclusion row.
        return OrderResult(
            order_id=order_id,
            status=OrderStatus.RECEIVED,
            symbol="",
            side=OrderSide.BUY,
            quantity=0,
            filled_quantity=0,
            avg_fill_price=None,
            message="submitted; no conclusion row in KIS daily-ccld yet",
        )
