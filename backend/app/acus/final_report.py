"""ACUS Stage 08 — 최종 리포트 (verdict + markdown).

verdict (FINAL_ROBUST 개수 N 기준):
  N ≥ 30        → STRONG_CANDIDATE_POOL_FOUND  (pool = STRONG_CANDIDATE_POOL)
  10 ≤ N < 30   → WEAK                          (pool = DATE_POOL — 추가 검증 필요)
  N < 10        → INSUFFICIENT                  (pool = INSUFFICIENT_POOL)

리포트는 *연구 자료* 이며 자동 적용 / 실전 전환 승인 / 주문 신호가 아니다. 운영자
명시 승인 + Paper 리허설 등 별도 절차 후에만 다음 단계로 진행 가능.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.acus import types as T


def _verdict_and_pool(n: int) -> "tuple[str, str]":
    if n >= T.POOL_STRONG_MIN:
        return T.VERDICT_STRONG, T.POOL_STRONG
    if n >= T.POOL_DATE_MIN:
        return T.VERDICT_WEAK, T.POOL_DATE
    return T.VERDICT_INSUFFICIENT, T.POOL_INSUFFICIENT


def _next_step_for(verdict: str) -> str:
    if verdict == T.VERDICT_STRONG:
        return ("Paper rehearsal 진입 검토 가치 있음 — 단, 운영자 명시 승인 + Paper 100건 / "
                "28거래일 표본 확보 + 별도 게이트 필요. 실거래 전환은 본 결과만으로 불가.")
    if verdict == T.VERDICT_WEAK:
        return "추가 데이터 (더 긴 기간 / 더 많은 종목) 또는 다른 timeframe 검토."
    return "한국 단타 5분봉 한계 — 일봉 / 미국 주식 / 다른 자산군 방향 전환 검토."


@dataclass(frozen=True)
class ACUSFinalReport:
    generated_at: str
    universe_size: int
    final_robust_count: int
    verdict: str
    pool_classification: str
    one_line_conclusion: str
    funnel: dict[str, Any]
    final_robust_symbols: "tuple[str, ...]"
    final_robust_detail: "tuple[dict[str, Any], ...]" = ()
    next_step: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    notes: "tuple[str, ...]" = ()

    is_order_signal: bool = False
    auto_apply_allowed: bool = False
    applied_to_runtime: bool = False
    is_live_authorization: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True
    disclaimer: str = T.DISCLAIMER_KO
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        T._assert_safety(self)
        if self.verdict not in (T.VERDICT_STRONG, T.VERDICT_WEAK, T.VERDICT_INSUFFICIENT):
            raise ValueError(f"invalid verdict: {self.verdict}")
        if self.pool_classification not in (T.POOL_STRONG, T.POOL_DATE, T.POOL_INSUFFICIENT):
            raise ValueError(f"invalid pool_classification: {self.pool_classification}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "universe_size": int(self.universe_size),
            "final_robust_count": int(self.final_robust_count),
            "verdict": self.verdict,
            "pool_classification": self.pool_classification,
            "one_line_conclusion": self.one_line_conclusion,
            "next_step": self.next_step,
            "funnel": dict(self.funnel),
            "final_robust_symbols": list(self.final_robust_symbols),
            "final_robust_detail": [dict(d) for d in self.final_robust_detail],
            "notes": list(self.notes),
            "is_order_signal": False,
            "auto_apply_allowed": False,
            "applied_to_runtime": False,
            "is_live_authorization": False,
            "contains_secret": False,
            "no_profit_guarantee": True,
            "disclaimer": self.disclaimer,
        }


def build_final_report(
    integration: dict[str, Any],
    *,
    started_at: str | None = None,
    completed_at: str | None = None,
    notes: "list[str] | None" = None,
    generated_at: str | None = None,
) -> ACUSFinalReport:
    gen = generated_at or datetime.now(timezone.utc).isoformat()
    n = int(integration.get("final_robust_count", 0))
    verdict, pool = _verdict_and_pool(n)
    one_line = {
        T.VERDICT_STRONG: f"FINAL_ROBUST {n}종목 발견 — Paper 리허설 검토 가치 있음 (실전 승인 아님).",
        T.VERDICT_WEAK: f"FINAL_ROBUST {n}종목 — 추가 검증 필요 (DATE_POOL).",
        T.VERDICT_INSUFFICIENT: f"FINAL_ROBUST {n}종목 — 5분봉 알파 부족, 다른 timeframe/시장 검토.",
    }[verdict]
    detail = tuple(integration.get("final_robust") or ())
    syms = tuple(integration.get("final_robust_symbols") or ())
    return ACUSFinalReport(
        generated_at=gen,
        universe_size=int(integration.get("universe_size", 0)),
        final_robust_count=n,
        verdict=verdict,
        pool_classification=pool,
        one_line_conclusion=one_line,
        funnel=dict(integration.get("funnel", {})),
        final_robust_symbols=syms,
        final_robust_detail=detail,
        next_step=_next_step_for(verdict),
        started_at=started_at,
        completed_at=completed_at,
        notes=tuple(notes or ()),
    )


def render_markdown(r: ACUSFinalReport) -> str:
    lines: list[str] = []
    lines.append(f"# ACUS 최종 리포트 — {r.verdict}")
    lines.append("")
    lines.append("> ⚠ 본 리포트는 다중 에이전트 교차검증 *연구 자료* 이며, 자동 적용 / 실전 전환")
    lines.append("> 승인 / 주문 신호가 아닙니다. 실거래 진입은 운영자 명시 승인 + Paper 리허설 +")
    lines.append("> 별도 게이트 절차가 필요합니다. 수익을 보장하지 않습니다.")
    lines.append("")
    lines.append("## 1. 개요")
    lines.append(f"- generated_at: `{r.generated_at}`")
    if r.started_at:
        lines.append(f"- started_at: `{r.started_at}`")
    if r.completed_at:
        lines.append(f"- completed_at: `{r.completed_at}`")
    lines.append(f"- universe_size: **{r.universe_size}**")
    lines.append(f"- final_robust_count: **{r.final_robust_count}**")
    lines.append(f"- verdict: **{r.verdict}** ({r.pool_classification})")
    lines.append(f"- one_line: {r.one_line_conclusion}")
    lines.append("")
    lines.append("## 2. Funnel (전체 universe 결과 — cherry-picking 아님)")
    for k, v in r.funnel.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## 3. FINAL_ROBUST 종목 (점수 내림차순)")
    if not r.final_robust_detail:
        lines.append("- (해당 종목 없음)")
    else:
        lines.append("| symbol | score | backtest | regime | liquidity | news | risk |")
        lines.append("|---|---|---|---|---|---|---|")
        for d in r.final_robust_detail:
            lines.append(
                f"| {d.get('symbol')} | {d.get('score')} | "
                f"{d.get('backtest_class')} | {d.get('regime_class')} | "
                f"{d.get('liquidity_class')} | {d.get('news_class')} | "
                f"{d.get('risk_class')} |"
            )
    lines.append("")
    lines.append("## 4. 다음 단계 제안")
    lines.append(f"- {r.next_step}")
    lines.append("")
    lines.append("## 5. 안전 invariant")
    lines.append("- is_order_signal: false")
    lines.append("- auto_apply_allowed: false")
    lines.append("- applied_to_runtime: false")
    lines.append("- is_live_authorization: false")
    lines.append("- contains_secret: false")
    lines.append("- no_profit_guarantee: true")
    lines.append("")
    if r.notes:
        lines.append("## 6. 노트")
        for n in r.notes:
            lines.append(f"- {n}")
        lines.append("")
    lines.append("---")
    lines.append("⚠ 실거래 승인 아님 · 주문은 KIS 모의 한정 · 수익 보장 아님")
    lines.append("⚠ 무인 운영 결과이며 운영자 검토 필요 · Paper 자동 진입 0 · 명시 승인 필요")
    return "\n".join(lines)
