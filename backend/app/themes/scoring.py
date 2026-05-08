"""테마 시그널 점수/등급 계산 (#22).

순수 함수 — DB / network / broker 의존성 0건. 점수는 0~100 정수,
등급은 STRONG / WATCH / WEAK / IGNORE.

본 모듈은 BUY/SELL/HOLD를 반환하지 않는다 (CLAUDE.md 절대 원칙). 점수와
등급은 *후보 필터* 신호로만 사용되며, 주문 결정은 RiskManager →
PermissionGate → OrderExecutor 단일 경로에서만 만들어진다.
"""

from __future__ import annotations


# 등급 임계 — `theme_signal_policy.md`와 lockstep.
GRADE_STRONG = 80
GRADE_WATCH  = 60
GRADE_WEAK   = 30
# < GRADE_WEAK 이면 IGNORE.

# 신뢰도가 낮으면 score를 깎아서 노이즈가 STRONG으로 분류되지 않도록.
LOW_CONFIDENCE_FACTOR = 0.6
LOW_CONFIDENCE_THRESHOLD = 50


def compute_theme_score(
    *,
    raw_score:  int | float,
    confidence: int = 100,
    related_symbol_count: int = 1,
    keyword_count:        int = 1,
) -> int:
    """raw_score(0~100) + confidence + 신호 풍부도를 묶어 0~100 정수 score.

    - raw_score: provider가 보고한 강도 (예: Google Trends interest, news mention 빈도).
    - confidence: provider/agent의 자신감. 50 미만이면 LOW_CONFIDENCE_FACTOR 적용.
    - related_symbol_count / keyword_count: 보강 요소. 1개씩이면 score 그대로,
      여러 종목/키워드면 약간 가산. 후보군이 명확할수록 신호가 의미 있음.
    """
    base = float(max(0, min(100, raw_score)))
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        base *= LOW_CONFIDENCE_FACTOR

    # 다양성 보강: 관련 심볼 ≥3 이면 +5, 키워드 ≥3 이면 +3 (각 cap).
    bonus = 0
    if related_symbol_count >= 3:
        bonus += 5
    if keyword_count >= 3:
        bonus += 3
    score = base + bonus
    return int(round(max(0.0, min(100.0, score))))


def grade_theme_signal(score: int) -> str:
    """0~100 → STRONG / WATCH / WEAK / IGNORE."""
    s = max(0, min(100, score))
    if s >= GRADE_STRONG:
        return "STRONG"
    if s >= GRADE_WATCH:
        return "WATCH"
    if s >= GRADE_WEAK:
        return "WEAK"
    return "IGNORE"
