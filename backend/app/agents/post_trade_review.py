"""P-27: PostTradeReviewAgent — decision episode 종료 후 판단 품질 복기.

각 episode 의 시장 스냅샷 / 4전략 vote / Agent Council 판단 / 주문 품질 / 사후
성과(P-25) / 매도 사유(P-26) 를 read-only 로 종합해 "이 판단이 좋았는가/나빴는가
+ 무엇을 개선할 수 있는가" 를 복기 등급 + 태그 + 개선 제안으로 기록한다.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *복기/분석 전용* — broker / OrderExecutor / route_order / 외부 HTTP
  import 0건, 주문을 만들거나 전송하지 않는다.
- **복기 결과는 *다음 주문 신호가 아니다*** — `PostTradeReview.is_order_signal=False`
  / `is_live_authorization=False` / `auto_apply_allowed=False` / `contains_secret=False`
  영구. 즉시 BUY/SELL 트리거로 사용 금지.
- secret / API key / 계좌번호 carry 0건.
- 결정적(deterministic) — 같은 episode → 같은 복기. 성과 데이터 부족 시
  DATA_INSUFFICIENT 로 남기며 episode 를 실패시키지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── 복기 상태 ──
STATUS_COMPLETE          = "COMPLETE"
STATUS_PENDING           = "PENDING"
STATUS_DATA_INSUFFICIENT = "DATA_INSUFFICIENT"

# ── 복기 등급 ──
GRADE_GOOD              = "GOOD"
GRADE_BAD               = "BAD"
GRADE_NEUTRAL           = "NEUTRAL"
GRADE_DATA_INSUFFICIENT = "DATA_INSUFFICIENT"

# ── 복기 태그 (10종 — BUY/SELL/HOLD 주문 신호 아님) ──
GOOD_DECISION       = "GOOD_DECISION"
BAD_DECISION        = "BAD_DECISION"
LATE_EXIT           = "LATE_EXIT"
EARLY_ENTRY         = "EARLY_ENTRY"
FALSE_BREAKOUT      = "FALSE_BREAKOUT"
MISSED_OPPORTUNITY  = "MISSED_OPPORTUNITY"
AVOIDED_LOSS        = "AVOIDED_LOSS"
WEAK_SIGNAL_ENTRY   = "WEAK_SIGNAL_ENTRY"
RISK_SAVED_TRADE    = "RISK_SAVED_TRADE"
IMPROVE_EXIT_TIMING = "IMPROVE_EXIT_TIMING"

VALID_TAGS: tuple[str, ...] = (
    GOOD_DECISION, BAD_DECISION, LATE_EXIT, EARLY_ENTRY, FALSE_BREAKOUT,
    MISSED_OPPORTUNITY, AVOIDED_LOSS, WEAK_SIGNAL_ENTRY, RISK_SAVED_TRADE,
    IMPROVE_EXIT_TIMING,
)

# primary_tag 선정 우선순위 (가장 구체적/중요한 것이 먼저).
_PRIMARY_PRIORITY: tuple[str, ...] = (
    RISK_SAVED_TRADE, AVOIDED_LOSS, FALSE_BREAKOUT, WEAK_SIGNAL_ENTRY,
    MISSED_OPPORTUNITY, LATE_EXIT, EARLY_ENTRY, IMPROVE_EXIT_TIMING,
    BAD_DECISION, GOOD_DECISION,
)

# 태그 → 한국어 개선 제안.
_SUGGESTION: dict[str, str] = {
    LATE_EXIT:           "MFE 대비 종가 수익률이 낮음 — 익절/청산 타이밍 보완 필요",
    IMPROVE_EXIT_TIMING: "고점 대비 반납이 큼 — 트레일링/부분 익절로 청산 규칙 개선 검토",
    EARLY_ENTRY:         "진입 직후 역행 발생 — 진입 트리거를 한 박자 늦추는 필터 검토",
    FALSE_BREAKOUT:      "돌파 실패(가짜 돌파) — 거래량/리테스트 확인 필터 추가 검토",
    WEAK_SIGNAL_ENTRY:   "약한 신호로 진입 — 최소 confidence/quality 임계 상향 검토",
    MISSED_OPPORTUNITY:  "관망 후 상승 — 진입 임계가 과도하게 보수적이었는지 검토",
    BAD_DECISION:        "손실로 종료 — 진입 근거/시장 국면 정합성 재점검",
}


@dataclass(frozen=True)
class ReviewThresholds:
    """복기 판정 임계 (조정 가능, default 보수적)."""
    late_exit_giveback_pct:   float = 1.0    # MFE - return_close >= → 늦은 청산
    early_entry_drawdown_pct: float = -0.5   # return_5m <= → 진입 직후 역행
    weak_confidence:          float = 0.55   # council confidence(0~1) 미만 → 약한 신호
    weak_quality:             int   = 60      # quality_score 미만 → 약한 신호
    flat_threshold_pct:       float = 0.1     # |return| < → 중립


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PostTradeReview:
    """거래 복기 결과 — episode.review 에 저장되는 안전 payload.

    **주문 신호가 아니다.** is_order_signal / is_live_authorization /
    auto_apply_allowed / contains_secret 모두 영구 False.
    """

    review_status:   str
    grade:           str
    primary_tag:     str | None = None
    tags:            list[str] = field(default_factory=list)
    summary:         str = ""
    improvement_suggestions: list[str] = field(default_factory=list)
    evidence:        dict[str, Any] = field(default_factory=dict)
    evaluated_action: str | None = None

    contains_secret:       bool = False
    is_order_signal:       bool = False
    is_live_authorization: bool = False
    auto_apply_allowed:    bool = False

    def __post_init__(self) -> None:
        if self.contains_secret is not False:
            raise ValueError("PostTradeReview.contains_secret must be False")
        if self.is_order_signal is not False:
            raise ValueError("PostTradeReview.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("PostTradeReview.is_live_authorization must be False")
        if self.auto_apply_allowed is not False:
            raise ValueError("PostTradeReview.auto_apply_allowed must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_status":   self.review_status,
            "grade":           self.grade,
            "primary_tag":     self.primary_tag,
            "tags":            list(self.tags),
            "summary":         self.summary,
            "improvement_suggestions": list(self.improvement_suggestions),
            "evidence":        dict(self.evidence),
            "evaluated_action": self.evaluated_action,
            "contains_secret":       False,
            "is_order_signal":       False,
            "is_live_authorization": False,
            "auto_apply_allowed":    False,
        }

    def review_summary(self) -> dict[str, Any]:
        return {
            "review_status": self.review_status,
            "grade":         self.grade,
            "primary_tag":   self.primary_tag,
            "summary":       self.summary,
        }


def _pick_primary(tags: list[str]) -> str | None:
    for t in _PRIMARY_PRIORITY:
        if t in tags:
            return t
    return tags[0] if tags else None


def _selected_strategies(episode: dict) -> list[str]:
    sel = episode.get("selected_strategies")
    if isinstance(sel, (list, tuple)) and sel:
        return [str(s).upper() for s in sel]
    c = episode.get("council")
    if isinstance(c, dict):
        sel = c.get("selected_strategies")
        if isinstance(sel, (list, tuple)):
            return [str(s).upper() for s in sel]
    return []


def review_episode(
    episode: dict[str, Any],
    *,
    thresholds: ReviewThresholds | None = None,
) -> PostTradeReview:
    """episode dict → PostTradeReview (deterministic, 예외 0).

    성과(outcome) 가 COMPLETE/PARTIAL 이어야 등급 산정 — PENDING/UNAVAILABLE 이거나
    없으면 DATA_INSUFFICIENT. 복기 결과는 *주문 신호가 아니다*.
    """
    thr = thresholds or ReviewThresholds()
    action = str(episode.get("final_action", "") or "").upper()
    outcome = episode.get("outcome") if isinstance(episode.get("outcome"), dict) else {}
    o_status = str(outcome.get("status", "") or "").upper()

    # 1. 성과 데이터 부족 → 복기 보류.
    if not outcome or o_status in ("", "PENDING", "UNAVAILABLE"):
        return PostTradeReview(
            review_status=STATUS_DATA_INSUFFICIENT,
            grade=GRADE_DATA_INSUFFICIENT,
            evaluated_action=action or None,
            summary="성과 데이터가 부족하여 복기를 보류합니다(성과 라벨링 후 재복기).",
            evidence={"outcome_status": o_status or "NONE"},
        )

    label = str(outcome.get("label", "") or "").upper()
    r5 = _f(outcome.get("return_5m"))
    r_close = _f(outcome.get("return_close"))
    mfe = _f(outcome.get("max_favorable_excursion"))
    mae = _f(outcome.get("max_adverse_excursion"))
    # 최종 수익률 — 종가 우선, 없으면 마지막 horizon.
    final_ret = r_close
    if final_ret is None:
        for k in ("return_60m", "return_30m", "return_10m", "return_5m"):
            v = _f(outcome.get(k))
            if v is not None:
                final_ret = v
                break

    council = episode.get("council") if isinstance(episode.get("council"), dict) else {}
    conf = _f(council.get("confidence"))
    if conf is None:
        # episode.confidence 는 0~100 정수 — 0~1 로 정규화.
        ec = _f(episode.get("confidence"))
        conf = (ec / 100.0) if ec is not None else None
    quality = _f(episode.get("quality_score"))
    if quality is None:
        quality = _f(council.get("quality_score"))
    sell_reason = {}
    if isinstance(council.get("sell_reason"), dict):
        sell_reason = council["sell_reason"]
    sell_code = str(sell_reason.get("reason_code", "") or "").upper()
    strategies = _selected_strategies(episode)

    tags: list[str] = []
    weak_signal = bool(
        (conf is not None and conf < thr.weak_confidence)
        or (quality is not None and quality < thr.weak_quality)
    )

    # 2. 등급 — 성과 라벨 + 행동 기준.
    favorable = label in ("PROFITABLE", "AVOIDED_LOSS")
    unfavorable = label in ("LOSS", "MISSED_OPPORTUNITY")
    if favorable:
        grade = GRADE_GOOD
    elif unfavorable:
        grade = GRADE_BAD
    else:
        grade = GRADE_NEUTRAL

    # 3. 태그.
    if action == "HOLD":
        if label == "MISSED_OPPORTUNITY":
            tags.append(MISSED_OPPORTUNITY)
        elif label == "AVOIDED_LOSS":
            tags.append(AVOIDED_LOSS)
    else:
        # BUY / SELL.
        if label == "PROFITABLE":
            tags.append(GOOD_DECISION)
        elif label == "LOSS":
            tags.append(BAD_DECISION)
            if action == "BUY" and weak_signal:
                tags.append(WEAK_SIGNAL_ENTRY)
            # 돌파 실패: ORB(돌파) 전략 진입인데 손실 + 단기 역행.
            if action == "BUY" and ("ORB" in strategies) and (
                    (r5 is not None and r5 < 0) or (final_ret is not None and final_ret < 0)):
                tags.append(FALSE_BREAKOUT)

        # SELL 이 위험 회피 사유로 손실을 막았는가 (favorable = 청산 후 추가 하락).
        if action == "SELL" and favorable and sell_code in (
                "STOP_LOSS", "TRAILING_STOP", "RISK_REDUCTION"):
            tags.append(RISK_SAVED_TRADE)

        # 진입 직후 역행 후 회복 → 진입 시점이 일렀다.
        if action == "BUY" and r5 is not None and r5 <= thr.early_entry_drawdown_pct \
                and final_ret is not None and final_ret > 0:
            tags.append(EARLY_ENTRY)

        # 고점 대비 반납 큼 → 늦은 청산 / 익절 타이밍 개선.
        # (의미있는 고점이 있었고(mfe ≥ 임계), 그 고점 대비 반납이 큰 경우만.)
        if mfe is not None and final_ret is not None \
                and mfe >= thr.late_exit_giveback_pct \
                and (mfe - final_ret) >= thr.late_exit_giveback_pct:
            tags.append(LATE_EXIT)
            tags.append(IMPROVE_EXIT_TIMING)

    # weak signal 인데 아직 태그 없고 BUY 면 명시.
    if action == "BUY" and weak_signal and WEAK_SIGNAL_ENTRY not in tags \
            and label != "PROFITABLE":
        tags.append(WEAK_SIGNAL_ENTRY)

    tags = list(dict.fromkeys(tags))  # de-dup, 순서 보존.
    primary = _pick_primary(tags)

    # 4. 개선 제안.
    suggestions: list[str] = []
    for t in tags:
        s = _SUGGESTION.get(t)
        if s and s not in suggestions:
            suggestions.append(s)

    # 5. 요약 문장.
    summary = _build_summary(action, grade, primary, label, strategies,
                             final_ret, mfe, sell_code)

    return PostTradeReview(
        review_status=STATUS_COMPLETE,
        grade=grade,
        primary_tag=primary,
        tags=tags,
        summary=summary,
        improvement_suggestions=suggestions,
        evidence={
            "outcome_status": o_status,
            "outcome_label":  label,
            "return_close":   r_close,
            "return_5m":      r5,
            "max_favorable_excursion": mfe,
            "max_adverse_excursion":   mae,
            "confidence":     conf,
            "quality_score":  quality,
            "selected_strategies": strategies,
            "sell_reason_code": sell_code or None,
        },
        evaluated_action=action or None,
    )


def _build_summary(action, grade, primary, label, strategies, final_ret, mfe,
                   sell_code) -> str:
    strat_txt = "+".join(strategies) if strategies else "전략"
    ret_txt = (f"{final_ret:+.2f}%" if final_ret is not None else "n/a")
    if primary == GOOD_DECISION:
        return f"{strat_txt} 진입이 수익({ret_txt})으로 연결됨 — 좋은 판단."
    if primary == RISK_SAVED_TRADE:
        return f"{sell_code or '위험 회피'} 청산으로 추가 손실을 회피함 — 좋은 판단."
    if primary == AVOIDED_LOSS:
        return "관망(HOLD)이 이후 하락을 피함 — 좋은 판단."
    if primary == MISSED_OPPORTUNITY:
        return f"관망 후 상승({ret_txt}) — 진입 기회를 놓침."
    if primary == FALSE_BREAKOUT:
        return f"{strat_txt} 돌파가 실패(가짜 돌파)하여 손실({ret_txt})."
    if primary == WEAK_SIGNAL_ENTRY:
        return f"약한 신호로 진입하여 손실({ret_txt})."
    if primary == LATE_EXIT or primary == IMPROVE_EXIT_TIMING:
        mfe_txt = (f"{mfe:+.2f}%" if mfe is not None else "n/a")
        return f"고점({mfe_txt}) 대비 종가({ret_txt}) 반납 — 청산 타이밍 보완 필요."
    if primary == EARLY_ENTRY:
        return f"진입 직후 역행 후 회복({ret_txt}) — 진입이 다소 일렀음."
    if primary == BAD_DECISION:
        return f"손실({ret_txt})로 종료 — 진입 근거 재점검 필요."
    if grade == GRADE_NEUTRAL:
        return f"중립적 결과({ret_txt}) — 특이 개선점 없음."
    return f"복기 완료 ({label or 'n/a'}, {ret_txt})."


def review_summary_for(review: dict[str, Any] | None) -> dict[str, Any]:
    """review dict → 목록 표시용 요약."""
    if not isinstance(review, dict) or not review.get("review_status"):
        return {}
    return {
        "review_status": review.get("review_status"),
        "grade":         review.get("grade"),
        "primary_tag":   review.get("primary_tag"),
        "summary":       review.get("summary"),
    }


__all__ = [
    "PostTradeReview", "ReviewThresholds", "review_episode", "review_summary_for",
    "VALID_TAGS",
    "STATUS_COMPLETE", "STATUS_PENDING", "STATUS_DATA_INSUFFICIENT",
    "GRADE_GOOD", "GRADE_BAD", "GRADE_NEUTRAL", "GRADE_DATA_INSUFFICIENT",
    "GOOD_DECISION", "BAD_DECISION", "LATE_EXIT", "EARLY_ENTRY", "FALSE_BREAKOUT",
    "MISSED_OPPORTUNITY", "AVOIDED_LOSS", "WEAK_SIGNAL_ENTRY", "RISK_SAVED_TRADE",
    "IMPROVE_EXIT_TIMING",
]
