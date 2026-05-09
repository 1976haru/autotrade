"""#49: Futures strategy package.

선물 전용 전략 인터페이스 + 초기 mock 전략들. 주식 `app.strategies` 패키지와
*분리* — 양방향 포지션, 계약 수, 증거금, 레버리지, 만기, 롤오버 같은 선물
고유 차원이 시그니처에 반영되어야 하기 때문.

자세한 contract: [`docs/futures_strategy_contract.md`](../../../../docs/futures_strategy_contract.md).
"""

from app.futures.strategies.base import (
    FuturesContractSizingHint,
    FuturesExitPlan,
    FuturesRolloverPlan,
    FuturesSignal,
    FuturesSignalAction,
    FuturesSignalExplanation,
    FuturesStrategyBase,
    FuturesStrategyContext,
    FuturesValidationResult,
)

__all__ = (
    "FuturesContractSizingHint",
    "FuturesExitPlan",
    "FuturesRolloverPlan",
    "FuturesSignal",
    "FuturesSignalAction",
    "FuturesSignalExplanation",
    "FuturesStrategyBase",
    "FuturesStrategyContext",
    "FuturesValidationResult",
)
