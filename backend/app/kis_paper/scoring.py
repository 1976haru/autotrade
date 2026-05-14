"""KIS Paper one-click test scoring (#89).

테스트 실행 결과를 0~100 점수 + 4 등급으로 평가.

본 모듈은 broker / OrderExecutor / route_order import 0건 — 순수 함수.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Grade(StrEnum):
    """4단계 등급. 본 enum 의 어떤 값도 *실거래 가능* 을 의미하지 않는다."""
    LONG_TERM_PAPER_CANDIDATE = "LONG_TERM_PAPER_CANDIDATE"  # 90~100
    PAPER_NEEDS_MORE          = "PAPER_NEEDS_MORE"           # 75~89
    STABILITY_FIX_NEEDED      = "STABILITY_FIX_NEEDED"       # 60~74
    DO_NOT_PROMOTE_TO_LIVE    = "DO_NOT_PROMOTE_TO_LIVE"     # 0~59


_GRADE_LABEL = {
    Grade.LONG_TERM_PAPER_CANDIDATE: "장기 Paper/Shadow 검증 후보",
    Grade.PAPER_NEEDS_MORE:          "Paper 추가 검증 필요",
    Grade.STABILITY_FIX_NEEDED:      "전략/주문 안정성 보완 필요",
    Grade.DO_NOT_PROMOTE_TO_LIVE:    "실전 검토 금지",
}


@dataclass(frozen=True)
class KisPaperScore:
    """0~100 + breakdown + 등급. **'실거래 가능' 라벨 0개 invariant**."""
    total:                int
    grade:                Grade
    grade_label:          str
    breakdown:            dict
    # 운영자에게 보여줄 1줄 평가. *"실거래 가능"* 류 금지 단어 미포함.
    one_liner:            str
    # invariant — 본 점수는 *실거래 활성화 라벨이 아니다*.
    is_live_authorization: bool = False
    is_order_signal:      bool = False
    # 0~5 의 attention badges (예: "rate_limit_hit", "api_error_burst")
    attention_flags:      tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.is_live_authorization is not False:
            raise ValueError(
                "KisPaperScore.is_live_authorization must be False — "
                "본 점수는 실거래 활성화 라벨이 아닙니다."
            )
        if self.is_order_signal is not False:
            raise ValueError("KisPaperScore.is_order_signal must be False")
        if not (0 <= int(self.total) <= 100):
            raise ValueError(f"total must be 0-100, got {self.total}")

        # 금지 단어 — 점수 문구에 들어가면 ValueError.
        banned = (
            "실거래 가능", "실거래 시작", "LIVE 가능", "LIVE 시작",
            "지금 매수", "지금 매도", "Place Order",
        )
        for w in banned:
            if w in self.one_liner:
                raise ValueError(
                    f"KisPaperScore.one_liner contains banned phrase: '{w}'"
                )

    def to_dict(self) -> dict:
        return {
            "total":                 int(self.total),
            "grade":                 self.grade.value,
            "grade_label":           self.grade_label,
            "breakdown":             dict(self.breakdown),
            "one_liner":             self.one_liner,
            "is_live_authorization": False,
            "is_order_signal":       False,
            "attention_flags":       list(self.attention_flags),
        }


@dataclass(frozen=True)
class ScoreInput:
    """scoring 입력 — engine 의 실행 결과를 dataclass 로 추출."""
    readiness_passed:        bool
    kis_paper_connected:     bool        # quick / slow 모드에서 KIS API 응답 OK
    balance_fetched:         bool
    ai_signal_generated:     bool
    orders_attempted:        int
    orders_executed:         int
    orders_rejected:         int
    fills_observed:          int
    unfilled_count:          int
    positions_refreshed:     bool
    risk_block_observed:     bool
    audit_rows_missing:      int        # 의도 vs audit row 미스매치 수
    errors_count:            int        # API rate limit / 500 / etc.
    rate_limit_hits:         int
    mode_used:               str        # "quick" / "slow" / "mock"


def score_run(inp: ScoreInput) -> KisPaperScore:
    """ScoreInput → KisPaperScore.

    가중치 (10+15+10+10+15+10+10+10+5+5 = 100):
      - readiness 통과 10
      - KIS paper 연결 성공 15 (mock 모드에서는 "mock 연결" 로 동일 가산)
      - 잔고 조회 성공 10
      - AI 판단 성공 10
      - 모의 주문 1건 이상 성공 15
      - 체결/미체결 조회 10
      - 포지션/잔고 재조회 10
      - risk block 정상 동작 10 (한 번이라도 RiskManager 차단을 관찰했는가)
      - audit 누락 0건 5
      - 오류 없음 5
    """
    bd: dict = {}

    bd["readiness_passed"]   = 10 if inp.readiness_passed   else 0
    bd["broker_connected"]   = 15 if inp.kis_paper_connected else 0
    bd["balance_fetched"]    = 10 if inp.balance_fetched    else 0
    bd["ai_signal"]          = 10 if inp.ai_signal_generated else 0
    bd["orders_executed"]    = 15 if inp.orders_executed >= 1 else 0
    bd["fills_observed"]     = (
        10 if (inp.fills_observed + inp.unfilled_count) >= 1 else 0
    )
    bd["positions_refresh"]  = 10 if inp.positions_refreshed else 0
    bd["risk_block_ok"]      = 10 if inp.risk_block_observed else 0
    bd["audit_no_missing"]   = 5 if inp.audit_rows_missing == 0 else 0
    bd["no_errors"]          = 5 if inp.errors_count == 0 else 0

    total = sum(bd.values())

    if total >= 90:
        grade = Grade.LONG_TERM_PAPER_CANDIDATE
        one_liner = (
            f"좋음 ({total}/100): 모의투자에서 주문 흐름이 정상입니다. "
            "그래도 실전 전 장기 검증이 필요합니다."
        )
    elif total >= 75:
        grade = Grade.PAPER_NEEDS_MORE
        one_liner = (
            f"양호 ({total}/100): 핵심 흐름은 동작합니다. Paper 운영 추가로 "
            "전략별 데이터 축적이 필요합니다."
        )
    elif total >= 60:
        grade = Grade.STABILITY_FIX_NEEDED
        one_liner = (
            f"주의 ({total}/100): 주문은 됐지만 거절/미체결/오류가 관찰됩니다. "
            "전략 / 주문 안정성 보완 후 재테스트 권장."
        )
    else:
        grade = Grade.DO_NOT_PROMOTE_TO_LIVE
        one_liner = (
            f"위험 ({total}/100): 리스크 차단 또는 오류가 많습니다. "
            "실전 검토 금지 — readiness 와 오류 메시지 확인 필요."
        )

    flags: list[str] = []
    if inp.rate_limit_hits > 0:
        flags.append("rate_limit_hit")
    if inp.errors_count >= 3:
        flags.append("api_error_burst")
    if inp.audit_rows_missing > 0:
        flags.append("audit_drift")
    if inp.orders_attempted > 0 and inp.orders_executed == 0:
        flags.append("orders_all_rejected")

    return KisPaperScore(
        total=int(total),
        grade=grade,
        grade_label=_GRADE_LABEL[grade],
        breakdown=bd,
        one_liner=one_liner,
        attention_flags=tuple(flags),
    )
