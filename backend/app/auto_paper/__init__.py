"""AI Paper Auto Loop — EXE 원클릭 시작/정지/긴급정지 서비스.

본 패키지는 PAPER/SIMULATION 모드 한정의 *고수준 lifecycle 컨트롤러* 다.
실제 주문 실행은 *없음* — tick 은 placeholder 로 cycle 카운트만 증가. 향후
별도 PR 에서 기존 `AutoTraderAgent.run_once` 또는 `LiveStrategyEngine` 을
tick 시점에 plug 한다.

절대 원칙 (CLAUDE.md, 테스트로 강제):
- broker / OrderExecutor / route_order 호출 0건
- ENABLE_LIVE_TRADING 이 True 여도 본 loop 은 실거래 진행 0건 (강제 paper)
- start() / tick() 어떤 경로도 broker.place_order 를 호출하지 않음
"""

from app.auto_paper.loop import (
    AutoPaperLoop,
    AutoPaperState,
    AutoPaperStatus,
    LoopAlreadyRunningError,
    LoopNotRunningError,
    get_auto_paper_loop,
)

__all__ = [
    "AutoPaperLoop",
    "AutoPaperState",
    "AutoPaperStatus",
    "LoopAlreadyRunningError",
    "LoopNotRunningError",
    "get_auto_paper_loop",
]
