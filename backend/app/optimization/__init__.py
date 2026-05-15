"""Strategy Optimization & Paper Readiness pipeline.

본 패키지는 *연구용* — 백테스트 그리드 서치 + 다중 지표 평가 + paper 후보
선정을 묶는다. 실 주문 / broker / AI client 호출 0건.

레이어:
- param_space: 전략별 grid 정의
- optimizer:   백테스트 실행 + metrics 계산
- paper_picker: 후보 선정 + ranking
"""

from app.optimization.optimizer import (
    OptimizationResult,
    evaluate_backtest,
    grid_search,
    grid_search_all,
)
from app.optimization.paper_picker import (
    PaperCandidate,
    PaperCandidateCriteria,
    pick_paper_candidates,
    rank_results,
)
from app.optimization.param_space import (
    ParamGrid,
    all_combinations,
    get_param_grid,
    supported_strategy_ids,
)

__all__ = [
    "OptimizationResult",
    "ParamGrid",
    "PaperCandidate",
    "PaperCandidateCriteria",
    "all_combinations",
    "evaluate_backtest",
    "get_param_grid",
    "grid_search",
    "grid_search_all",
    "pick_paper_candidates",
    "rank_results",
    "supported_strategy_ids",
]
