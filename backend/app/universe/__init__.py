"""Universe resolution package.

Auto Paper Loop / 진단(run-once) 흐름이 *후보 종목군(universe)* 을 결정할 때
사용한다. 관심종목(watchlist)이 비어 있어도 PAPER 테스트가 가능하도록 시가총액
상위 50 종목 fallback universe 를 제공한다.

**fallback universe 는 *투자 추천이 아니다*** — Paper 검증용 후보군일 뿐이며,
주문 신호 / 매수 추천이 아니다 (`is_order_signal=False` /
`is_investment_advice=False` 영구).
"""

from app.universe.default_universe import (
    FALLBACK_MARKET_CAP_TOP50,
    UniverseResolution,
    UniverseSource,
    get_default_universe,
)

__all__ = [
    "FALLBACK_MARKET_CAP_TOP50",
    "UniverseResolution",
    "UniverseSource",
    "get_default_universe",
]
