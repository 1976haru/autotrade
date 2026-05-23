"""P-30: 모의매매(Paper) 100건 성과 리포트 — 데이터 기반 실전 전환 *검토* 자료.

실전 전환 여부를 *감* 으로 결정하지 않도록, 최소 100건 이상의 Paper decision
episode(P-21~P-28) 기록으로 승률 / 손익비 / profit factor / MDD / 전략별 성과 /
매수불가 사유 / 주문품질 / 매도사유 성과 / 복기 결과 / 포트폴리오 정합성을 종합한
리포트를 만든다.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *리포트 전용* — broker / OrderExecutor / route_order / 외부 HTTP /
  실 계좌 잔고 조회 import 0건. 주문을 만들지 않는다.
- **실전 전환 자동 승인 0건** — 등급이 좋아도 자동 LIVE 전환 없음.
  `PaperGateReport.is_live_authorization=False` / `auto_live_promotion=False`
  / `uses_real_account_balance=False` 영구.
- **100건 미만이면 LIVE canary 검토 등급 부여 금지** (INSUFFICIENT_SAMPLE 강제).
- 과장된 수익 약속 표현(보장/확정/무조건 류) 0건 (정적 grep 가드).
- secret / API key / 계좌번호 carry 0건. 결정적(deterministic).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.analytics.metrics import (
    compute_loss_streak,
    compute_max_drawdown,
    compute_profit_factor,
)
from app.analytics.strategy_performance import (
    calculate_strategy_performance,
    episode_return,
)

# 표본 기준.
MIN_SAMPLE_TRADES = 100
MIN_TRADING_DAYS = 10

# 실전 전환 가능성 등급.
GRADE_INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
GRADE_BLOCKED_BY_RISK     = "BLOCKED_BY_RISK"
GRADE_NOT_READY           = "NOT_READY"
GRADE_READY_FOR_EXTENDED_PAPER = "READY_FOR_EXTENDED_PAPER"
GRADE_READY_FOR_SMALL_LIVE_CANARY_REVIEW = "READY_FOR_SMALL_LIVE_CANARY_REVIEW"

# 리스크 차단 임계.
MAX_DRAWDOWN_BLOCK   = 0.15   # > 15% → BLOCKED_BY_RISK
MAX_STREAK_BLOCK     = 10     # >= 10 연속손실 → BLOCKED_BY_RISK
MIN_PROFIT_FACTOR    = 1.0    # < 1.0 → BLOCKED_BY_RISK


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _date_of(episode: dict[str, Any]) -> str | None:
    ts = episode.get("created_at")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.date().isoformat()
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class PaperGateReport:
    """Paper Gate 성과 리포트 — 분석/검토 자료. 실전 자동 전환 / 주문 신호 아님."""

    report_id:     str
    created_at:    str
    trading_days:  int
    period:        dict[str, Any]
    sample:        dict[str, Any]
    performance:   dict[str, Any]
    strategy_performance: dict[str, Any]
    blocked_reasons_top:  list[dict[str, Any]]
    order_quality:        dict[str, Any]
    sell_reason_performance: dict[str, Any]
    review_summary:       dict[str, Any]
    portfolio_integrity:  dict[str, Any]
    readiness:            dict[str, Any]
    required_actions:     list[str] = field(default_factory=list)
    note:                 str = ""

    contains_secret:        bool = False
    uses_real_account_balance: bool = False
    auto_live_promotion:    bool = False
    is_order_signal:        bool = False
    is_live_authorization:  bool = False

    def __post_init__(self) -> None:
        if self.contains_secret is not False:
            raise ValueError("PaperGateReport.contains_secret must be False")
        if self.uses_real_account_balance is not False:
            raise ValueError("PaperGateReport.uses_real_account_balance must be False")
        if self.auto_live_promotion is not False:
            raise ValueError("PaperGateReport.auto_live_promotion must be False")
        if self.is_order_signal is not False:
            raise ValueError("PaperGateReport.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("PaperGateReport.is_live_authorization must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id":     self.report_id,
            "created_at":    self.created_at,
            "trading_days":  self.trading_days,
            "period":        dict(self.period),
            "sample":        dict(self.sample),
            "performance":   dict(self.performance),
            "strategy_performance": dict(self.strategy_performance),
            "blocked_reasons_top":  list(self.blocked_reasons_top),
            "order_quality":        dict(self.order_quality),
            "sell_reason_performance": dict(self.sell_reason_performance),
            "review_summary":       dict(self.review_summary),
            "portfolio_integrity":  dict(self.portfolio_integrity),
            "readiness":            dict(self.readiness),
            "required_actions":     list(self.required_actions),
            "note":                 self.note,
            "contains_secret":          False,
            "uses_real_account_balance": False,
            "auto_live_promotion":      False,
            "is_order_signal":          False,
            "is_live_authorization":    False,
            "disclaimer": (
                "본 리포트는 Paper 모의매매 성과 분석이며 실제 계좌 성과가 아니고 "
                "수익을 보장하지 않습니다. 실전 전환은 별도 수동 승인과 Live 자금 "
                "검토가 필요하며, 등급이 좋아도 자동으로 실전 전환되지 않습니다."
            ),
        }


def _overall_performance(returns: list[float]) -> dict[str, Any]:
    n = len(returns)
    if n == 0:
        return {"win_rate": 0.0, "average_return": 0.0, "average_win": 0.0,
                "average_loss": 0.0, "payoff_ratio": None, "profit_factor": 0.0,
                "max_drawdown": 0.0, "max_consecutive_losses": 0, "expectancy": 0.0}
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    win_rate = round(len(wins) / n, 4)
    avg_win = round(sum(wins) / len(wins), 4) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 4) if losses else 0.0
    pf = compute_profit_factor(returns)
    pf = round(pf, 4) if isinstance(pf, (int, float)) else pf
    return {
        "win_rate": win_rate,
        "average_return": round(sum(returns) / n, 4),
        "average_win": avg_win, "average_loss": avg_loss,
        "payoff_ratio": (round(abs(avg_win / avg_loss), 4) if avg_loss != 0 else None),
        "profit_factor": pf,
        "max_drawdown": round(compute_max_drawdown(returns), 4),
        "max_consecutive_losses": compute_loss_streak(returns),
        "expectancy": round((win_rate * avg_win) + ((1 - win_rate) * avg_loss), 4),
    }


def _blocked_reasons_top(episodes: list[dict[str, Any]], top_n: int = 10) -> list[dict[str, Any]]:
    """매수 불가/주문 미발생 사유(reason_code) TOP 집계."""
    counts: dict[str, int] = {}
    for ep in episodes:
        submitted = bool((ep.get("kis_order_result") or {}).get("submitted")) \
            or bool(ep.get("broker_order_no"))
        action = str(ep.get("final_action", "") or "").upper()
        if submitted and action in ("BUY", "SELL"):
            continue   # 실제 주문 발생 → 매수불가 아님.
        rc = ep.get("reason_code")
        if not rc:
            rc = "NO_REASON_CODE" if action == "HOLD" else "UNKNOWN"
        counts[rc] = counts.get(rc, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"reason_code": k, "count": v} for k, v in ranked[:top_n]]


def _order_quality_summary(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_fill: dict[str, int] = {}
    latencies: list[float] = []
    slippages: list[float] = []
    rejected = partial = 0
    for ep in episodes:
        q = (ep.get("kis_order_result") or {}).get("order_quality")
        if not isinstance(q, dict):
            continue
        st = q.get("order_status") or "UNKNOWN"
        by_status[st] = by_status.get(st, 0) + 1
        fs = q.get("fill_status") or "NONE"
        by_fill[fs] = by_fill.get(fs, 0) + 1
        lat = _f(q.get("latency_ms"))
        if lat is not None:
            latencies.append(lat)
        slip = _f(q.get("slippage_bps"))
        if slip is not None:
            slippages.append(slip)
        if st == "REJECTED":
            rejected += 1
        if q.get("partial_fill"):
            partial += 1
    return {
        "by_order_status": by_status, "by_fill_status": by_fill,
        "avg_latency_ms": (round(sum(latencies) / len(latencies), 1) if latencies else None),
        "avg_slippage_bps": (round(sum(slippages) / len(slippages), 2) if slippages else None),
        "rejected_count": rejected, "partial_fill_count": partial,
    }


def _sell_reason_performance(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """매도 사유별 거래 수 + 평균 수익률(추정)."""
    agg: dict[str, dict[str, Any]] = {}
    for ep in episodes:
        if str(ep.get("final_action", "") or "").upper() != "SELL":
            continue
        sr = (ep.get("council") or {}).get("sell_reason")
        if not isinstance(sr, dict) or not sr.get("reason_code"):
            continue
        code = sr["reason_code"]
        bucket = agg.setdefault(code, {"count": 0, "returns": []})
        bucket["count"] += 1
        r = episode_return(ep)
        if r is not None:
            bucket["returns"].append(r)
    out: dict[str, Any] = {}
    for code, b in agg.items():
        rets = b["returns"]
        out[code] = {
            "count": b["count"],
            "evaluated": len(rets),
            "average_return": (round(sum(rets) / len(rets), 4) if rets else None),
            "win_rate": (round(sum(1 for r in rets if r > 0) / len(rets), 4) if rets else None),
        }
    return out


def _review_summary(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    by_grade: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    for ep in episodes:
        rv = ep.get("review")
        if not isinstance(rv, dict) or not rv.get("review_status"):
            continue
        g = rv.get("grade") or "UNKNOWN"
        by_grade[g] = by_grade.get(g, 0) + 1
        for t in (rv.get("tags") or []):
            if t:
                by_tag[t] = by_tag.get(t, 0) + 1
    return {"by_grade": by_grade, "by_tag": by_tag}


def _portfolio_integrity(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """포트폴리오 정합성 점검 — 주문 발생했는데 품질 로그 누락 등 의심 카운트."""
    checked = 0
    mismatches = 0
    for ep in episodes:
        submitted = bool((ep.get("kis_order_result") or {}).get("submitted"))
        if not submitted:
            continue
        checked += 1
        q = (ep.get("kis_order_result") or {}).get("order_quality")
        if not isinstance(q, dict) or not q.get("order_status"):
            mismatches += 1
    return {
        "checked": checked, "mismatches": mismatches,
        "status": "OK" if mismatches == 0 else "MISMATCH_SUSPECTED",
    }


def evaluate_paper_gate_readiness(
    *, evaluated_trades: int, trading_days: int, performance: dict[str, Any],
    portfolio_integrity: dict[str, Any],
) -> dict[str, Any]:
    """성과 + 표본 → 실전 전환 가능성 등급 (자동 전환 아님).

    100건 미만이면 무조건 INSUFFICIENT_SAMPLE — LIVE canary 검토 등급 금지.
    """
    pf = performance.get("profit_factor")
    pf_f = float(pf) if isinstance(pf, (int, float)) else None
    mdd = float(performance.get("max_drawdown") or 0.0)
    streak = int(performance.get("max_consecutive_losses") or 0)
    win_rate = float(performance.get("win_rate") or 0.0)
    expectancy = float(performance.get("expectancy") or 0.0)
    actions: list[str] = []

    # 1. 표본 부족 — LIVE 검토 금지.
    if evaluated_trades < MIN_SAMPLE_TRADES or trading_days < MIN_TRADING_DAYS:
        actions.append(
            f"평가 거래 {evaluated_trades}/{MIN_SAMPLE_TRADES}건, "
            f"거래일 {trading_days}/{MIN_TRADING_DAYS}일 — 추가 Paper 검증 필요.")
        return {"grade": GRADE_INSUFFICIENT_SAMPLE, "can_review_live_canary": False,
                "reasons": actions}

    # 2. 리스크 차단.
    risk_reasons: list[str] = []
    if portfolio_integrity.get("status") != "OK":
        risk_reasons.append("포트폴리오 정합성 불일치 의심 — 리스크로 인해 차단.")
    if mdd > MAX_DRAWDOWN_BLOCK:
        risk_reasons.append(f"최대 낙폭 {round(mdd * 100, 1)}% > {int(MAX_DRAWDOWN_BLOCK * 100)}% — 차단.")
    if streak >= MAX_STREAK_BLOCK:
        risk_reasons.append(f"연속 손실 {streak}회 ≥ {MAX_STREAK_BLOCK} — 차단.")
    if pf_f is not None and pf_f < MIN_PROFIT_FACTOR:
        risk_reasons.append(f"profit factor {round(pf_f, 2)} < {MIN_PROFIT_FACTOR} — 실전 전환 보류.")
    if risk_reasons:
        return {"grade": GRADE_BLOCKED_BY_RISK, "can_review_live_canary": False,
                "reasons": risk_reasons}

    # 3. 강한 성과 → 소액 실전 *검토* 가능 (자동 승인 아님).
    if (pf_f is not None and pf_f >= 1.5 and win_rate >= 0.5 and mdd <= 0.10
            and streak < 5 and expectancy > 0):
        actions.append("소액 실전(canary) 검토 가능 — 단, 별도 수동 승인 + Live 자금 검토 필요.")
        return {"grade": GRADE_READY_FOR_SMALL_LIVE_CANARY_REVIEW,
                "can_review_live_canary": True, "reasons": actions}

    # 4. 양호 → 추가 Paper 권장.
    if (pf_f is not None and pf_f >= 1.2 and expectancy > 0 and mdd <= MAX_DRAWDOWN_BLOCK):
        actions.append("추가 Paper 검증 필요 — 표본/일수 확대 후 재평가 권장.")
        return {"grade": GRADE_READY_FOR_EXTENDED_PAPER, "can_review_live_canary": False,
                "reasons": actions}

    # 5. 그 외 — 준비 안 됨.
    actions.append("기대값/손익비가 충분치 않음 — 전략 보완 후 재검증 필요.")
    return {"grade": GRADE_NOT_READY, "can_review_live_canary": False, "reasons": actions}


def generate_paper_gate_report(
    episodes: list[dict[str, Any]],
    *,
    trading_days: int | None = None,
    now: datetime | None = None,
) -> PaperGateReport:
    """episode 목록 → Paper Gate 성과 리포트 (deterministic, 예외 0).

    실제 계좌 잔고가 아니라 episode.outcome(P-25) 추정 수익률만 사용한다.
    """
    episodes = [e for e in (episodes or []) if isinstance(e, dict)]
    created = now or datetime.now(timezone.utc)

    total_decisions = len(episodes)
    total_orders = sum(
        1 for e in episodes
        if bool((e.get("kis_order_result") or {}).get("submitted")) or e.get("broker_order_no"))
    filled_orders = sum(
        1 for e in episodes
        if str(((e.get("kis_order_result") or {}).get("order_quality") or {}).get("order_status", "")
                ).upper() in ("FILLED", "PARTIALLY_FILLED"))
    returns = [r for r in (episode_return(e) for e in episodes) if r is not None]
    evaluated_trades = len(returns)

    dates = sorted({d for d in (_date_of(e) for e in episodes) if d})
    if trading_days is None:
        trading_days = len(dates)
    period = {"start_date": (dates[0] if dates else None),
              "end_date": (dates[-1] if dates else None)}

    performance = _overall_performance(returns)
    strat_report = calculate_strategy_performance(episodes).to_dict()
    blocked = _blocked_reasons_top(episodes)
    oq = _order_quality_summary(episodes)
    srp = _sell_reason_performance(episodes)
    review = _review_summary(episodes)
    integrity = _portfolio_integrity(episodes)

    readiness = evaluate_paper_gate_readiness(
        evaluated_trades=evaluated_trades, trading_days=trading_days,
        performance=performance, portfolio_integrity=integrity,
    )
    required_actions = list(readiness.get("reasons", []))
    if evaluated_trades < MIN_SAMPLE_TRADES:
        required_actions.append("100건 미만 — 실전 전환 검토 불가, Paper 기록 누적 지속.")

    sample = {
        "total_decisions": total_decisions,
        "total_orders": total_orders,
        "filled_orders": filled_orders,
        "evaluated_trades": evaluated_trades,
        "meets_100_sample": evaluated_trades >= MIN_SAMPLE_TRADES,
        "meets_min_trading_days": trading_days >= MIN_TRADING_DAYS,
    }

    seed = f"{created.isoformat()}|{total_decisions}|{evaluated_trades}|{readiness['grade']}"
    rid = "pgr-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]  # noqa: S324

    return PaperGateReport(
        report_id=rid, created_at=created.isoformat(),
        trading_days=int(trading_days), period=period, sample=sample,
        performance=performance, strategy_performance=strat_report,
        blocked_reasons_top=blocked, order_quality=oq,
        sell_reason_performance=srp, review_summary=review,
        portfolio_integrity=integrity, readiness=readiness,
        required_actions=required_actions,
        note="Paper 모의매매 기준 성과 — 실전 전환은 별도 수동 승인 + Live 자금 검토 필요.",
    )


__all__ = [
    "PaperGateReport", "generate_paper_gate_report", "evaluate_paper_gate_readiness",
    "MIN_SAMPLE_TRADES", "MIN_TRADING_DAYS",
    "GRADE_INSUFFICIENT_SAMPLE", "GRADE_BLOCKED_BY_RISK", "GRADE_NOT_READY",
    "GRADE_READY_FOR_EXTENDED_PAPER", "GRADE_READY_FOR_SMALL_LIVE_CANARY_REVIEW",
]
