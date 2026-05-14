"""KIS Paper One-Click Test (#89).

한투 모의투자 API 를 사용한 *원클릭* AI 자동매매 모의 테스트 orchestration.

본 패키지의 핵심 invariant (CLAUDE.md 절대 원칙 + #89):

1. **실계좌 주문 0건** — `KisBrokerAdapter(is_paper=True)` 또는 `MockBroker` 만
   사용. `place_order(is_paper=False)` 는 `NotImplementedError` (kis.py L181).
2. **KIS_IS_PAPER=false 면 BLOCKED** — readiness 가 즉시 거부.
3. **ENABLE_LIVE_TRADING=true 면 BLOCKED** — readiness 거부.
4. **ENABLE_AI_EXECUTION=true 면 BLOCKED** — readiness 거부.
5. 모든 주문은 기존 sanctioned 경로 (`route_order` → `RiskManager` →
   `PermissionGate` → `OrderExecutor`) 그대로 — 본 패키지는 *우회* 0건.
6. **MockBroker 로의 silent fallback 0건** — KIS paper 실패 시 사용자에게
   친화 메시지로 surface 하며 자동으로 mock 으로 swap 하지 *않는다*.
7. 호출 속도 제한: 주문 사이 ≥3초, quick 모드 ≤3건, slow 모드 ≤50건.
8. 최소 주문 금액 (`min_paper_test_notional`) 10,000원 기본 — 1회 주문
   금액이 그 이하면 quantity 감소.

본 패키지는 broker / OrderExecutor / route_order 모듈을 *직접* import 한다 —
하지만 *오케스트레이션* 만 한다 (가드 우회 0건).
"""

from app.kis_paper.readiness import (
    BlockedReason,
    KisPaperReadiness,
    evaluate_readiness,
)
from app.kis_paper.scoring import KisPaperScore, score_run
from app.kis_paper.engine import (
    KisPaperEngine,
    KisPaperRunReport,
    KisPaperRunState,
    TestMode,
    get_engine,
)

__all__ = [
    "BlockedReason",
    "KisPaperReadiness",
    "evaluate_readiness",
    "KisPaperScore",
    "score_run",
    "KisPaperEngine",
    "KisPaperRunReport",
    "KisPaperRunState",
    "TestMode",
    "get_engine",
]
