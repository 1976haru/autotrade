"""KIS-INTRADAY-50-6M-ROOT-CAUSE-REBUILD-01 — 재설계 실험용 포트폴리오 시뮬 v2.

원본 `portfolio_capital_sim` 위에 *실험 knob* 을 추가한 per-timestamp 시뮬레이터:
universe 필터 / 신호 선택 랭킹(선착순 외) / 비용 민감도 / 진입 시간 컷오프 + 강제청산 /
Agent 역할(진입권 회수) / 장세 필터 / worst-month 방어(월간 DD/연속손실/쿨다운).

**read-only · Paper/Backtest only.** broker / OrderExecutor / route_order / KIS 주문 API
import·호출 0건. 실주문/실체결 0건.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

_KST = timezone(timedelta(hours=9))
_GRADE_SCORE = {"GO": 3, "TUNE": 2, "WATCH": 1, "EXCLUDE": 0}

SELECTION_MODES = (
    "earliest_first", "symbol_grade_rank", "strategy_pf_rank",
    "expected_move_after_cost_rank", "time_bucket_edge_rank",
    "recent_symbol_momentum_rank", "volatility_adjusted_rank", "composite_rank",
)
AGENT_MODES = (
    "AGENT_ENTRY_DECIDER", "AGENT_OFF", "AGENT_RISK_FILTER_ONLY",
    "AGENT_POSITION_SIZER_ONLY", "AGENT_VETO_ONLY", "AGENT_AFTER_TRADE_REVIEW_ONLY",
)


@dataclass(frozen=True)
class SimV2Config:
    initial_capital: float = 10_000_000.0
    max_positions: int = 5
    min_position_notional: float = 1_000_000.0
    max_position_notional: float = 2_000_000.0
    fee_bps_per_side: float = 1.5
    sell_tax_bps: float = 18.0
    slippage_bps: float = 5.0
    # 실험 knob.
    universe: frozenset[str] | None = None            # None=전체
    selection_mode: str = "earliest_first"
    agent_mode: str = "AGENT_ENTRY_DECIDER"
    min_quality_for_risk_filter: float = 60.0
    entry_cutoff_min: int | None = None               # 이 시각(분) 이후 신규 진입 금지
    forced_close_min: int | None = None               # 이 시각(분) 이후 강제청산
    monthly_dd_stop_pct: float = 0.0                  # 월 고점 대비 -x% 도달 시 그 달 진입 중단
    consecutive_loss_stop: int = 0                    # 연속손실 n 초과 시 진입 중단(다음날 해제)
    weekly_loss_cooldown_pct: float = 0.0             # 주간 -x% 시 cooldown_days 진입 중단
    cooldown_days: int = 3
    worst_symbol_cooldown: bool = False
    worst_strategy_cooldown: bool = False
    regime_block_down_day: bool = False               # 시장(횡단면) 하락일 진입 중단
    # 랭킹 보조 입력.
    grade_map: dict[str, str] = field(default_factory=dict)
    strategy_pf: dict[str, float | None] = field(default_factory=dict)
    bucket_edge: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SimV2Result:
    config_label: str
    total_return_pct: float
    final_equity: float
    profit_factor: float | None
    max_drawdown_pct: float | None
    expectancy: float | None
    win_rate: float | None
    payoff_ratio: float | None
    trade_count: int
    avg_trades_per_day: float | None
    worst_day_pnl: float | None
    worst_consecutive_losses: int
    avg_hold_minutes: float | None
    monthly_returns: dict[str, float]
    walk_forward_score: float | None
    oos_positive: bool
    turnover: float | None
    cost_paid_total: float
    tax_paid_total: float
    slippage_paid_total: float
    trading_days: int
    by_time_bucket: dict[str, Any] = field(default_factory=dict)
    by_hold_bucket: dict[str, Any] = field(default_factory=dict)
    by_strategy: dict[str, Any] = field(default_factory=dict)
    by_grade: dict[str, Any] = field(default_factory=dict)
    skipped_max_positions: int = 0
    skipped_no_cash: int = 0
    low_confidence: bool = False
    # 안전 불변값.
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    do_not_auto_apply: bool = True
    contains_secret: bool = False
    no_profit_guarantee: bool = True

    def __post_init__(self) -> None:
        if self.is_live_authorization or self.broker_order_sent or self.order_created:
            raise ValueError("unsafe invariant True")
        if not self.do_not_auto_apply or not self.no_profit_guarantee:
            raise ValueError("do_not_auto_apply / no_profit_guarantee must be True")


def _hold_bucket(mins: float) -> str:
    if mins <= 30:
        return "0-30m"
    if mins <= 60:
        return "30-60m"
    if mins <= 120:
        return "60-120m"
    if mins <= 240:
        return "120-240m"
    return "240m+"


def _is_candidate(sig: dict[str, Any], agent_mode: str, min_quality: float) -> bool:
    council_buy = sig["council_action"] == "BUY"
    single_buy = bool(sig["single_buys"])
    if agent_mode == "AGENT_ENTRY_DECIDER":
        return council_buy
    if agent_mode == "AGENT_POSITION_SIZER_ONLY":
        return council_buy
    if agent_mode in ("AGENT_OFF", "AGENT_AFTER_TRADE_REVIEW_ONLY"):
        return single_buy
    if agent_mode == "AGENT_VETO_ONLY":
        return single_buy and sig["council_action"] != "HOLD"
    if agent_mode == "AGENT_RISK_FILTER_ONLY":
        return single_buy and sig.get("quality_score", 0.0) >= min_quality
    return council_buy


def _rank_key(sig: dict[str, Any], cfg: SimV2Config, bar: Any, day_open: float | None) -> float:
    mode = cfg.selection_mode
    rt_cost_pct = (2 * cfg.fee_bps_per_side + cfg.sell_tax_bps + 2 * cfg.slippage_bps) / 100.0
    if mode == "symbol_grade_rank":
        return _GRADE_SCORE.get(cfg.grade_map.get(bar.symbol, "WATCH"), 1) + sig["confidence"] / 10
    if mode == "strategy_pf_rank":
        pfs = [cfg.strategy_pf.get(s) for s in sig["single_buys"] or sig["selected"]]
        pfs = [p for p in pfs if p is not None]
        return max(pfs) if pfs else 0.0
    if mode == "expected_move_after_cost_rank":
        return sig["target_pct"] - rt_cost_pct
    if mode == "time_bucket_edge_rank":
        return cfg.bucket_edge.get(sig["time_bucket"], 0.0)
    if mode == "recent_symbol_momentum_rank":
        return (bar.close - day_open) / day_open if day_open else 0.0
    if mode == "volatility_adjusted_rank":
        # 변동성 proxy: 당일 시가 대비 현재 이격 절댓값. 낮을수록(안정) 우선 → 음수 부호.
        dev = abs((bar.close - day_open) / day_open) if day_open else 0.01
        return sig["confidence"] / (dev + 0.005)
    if mode == "composite_rank":
        g = _GRADE_SCORE.get(cfg.grade_map.get(bar.symbol, "WATCH"), 1) / 3.0
        pfs = [cfg.strategy_pf.get(s) for s in sig["single_buys"] or sig["selected"]]
        pfs = [p for p in pfs if p is not None]
        spf = (max(pfs) - 1.0) if pfs else 0.0
        em = (sig["target_pct"] - rt_cost_pct) / 3.0
        be = cfg.bucket_edge.get(sig["time_bucket"], 0.0) * 20
        return 0.35 * g + 0.25 * spf + 0.20 * em + 0.10 * be + 0.10 * sig["confidence"]
    return 0.0  # earliest_first → 동률, symbol 순(안정 정렬)


def run_sim_v2(bars: Sequence[Any], signals: dict[tuple[str, str], dict[str, Any]],
               cfg: SimV2Config, *, label: str = "") -> SimV2Result:
    from app.backtest.strategy_council_backtest import _group_by_day

    days = _group_by_day(bars)
    # ts → [(symbol, bar, is_last_of_day)]; day_open[(sym,date)]; cross-sectional day return.
    by_ts: dict[datetime, list[tuple[str, Any, bool]]] = defaultdict(list)
    day_open: dict[tuple[str, Any], float] = {}
    day_first_last: dict[Any, dict[str, list[float]]] = defaultdict(lambda: {"o": [], "c": []})
    for day_bars in days:
        sym = day_bars[0].symbol
        dt0 = day_bars[0].timestamp.astimezone(_KST).date()
        day_open[(sym, dt0)] = day_bars[0].open
        day_first_last[dt0]["o"].append(day_bars[0].open)
        day_first_last[dt0]["c"].append(day_bars[-1].close)
        last_i = len(day_bars) - 1
        for i, b in enumerate(day_bars):
            by_ts[b.timestamp].append((sym, b, i == last_i))
    # 횡단면 시장 일간수익. **look-ahead 방지**: 진입 판단에는 *전일* 수익만 사용한다
    # (당일 종가는 장중 진입 시점에 알 수 없음). market_day_ret 은 *전일* 시장수익.
    _same_day: dict[Any, float] = {}
    for d, ol in day_first_last.items():
        if ol["o"] and ol["c"]:
            rets = [(c - o) / o for o, c in zip(ol["o"], ol["c"]) if o]
            _same_day[d] = statistics.median(rets) if rets else 0.0
    _ordered_dates = sorted(_same_day)
    market_day_ret: dict[Any, float] = {}   # date → *전일* 시장수익 (없으면 0=중립).
    for idx, d in enumerate(_ordered_dates):
        market_day_ret[d] = _same_day[_ordered_dates[idx - 1]] if idx > 0 else 0.0

    fee = cfg.fee_bps_per_side / 10_000.0
    tax = cfg.sell_tax_bps / 10_000.0
    slip = cfg.slippage_bps / 10_000.0

    cash = cfg.initial_capital
    positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    cost_paid = tax_paid = slip_paid = 0.0
    buy_notional_total = 0.0
    skipped_max = skipped_cash = 0

    daily_equity: list[tuple[Any, float]] = []
    cur_date: Any = None
    month_peak_equity: dict[str, float] = {}
    month_start_equity: dict[str, float] = {}
    blocked_months: set[str] = set()
    cooldown_until_date: Any = None
    consec_losses = 0
    symbol_cooldown: dict[str, int] = {}     # symbol → 진입금지 남은 일수(완화)
    strategy_loss: dict[str, float] = defaultdict(float)

    def _close(sym: str, price: float, ts: str, reason: str) -> None:
        nonlocal cash, cost_paid, tax_paid, slip_paid, consec_losses
        pos = positions.pop(sym)
        exit_px = price * (1 - slip)
        proceeds = pos["qty"] * exit_px
        sell_fee = proceeds * fee
        sell_tax = proceeds * tax
        slip_paid += pos["qty"] * price * slip
        cost_paid += sell_fee
        tax_paid += sell_tax
        net = (proceeds - sell_fee - sell_tax) - pos["cost_basis"]
        cash += proceeds - sell_fee - sell_tax
        hold_min = (datetime.fromisoformat(ts) - datetime.fromisoformat(pos["entry_ts"])
                    ).total_seconds() / 60.0
        trades.append({
            "symbol": sym, "entry_ts": pos["entry_ts"], "exit_ts": ts,
            "net_pnl": net, "hold_minutes": hold_min, "exit_reason": reason,
            "entry_bucket": pos["entry_bucket"], "strategy": pos["strategy"],
            "grade": cfg.grade_map.get(sym, "WATCH")})
        strategy_loss[pos["strategy"]] += min(0.0, net)
        consec_losses = consec_losses + 1 if net < 0 else 0

    for ts in sorted(by_ts):
        date = ts.astimezone(_KST).date()
        mkey = f"{date.year}-{date.month:02d}"
        if cur_date is not None and date != cur_date:
            daily_equity.append((cur_date, cash))
        cur_date = date
        if mkey not in month_start_equity:
            month_start_equity[mkey] = cash
            month_peak_equity[mkey] = cash
        month_peak_equity[mkey] = max(month_peak_equity[mkey], cash)

        rows = by_ts[ts]
        for sym, bar, is_last in rows:
            if sym in positions:
                pos = positions[sym]
                fc = (cfg.forced_close_min is not None
                      and (bar.timestamp.astimezone(_KST).hour * 60
                           + bar.timestamp.astimezone(_KST).minute) >= cfg.forced_close_min)
                if bar.low <= pos["stop"]:
                    _close(sym, pos["stop"], ts.isoformat(), "STOP")
                elif bar.high >= pos["target"]:
                    _close(sym, pos["target"], ts.isoformat(), "TARGET")
                elif is_last or fc:
                    _close(sym, bar.close, ts.isoformat(), "FORCED" if fc and not is_last else "EOD")

        # 진입 게이트 (월간 DD / 쿨다운 / 연속손실 / 장세).
        entries_allowed = True
        if mkey in blocked_months:
            entries_allowed = False
        if cfg.monthly_dd_stop_pct and month_peak_equity[mkey] > 0:
            dd = (month_peak_equity[mkey] - cash) / month_peak_equity[mkey] * 100
            if dd >= cfg.monthly_dd_stop_pct:
                blocked_months.add(mkey)
                entries_allowed = False
        if cooldown_until_date is not None and date <= cooldown_until_date:
            entries_allowed = False
        if cfg.consecutive_loss_stop and consec_losses > cfg.consecutive_loss_stop:
            entries_allowed = False
        if cfg.regime_block_down_day and market_day_ret.get(date, 0.0) < 0:
            entries_allowed = False

        if not entries_allowed:
            continue

        # 후보 수집.
        mod = ts.astimezone(_KST).hour * 60 + ts.astimezone(_KST).minute
        if cfg.entry_cutoff_min is not None and mod >= cfg.entry_cutoff_min:
            continue
        cands = []
        for sym, bar, is_last in rows:
            if is_last or sym in positions:
                continue
            if cfg.universe is not None and sym not in cfg.universe:
                continue
            if cfg.worst_symbol_cooldown and symbol_cooldown.get(sym, 0) > 0:
                continue
            sig = signals.get((sym, ts.isoformat()))
            if sig is None or not _is_candidate(sig, cfg.agent_mode, cfg.min_quality_for_risk_filter):
                continue
            cands.append((sym, bar, sig))
        if not cands:
            continue
        if cfg.selection_mode != "earliest_first":
            cands.sort(key=lambda x: _rank_key(x[2], cfg, x[1], day_open.get((x[0], date))),
                       reverse=True)
        for sym, bar, sig in cands:
            if len(positions) >= cfg.max_positions:
                skipped_max += 1
                continue
            entry_px = bar.close * (1 + slip)
            notional = min(cfg.max_position_notional, cash)
            if cfg.agent_mode == "AGENT_POSITION_SIZER_ONLY":
                notional = min(notional, cfg.max_position_notional * (0.5 + 0.5 * sig["confidence"]))
            if notional < cfg.min_position_notional:
                skipped_cash += 1
                continue
            qty = int(math.floor(notional / entry_px))
            if qty <= 0:
                skipped_cash += 1
                continue
            gross = qty * entry_px
            buy_fee = gross * fee
            cost_basis = gross + buy_fee
            if cost_basis > cash:
                qty = int(math.floor(cash / (entry_px * (1 + fee))))
                if qty <= 0 or qty * entry_px < cfg.min_position_notional:
                    skipped_cash += 1
                    continue
                gross = qty * entry_px
                cost_basis = gross + gross * fee
            cost_paid += gross * fee
            slip_paid += qty * bar.close * slip
            buy_notional_total += gross
            cash -= cost_basis
            strat = (sig["selected"][0] if sig["selected"]
                     else (sig["single_buys"][0] if sig["single_buys"] else "UNKNOWN"))
            positions[sym] = {
                "entry_px": entry_px, "qty": qty, "entry_ts": ts.isoformat(),
                "cost_basis": cost_basis, "entry_bucket": sig["time_bucket"], "strategy": strat,
                "stop": entry_px * (1 - sig["stop_pct"] / 100.0),
                "target": entry_px * (1 + sig["target_pct"] / 100.0)}

    for sym in list(positions):
        b = None
        for _, bar, _il in by_ts[sorted(by_ts)[-1]]:
            if bar.symbol == sym:
                b = bar
        px = b.close if b else positions[sym]["entry_px"]
        _close(sym, px, sorted(by_ts)[-1].isoformat(), "EOD")
    if cur_date is not None:
        daily_equity.append((cur_date, cash))

    return _summarize_v2(cfg, label, trades, daily_equity, cost_paid, tax_paid, slip_paid,
                         buy_notional_total, skipped_max, skipped_cash)


def _summarize_v2(cfg, label, trades, daily_equity, cost_paid, tax_paid, slip_paid,
                  buy_notional_total, skipped_max, skipped_cash) -> SimV2Result:
    final_equity = daily_equity[-1][1] if daily_equity else cfg.initial_capital
    total_ret = (final_equity - cfg.initial_capital) / cfg.initial_capital * 100.0
    n = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] < 0]
    gp = sum(t["net_pnl"] for t in wins)
    gl = abs(sum(t["net_pnl"] for t in losses))
    pf = round(gp / gl, 4) if gl else None
    win_rate = round(len(wins) / n, 4) if n else None
    avg_win = (gp / len(wins)) if wins else None
    avg_loss = (-gl / len(losses)) if losses else None
    payoff = round(abs(avg_win / avg_loss), 4) if (avg_win and avg_loss) else None
    expectancy = round(sum(t["net_pnl"] for t in trades) / n, 2) if n else None

    eq = [e for _, e in daily_equity] or [cfg.initial_capital]
    peak = eq[0]
    mdd = 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    mdd_pct = round(mdd * 100, 2)

    trading_days = len(daily_equity)
    # 월별.
    monthly: dict[str, float] = {}
    m_s: dict[str, float] = {}
    m_e: dict[str, float] = {}
    prev = cfg.initial_capital
    for d, e in daily_equity:
        mk = f"{d.year}-{d.month:02d}"
        m_s.setdefault(mk, prev)
        m_e[mk] = e
        prev = e
    for mk in m_s:
        monthly[mk] = round((m_e[mk] - m_s[mk]) / m_s[mk] * 100, 4) if m_s[mk] else 0.0
    pos_months = sum(1 for v in monthly.values() if v > 0)
    wf = round(100 * pos_months / len(monthly), 1) if monthly else None
    # OOS: 마지막 1/3 구간 양(+)?
    oos_positive = False
    if len(daily_equity) >= 6:
        third = len(daily_equity) // 3
        seg = daily_equity[2 * third:]
        if seg and seg[0][1] > 0:
            oos_positive = (seg[-1][1] - seg[0][1]) >= 0

    day_pnl: dict[Any, float] = defaultdict(float)
    for t in trades:
        day_pnl[datetime.fromisoformat(t["exit_ts"]).astimezone(_KST).date()] += t["net_pnl"]
    worst_day = round(min(day_pnl.values()), 2) if day_pnl else None
    streak = worst_streak = 0
    for t in sorted(trades, key=lambda x: x["exit_ts"]):
        streak = streak + 1 if t["net_pnl"] < 0 else 0
        worst_streak = max(worst_streak, streak)
    avg_hold = round(statistics.mean([t["hold_minutes"] for t in trades]), 2) if trades else None

    def _bucket_stats(key: str) -> dict[str, Any]:
        agg: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            agg[t[key]].append(t["net_pnl"])
        out = {}
        for k, v in agg.items():
            w = [x for x in v if x > 0]
            out[k] = {"trades": len(v), "net_pnl": round(sum(v), 2),
                      "win_rate": round(len(w) / len(v), 4) if v else None,
                      "expectancy": round(sum(v) / len(v), 2) if v else None}
        return out

    return SimV2Result(
        config_label=label, total_return_pct=round(total_ret, 4),
        final_equity=round(final_equity, 2), profit_factor=pf, max_drawdown_pct=mdd_pct,
        expectancy=expectancy, win_rate=win_rate, payoff_ratio=payoff, trade_count=n,
        avg_trades_per_day=round(n / trading_days, 3) if trading_days else None,
        worst_day_pnl=worst_day, worst_consecutive_losses=worst_streak,
        avg_hold_minutes=avg_hold, monthly_returns=monthly, walk_forward_score=wf,
        oos_positive=oos_positive,
        turnover=round(buy_notional_total / cfg.initial_capital, 3),
        cost_paid_total=round(cost_paid, 2), tax_paid_total=round(tax_paid, 2),
        slippage_paid_total=round(slip_paid, 2), trading_days=trading_days,
        by_time_bucket=_bucket_stats("entry_bucket"),
        by_hold_bucket=_hold_stats(trades),
        by_strategy=_bucket_stats("strategy"), by_grade=_bucket_stats("grade"),
        skipped_max_positions=skipped_max, skipped_no_cash=skipped_cash,
        low_confidence=(n < 100))


def _hold_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    agg: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        agg[_hold_bucket(t["hold_minutes"])].append(t["net_pnl"])
    out = {}
    for k, v in agg.items():
        w = [x for x in v if x > 0]
        out[k] = {"trades": len(v), "net_pnl": round(sum(v), 2),
                  "win_rate": round(len(w) / len(v), 4) if v else None,
                  "expectancy": round(sum(v) / len(v), 2) if v else None}
    return out


def result_to_dict(r: SimV2Result) -> dict[str, Any]:
    return {
        "config_label": r.config_label, "total_return_pct": r.total_return_pct,
        "final_equity": r.final_equity, "profit_factor": r.profit_factor,
        "max_drawdown_pct": r.max_drawdown_pct, "expectancy": r.expectancy,
        "win_rate": r.win_rate, "payoff_ratio": r.payoff_ratio, "trade_count": r.trade_count,
        "avg_trades_per_day": r.avg_trades_per_day, "worst_day_pnl": r.worst_day_pnl,
        "worst_consecutive_losses": r.worst_consecutive_losses,
        "avg_hold_minutes": r.avg_hold_minutes, "monthly_returns": r.monthly_returns,
        "walk_forward_score": r.walk_forward_score, "oos_positive": r.oos_positive,
        "turnover": r.turnover, "cost_paid_total": r.cost_paid_total,
        "tax_paid_total": r.tax_paid_total, "slippage_paid_total": r.slippage_paid_total,
        "trading_days": r.trading_days, "by_time_bucket": r.by_time_bucket,
        "by_hold_bucket": r.by_hold_bucket, "by_strategy": r.by_strategy,
        "by_grade": r.by_grade, "skipped_max_positions": r.skipped_max_positions,
        "skipped_no_cash": r.skipped_no_cash, "low_confidence": r.low_confidence,
        "is_live_authorization": r.is_live_authorization, "broker_order_sent": r.broker_order_sent,
        "order_created": r.order_created, "do_not_auto_apply": r.do_not_auto_apply,
        "contains_secret": r.contains_secret, "no_profit_guarantee": r.no_profit_guarantee,
    }
