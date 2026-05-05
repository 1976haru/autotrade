from app.brokers.base import (
    Balance,
    BrokerAdapter,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
)
from app.core.config import get_settings


_STUB_MESSAGE = (
    "KIS adapter is a stub. Real KIS REST integration lands in a follow-up PR "
    "and will land first as LIVE_SHADOW (read-only quotes/balance), then PAPER, "
    "then LIVE_MANUAL_APPROVAL — never AI-executed."
)


class KisBrokerAdapter(BrokerAdapter):
    """한국투자증권(KIS) 브로커 어댑터 — 의도적으로 stub.

    이 클래스는 `BrokerAdapter` 인터페이스를 구현하지만 모든 호출은
    NotImplementedError를 던진다. 신규 모듈 구조를 미리 잡되 실제 주문 경로를
    절대 노출하지 않기 위한 안전 장치다.

    CLAUDE.md 절대 원칙:
    - AI가 이 어댑터의 place_order를 직접 호출하는 경로를 만들지 않는다.
    - 모든 주문은 RiskManager → PermissionGate → OrderExecutor를 거친다.
    - 기본 운용모드는 SIMULATION/PAPER이며 LIVE_AI_EXECUTION은 기본 비활성화.

    실제 구현 단계:
    1. LIVE_SHADOW — 시세/잔고/포지션 read-only, 주문 금지
    2. PAPER — KIS 모의투자 계좌(가상 자금)로 주문 라우팅
    3. LIVE_MANUAL_APPROVAL — 실계좌 + 사용자 승인 후에만 체결
    """

    def __init__(
        self,
        *,
        app_key:    str | None = None,
        app_secret: str | None = None,
        account_no: str | None = None,
        is_paper:   bool | None = None,
    ):
        settings = get_settings()
        self.app_key    = app_key    if app_key    is not None else settings.kis_app_key
        self.app_secret = app_secret if app_secret is not None else settings.kis_app_secret
        self.account_no = account_no if account_no is not None else settings.kis_account_no
        self.is_paper   = is_paper   if is_paper   is not None else settings.kis_is_paper

    def has_credentials(self) -> bool:
        return bool(self.app_key and self.app_secret and self.account_no)

    async def get_price(self, symbol: str) -> Quote:
        raise NotImplementedError(_STUB_MESSAGE)

    async def get_balance(self) -> Balance:
        raise NotImplementedError(_STUB_MESSAGE)

    async def get_positions(self) -> list[Position]:
        raise NotImplementedError(_STUB_MESSAGE)

    async def place_order(self, order: OrderRequest) -> OrderResult:
        # Extra-explicit message: this stub will never reach a real broker.
        raise NotImplementedError(
            "KIS place_order is intentionally not implemented. "
            "Live order routing requires SHADOW-mode validation and PermissionGate "
            "approval before any real-call code lands."
        )

    async def cancel_order(self, order_id: str) -> OrderResult:
        raise NotImplementedError(_STUB_MESSAGE)

    async def get_order_status(self, order_id: str) -> OrderResult:
        raise NotImplementedError(_STUB_MESSAGE)
