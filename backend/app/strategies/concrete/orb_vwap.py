from app.backtest.types import Bar, Signal
from app.strategies.base import Strategy


class OrbVwapStrategy(Strategy):
    """Opening Range Breakout + VWAP 결합 전략.

    오프닝 N분 동안 형성된 가격 범위(ORB)를 돌파하고 VWAP 위에서 확정될 때
    추세 방향으로 진입. 단타 자동매매에서 가장 자주 쓰이는 패턴 중 하나.

    NOTE (131): 현재는 stub. on_bar는 항상 HOLD를 반환하며 어떤 시그널도
    생산하지 않는다. metadata만 명시해 운영자/감사가 "예정된 전략의 contract"
    를 미리 검토할 수 있게 한다. 실제 구현은 별도 PR로 추가 예정.
    """

    entry = (
        "오프닝 ORB 윈도우(기본 30분) 형성 후, 봉 마감이 ORB 상단을 돌파하고 "
        "동시에 VWAP 위라면 BUY"
    )
    exit = (
        "익일 종가, ORB 중간선 회귀, 또는 VWAP 하향 이탈 중 가장 먼저 발생한 시점"
    )
    invalidation = (
        "VWAP 하향 이탈 후 5분 이상 회복 실패, 또는 ORB 하단 재진입"
    )
    required_regime = "trending_up"  # 강한 단방향 추세에서 가장 잘 작동
    risk_profile = {
        "position_size_pct": 5,
        "stop_loss_pct":     1.5,  # 단기 변동 흡수
        "max_concurrent":    2,
    }

    def __init__(self, orb_minutes: int = 30, vwap_window: int = 60):
        # TODO(131-followup): 실제 ORB 윈도우 추적 + VWAP 계산 + 돌파 판정 구현.
        if orb_minutes < 1 or vwap_window < 1:
            raise ValueError("ORB / VWAP windows must be positive")
        self.orb_minutes = orb_minutes
        self.vwap_window = vwap_window

    def on_bar(self, bars: list[Bar]) -> Signal:
        # TODO(131-followup): ORB high/low 추적, VWAP 누적 계산, 돌파 시 BUY.
        # 현재는 어떤 신호도 만들지 않아 자동매매 안전성에 영향 없음.
        return Signal.HOLD
