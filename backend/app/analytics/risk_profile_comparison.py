"""#4-RiskProfileCompare: Paper 운용 성향 3종 비교 리포트.

같은 기간 / 같은 explanation + price 데이터를 기준으로 CONSERVATIVE / BALANCED /
AGGRESSIVE 3개 프리셋으로 Paper bridge 를 *각각* 실행해 결과를 비교한다.

본 모듈은 *advisory 분석 리포트* 만 만든다 — 실 broker 호출 0건, 자동
프리셋 변경 0건. 기본 추천은 항상 BALANCED (사용자가 별도 분석 후 명시
승인하기 전까지).

## 측정 지표

- signal_count                : explanation 입력 entry 수
- paper_decision_count        : bridge 가 만든 PaperDecision 수
- buy_count / sell_count / hold_count / exit_count / no_op_count
- buy_ratio / sell_ratio / hold_ratio / exit_ratio / no_op_ratio
- win_rate                    : pnl > 0 비율 (filled trades 중)
- expectancy                  : mean pnl per filled trade
- profit_factor               : sum(positive pnl) / |sum(negative pnl)|
- max_drawdown                : 누적 pnl 의 최저 trough
- loss_streak                 : 연속 losing trade 최댓값
- risk_veto_count             : veto BLOCK 으로 차단된 decision 수
- stale_data_violation_count  : risk_flag STALE_DATA 카운트
- duplicate_signal_count      : risk_flag DUPLICATE_SIGNAL 카운트
- position_size_avg           : sized BUY 의 평균 quantity
- paper_pnl_estimate          : 누적 pnl 추정값 (가상 체결만)

## 추천 정책

기본 추천은 **BALANCED**. AGGRESSIVE 가 expectancy / win_rate 가 높아도 본
모듈은 BALANCED 를 우선한다 (CLAUDE.md 절대 원칙 — 손실 방어 우선).
AGGRESSIVE 추천 라벨은 *명시 분석 후 운영자 결정* 흐름에서 별도 PR 로 진입.

## 절대 invariant

1. broker / OrderExecutor / route_order import 0건 (정적 가드).
2. `ComparisonReport.is_order_signal=False` / `auto_apply_allowed=False` /
   `is_live_authorization=False` 영구.
3. AGGRESSIVE 라벨도 *실거래 활성화 허가가 아님* — `is_live_authorization`
   영구 False.
4. INSUFFICIENT_DATA — explanation 이 empty (entry 0개) 인 경우 모든 metric
   None 처리 + recommended_profile="BALANCED" + status="INSUFFICIENT_DATA".
5. DB write 0건, secret 0건, settings mutation 0건.
"""

from __future__ import annotations

import json as _json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.agents.paper_decision_bridge import (
    PositionSnapshot,
    bridge_explanation_to_paper_decisions,
)
from app.agents.paper_start_explanation import PaperStartExplanation
from app.agents.risk_profile import (
    DEFAULT_RISK_PROFILE,
    RiskProfile,
    policy_for,
    sizing_policy_for,
)


COMPARISON_SCHEMA_VERSION = "1.0"

_log = logging.getLogger("autotrade.risk_profile_comparison")


# ─────────────────────────────────────────────────────────────────────────────
# DTOs
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProfileResult:
    """단일 프리셋의 Paper 결과 metrics — *advisory*."""

    profile:                  str        # CONSERVATIVE / BALANCED / AGGRESSIVE
    signal_count:             int                  = 0
    paper_decision_count:     int                  = 0

    buy_count:                int                  = 0
    sell_count:               int                  = 0
    hold_count:               int                  = 0
    exit_count:               int                  = 0
    no_op_count:              int                  = 0

    buy_ratio:                float                = 0.0
    sell_ratio:               float                = 0.0
    hold_ratio:               float                = 0.0
    exit_ratio:               float                = 0.0
    no_op_ratio:              float                = 0.0

    win_rate:                 float | None         = None    # 0~1 or None
    expectancy:               float | None         = None
    profit_factor:            float | None         = None
    max_drawdown:             float | None         = None
    loss_streak:              int                  = 0

    risk_veto_count:          int                  = 0
    stale_data_violation_count:int                 = 0
    duplicate_signal_count:   int                  = 0
    position_size_avg:        float | None         = None
    paper_pnl_estimate:       float                = 0.0

    # 절대 invariant — 본 결과는 *advisory*.
    is_order_signal:          bool = False
    auto_apply_allowed:       bool = False
    is_live_authorization:    bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"ProfileResult.{name} must be False.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile":                    self.profile,
            "signal_count":               int(self.signal_count),
            "paper_decision_count":       int(self.paper_decision_count),
            "buy_count":                  int(self.buy_count),
            "sell_count":                 int(self.sell_count),
            "hold_count":                 int(self.hold_count),
            "exit_count":                 int(self.exit_count),
            "no_op_count":                int(self.no_op_count),
            "buy_ratio":                  float(self.buy_ratio),
            "sell_ratio":                 float(self.sell_ratio),
            "hold_ratio":                 float(self.hold_ratio),
            "exit_ratio":                 float(self.exit_ratio),
            "no_op_ratio":                float(self.no_op_ratio),
            "win_rate":                   self.win_rate,
            "expectancy":                 self.expectancy,
            "profit_factor":              self.profit_factor,
            "max_drawdown":               self.max_drawdown,
            "loss_streak":                int(self.loss_streak),
            "risk_veto_count":            int(self.risk_veto_count),
            "stale_data_violation_count": int(self.stale_data_violation_count),
            "duplicate_signal_count":     int(self.duplicate_signal_count),
            "position_size_avg":          self.position_size_avg,
            "paper_pnl_estimate":         float(self.paper_pnl_estimate),
            "is_order_signal":            False,
            "auto_apply_allowed":         False,
            "is_live_authorization":      False,
        }


@dataclass(frozen=True)
class ComparisonReport:
    """3 프리셋 비교 결과 + 추천."""

    generated_at:           str
    schema_version:         str

    status:                 str         # OK / INSUFFICIENT_DATA
    period_label:           str

    results:                list[ProfileResult]
    recommended_profile:    str         # 기본값 BALANCED
    recommendation_reason:  str

    notes:                  list[str]               = field(default_factory=list)
    advisory_disclaimer:    str = (
        "본 리포트는 *advisory* — Paper 결과 비교 분석만, 실거래 주문 0건. "
        "AGGRESSIVE 가 metric 상 우위라도 실거래 안전장치를 우회하지 않으며, "
        "기본 추천은 BALANCED 입니다. is_order_signal=False / "
        "auto_apply_allowed=False / is_live_authorization=False."
    )

    is_order_signal:        bool = False
    auto_apply_allowed:     bool = False
    is_live_authorization:  bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"ComparisonReport.{name} must be False.")
        if self.recommended_profile not in {p.value for p in RiskProfile}:
            raise ValueError(
                f"recommended_profile must be RiskProfile value, got "
                f"{self.recommended_profile!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":         self.generated_at,
            "schema_version":       self.schema_version,
            "status":               self.status,
            "period_label":         self.period_label,
            "results":              [r.to_dict() for r in self.results],
            "recommended_profile":  self.recommended_profile,
            "recommendation_reason": self.recommendation_reason,
            "notes":                list(self.notes),
            "advisory_disclaimer":  self.advisory_disclaimer,
            "is_order_signal":      False,
            "auto_apply_allowed":   False,
            "is_live_authorization": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Trade simulation — 가상 손익 추정 (deterministic, broker 호출 0건).
# ─────────────────────────────────────────────────────────────────────────────
#
# 본 비교의 *pnl* 은 실제 시장 데이터가 아닌 *입력 시나리오의 next_price* 를
# 사용한 결정론적 추정. caller 가 `pnl_lookup[(strategy,symbol)]` 으로 가상
# pnl-per-share 를 직접 제공하면 그대로 사용. 미제공 시 0 으로 처리.


def _simulate_trade_pnls(
    *,
    decisions: list[Any],
    pnl_lookup: dict[tuple[str, str], float] | None,
) -> list[float]:
    """`decisions` 중 BUY/SELL/EXIT filled 만 골라 가상 pnl 시퀀스 반환."""
    if not decisions:
        return []
    lookup = dict(pnl_lookup or {})
    pnls: list[float] = []
    for d in decisions:
        action = d.action.value if hasattr(d.action, "value") else d.action
        if action not in ("BUY", "SELL", "EXIT"):
            continue
        # paper_fill_status PAPER_FILLED 만 counted (NA / REJECTED 제외).
        fill = (d.paper_fill_status.value
                if hasattr(d.paper_fill_status, "value")
                else d.paper_fill_status)
        if fill != "PAPER_FILLED":
            continue
        pps = float(lookup.get((d.strategy, d.symbol), 0.0))
        qty = abs(int(d.virtual_position_delta or 0))
        pnls.append(pps * qty)
    return pnls


def _max_drawdown(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > mdd:
            mdd = dd
    return float(mdd)


def _loss_streak(pnls: list[float]) -> int:
    if not pnls:
        return 0
    cur = 0
    best = 0
    for p in pnls:
        if p < 0:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return int(best)


def _win_rate(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    wins = sum(1 for p in pnls if p > 0)
    return float(wins) / float(len(pnls))


def _expectancy(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    return float(sum(pnls)) / float(len(pnls))


def _profit_factor(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    pos = sum(p for p in pnls if p > 0)
    neg = -sum(p for p in pnls if p < 0)
    if neg <= 0:
        # 손실 0 — 무한대 PF 는 inf 로 표시하지 않고 None (분석 모호).
        return None if pos == 0 else float(pos)
    return float(pos) / float(neg)


# ─────────────────────────────────────────────────────────────────────────────
# Per-profile runner
# ─────────────────────────────────────────────────────────────────────────────


def _build_profile_result(
    *,
    profile:           RiskProfile,
    explanation:       PaperStartExplanation,
    loop_state:        str,
    positions:         list[PositionSnapshot],
    price_lookup:      dict[tuple[str, str], float] | None,
    account_equity:    float | None,
    confidence_lookup: dict[tuple[str, str], float] | None,
    pnl_lookup:        dict[tuple[str, str], float] | None,
) -> ProfileResult:
    """단일 프리셋으로 bridge 를 1회 실행해 metric 산출."""
    sizing = sizing_policy_for(profile)
    veto_max = policy_for(profile).risk_veto_max_flags
    report = bridge_explanation_to_paper_decisions(
        explanation=explanation,
        loop_state=loop_state,
        positions=list(positions),
        sizing_policy=sizing,
        price_lookup=price_lookup,
        account_equity=account_equity,
        confidence_lookup=confidence_lookup,
        risk_veto_max_flags=int(veto_max),
        record=False,    # 비교 분석 — ledger 영구 기록 X.
    )

    decisions = list(report.decisions)
    n = len(decisions)
    by_action: dict[str, int] = {}
    sized_quantities: list[int] = []
    for d in decisions:
        action = d.action.value if hasattr(d.action, "value") else d.action
        by_action[action] = by_action.get(action, 0) + 1
        # sized BUY/SELL/EXIT 의 평균 size — sizing_quantity 또는 |delta|.
        meta = dict(d.metadata or {})
        sq = meta.get("sizing_quantity")
        if action in ("BUY", "SELL", "EXIT"):
            if isinstance(sq, (int, float)) and sq > 0:
                sized_quantities.append(int(sq))
            elif abs(int(d.virtual_position_delta or 0)) > 0:
                sized_quantities.append(abs(int(d.virtual_position_delta)))

    def _ratio(c: int) -> float:
        return float(c) / float(n) if n > 0 else 0.0

    buy_count   = by_action.get("BUY",   0)
    sell_count  = by_action.get("SELL",  0)
    hold_count  = by_action.get("HOLD",  0)
    exit_count  = by_action.get("EXIT",  0)
    no_op_count = by_action.get("NO_OP", 0)

    # risk_veto / risk_flag breakdown 은 bridge metadata.risk_veto.summary 에 carry.
    veto_summary: dict[str, int] = {}
    veto_meta = report.metadata.get("risk_veto") or {}
    if isinstance(veto_meta, dict):
        veto_summary = dict(veto_meta.get("summary") or {})
    vetoed_count = (
        int(veto_meta.get("vetoed_count", 0))
        if isinstance(veto_meta, dict) else 0
    )

    # PnL 추정.
    pnls = _simulate_trade_pnls(decisions=decisions, pnl_lookup=pnl_lookup)
    paper_pnl_estimate = float(sum(pnls)) if pnls else 0.0

    pos_avg = (
        float(sum(sized_quantities)) / float(len(sized_quantities))
        if sized_quantities else None
    )

    signal_count = (
        len(explanation.recommended_explanations)
        + len(explanation.watchlist_explanations)
        + len(explanation.excluded_explanations)
    )

    return ProfileResult(
        profile=profile.value,
        signal_count=signal_count,
        paper_decision_count=n,
        buy_count=buy_count,
        sell_count=sell_count,
        hold_count=hold_count,
        exit_count=exit_count,
        no_op_count=no_op_count,
        buy_ratio=_ratio(buy_count),
        sell_ratio=_ratio(sell_count),
        hold_ratio=_ratio(hold_count),
        exit_ratio=_ratio(exit_count),
        no_op_ratio=_ratio(no_op_count),
        win_rate=_win_rate(pnls),
        expectancy=_expectancy(pnls),
        profit_factor=_profit_factor(pnls),
        max_drawdown=_max_drawdown(pnls),
        loss_streak=_loss_streak(pnls),
        risk_veto_count=vetoed_count,
        stale_data_violation_count=int(veto_summary.get("STALE_DATA", 0)),
        duplicate_signal_count=int(veto_summary.get("DUPLICATE_SIGNAL", 0)),
        position_size_avg=pos_avg,
        paper_pnl_estimate=paper_pnl_estimate,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main entry — compare_profiles
# ─────────────────────────────────────────────────────────────────────────────


_PROFILES_TO_COMPARE: tuple[RiskProfile, ...] = (
    RiskProfile.CONSERVATIVE,
    RiskProfile.BALANCED,
    RiskProfile.AGGRESSIVE,
)


def compare_profiles(
    *,
    explanation:       PaperStartExplanation,
    loop_state:        str                                = "RUNNING",
    positions:         Iterable[PositionSnapshot] | None  = None,
    price_lookup:      dict[tuple[str, str], float] | None = None,
    account_equity:    float | None                       = None,
    confidence_lookup: dict[tuple[str, str], float] | None = None,
    pnl_lookup:        dict[tuple[str, str], float] | None = None,
    period_label:      str                                = "ad-hoc",
    now:               datetime | None                    = None,
) -> ComparisonReport:
    """3 프리셋 비교 실행 → `ComparisonReport`.

    *broker 호출 0건* — bridge 가 record=False 로 호출되어 ledger / decision_log
    영구 기록도 일어나지 *않음* (분석 전용).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    pos_list = [p for p in (positions or []) if isinstance(p, PositionSnapshot)]

    has_entries = (
        len(explanation.recommended_explanations)
        + len(explanation.watchlist_explanations)
        + len(explanation.excluded_explanations)
    ) > 0

    if not has_entries:
        # 후보 데이터 부족 — 모든 metric None 처리.
        empty_results = [
            ProfileResult(profile=p.value, signal_count=0)
            for p in _PROFILES_TO_COMPARE
        ]
        return ComparisonReport(
            generated_at=now.isoformat(),
            schema_version=COMPARISON_SCHEMA_VERSION,
            status="INSUFFICIENT_DATA",
            period_label=period_label,
            results=empty_results,
            recommended_profile=DEFAULT_RISK_PROFILE.value,
            recommendation_reason=(
                "데이터 부족 — explanation 에 entry 0개. 기본값 BALANCED 유지."
            ),
            notes=["INSUFFICIENT_DATA: explanation entries == 0"],
        )

    results: list[ProfileResult] = []
    for profile in _PROFILES_TO_COMPARE:
        try:
            r = _build_profile_result(
                profile=profile,
                explanation=explanation,
                loop_state=loop_state,
                positions=pos_list,
                price_lookup=price_lookup,
                account_equity=account_equity,
                confidence_lookup=confidence_lookup,
                pnl_lookup=pnl_lookup,
            )
        except Exception as exc:  # noqa: BLE001 — 결정론적 — 일반 fallback.
            _log.warning(
                "[risk_profile_compare] %s build failed: %s: %s",
                profile.value, type(exc).__name__, exc,
            )
            r = ProfileResult(profile=profile.value)
        results.append(r)

    # 추천 — 기본 BALANCED. 본 모듈은 AGGRESSIVE 우위 metric 이 있어도
    # *자동* 으로 AGGRESSIVE 를 추천하지 *않는다*. 운영자가 별도 검토 흐름에서
    # 결정.
    rec_reason = (
        "기본 추천은 BALANCED — 손실 방어 우선 정책. AGGRESSIVE 가 expectancy / "
        "win_rate / profit_factor 상 우위라도 자동 채택하지 않음. 운영자 명시 "
        "검토 + 별도 옵트인 PR 후에만 다른 프리셋으로 전환 가능."
    )

    return ComparisonReport(
        generated_at=now.isoformat(),
        schema_version=COMPARISON_SCHEMA_VERSION,
        status="OK",
        period_label=period_label,
        results=results,
        recommended_profile=DEFAULT_RISK_PROFILE.value,
        recommendation_reason=rec_reason,
        notes=[
            "기간/입력 데이터는 동일 — 3 프리셋의 *임계값 차이만* 비교 대상.",
            "본 결과는 *advisory* — 실거래 활성화 / 자동 프리셋 변경 0건.",
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Report writers — JSON / Markdown / CSV
# ─────────────────────────────────────────────────────────────────────────────


def _fmt_num(v: Any, places: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{places}f}"
    return str(v)


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def render_markdown(report: ComparisonReport) -> str:
    """운영자 친화 한글 markdown."""
    lines: list[str] = []
    lines.append("# Paper 운용 성향 비교 리포트")
    lines.append("")
    lines.append("> *advisory* — Paper 결과 비교만, 실거래 주문 0건. ")
    lines.append("> AGGRESSIVE 가 metric 상 우위라도 실거래 안전장치를 우회하지 않으며,")
    lines.append("> 기본 추천은 BALANCED 입니다.")
    lines.append("")
    lines.append(f"- 생성: `{report.generated_at}`")
    lines.append(f"- schema_version: `{report.schema_version}`")
    lines.append(f"- 상태: `{report.status}`")
    lines.append(f"- 기간: `{report.period_label}`")
    lines.append(f"- 추천 프리셋: **{report.recommended_profile}**")
    lines.append("")
    lines.append("## 추천 사유")
    lines.append("")
    lines.append(report.recommendation_reason)
    lines.append("")
    lines.append("## 비교 매트릭스")
    lines.append("")
    header = (
        "| 지표 | "
        + " | ".join(r.profile for r in report.results)
        + " |"
    )
    sep = "|" + "|".join(["---"] * (len(report.results) + 1)) + "|"
    lines.append(header)
    lines.append(sep)

    def _row(label: str, getter):
        vals = [_fmt_num(getter(r)) for r in report.results]
        lines.append(f"| {label} | " + " | ".join(vals) + " |")

    def _row_pct(label: str, getter):
        vals = [_fmt_pct(getter(r)) for r in report.results]
        lines.append(f"| {label} | " + " | ".join(vals) + " |")

    _row("signal_count",            lambda r: r.signal_count)
    _row("paper_decision_count",    lambda r: r.paper_decision_count)
    _row("BUY",                     lambda r: r.buy_count)
    _row("SELL",                    lambda r: r.sell_count)
    _row("HOLD",                    lambda r: r.hold_count)
    _row("EXIT",                    lambda r: r.exit_count)
    _row("NO_OP",                   lambda r: r.no_op_count)
    _row_pct("BUY ratio",           lambda r: r.buy_ratio)
    _row_pct("HOLD ratio",          lambda r: r.hold_ratio)
    _row_pct("win_rate",            lambda r: r.win_rate)
    _row("expectancy",              lambda r: r.expectancy)
    _row("profit_factor",           lambda r: r.profit_factor)
    _row("max_drawdown",            lambda r: r.max_drawdown)
    _row("loss_streak",             lambda r: r.loss_streak)
    _row("risk_veto_count",         lambda r: r.risk_veto_count)
    _row("stale_data_violation_count",  lambda r: r.stale_data_violation_count)
    _row("duplicate_signal_count",  lambda r: r.duplicate_signal_count)
    _row("position_size_avg",       lambda r: r.position_size_avg)
    _row("paper_pnl_estimate",      lambda r: r.paper_pnl_estimate)
    lines.append("")
    if report.notes:
        lines.append("## 비교 노트")
        for note in report.notes:
            lines.append(f"- {note}")
        lines.append("")
    lines.append("## 안전 invariant")
    lines.append("")
    lines.append(
        "- `is_order_signal=False` / `auto_apply_allowed=False` / "
        "`is_live_authorization=False`"
    )
    lines.append(
        "- AGGRESSIVE 라벨도 *실거래 활성화 허가가 아님* — 안전장치 영구 잠금."
    )
    lines.append("- broker / OrderExecutor / route_order 호출 0건 (정적 가드).")
    lines.append("")
    lines.append(report.advisory_disclaimer)
    lines.append("")
    return "\n".join(lines)


def render_ranking_csv(report: ComparisonReport) -> str:
    """expectancy 내림차순 ranking — 행 = 프리셋."""
    headers = [
        "profile", "expectancy", "win_rate", "profit_factor",
        "max_drawdown", "loss_streak", "paper_decision_count",
        "risk_veto_count", "paper_pnl_estimate",
    ]
    sorted_rows = sorted(
        report.results,
        key=lambda r: (r.expectancy if r.expectancy is not None else -1e18),
        reverse=True,
    )
    out: list[str] = [",".join(headers)]
    for r in sorted_rows:
        row = [
            r.profile,
            _fmt_num(r.expectancy),
            _fmt_num(r.win_rate),
            _fmt_num(r.profit_factor),
            _fmt_num(r.max_drawdown),
            str(int(r.loss_streak)),
            str(int(r.paper_decision_count)),
            str(int(r.risk_veto_count)),
            _fmt_num(r.paper_pnl_estimate),
        ]
        out.append(",".join(row))
    return "\n".join(out) + "\n"


def write_reports(
    report: ComparisonReport,
    out_dir: Path | str,
) -> dict[str, Path]:
    """JSON / Markdown / CSV 3개 파일을 out_dir 에 작성.

    Returns: 작성된 path dict.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "risk_profile_comparison_summary.json"
    md_path   = out / "risk_profile_comparison_report.md"
    csv_path  = out / "risk_profile_comparison_ranking.csv"

    json_path.write_text(
        _json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    csv_path.write_text(render_ranking_csv(report), encoding="utf-8")

    return {"summary_json": json_path, "report_md": md_path, "ranking_csv": csv_path}


__all__ = [
    "COMPARISON_SCHEMA_VERSION",
    "ProfileResult",
    "ComparisonReport",
    "compare_profiles",
    "render_markdown",
    "render_ranking_csv",
    "write_reports",
]
