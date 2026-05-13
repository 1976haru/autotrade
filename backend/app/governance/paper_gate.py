"""Paper Gate evaluator (#72).

Paper 모드 4주 이상 운용 결과를 `docs/promotion_policy.md` Paper 기준으로
평가한다. PASS는 *Live Manual Approval 검토 가능* 을 의미하며 **실거래
자동 허가가 아니다** — 운영자가 별도 옵트인 PR을 통해서만 LIVE 모드로
진행 가능.

CLAUDE.md 절대 원칙:
- 본 모듈은 *판단만* 한다. broker / OrderExecutor / route_order 호출 0건.
- DB는 read-only — INSERT/UPDATE/DELETE 0건 (정적 grep 가드).
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING`
  변경 0건 — 본 모듈은 어떤 안전 플래그도 mutate 하지 않는다.
- PASS 라벨은 *권고*일 뿐 — 호출자(운영자, 별도 PR)가 직접 확인하고 결정.

평가 기준 (`docs/paper_gate_policy.md` 와 lockstep):
- PASS:    28일 이상 + 100건 이상 + expectancy 양수 + PF ≥ 1.2 +
           MDD ≤ 한도 + 손실한도 위반 0 + audit 누락 0 +
           stale/duplicate 위반 0
- CAUTION: PASS 임계 충족 but best_day_share>0.5 / 시간대 손실 집중 /
           rejection 비율 높음 / paper-backtest 괴리 큼 등
- FAIL:    표본 부족 / 기대값 음수 / PF 미달 / 손실한도 위반 / audit 누락

invariant:
- `PaperGateResult.is_live_authorization=False` 항상 — 본 dataclass 생성
  시점에 `__post_init__`이 강제. PASS 라벨이 *실거래 허가*로 잘못 해석되는
  것을 방지.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ---------- enums / thresholds ----------


class PaperGateVerdict(StrEnum):
    """Paper Gate 4단계 판정.

    UNKNOWN: 데이터 부족 / 입력 누락 — *PASS가 아니다*. 호출자는 추가
    데이터를 확보하거나 보수적으로 FAIL 취급.
    """
    PASS    = "PASS"
    CAUTION = "CAUTION"
    FAIL    = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PaperGateThresholds:
    """Paper Gate 임계치. promotion_policy.md / strategy_promotion.py와 lockstep.

    필드 default는 보수적 — env override는 후속 PR (실제 paper 운영 단계에서
    민감도 조정).
    """
    # 핵심 PASS 기준.
    min_active_days:               int   = 28
    min_trade_count:               int   = 100
    min_expectancy:                float = 0.0          # *양수* — 0초과
    min_profit_factor:             float = 1.2
    max_drawdown_pct:              float = 0.15         # 초기 자본의 15%
    max_loss_limit_violations:     int   = 0
    max_audit_missing:             int   = 0
    max_stale_or_duplicate:        int   = 0

    # CAUTION 임계.
    caution_best_day_pnl_share:    float = 0.5          # 하루가 전체 손익의 50% 초과 의존
    caution_rejection_rate:        float = 0.3          # 30% 초과 rejection
    caution_max_hourly_loss_share: float = 0.6          # 특정 시간대가 손실의 60% 초과
    caution_paper_vs_backtest_pf_drift: float = 0.5     # |paper_pf - bt_pf| / bt_pf > 0.5

    # 최소 운영 일수 누락 + ai_low_confidence_burst 등 *고급 CAUTION* 후속 PR.


# ---------- input DTO ----------


@dataclass(frozen=True)
class PaperGateInput:
    """Paper Gate 평가 입력.

    수치는 외부 collector(`paper_gate_collector.py` 또는 운영자 수동)가 채운다.
    본 모듈은 *판단*만 — 데이터 수집 / 외부 API / DB write 0건.

    필수 의미:
    - trade_count: 종결된 (체결된) 매매 신호 또는 주문 수.
    - winning_pnl_sum / losing_pnl_sum: PF 계산용. 부호는 *절댓값* 으로
      넣어도 무방 — 본 모듈이 abs로 변환.
    - active_days: 매매가 일어난 *영업일* 수.
    - max_drawdown_value: 누적 곡선상 절댓값 (음수가 아니라 양의 정수 손실폭).
    - initial_cash: MDD ratio 계산 base.
    - loss_limit_violations: `RiskPolicy.max_daily_loss` 초과한 일수.
    - audit_missing_count: paper 주문 중 OrderAuditLog row 누락 건수.
    - stale_or_duplicate_violations: OrderGuard / staleness 가드 위반 누적.
    - best_day_pnl_share: 가장 좋은 하루의 손익이 전체 손익에서 차지하는 비율.
    - rejection_rate: REJECTED 주문 / 전체 주문 비율.
    - hourly_loss_top_share: 가장 손실이 큰 시간대가 전체 손실에서 차지하는 비율.
    - paper_vs_backtest_pf_drift: |paper_pf - backtest_pf| / backtest_pf (옵션).
    """
    strategy_name:                 str
    period_start:                  datetime
    period_end:                    datetime

    trade_count:                   int   = 0
    active_days:                   int   = 0
    winning_pnl_sum:               int   = 0
    losing_pnl_sum:                int   = 0
    expectancy:                    float = 0.0
    max_drawdown_value:            int   = 0
    initial_cash:                  int   = 10_000_000

    loss_limit_violations:         int   = 0
    audit_missing_count:           int   = 0
    stale_or_duplicate_violations: int   = 0
    rejection_rate:                float = 0.0

    best_day_pnl_share:            float | None = None
    hourly_loss_top_share:         float | None = None
    paper_vs_backtest_pf_drift:    float | None = None

    fill_polling_consistent:       bool  = True
    client_order_id_idempotent:    bool  = True

    @property
    def profit_factor(self) -> float | None:
        wins   = abs(int(self.winning_pnl_sum))
        losses = abs(int(self.losing_pnl_sum))
        if losses == 0:
            # 손실이 0이면 PF는 무한대 — 보수적으로 큰 수치로 보고.
            return float("inf") if wins > 0 else None
        return wins / losses

    @property
    def max_drawdown_pct(self) -> float:
        if self.initial_cash <= 0:
            return 0.0
        return abs(self.max_drawdown_value) / float(self.initial_cash)

    @property
    def period_days(self) -> int:
        delta = (self.period_end - self.period_start).days
        return max(0, int(delta))


# ---------- result DTO ----------


@dataclass
class PaperGateResult:
    """Paper Gate 평가 결과.

    invariants (코드 단 강제):
    - `is_live_authorization=False` 항상 (PASS 라벨이 실거래 허가가 아님 강제).
    - `is_order_signal=False` 항상 (BUY/SELL/HOLD 신호가 아님).

    PASS 라벨은 *Live Manual Approval 검토 가능* 을 의미 — 별도 PR과
    사용자 명시 승인 전까지 LIVE 모드 활성화 금지.
    """
    strategy_name:           str
    period_start:            datetime
    period_end:              datetime
    verdict:                 PaperGateVerdict
    passed_criteria:         list[str] = field(default_factory=list)
    failed_criteria:         list[str] = field(default_factory=list)
    cautions:                list[str] = field(default_factory=list)
    metrics:                 dict[str, Any] = field(default_factory=dict)
    thresholds:              dict[str, Any] = field(default_factory=dict)
    next_step:               str = ""
    is_live_authorization:   bool = False
    is_order_signal:         bool = False
    generated_at:            datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        # invariant — PASS 라벨이 실거래 허가가 아님 강제.
        if self.is_live_authorization is not False:
            raise ValueError(
                "PaperGateResult.is_live_authorization must be False. "
                "PASS verdict means 'eligible for Live Manual Approval review', "
                "NOT 'authorize live trading'."
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "PaperGateResult.is_order_signal must be False — Paper Gate "
                "does not produce BUY/SELL/HOLD signals."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name":         self.strategy_name,
            "period_start":          self.period_start.isoformat(),
            "period_end":            self.period_end.isoformat(),
            "verdict":               self.verdict.value,
            "passed_criteria":       list(self.passed_criteria),
            "failed_criteria":       list(self.failed_criteria),
            "cautions":              list(self.cautions),
            "metrics":               dict(self.metrics),
            "thresholds":            dict(self.thresholds),
            "next_step":             self.next_step,
            "is_live_authorization": self.is_live_authorization,
            "is_order_signal":       self.is_order_signal,
            "generated_at":          self.generated_at.isoformat(),
            # invariant — Paper Gate 평가는 안전 플래그를 변경하지 않는다.
            "live_flag_changed":     False,
            "mode_changed":          False,
        }


# ---------- evaluator ----------


def _format_pf(pf: float | None) -> str:
    if pf is None:
        return "N/A"
    if pf == float("inf"):
        return "∞ (no losing trades)"
    return f"{pf:.2f}"


def evaluate_paper_gate(
    inp: PaperGateInput,
    thresholds: PaperGateThresholds | None = None,
) -> PaperGateResult:
    """Paper Gate 평가 — 입력 DTO → 결과 DTO.

    어떤 경우에도 외부 시스템에 영향을 주지 않는다. 본 함수는 결정 트리만
    수행하며, 호출자가 markdown 리포트로 변환하거나 운영자에게 surface한다.
    """
    th = thresholds or PaperGateThresholds()
    passed: list[str] = []
    failed: list[str] = []
    cautions: list[str] = []

    period_days = inp.period_days
    pf          = inp.profit_factor
    mdd_pct     = inp.max_drawdown_pct

    # --- PASS 기준 ---
    # 1) 기간 28일 이상.
    if period_days >= th.min_active_days:
        passed.append(
            f"운영 기간 {period_days}일 ≥ {th.min_active_days}일."
        )
    else:
        failed.append(
            f"운영 기간 {period_days}일 < {th.min_active_days}일 — 표본 부족."
        )

    # 2) 매매 신호 / 주문 수.
    if inp.trade_count >= th.min_trade_count:
        passed.append(
            f"매매 신호 {inp.trade_count}건 ≥ {th.min_trade_count}건."
        )
    else:
        failed.append(
            f"매매 신호 {inp.trade_count}건 < {th.min_trade_count}건 — 표본 부족."
        )

    # 3) 기대값 양수.
    if inp.expectancy > th.min_expectancy:
        passed.append(f"기대값 {inp.expectancy:.2f} > 0.")
    else:
        failed.append(
            f"기대값 {inp.expectancy:.2f} ≤ {th.min_expectancy} — 양수 미달."
        )

    # 4) Profit Factor ≥ 1.2.
    if pf is not None and pf >= th.min_profit_factor:
        passed.append(f"PF {_format_pf(pf)} ≥ {th.min_profit_factor}.")
    else:
        failed.append(
            f"PF {_format_pf(pf)} < {th.min_profit_factor} — 손익 효율 부족."
        )

    # 5) MDD 한도.
    if mdd_pct <= th.max_drawdown_pct:
        passed.append(
            f"MDD {mdd_pct:.2%} ≤ {th.max_drawdown_pct:.2%}."
        )
    else:
        failed.append(
            f"MDD {mdd_pct:.2%} > {th.max_drawdown_pct:.2%} — 한도 초과."
        )

    # 6) 손실한도 위반 0회.
    if inp.loss_limit_violations <= th.max_loss_limit_violations:
        passed.append(
            f"손실한도 위반 {inp.loss_limit_violations}회 ≤ "
            f"{th.max_loss_limit_violations}회."
        )
    else:
        failed.append(
            f"손실한도 위반 {inp.loss_limit_violations}회 — "
            f"RiskPolicy.max_daily_loss 초과 (허용 {th.max_loss_limit_violations})."
        )

    # 7) audit 누락 0회.
    if inp.audit_missing_count <= th.max_audit_missing:
        passed.append(f"OrderAuditLog 누락 {inp.audit_missing_count}회.")
    else:
        failed.append(
            f"OrderAuditLog 누락 {inp.audit_missing_count}회 — "
            f"감사 흐름 깨짐 (허용 {th.max_audit_missing})."
        )

    # 8) stale / duplicate 위반 0회.
    if inp.stale_or_duplicate_violations <= th.max_stale_or_duplicate:
        passed.append(
            f"stale/duplicate 위반 {inp.stale_or_duplicate_violations}회 ≤ "
            f"{th.max_stale_or_duplicate}회."
        )
    else:
        failed.append(
            f"stale/duplicate 위반 {inp.stale_or_duplicate_violations}회 — "
            f"OrderGuard/freshness 가드 통과 실패."
        )

    # 9) FillPolling 정합성 / idempotency.
    if inp.fill_polling_consistent:
        passed.append("FillPolling 정합성 OK.")
    else:
        failed.append("FillPolling 정합성 실패 — broker view vs audit drift.")
    if inp.client_order_id_idempotent:
        passed.append("client_order_id idempotency OK.")
    else:
        failed.append("client_order_id idempotency 실패 — 같은 ID로 중복 주문 가능.")

    # --- CAUTION 기준 (PASS 임계 통과해도 surface) ---
    if inp.best_day_pnl_share is not None and inp.best_day_pnl_share > th.caution_best_day_pnl_share:
        cautions.append(
            f"하루 의존도 {inp.best_day_pnl_share:.1%} > "
            f"{th.caution_best_day_pnl_share:.1%} — 특정 하루 수익에 과도 의존."
        )
    if inp.rejection_rate > th.caution_rejection_rate:
        cautions.append(
            f"rejection 비율 {inp.rejection_rate:.1%} > "
            f"{th.caution_rejection_rate:.1%} — RiskManager / OrderGuard 반복 거부."
        )
    if inp.hourly_loss_top_share is not None and inp.hourly_loss_top_share > th.caution_max_hourly_loss_share:
        cautions.append(
            f"시간대 손실 집중 {inp.hourly_loss_top_share:.1%} > "
            f"{th.caution_max_hourly_loss_share:.1%} — 특정 시간대 손실 편중."
        )
    if (inp.paper_vs_backtest_pf_drift is not None and
            inp.paper_vs_backtest_pf_drift > th.caution_paper_vs_backtest_pf_drift):
        cautions.append(
            f"Paper vs Backtest PF 괴리 {inp.paper_vs_backtest_pf_drift:.1%} > "
            f"{th.caution_paper_vs_backtest_pf_drift:.1%} — 실 환경 vs 백테스트 분포 차이."
        )

    # --- verdict ---
    if not passed and not failed:
        verdict = PaperGateVerdict.UNKNOWN
    elif failed:
        verdict = PaperGateVerdict.FAIL
    elif cautions:
        verdict = PaperGateVerdict.CAUTION
    else:
        verdict = PaperGateVerdict.PASS

    next_step = _next_step_for_verdict(verdict)

    return PaperGateResult(
        strategy_name=inp.strategy_name,
        period_start=inp.period_start,
        period_end=inp.period_end,
        verdict=verdict,
        passed_criteria=passed,
        failed_criteria=failed,
        cautions=cautions,
        metrics={
            "period_days":                    period_days,
            "trade_count":                    inp.trade_count,
            "active_days":                    inp.active_days,
            "expectancy":                     round(inp.expectancy, 4),
            "profit_factor":                  _format_pf(pf),
            "max_drawdown_pct":               round(mdd_pct, 4),
            "loss_limit_violations":          inp.loss_limit_violations,
            "audit_missing_count":            inp.audit_missing_count,
            "stale_or_duplicate_violations":  inp.stale_or_duplicate_violations,
            "rejection_rate":                 round(inp.rejection_rate, 4),
            "best_day_pnl_share":             inp.best_day_pnl_share,
            "hourly_loss_top_share":          inp.hourly_loss_top_share,
            "paper_vs_backtest_pf_drift":     inp.paper_vs_backtest_pf_drift,
            "fill_polling_consistent":        inp.fill_polling_consistent,
            "client_order_id_idempotent":     inp.client_order_id_idempotent,
        },
        thresholds={
            "min_active_days":               th.min_active_days,
            "min_trade_count":               th.min_trade_count,
            "min_expectancy":                th.min_expectancy,
            "min_profit_factor":             th.min_profit_factor,
            "max_drawdown_pct":              th.max_drawdown_pct,
            "max_loss_limit_violations":     th.max_loss_limit_violations,
            "max_audit_missing":             th.max_audit_missing,
            "max_stale_or_duplicate":        th.max_stale_or_duplicate,
        },
        next_step=next_step,
    )


def _next_step_for_verdict(v: PaperGateVerdict) -> str:
    if v == PaperGateVerdict.PASS:
        return (
            "Live Manual Approval 검토 가능 (실거래 허가 아님). "
            "별도 옵트인 PR + 사용자 명시 승인 후에만 LIVE 진입."
        )
    if v == PaperGateVerdict.CAUTION:
        return (
            "CAUTION 사유를 운영자가 검토. Paper 추가 운용 또는 "
            "원인 분석 후 재평가 권장."
        )
    if v == PaperGateVerdict.FAIL:
        return (
            "Paper / Shadow 추가 운용으로 표본·지표 재확보. "
            "실거래 진입 금지 — 본 PR로 LIVE flag 변경 0건."
        )
    return (
        "데이터 부족 — 입력을 확보 후 재평가. 보수적으로 FAIL 취급 권장."
    )


# ---------- markdown report ----------


def render_markdown_report(result: PaperGateResult) -> str:
    """Paper Gate 결과를 markdown 리포트로 변환.

    상단에 *실거래 허가 아님* 고지를 강제. 리포트 어느 구간에도 BUY/SELL/HOLD
    문구를 포함하지 않는다 — Paper Gate는 주문 신호가 아니다.
    """
    lines: list[str] = []
    lines.append(f"# Paper Gate Report — {result.strategy_name}")
    lines.append("")
    lines.append(
        f"_생성: {result.generated_at.isoformat()} · "
        f"기간: {result.period_start.date()} ~ {result.period_end.date()}_"
    )
    lines.append("")
    lines.append("> ⚠️ **본 리포트는 *실거래 허가가 아니다*.**")
    lines.append("> ")
    lines.append(
        "> PASS는 *Live Manual Approval 검토 가능*을 의미. 실거래 진입은 "
        "별도 옵트인 PR + 사용자 명시 승인 + `ENABLE_LIVE_TRADING=true` "
        "(현재 default false) 모두 필요."
    )
    lines.append("")
    lines.append("## 판정")
    lines.append("")
    lines.append(f"- **Verdict: `{result.verdict.value}`**")
    lines.append(f"- 다음 단계: {result.next_step}")
    lines.append("")
    if result.failed_criteria:
        lines.append("## 미충족 기준 (FAIL 사유)")
        for c in result.failed_criteria:
            lines.append(f"- ❌ {c}")
        lines.append("")
    if result.cautions:
        lines.append("## CAUTION 항목 (운영자 검토 권장)")
        for c in result.cautions:
            lines.append(f"- ⚠️ {c}")
        lines.append("")
    if result.passed_criteria:
        lines.append("## 충족 기준")
        for c in result.passed_criteria:
            lines.append(f"- ✅ {c}")
        lines.append("")
    lines.append("## 메트릭")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    for k, v in result.metrics.items():
        lines.append(f"| `{k}` | {v} |")
    lines.append("")
    lines.append("## 임계 (PaperGateThresholds)")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    for k, v in result.thresholds.items():
        lines.append(f"| `{k}` | {v} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "이 리포트는 *판단 보조 자료*입니다. RiskManager / PermissionGate / "
        "OrderExecutor 우회 금지. 본 PR로 어떤 LIVE 플래그 / 안전 플래그도 "
        "변경되지 않습니다."
    )
    return "\n".join(lines)
