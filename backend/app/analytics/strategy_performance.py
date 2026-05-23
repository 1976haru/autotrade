"""P-28: 전략별 성과 대시보드 — decision episode(P-21~P-27) 기반 전략 성과 분석.

ORB / MOMENTUM / GAP / VWAP 단일 전략과 Agent Council 최종판단의 성과(승률 /
평균수익 / profit factor / MDD / 시간대·국면·risk_profile별)를 episode 기록만으로
계산한다. **실제 계좌 잔고를 사용하지 않는다** — 모든 수익률은 episode.outcome
(P-25 라벨링)의 추정 수익률 / realized_pnl 기반.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *분석 전용* — broker / OrderExecutor / route_order / 외부 HTTP /
  실 계좌 조회 import 0건, 주문을 만들지 않는다.
- secret / API key / 계좌번호 carry 0건 (episode dict 는 이미 sanitize 됨).
- `StrategyPerformanceReport.is_live_authorization=False` / `is_order_signal=False`
  / `uses_account_balance=False` 영구.
- 결정적(deterministic) — 같은 episode 목록 → 같은 결과. 성과 데이터(outcome)
  없는 episode 는 evaluated 에서 제외(승률 분모에 들어가지 않음).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.analytics.metrics import (
    compute_loss_streak,
    compute_max_drawdown,
    compute_profit_factor,
    safe_float,
)

# 단일 전략 + Agent Council.
SINGLE_STRATEGIES: tuple[str, ...] = ("ORB", "MOMENTUM", "GAP", "VWAP")
AGENT_COUNCIL = "AGENT_COUNCIL"
DEFAULT_STRATEGY_ORDER: tuple[str, ...] = (*SINGLE_STRATEGIES, AGENT_COUNCIL)

# risk_profile / market_regime / 시간대 버킷.
RISK_PROFILES: tuple[str, ...] = ("CONSERVATIVE", "BALANCED", "AGGRESSIVE")
TIME_PHASES: tuple[str, ...] = (
    "PRE_MARKET", "OPENING_RANGE", "MORNING", "MIDDAY", "CLOSING", "AFTER_MARKET",
)

_KST = timezone(timedelta(hours=9))


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def episode_return(episode: dict[str, Any]) -> float | None:
    """episode 의 대표 수익률(%) — realized_pnl 우선, 없으면 종가/최장 horizon.

    실제 계좌 잔고가 아니라 outcome(P-25) 의 추정 수익률을 사용한다.
    """
    outcome = episode.get("outcome")
    if not isinstance(outcome, dict):
        return None
    status = str(outcome.get("status", "") or "").upper()
    if status in ("", "PENDING", "UNAVAILABLE"):
        return None
    rp = _f(outcome.get("realized_pnl"))
    if rp is not None:
        return rp
    rc = _f(outcome.get("return_close"))
    if rc is not None:
        return rc
    for k in ("return_60m", "return_30m", "return_10m", "return_5m"):
        v = _f(outcome.get(k))
        if v is not None:
            return v
    return None


def _votes(episode: dict[str, Any]) -> list[dict[str, Any]]:
    v = episode.get("votes")
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _selected(episode: dict[str, Any]) -> list[str]:
    sel = episode.get("selected_strategies")
    if isinstance(sel, (list, tuple)) and sel:
        return [str(s).upper() for s in sel]
    c = episode.get("council")
    if isinstance(c, dict) and isinstance(c.get("selected_strategies"), (list, tuple)):
        return [str(s).upper() for s in c["selected_strategies"]]
    return []


def _has_order(episode: dict[str, Any]) -> bool:
    if episode.get("broker_order_no"):
        return True
    kor = episode.get("kis_order_result")
    return bool(isinstance(kor, dict) and kor.get("submitted"))


def _is_filled(episode: dict[str, Any]) -> bool:
    kor = episode.get("kis_order_result")
    if not isinstance(kor, dict):
        return False
    q = kor.get("order_quality")
    if isinstance(q, dict):
        st = str(q.get("order_status", "") or "").upper()
        return st in ("FILLED", "PARTIALLY_FILLED")
    return False


def _risk_profile(episode: dict[str, Any]) -> str:
    c = episode.get("council")
    if isinstance(c, dict) and c.get("risk_profile"):
        return str(c["risk_profile"]).upper()
    return "UNKNOWN"


def _market_regime(episode: dict[str, Any]) -> str:
    c = episode.get("council")
    if isinstance(c, dict) and c.get("market_regime"):
        return str(c["market_regime"]).upper()
    ms = episode.get("market_summary")
    if isinstance(ms, dict) and ms.get("market_regime"):
        return str(ms["market_regime"]).upper()
    return "UNKNOWN"


def _time_phase(episode: dict[str, Any]) -> str:
    """created_at(ISO, UTC) → KST 시간대 버킷."""
    ts = episode.get("created_at")
    if not ts:
        return "UNKNOWN"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        kst = dt.astimezone(_KST)
    except (ValueError, TypeError):
        return "UNKNOWN"
    minutes = kst.hour * 60 + kst.minute
    if minutes < 9 * 60:
        return "PRE_MARKET"
    if minutes < 9 * 60 + 30:
        return "OPENING_RANGE"
    if minutes < 11 * 60 + 30:
        return "MORNING"
    if minutes < 13 * 60 + 30:
        return "MIDDAY"
    if minutes < 15 * 60 + 30:
        return "CLOSING"
    return "AFTER_MARKET"


@dataclass(frozen=True)
class StrategyPerformanceReport:
    """전략별 성과 종합 — 분석 전용. 주문 신호 / 실거래 권한 아님."""

    status:           str               # OK / INSUFFICIENT_DATA
    total_episodes:   int
    evaluated_episodes: int
    strategies:       list[dict[str, Any]] = field(default_factory=list)
    by_risk_profile:  dict[str, Any] = field(default_factory=dict)
    by_market_regime: dict[str, Any] = field(default_factory=dict)
    by_time_phase:    dict[str, Any] = field(default_factory=dict)
    agent_vs_single:  dict[str, Any] = field(default_factory=dict)
    note:             str = ""

    contains_secret:       bool = False
    uses_account_balance:  bool = False
    is_order_signal:       bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.contains_secret is not False:
            raise ValueError("StrategyPerformanceReport.contains_secret must be False")
        if self.uses_account_balance is not False:
            raise ValueError("StrategyPerformanceReport.uses_account_balance must be False")
        if self.is_order_signal is not False:
            raise ValueError("StrategyPerformanceReport.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("StrategyPerformanceReport.is_live_authorization must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status":             self.status,
            "total_episodes":     self.total_episodes,
            "evaluated_episodes": self.evaluated_episodes,
            "strategies":         list(self.strategies),
            "by_risk_profile":    dict(self.by_risk_profile),
            "by_market_regime":   dict(self.by_market_regime),
            "by_time_phase":      dict(self.by_time_phase),
            "agent_vs_single":    dict(self.agent_vs_single),
            "note":               self.note,
            "contains_secret":       False,
            "uses_account_balance":  False,
            "is_order_signal":       False,
            "is_live_authorization": False,
            "advisory_disclaimer": (
                "성과 분석은 Paper/episode 기록(추정 수익률) 기준이며 실제 계좌 "
                "잔고가 아닙니다. 본 화면은 분석용이며 주문 신호가 아닙니다."
            ),
        }


def _metrics_from_returns(returns: list[float]) -> dict[str, Any]:
    """수익률 목록 → 승률 / 평균 / payoff / profit_factor / MDD / 연속손실."""
    n = len(returns)
    if n == 0:
        return {
            "trade_count": 0, "win_count": 0, "loss_count": 0,
            "win_rate": 0.0, "average_return": 0.0,
            "average_win": 0.0, "average_loss": 0.0, "payoff_ratio": None,
            "profit_factor": 0.0, "max_loss": 0.0, "max_drawdown": 0.0,
            "expectancy": 0.0, "max_consecutive_losses": 0,
        }
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    win_rate = round(len(wins) / n, 4)
    avg_return = round(sum(returns) / n, 4)
    avg_win = round(sum(wins) / len(wins), 4) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 4) if losses else 0.0
    payoff = round(abs(avg_win / avg_loss), 4) if avg_loss != 0 else None
    pf = compute_profit_factor(returns)
    pf = round(pf, 4) if isinstance(pf, (int, float)) else pf
    max_loss = round(min(returns), 4)
    mdd = round(compute_max_drawdown(returns), 4)
    expectancy = round((win_rate * avg_win) + ((1 - win_rate) * avg_loss), 4)
    return {
        "trade_count": n, "win_count": len(wins), "loss_count": len(losses),
        "win_rate": win_rate, "average_return": avg_return,
        "average_win": avg_win, "average_loss": avg_loss, "payoff_ratio": payoff,
        "profit_factor": pf, "max_loss": max_loss, "max_drawdown": mdd,
        "expectancy": expectancy,
        "max_consecutive_losses": compute_loss_streak(returns),
    }


def _strategy_block(strategy: str, episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """단일 전략(또는 AGENT_COUNCIL) 성과 블록."""
    decision_count = 0
    buy_v = sell_v = hold_v = 0
    selected_count = 0
    order_count = 0
    filled_count = 0
    returns: list[float] = []

    for ep in episodes:
        if strategy == AGENT_COUNCIL:
            action = str(ep.get("final_action", "") or "").upper()
            participated = action in ("BUY", "SELL", "HOLD")
            selected = action in ("BUY", "SELL")
        else:
            votes = _votes(ep)
            sig = None
            for v in votes:
                if str(v.get("strategy", "")).upper() == strategy:
                    sig = str(v.get("signal", "")).upper()
                    break
            participated = sig is not None
            if sig == "BUY":
                buy_v += 1
            elif sig == "SELL":
                sell_v += 1
            elif sig == "HOLD":
                hold_v += 1
            selected = strategy in _selected(ep)

        if participated:
            decision_count += 1
        if selected:
            selected_count += 1
            if _has_order(ep):
                order_count += 1
            if _is_filled(ep):
                filled_count += 1
            r = episode_return(ep)
            if r is not None:
                returns.append(r)

    metrics = _metrics_from_returns(returns)
    return {
        "strategy":        strategy,
        "decision_count":  decision_count,
        "buy_vote_count":  buy_v,
        "sell_vote_count": sell_v,
        "hold_vote_count": hold_v,
        "selected_count":  selected_count,
        "order_count":     order_count,
        "filled_count":    filled_count,
        "evaluated_count": metrics["trade_count"],
        **metrics,
    }


def _bucket_block(episodes: list[dict[str, Any]], key_fn) -> dict[str, Any]:
    """episode 를 key_fn 으로 버킷팅 → 버킷별 성과(Agent Council 최종판단 기준)."""
    buckets: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    for ep in episodes:
        action = str(ep.get("final_action", "") or "").upper()
        if action not in ("BUY", "SELL"):
            continue
        k = key_fn(ep)
        counts[k] = counts.get(k, 0) + 1
        r = episode_return(ep)
        if r is not None:
            buckets.setdefault(k, []).append(r)
    out: dict[str, Any] = {}
    for k, cnt in counts.items():
        m = _metrics_from_returns(buckets.get(k, []))
        out[k] = {
            "decision_count": cnt,
            "trade_count":    m["trade_count"],
            "win_rate":       m["win_rate"],
            "average_return": m["average_return"],
            "profit_factor":  m["profit_factor"],
            "max_loss":       m["max_loss"],
        }
    return out


def _agent_vs_single(strategy_blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Agent Council 최종판단 성과 vs 최고 단일 전략 비교."""
    by_name = {b["strategy"]: b for b in strategy_blocks}
    agent = by_name.get(AGENT_COUNCIL, {})
    agent_pf = agent.get("profit_factor")
    agent_pf_f = safe_float(agent_pf) if isinstance(agent_pf, (int, float)) else None

    best_name = None
    best_pf = None
    for s in SINGLE_STRATEGIES:
        b = by_name.get(s)
        if not b or b.get("evaluated_count", 0) <= 0:
            continue
        pf = b.get("profit_factor")
        pf_f = safe_float(pf) if isinstance(pf, (int, float)) else None
        if pf_f is None:
            continue
        if best_pf is None or pf_f > best_pf:
            best_pf = pf_f
            best_name = s

    if agent_pf_f is None or best_pf is None:
        return {
            "agent_outperforms_best_single": None,
            "best_single_strategy":   best_name,
            "best_single_profit_factor": (round(best_pf, 4) if best_pf is not None else None),
            "agent_profit_factor":    (round(agent_pf_f, 4) if agent_pf_f is not None else None),
            "agent_edge":             None,
            "warning": "비교에 필요한 성과 데이터가 부족합니다(추정 수익률 누적 필요).",
        }
    edge = round(agent_pf_f - best_pf, 4)
    outperforms = agent_pf_f >= best_pf
    warning = None
    if not outperforms:
        warning = (f"Agent Council profit factor({round(agent_pf_f, 2)})가 "
                   f"{best_name} 단독({round(best_pf, 2)})보다 낮습니다.")
    return {
        "agent_outperforms_best_single": outperforms,
        "best_single_strategy":   best_name,
        "best_single_profit_factor": round(best_pf, 4),
        "agent_profit_factor":    round(agent_pf_f, 4),
        "agent_edge":             edge,
        "warning":                warning,
    }


def calculate_strategy_performance(
    episodes: list[dict[str, Any]],
    *,
    strategy_order: tuple[str, ...] | None = None,
    min_trades: int = 1,
) -> StrategyPerformanceReport:
    """episode 목록 → 전략별 성과 종합 리포트 (deterministic, 예외 0).

    `episodes` 는 episode_to_dict 결과 dict 목록(이미 sanitize 됨). 실제 계좌
    잔고를 사용하지 않으며, 수익률은 episode.outcome(P-25) 추정값.
    """
    episodes = [e for e in (episodes or []) if isinstance(e, dict)]
    total = len(episodes)
    evaluated = sum(1 for e in episodes if episode_return(e) is not None)
    order = strategy_order or DEFAULT_STRATEGY_ORDER

    blocks = [_strategy_block(s, episodes) for s in order]
    by_risk = _bucket_block(episodes, _risk_profile)
    by_regime = _bucket_block(episodes, _market_regime)
    by_phase = _bucket_block(episodes, _time_phase)
    comparison = _agent_vs_single(blocks)

    if total == 0 or evaluated < max(1, int(min_trades)):
        return StrategyPerformanceReport(
            status="INSUFFICIENT_DATA",
            total_episodes=total,
            evaluated_episodes=evaluated,
            strategies=blocks,
            by_risk_profile=by_risk,
            by_market_regime=by_regime,
            by_time_phase=by_phase,
            agent_vs_single=comparison,
            note=("성과(outcome) 가 평가된 episode 가 부족합니다 — "
                  "사후 성과 라벨링(P-25) 누적 후 재계산하세요."),
        )

    return StrategyPerformanceReport(
        status="OK",
        total_episodes=total,
        evaluated_episodes=evaluated,
        strategies=blocks,
        by_risk_profile=by_risk,
        by_market_regime=by_regime,
        by_time_phase=by_phase,
        agent_vs_single=comparison,
        note="성과는 episode 추정 수익률 기준 — 실거래 전환은 별도 Paper Gate 필요.",
    )


__all__ = [
    "StrategyPerformanceReport",
    "calculate_strategy_performance",
    "episode_return",
    "SINGLE_STRATEGIES", "AGENT_COUNCIL", "DEFAULT_STRATEGY_ORDER",
    "RISK_PROFILES", "TIME_PHASES",
]
