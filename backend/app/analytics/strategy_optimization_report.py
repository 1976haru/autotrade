"""3-08 — 운영자(비개발자)용 전략 최적화 Markdown 리포트.

3-02 (real data backtest) / 3-03 (parameter optimization) / 3-04 (walk-forward)
/ 3-05 (stress test) / 3-06 (성과 지표 표준화) / 3-07 (paper 후보 통합) 산출물을
*read-only* 로 종합해 비개발자가 이해할 수 있는 Markdown 2종을 생성:

- ``strategy_optimization_report.md`` — 12 섹션 상세 리포트
- ``operator_summary.md``             — 한 페이지 요약

핵심 원칙:
- **후보 0건도 리포트 생성** — "사용 가능한 전략이 아직 없습니다" + 사유 carry.
- **단계별 추적성** — 어느 단계에서 탈락했는지 운영자에게 명시.
- **비개발자 문체** — "지금 쓸 수 있다 / 아직 안 된다 / 데이터 부족 /
  모의투자에서 확인 필요" 의 4 라벨로 분류.

절대 invariant (테스트로 lock):
- 본 모듈은 *advisory only* — `OperatorReport.is_order_signal=False` /
  `auto_apply_allowed=False` / `is_live_authorization=False` 불변
  (dataclass `__post_init__` ValueError 가드).
- broker / OrderExecutor / route_order import 0건.
- 외부 HTTP / AI SDK / `app.core.config.get_settings` import 0건.
- secret / API key / 계좌번호 노출 0건.
- "지금 매수" / "지금 매도" / "Place Order" / "실거래 시작" /
  "ENABLE_LIVE_TRADING 토글" 라벨 0건.

판정 상태 (`ReportStatus`):
- ``READY_FOR_PAPER``    — 모든 단계 통과, paper 모의운용 검토 가능.
- ``NEED_MORE_DATA``     — 거래 횟수 / fold 부족 / 데이터 누락.
- ``REJECTED_BY_RISK``   — 백테스트 / 최적화 단계에서 risk 임계 위반.
- ``OVERFIT_RISK``       — walk-forward 가 OVERFIT_RISK verdict.
- ``STRESS_FAILED``      — stress test 시나리오 하나 이상 FAIL.
- ``NO_CANDIDATE``       — 종합적으로 paper 후보 자격 없음 (보수적 라벨).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.analytics.metrics import PERFORMANCE_METRIC_KEYS
from app.analytics.paper_candidate_aggregator import (
    AggregatedCandidate,
    AggregationInputs,
    PASS_VERDICTS_PER_STAGE,
    PipelineStage,
    aggregate_candidates,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. ReportStatus + StrategyEntry + OperatorReport 데이터 모델
# ─────────────────────────────────────────────────────────────────────────────


class ReportStatus(StrEnum):
    """6 상태 — 비개발자가 보고 "쓸 수 있는지 아닌지" 즉시 판단."""

    READY_FOR_PAPER  = "READY_FOR_PAPER"
    NEED_MORE_DATA   = "NEED_MORE_DATA"
    REJECTED_BY_RISK = "REJECTED_BY_RISK"
    OVERFIT_RISK     = "OVERFIT_RISK"
    STRESS_FAILED    = "STRESS_FAILED"
    NO_CANDIDATE     = "NO_CANDIDATE"


# 비개발자용 한 줄 설명. 결정/판단 도구가 아니라 "현재 상태" 표기.
_STATUS_LABEL_KO: dict[ReportStatus, str] = {
    ReportStatus.READY_FOR_PAPER:  "모의투자(Paper)에서 시작 검토 가능",
    ReportStatus.NEED_MORE_DATA:   "데이터 부족 — 더 모은 뒤 재평가",
    ReportStatus.REJECTED_BY_RISK: "위험 한도 위반 — 아직 사용 안 됨",
    ReportStatus.OVERFIT_RISK:     "과최적화 의심 — 아직 사용 안 됨",
    ReportStatus.STRESS_FAILED:    "스트레스 테스트 불합격 — 아직 사용 안 됨",
    ReportStatus.NO_CANDIDATE:     "현재 후보 자격 없음",
}


@dataclass(frozen=True)
class StrategyEntry:
    """단일 (strategy, symbol, params) 의 리포트 단위 결과."""

    strategy_id:        str
    display_name:       str
    symbol:             str
    params:             dict[str, Any]
    status:             ReportStatus
    pipeline_stages:    list[PipelineStage]    = field(default_factory=list)
    risk_metrics:       dict[str, Any]         = field(default_factory=dict)
    exclusion_reasons:  list[str]              = field(default_factory=list)
    risk_signals:       list[str]              = field(default_factory=list)
    score:              float                  = 0.0

    def __post_init__(self) -> None:
        # 후보 단위 invariant — 본 entry 는 advisory only.
        if not isinstance(self.status, ReportStatus):
            raise ValueError("status must be a ReportStatus")

    def passed_stages(self) -> list[str]:
        passed: list[str] = []
        for s in self.pipeline_stages:
            allowed = PASS_VERDICTS_PER_STAGE.get(s.name, set())
            if s.verdict in allowed:
                passed.append(s.name)
        return sorted(passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id":        self.strategy_id,
            "display_name":       self.display_name,
            "symbol":             self.symbol,
            "params":             dict(self.params),
            "status":             self.status.value,
            "status_label_ko":    _STATUS_LABEL_KO[self.status],
            "pipeline_stages":    [s.to_dict() for s in self.pipeline_stages],
            "passed_stages":      self.passed_stages(),
            "risk_metrics":       dict(self.risk_metrics),
            "exclusion_reasons":  list(self.exclusion_reasons),
            "risk_signals":       list(self.risk_signals),
            "score":              float(self.score),
            # advisory invariant.
            "is_order_signal":    False,
            "auto_apply_allowed": False,
        }


@dataclass(frozen=True)
class OperatorReport:
    """3-08 상위 리포트 — markdown 2종 generator 가 본 객체에서 렌더링."""

    generated_at:                 str
    overall_status:               ReportStatus
    paper_ready_count:            int
    excluded_count:               int
    entries:                      list[StrategyEntry] = field(default_factory=list)
    paper_candidates:             list[StrategyEntry] = field(default_factory=list)
    excluded:                     list[StrategyEntry] = field(default_factory=list)
    reasons_no_candidate:         list[str]           = field(default_factory=list)
    ai_agent_risk_signals:        list[str]           = field(default_factory=list)
    next_user_actions:            list[str]           = field(default_factory=list)
    metadata:                     dict[str, Any]      = field(default_factory=dict)

    # 절대 invariant — 본 리포트는 *advisory only*. 자동 paper trader 시작 /
    # 자동 실거래 활성화 / 자동 promotion 변경 의미 *없음*.
    is_order_signal:              bool = False
    auto_apply_allowed:           bool = False
    is_live_authorization:        bool = False
    is_investment_advice:         bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("OperatorReport.is_order_signal must be False")
        if self.auto_apply_allowed is not False:
            raise ValueError("OperatorReport.auto_apply_allowed must be False")
        if self.is_live_authorization is not False:
            raise ValueError("OperatorReport.is_live_authorization must be False")
        if self.is_investment_advice is not False:
            raise ValueError("OperatorReport.is_investment_advice must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":           self.generated_at,
            "overall_status":         self.overall_status.value,
            "overall_status_label":   _STATUS_LABEL_KO[self.overall_status],
            "paper_ready_count":      self.paper_ready_count,
            "excluded_count":         self.excluded_count,
            "entries":                [e.to_dict() for e in self.entries],
            "paper_candidates":       [e.to_dict() for e in self.paper_candidates],
            "excluded":               [e.to_dict() for e in self.excluded],
            "reasons_no_candidate":   list(self.reasons_no_candidate),
            "ai_agent_risk_signals":  list(self.ai_agent_risk_signals),
            "next_user_actions":      list(self.next_user_actions),
            "metadata":               dict(self.metadata),
            # 최상위 invariant — JSON consumer 측에서도 안전.
            "is_order_signal":        False,
            "auto_apply_allowed":     False,
            "is_live_authorization":  False,
            "is_investment_advice":   False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. 입력 — paper_candidate_config + 5 단계 summary 직접 조합
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReportInputs:
    """3-08 입력 — 모두 optional. 없으면 해당 단계 skip."""

    paper_candidate_config_path:  str | None = None
    backtest_summary_path:        str | None = None
    optimization_summary_path:    str | None = None
    walk_forward_summary_path:    str | None = None
    stress_test_summary_path:     str | None = None


def _load_json(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. 상태 분류 + risk signal 계산
# ─────────────────────────────────────────────────────────────────────────────


# 비개발자가 즉시 식별 가능한 위험 신호 — risk_metrics 기반.
def _compute_risk_signals(
    risk_metrics: dict[str, Any],
    pipeline_stages: list[PipelineStage],
) -> list[str]:
    """단순 임계 기반 신호 — 추정값 / 절대 판단 아님."""
    signals: list[str] = []
    pf = risk_metrics.get("profit_factor")
    if isinstance(pf, (int, float)) and pf < 1.0:
        signals.append(f"profit_factor_below_1 ({pf:.2f})")
    mdd = risk_metrics.get("max_drawdown")
    if isinstance(mdd, (int, float)) and mdd >= 0.15:
        signals.append(f"high_max_drawdown ({mdd:.2%})")
    win = risk_metrics.get("win_rate")
    if isinstance(win, (int, float)) and win < 0.40:
        signals.append(f"low_win_rate ({win:.1%})")
    expectancy = risk_metrics.get("expectancy")
    if isinstance(expectancy, (int, float)) and expectancy <= 0:
        signals.append(f"non_positive_expectancy ({expectancy:.2f})")
    streak = risk_metrics.get("loss_streak")
    if isinstance(streak, (int, float)) and streak >= 5:
        signals.append(f"long_loss_streak ({int(streak)})")
    trades = risk_metrics.get("trade_count")
    if isinstance(trades, (int, float)) and trades < 30:
        signals.append(f"low_trade_count ({int(trades)})")
    # 수수료 / 슬리피지 영향.
    raw = risk_metrics.get("total_return")
    slip = risk_metrics.get("slippage_adjusted_return")
    if (isinstance(raw, (int, float)) and isinstance(slip, (int, float))
            and raw > 0 and slip <= 0):
        signals.append("fees_slippage_eliminate_profit")
    # walk-forward overfit.
    for s in pipeline_stages:
        if s.name == "3-04" and s.verdict == "OVERFIT_RISK":
            signals.append("walk_forward_overfit_risk")
        if s.name == "3-05" and s.verdict == "FAIL":
            signals.append("stress_test_scenario_fail")
        if s.name == "3-05" and s.verdict == "WARN":
            signals.append("stress_test_scenario_warn")
    return signals


def _classify_status(candidate: AggregatedCandidate) -> ReportStatus:
    """비개발자용 status 분류 — 가장 *나쁜 신호* 우선."""
    stage_verdicts = {s.name: s.verdict for s in candidate.pipeline_stages}

    # 데이터 부족 — INSUFFICIENT_DATA / 누락 단계.
    required = {"3-02", "3-03", "3-04", "3-05"}
    missing = required - set(stage_verdicts.keys())
    if missing:
        return ReportStatus.NEED_MORE_DATA
    if any(v == "INSUFFICIENT_DATA" for v in stage_verdicts.values()):
        return ReportStatus.NEED_MORE_DATA

    # 과최적화.
    if stage_verdicts.get("3-04") == "OVERFIT_RISK":
        return ReportStatus.OVERFIT_RISK

    # stress test 실패.
    if stage_verdicts.get("3-05") in {"FAIL", "WARN"}:
        return ReportStatus.STRESS_FAILED

    # 3-02 / 3-03 단계 통과 못함 → risk 위반.
    if (stage_verdicts.get("3-02") not in PASS_VERDICTS_PER_STAGE["3-02"]
            or stage_verdicts.get("3-03") not in PASS_VERDICTS_PER_STAGE["3-03"]):
        return ReportStatus.REJECTED_BY_RISK

    # 모든 단계 통과.
    if candidate.all_stages_passed(required):
        return ReportStatus.READY_FOR_PAPER

    # fallback — 어떤 단계에서든 통과 못함.
    return ReportStatus.NO_CANDIDATE


def _exclusion_reasons_for(
    candidate: AggregatedCandidate, status: ReportStatus,
) -> list[str]:
    """비개발자용 한국어 사유 — status + stage verdict 조합."""
    if status == ReportStatus.READY_FOR_PAPER:
        return []
    reasons: list[str] = []
    stage_verdicts = {s.name: s.verdict for s in candidate.pipeline_stages}
    required = {"3-02", "3-03", "3-04", "3-05"}
    missing = required - set(stage_verdicts.keys())
    for m in sorted(missing):
        reasons.append(f"{m}_단계_결과_없음")
    for s in candidate.pipeline_stages:
        allowed = PASS_VERDICTS_PER_STAGE.get(s.name, set())
        if s.verdict not in allowed:
            reasons.append(f"{s.name}_단계_탈락_verdict={s.verdict}")
    return reasons


def _display_name_for(strategy_id: str) -> str:
    """전략 친화 이름. registry_metadata 가 없으면 strategy_id 반환.

    의존성 최소화 — import 실패해도 raise 없이 fallback.
    """
    try:
        from app.strategies.registry_metadata import beginner_metadata
        meta = beginner_metadata(strategy_id)
        if meta is not None and meta.display_name.strip():
            return meta.display_name
    except Exception:  # noqa: BLE001
        pass
    return strategy_id


# ─────────────────────────────────────────────────────────────────────────────
# 4. build_operator_report — 메인 진입점
# ─────────────────────────────────────────────────────────────────────────────


def build_operator_report(
    inputs: ReportInputs,
    *,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> OperatorReport:
    """5 단계 산출물 + paper_candidate_config 종합 → OperatorReport."""
    if now is None:
        now = datetime.now(timezone.utc)

    # 1) Aggregate (3-07 module 재사용 — 단일 진실 유지).
    aggregated = aggregate_candidates(AggregationInputs(
        backtest_summary_path=inputs.backtest_summary_path,
        optimization_summary_path=inputs.optimization_summary_path,
        walk_forward_summary_path=inputs.walk_forward_summary_path,
        stress_test_summary_path=inputs.stress_test_summary_path,
    ))

    # 2) paper_candidate_config — 우선 사용 (운영자가 별도 생성한 결과 carry).
    pcc = _load_json(inputs.paper_candidate_config_path)
    paper_keys: set[tuple] = set()
    if isinstance(pcc, dict):
        for c in (pcc.get("candidates") or []):
            if not isinstance(c, dict):
                continue
            params = c.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            paper_keys.add((
                c.get("strategy") or "",
                c.get("symbol") or "",
                tuple(sorted((str(k), str(v)) for k, v in params.items())),
            ))

    # 3) 각 aggregated 후보 → StrategyEntry.
    entries: list[StrategyEntry] = []
    paper_candidates: list[StrategyEntry] = []
    excluded: list[StrategyEntry] = []

    for c in aggregated:
        status = _classify_status(c)
        params_key = tuple(sorted((str(k), str(v)) for k, v in c.params.items()))
        is_paper_candidate = (
            (c.strategy, c.symbol, params_key) in paper_keys
            or status == ReportStatus.READY_FOR_PAPER
        )

        entry = StrategyEntry(
            strategy_id=c.strategy,
            display_name=_display_name_for(c.strategy),
            symbol=c.symbol,
            params=c.params,
            status=ReportStatus.READY_FOR_PAPER if is_paper_candidate else status,
            pipeline_stages=list(c.pipeline_stages),
            risk_metrics=dict(c.risk_metrics),
            exclusion_reasons=_exclusion_reasons_for(c, status),
            risk_signals=_compute_risk_signals(c.risk_metrics, c.pipeline_stages),
            score=c.score,
        )
        entries.append(entry)
        if is_paper_candidate:
            paper_candidates.append(entry)
        else:
            excluded.append(entry)

    # 4) score 내림차순 정렬.
    paper_candidates.sort(key=lambda e: e.score, reverse=True)
    excluded.sort(key=lambda e: e.score, reverse=True)

    # 5) reasons_no_candidate — paper_candidate_config 또는 자체 계산.
    reasons_no_candidate: list[str] = []
    if isinstance(pcc, dict):
        reasons_no_candidate = [
            str(r) for r in (pcc.get("reasons_no_candidate") or [])
        ]
    if not paper_candidates and not reasons_no_candidate:
        if not aggregated:
            reasons_no_candidate.append("no_pipeline_results_loaded")
        else:
            reasons_no_candidate.append(
                "no_candidate_passed_all_required_stages"
            )

    # 6) overall status — paper 후보 있으면 READY_FOR_PAPER, 없으면 가장
    #    빈도 높은 탈락 사유 또는 NO_CANDIDATE.
    if paper_candidates:
        overall_status = ReportStatus.READY_FOR_PAPER
    elif aggregated:
        # 가장 흔한 status 카운트.
        from collections import Counter
        counts = Counter(e.status for e in excluded)
        # READY_FOR_PAPER 외에서 max — 동률이면 우선순위 NEED_MORE_DATA <
        # OVERFIT_RISK < STRESS_FAILED < REJECTED_BY_RISK < NO_CANDIDATE.
        priority = [
            ReportStatus.NO_CANDIDATE,
            ReportStatus.REJECTED_BY_RISK,
            ReportStatus.STRESS_FAILED,
            ReportStatus.OVERFIT_RISK,
            ReportStatus.NEED_MORE_DATA,
        ]
        if counts:
            ordered = sorted(
                counts.items(),
                key=lambda kv: (-kv[1], priority.index(kv[0]) if kv[0] in priority else 99),
            )
            overall_status = ordered[0][0]
        else:
            overall_status = ReportStatus.NO_CANDIDATE
    else:
        overall_status = ReportStatus.NO_CANDIDATE

    # 7) AI agent 가 참고할 위험 신호 — 모든 entry 의 risk_signals 합집합 + dedupe.
    ai_signals: list[str] = []
    seen: set[str] = set()
    for e in entries:
        for sig in e.risk_signals:
            base = sig.split(" (")[0]   # 카테고리 단위 합치기.
            if base not in seen:
                seen.add(base)
                ai_signals.append(base)

    # 8) 사용자 다음 행동.
    next_actions = _next_user_actions(
        overall_status, paper_candidates, reasons_no_candidate, ai_signals,
    )

    return OperatorReport(
        generated_at=now.isoformat(),
        overall_status=overall_status,
        paper_ready_count=len(paper_candidates),
        excluded_count=len(excluded),
        entries=entries,
        paper_candidates=paper_candidates,
        excluded=excluded,
        reasons_no_candidate=reasons_no_candidate,
        ai_agent_risk_signals=ai_signals,
        next_user_actions=next_actions,
        metadata=dict(metadata or {}),
    )


def _next_user_actions(
    overall_status: ReportStatus,
    paper_candidates: list[StrategyEntry],
    reasons_no_candidate: list[str],
    ai_signals: list[str],
) -> list[str]:
    """비개발자가 즉시 실행 가능한 다음 행동 — '주문 실행' 같은 위험한
    동사는 절대 사용하지 않는다.
    """
    actions: list[str] = []
    if overall_status == ReportStatus.READY_FOR_PAPER and paper_candidates:
        actions.append(
            "후보 전략을 *모의투자(Paper)* 환경에서 4 주 이상 운용하며 결과 관찰"
        )
        actions.append(
            "모의투자 결과가 본 리포트의 백테스트 결과와 *크게 다르면* 즉시 중지하고 점검"
        )
        actions.append(
            "AI Agent 가 표시하는 위험 신호 (`ai_agent_risk_signals`) 를 매일 확인"
        )
    elif overall_status == ReportStatus.NEED_MORE_DATA:
        actions.append("거래 횟수 / 백테스트 기간을 늘려 데이터를 더 모은 뒤 재평가")
        actions.append("walk-forward fold 수 또는 백테스트 기간 확장 검토")
    elif overall_status == ReportStatus.OVERFIT_RISK:
        actions.append("과최적화 위험 — 파라미터 단순화 또는 다른 데이터 구간으로 재검증")
        actions.append("Strategy Researcher Agent 리포트 검토 후 별도 PR")
    elif overall_status == ReportStatus.STRESS_FAILED:
        actions.append("스트레스 시나리오 실패 — 슬리피지 / 갭 / 거래량 부족 대응 검토")
        actions.append("위험 한도(MDD / 손실 streak) 강화 후 재 스트레스 테스트")
    elif overall_status == ReportStatus.REJECTED_BY_RISK:
        actions.append("백테스트 또는 파라미터 최적화 단계의 위험 한도 위반 — 임계 재조정")
    else:
        actions.append(
            "현재 paper 모의운용 가능한 전략이 없습니다 — 사유를 검토하고 보완"
        )
    # 공통 권고 — 항상 추가.
    actions.append(
        "본 리포트는 *추정 분석*이며 *투자 조언이 아닙니다* — 실거래 활성화는 별도 옵트인 절차 필요"
    )
    return actions


# ─────────────────────────────────────────────────────────────────────────────
# 5. Markdown 렌더링 — 12 섹션 전체 + summary 1 페이지
# ─────────────────────────────────────────────────────────────────────────────


_DISCLAIMER_HEADER = (
    "> ⚠️ **본 리포트는 추정 분석이며 *투자 조언이 아닙니다.***  \n"
    "> 자동 paper trader 시작 / 자동 실거래 활성화를 의미하지 않습니다.  \n"
    "> 모의투자에서 확인 필요. (`is_order_signal=False` / `auto_apply_allowed=False` / "
    "`is_live_authorization=False`)"
)


def _format_metric(v: Any, fmt: str = "{:.4f}") -> str:
    if v is None:
        return "-"
    if isinstance(v, (int, float)):
        try:
            return fmt.format(v)
        except (ValueError, TypeError):
            return str(v)
    return str(v)


def _format_pct(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "-"
    return f"{v:.2%}"


def _format_params(params: dict[str, Any]) -> str:
    if not params:
        return "(default)"
    return ", ".join(f"{k}={v}" for k, v in sorted(params.items()))


def _render_metric_row(e: StrategyEntry) -> str:
    m = e.risk_metrics
    return (
        f"| {e.display_name} (`{e.strategy_id}`) | {e.symbol} | "
        f"{_format_metric(m.get('expectancy'), '{:.2f}')} | "
        f"{_format_metric(m.get('profit_factor'), '{:.2f}')} | "
        f"{_format_pct(m.get('max_drawdown'))} | "
        f"{_format_pct(m.get('win_rate'))} | "
        f"{_format_metric(m.get('trade_count'), '{:.0f}')} |"
    )


def render_full_markdown(report: OperatorReport) -> str:
    """12 섹션 전체 리포트."""
    lines: list[str] = []
    lines.append("# 전략 최적화 리포트 (3-08)")
    lines.append("")
    lines.append(_DISCLAIMER_HEADER)
    lines.append("")
    lines.append(f"- 생성 시각: `{report.generated_at}`")
    lines.append(
        f"- 전체 판정: **{report.overall_status.value}** "
        f"({_STATUS_LABEL_KO[report.overall_status]})"
    )
    lines.append(f"- Paper 후보: {report.paper_ready_count}건")
    lines.append(f"- 제외 후보: {report.excluded_count}건")
    lines.append("")

    # § 1. 전체 결론
    lines.append("## 1. 전체 결론")
    lines.append("")
    if report.overall_status == ReportStatus.READY_FOR_PAPER:
        lines.append(
            "✅ **모의투자(Paper) 환경에서 시작 검토 가능한 전략이 있습니다.** "
            "단, *실거래 활성화는 아닙니다* — 4 주 이상 paper 운용 후 별도 게이트 통과 필요."
        )
    else:
        lines.append(
            f"❌ **지금 사용 가능한 전략이 없습니다.** "
            f"상태: `{report.overall_status.value}` — {_STATUS_LABEL_KO[report.overall_status]}"
        )
    lines.append("")

    # § 2. 전략별 순위
    lines.append("## 2. 전략별 순위")
    lines.append("")
    if report.entries:
        lines.append("| 전략 | 종목 | 기대값 | PF | MDD | 승률 | 거래수 |")
        lines.append("|---|---|---|---|---|---|---|")
        for e in sorted(report.entries, key=lambda x: x.score, reverse=True):
            lines.append(_render_metric_row(e))
    else:
        lines.append("(평가된 전략 없음)")
    lines.append("")

    # § 3. Paper 후보 전략
    lines.append("## 3. Paper 후보 전략 (지금 *모의투자* 환경에서 검토 가능)")
    lines.append("")
    if report.paper_candidates:
        for i, e in enumerate(report.paper_candidates, start=1):
            lines.append(f"### 3.{i}. {e.display_name} (`{e.strategy_id}`)")
            lines.append("")
            lines.append(f"- 종목: `{e.symbol}`")
            lines.append(f"- 파라미터: `{_format_params(e.params)}`")
            lines.append(f"- 통과 단계: {', '.join(e.passed_stages()) or '(없음)'}")
            lines.append(f"- 점수: `{e.score:.4f}`")
            if e.risk_signals:
                lines.append("- 주의 신호:")
                for sig in e.risk_signals:
                    lines.append(f"  - `{sig}`")
            lines.append("")
    else:
        lines.append("(현재 Paper 후보 없음)")
        lines.append("")

    # § 4. 후보 없음 사유
    lines.append("## 4. 후보가 없는 경우 사유")
    lines.append("")
    if report.reasons_no_candidate:
        for r in report.reasons_no_candidate:
            lines.append(f"- `{r}`")
    else:
        lines.append("(해당 없음 — 후보 1개 이상 존재)")
    lines.append("")

    # § 5. 제외된 전략과 제외 사유
    lines.append("## 5. 제외된 전략과 사유")
    lines.append("")
    if report.excluded:
        for e in report.excluded:
            lines.append(
                f"- **{e.display_name}** (`{e.strategy_id}` / `{e.symbol}`) — "
                f"`{e.status.value}` ({_STATUS_LABEL_KO[e.status]})"
            )
            for reason in e.exclusion_reasons:
                lines.append(f"  - {reason}")
    else:
        lines.append("(제외 후보 없음)")
    lines.append("")

    # § 6. 수수료·슬리피지 반영 결과
    lines.append("## 6. 수수료·슬리피지 반영 결과")
    lines.append("")
    lines.append("| 전략 | 종목 | raw 수익률 | 수수료 반영 후 | 슬리피지 반영 후 |")
    lines.append("|---|---|---|---|---|")
    has_fee_data = False
    for e in report.entries:
        m = e.risk_metrics
        raw = m.get("total_return")
        fee = m.get("fee_adjusted_return")
        slip = m.get("slippage_adjusted_return")
        if raw is None and fee is None and slip is None:
            continue
        has_fee_data = True
        lines.append(
            f"| {e.display_name} | {e.symbol} | "
            f"{_format_pct(raw)} | {_format_pct(fee)} | {_format_pct(slip)} |"
        )
    if not has_fee_data:
        lines.append("| (수수료/슬리피지 데이터 없음) | - | - | - | - |")
    lines.append("")
    lines.append(
        "_수수료 / 슬리피지 차감 후에도 기대수익이 양수인지 확인하세요. "
        "차감 후 음수이면 실거래 시 손실 위험이 큽니다._"
    )
    lines.append("")

    # § 7. Walk-forward 결과
    lines.append("## 7. Walk-forward 결과 (과최적화 점검)")
    lines.append("")
    lines.append("| 전략 | 종목 | Walk-forward verdict | fold 수 |")
    lines.append("|---|---|---|---|")
    has_wf = False
    for e in report.entries:
        wf_stage = next((s for s in e.pipeline_stages if s.name == "3-04"), None)
        if wf_stage is None:
            continue
        has_wf = True
        fold_count = wf_stage.extra.get("fold_count", "-")
        lines.append(
            f"| {e.display_name} | {e.symbol} | `{wf_stage.verdict}` | {fold_count} |"
        )
    if not has_wf:
        lines.append("| (Walk-forward 데이터 없음) | - | - | - |")
    lines.append("")

    # § 8. Stress Test 결과
    lines.append("## 8. Stress Test 결과 (극단 시나리오 견고성)")
    lines.append("")
    lines.append("| 전략 | 종목 | stress verdict | 통과 시나리오 / 전체 |")
    lines.append("|---|---|---|---|")
    has_st = False
    for e in report.entries:
        st_stage = next((s for s in e.pipeline_stages if s.name == "3-05"), None)
        if st_stage is None:
            continue
        has_st = True
        passed = st_stage.extra.get("scenarios_passed", "-")
        total  = st_stage.extra.get("scenario_count", "-")
        lines.append(
            f"| {e.display_name} | {e.symbol} | `{st_stage.verdict}` | {passed} / {total} |"
        )
    if not has_st:
        lines.append("| (Stress Test 데이터 없음) | - | - | - |")
    lines.append("")

    # § 9. MDD / PF / 기대값 / 승률 / 거래횟수
    lines.append("## 9. 핵심 성과 지표 (3-06 표준 14 키)")
    lines.append("")
    lines.append(f"표준 키 (`PERFORMANCE_METRIC_KEYS`, n={len(PERFORMANCE_METRIC_KEYS)}):")
    lines.append("")
    lines.append("```")
    for k in PERFORMANCE_METRIC_KEYS:
        lines.append(f"- {k}")
    lines.append("```")
    lines.append("")
    lines.append("| 전략 | 종목 | MDD | PF | 기대값 | 승률 | 거래수 | loss_streak |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for e in report.entries:
        m = e.risk_metrics
        lines.append(
            f"| {e.display_name} | {e.symbol} | "
            f"{_format_pct(m.get('max_drawdown'))} | "
            f"{_format_metric(m.get('profit_factor'), '{:.2f}')} | "
            f"{_format_metric(m.get('expectancy'), '{:.2f}')} | "
            f"{_format_pct(m.get('win_rate'))} | "
            f"{_format_metric(m.get('trade_count'), '{:.0f}')} | "
            f"{_format_metric(m.get('loss_streak'), '{:.0f}')} |"
        )
    if not report.entries:
        lines.append("| (데이터 없음) | - | - | - | - | - | - | - |")
    lines.append("")

    # § 10. AI Agent 가 참고할 위험 신호
    lines.append("## 10. AI Agent 가 참고할 위험 신호")
    lines.append("")
    if report.ai_agent_risk_signals:
        for sig in report.ai_agent_risk_signals:
            lines.append(f"- `{sig}`")
        lines.append("")
        lines.append(
            "_AI Agent 가 의사결정 컨텍스트로 *참고* — 자동 차단 트리거 아님. "
            "RiskManager / OrderGuard 흐름은 영향 없음._"
        )
    else:
        lines.append("(현재 surface 된 위험 신호 없음)")
    lines.append("")

    # § 11. 다음 Paper 모의운용 가능 여부
    lines.append("## 11. 다음 단계: Paper 모의운용 가능 여부")
    lines.append("")
    if report.paper_ready_count > 0:
        lines.append(
            f"✅ **{report.paper_ready_count}개** 전략이 모의투자(Paper) 환경에서 "
            f"운영자 검토 후 *수동 시작* 가능합니다."
        )
        lines.append("")
        lines.append(
            "**중요**: 본 리포트는 모의투자 *시작*을 자동으로 수행하지 않습니다. "
            "운영자가 BotControl / LiveEngine 흐름에서 *명시 시작* 해야 합니다."
        )
    else:
        lines.append(
            "❌ 현재 모의투자(Paper)에서 시작 가능한 전략이 없습니다. "
            "위 §4·§5 의 사유를 검토하고 보완하세요."
        )
    lines.append("")

    # § 12. 사용자가 해야 할 다음 행동
    lines.append("## 12. 사용자가 해야 할 다음 행동")
    lines.append("")
    for action in report.next_user_actions:
        lines.append(f"- {action}")
    lines.append("")

    # 푸터.
    lines.append("---")
    lines.append("")
    lines.append(
        "_본 리포트는 read-only 분석 자료입니다. broker / OrderExecutor / "
        "route_order 호출 0건, 안전 flag mutate 0건._"
    )
    lines.append("")

    return "\n".join(lines)


def render_summary_markdown(report: OperatorReport) -> str:
    """한 페이지 운영자 요약 — 비개발자가 5 분 안에 읽도록."""
    lines: list[str] = []
    lines.append("# 전략 최적화 요약 (3-08)")
    lines.append("")
    lines.append(_DISCLAIMER_HEADER)
    lines.append("")
    lines.append(f"- 생성 시각: `{report.generated_at}`")
    lines.append(
        f"- 전체 판정: **{report.overall_status.value}** "
        f"({_STATUS_LABEL_KO[report.overall_status]})"
    )
    lines.append("")
    lines.append("## 결론 한 줄")
    lines.append("")
    if report.overall_status == ReportStatus.READY_FOR_PAPER:
        names = ", ".join(
            f"{e.display_name} (`{e.strategy_id}`/`{e.symbol}`)"
            for e in report.paper_candidates
        )
        lines.append(
            f"✅ **모의투자(Paper)에서 검토 가능: {report.paper_ready_count}건** — {names}"
        )
    else:
        lines.append(
            f"❌ **현재 사용 가능한 전략 없음** — `{report.overall_status.value}`. "
            f"상세는 전체 리포트의 §4·§5 참고."
        )
    lines.append("")
    lines.append("## 핵심 숫자")
    lines.append("")
    lines.append(f"- 평가된 전략 수: {len(report.entries)}")
    lines.append(f"- Paper 후보: {report.paper_ready_count}")
    lines.append(f"- 제외: {report.excluded_count}")
    lines.append(f"- AI Agent 위험 신호: {len(report.ai_agent_risk_signals)}건")
    lines.append("")
    if report.ai_agent_risk_signals:
        lines.append("### 주요 위험 신호 (상위 5개)")
        lines.append("")
        for sig in report.ai_agent_risk_signals[:5]:
            lines.append(f"- `{sig}`")
        lines.append("")
    lines.append("## 다음 행동")
    lines.append("")
    for action in report.next_user_actions:
        lines.append(f"- {action}")
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 6. 파일 저장
# ─────────────────────────────────────────────────────────────────────────────


def write_report_files(
    report: OperatorReport, output_dir: str | Path,
) -> dict[str, Path]:
    """``reports/strategy_optimization/`` 에 2개 markdown 작성.

    - ``strategy_optimization_report.md``  — 전체 12 섹션
    - ``operator_summary.md``              — 한 페이지 요약

    경고: ``reports/*`` 는 ``.gitignore`` — git 미커밋. 테스트에서는
    ``tmp_path`` 로 생성 확인만.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    full_path = out_dir / "strategy_optimization_report.md"
    summary_path = out_dir / "operator_summary.md"
    full_path.write_text(render_full_markdown(report), encoding="utf-8")
    summary_path.write_text(render_summary_markdown(report), encoding="utf-8")

    return {
        "strategy_optimization_report": full_path,
        "operator_summary":              summary_path,
    }
