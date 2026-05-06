from abc import ABC, abstractmethod

from app.backtest.types import Bar, Signal


# 131: 각 전략은 단순한 on_bar 시그널 생산자가 아니라 운영자가 사후 분석할 때
# "어떤 조건에서 진입했고 어떻게 청산하며 무엇이 신호를 무효화하는가"를 코드와
# 함께 명시한 contract여야 한다. CLAUDE.md의 "수익률보다 손실 방어와 감사
# 로그"와 같은 맥락 — 전략 자체도 운영자/감사가 읽을 수 있는 형태로 둔다.
#
# Class-level attributes로 두므로 Python의 일반적인 override 메커니즘으로
# 상속받아 채울 수 있고, frontend는 /api/strategies/registry 응답을 통해
# 그대로 surface한다 (build_strategy 시 instance 만들지 않고도 metadata 조회).
class Strategy(ABC):
    """공용 Strategy 추상클래스.

    구현체는 아래 metadata를 class-level로 채워야 한다. None을 그대로 두면
    describe_strategy() 응답에서 빈 문자열/빈 dict로 직렬화되며, 운영자가
    "이 전략은 미완성"이라는 신호로 인지한다.
    """

    # 사람이 읽는 진입 조건 설명. 코드 한 줄 또는 짧은 문장.
    entry:        str = ""
    # 청산 조건 — Take-profit / 시그널 반전 / 시간 종료 등.
    exit:         str = ""
    # 무효화 조건 — "이 신호가 더 이상 유효하지 않다"고 판단하는 기준.
    # (예: stop loss hit, 규제/공시, 거래정지)
    invalidation: str = ""
    # 시장 체제 — "trending_up" / "trending_down" / "ranging" / "any" / "high_vol".
    # 미래 Market Regime detector와 매칭하기 위한 hint이며 현재는 문자열로 기록.
    required_regime: str = "any"
    # 리스크 프로파일 — RiskManager가 직접 적용하는 limit이 아니라 운영자가
    # "이 전략은 어느 정도의 위험 범위에서 동작 가능한지" 표현하는 metadata.
    # 예: {"position_size_pct": 5, "stop_loss_pct": 2, "max_concurrent": 1}.
    risk_profile: dict = {}

    @abstractmethod
    def on_bar(self, bars: list[Bar]) -> Signal:
        """현재 봉까지의 히스토리를 받아 다음 행동 신호를 반환."""
