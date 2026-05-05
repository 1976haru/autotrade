from datetime import datetime, timezone

from app.brokers.base import (
    Balance,
    BrokerAdapter,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
)
from app.brokers.kis_client import KisApiError, KisClient
from app.core.config import get_settings


_STUB_MESSAGE = (
    "Not yet wired in SHADOW mode. cancel / order_status land in follow-up PRs. "
    "Real LIVE order routing is gated by RiskManager + PermissionGate and is "
    "never AI-executed (CLAUDE.md)."
)


class KisBrokerAdapter(BrokerAdapter):
    """한국투자증권(KIS) 브로커 어댑터.

    현재 단계 (LIVE_SHADOW read-only):
    - `get_price` — 실 API 호출 (quote)
    - `get_balance` / `get_positions` — KIS inquire-balance 한 번 호출에서 분리
    - `cancel_order` / `get_order_status` — NotImplementedError (다음 PR)
    - `place_order` — SHADOW 모드에서 실 broker로 절대 가지 않음 (명시적 거부)

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
    ):
        settings = get_settings()
        self.app_key    = app_key    if app_key    is not None else settings.kis_app_key
        self.app_secret = app_secret if app_secret is not None else settings.kis_app_secret
        self.account_no = account_no if account_no is not None else settings.kis_account_no
        self.is_paper   = is_paper   if is_paper   is not None else settings.kis_is_paper
        self._client = client

    def has_credentials(self) -> bool:
        return bool(self.app_key and self.app_secret and self.account_no)

    @property
    def client(self) -> KisClient:
        if self._client is None:
            if not self.app_key or not self.app_secret:
                raise RuntimeError(
                    "KIS credentials are not configured; set KIS_APP_KEY / KIS_APP_SECRET"
                )
            self._client = KisClient(self.app_key, self.app_secret, self.is_paper)
        return self._client

    async def get_price(self, symbol: str) -> Quote:
        raw = await self.client.get_price(symbol)
        output = raw.get("output") or {}
        price_str = output.get("stck_prpr")
        if price_str is None:
            raise KisApiError(f"KIS quote response missing output.stck_prpr: {raw}")
        return Quote(
            symbol=symbol,
            price=int(price_str),
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="kis",
        )

    def _split_account(self) -> tuple[str, str]:
        if not self.account_no or len(self.account_no) < 10:
            raise RuntimeError(
                "KIS account number must be at least 10 chars (8 + 2 split); "
                f"got {self.account_no!r}"
            )
        return self.account_no[:-2], self.account_no[-2:]

    async def get_balance(self) -> Balance:
        cano, prdt = self._split_account()
        raw = await self.client.inquire_balance(cano, prdt)
        output2 = (raw.get("output2") or [{}])[0]
        cash   = int(output2.get("dnca_tot_amt", "0"))
        equity = int(output2.get("tot_evlu_amt", "0"))
        return Balance(cash=cash, equity=equity, buying_power=cash, currency="KRW")

    async def get_positions(self) -> list[Position]:
        cano, prdt = self._split_account()
        raw = await self.client.inquire_balance(cano, prdt)
        positions: list[Position] = []
        for item in raw.get("output1") or []:
            qty = int(item.get("hldg_qty", "0") or "0")
            if qty <= 0:
                continue
            positions.append(Position(
                symbol=item.get("pdno", ""),
                quantity=qty,
                avg_price=int(float(item.get("pchs_avg_pric", "0") or "0")),
                market_price=int(item.get("prpr", "0") or "0"),
            ))
        return positions

    async def place_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError(
            "place_order is intentionally disabled for KIS. SHADOW mode never "
            "places real orders; LIVE order routing requires PermissionGate "
            "approval and lands in a separate PR — never AI-executed."
        )

    async def cancel_order(self, order_id: str) -> OrderResult:
        raise NotImplementedError(_STUB_MESSAGE)

    async def get_order_status(self, order_id: str) -> OrderResult:
        raise NotImplementedError(_STUB_MESSAGE)
