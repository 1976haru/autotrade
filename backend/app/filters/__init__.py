"""Signal/order filter layer (#32).

전략 신호를 1차로 만드는 모듈(`app.strategies`)과 실제 주문 결정을 하는 모듈
(`app.risk`/`app.permission`/`app.execution`) 사이의 *advisory layer*.

Filter는 signal을 변환할 수 있지만 broker / RiskManager / PermissionGate /
OrderExecutor / route_order 어떤 것도 import하거나 호출하지 않는다 —
CLAUDE.md 절대 원칙 2와 동일.
"""

from app.filters.market_regime import (
    MarketRegime,
    MarketRegimeFilter,
    RegimeDecision,
    RegimeDecisionKind,
    apply_regime_filter_to_signal,
)


__all__ = [
    "MarketRegime",
    "MarketRegimeFilter",
    "RegimeDecision",
    "RegimeDecisionKind",
    "apply_regime_filter_to_signal",
]
