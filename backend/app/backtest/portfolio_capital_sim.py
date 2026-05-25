"""WF-6M-50SYMBOLS-01 — 1000만원 포트폴리오 자금곡선 시뮬레이션 (Paper/Backtest only).

여러 종목의 5분봉을 *공유 자본* 으로 동시에 운용하는 포트폴리오 시뮬레이터. Agent Council
의 final_action(BUY) 을 진입 신호로, exit_plan(stop_loss/take_profit) + 장마감 강제청산을
청산 규칙으로 사용한다. **실주문 0건 / 실체결 0건** — 순수 backtest.

현실 반영:
- 초기 자본 10,000,000 KRW (config).
- 동시 보유 최대 5종목, 종목당 1,000,000~2,000,000 KRW.
- 현금 부족 시 진입 금지, 동일 종목 중복 진입 금지.
- 위탁수수료 + 증권거래세(매도) + 슬리피지 반영 (비용 후 성과).
- **오버나이트 없음** (장중 단타) — 모든 포지션은 당일 장마감에 강제청산.

기존 인프라 재사용: `strategy_council_backtest._group_by_day` / `_build_input` /
`_time_phase_for` + `agent_council.run_agent_council`. broker / OrderExecutor /
route_order / KIS 주문 API 를 import·호출하지 않는다.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

_KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class SimConfig:
    initial_capital: float = 10_000_000.0
    max_positions: int = 5
    min_position_notional: float = 1_000_000.0
    max_position_notional: float = 2_000_000.0
    risk_profile: str = "BALANCED"
    opening_range_bars: int = 3
    recent_closes_window: int = 5
    fee_bps_per_side: float = 1.5      # 위탁수수료 (편도)
    sell_tax_bps: float = 18.0         # 증권거래세 (매도 시)
    slippage_bps: float = 5.0          # 체결 슬리피지 (편도)
    min_confidence: float = 0.0        # 추가 진입 게이트 (기본 council 게이트만)
    max_trades_per_day: int = 0        # 0 = 무제한 (과도 회전 제한용)
    default_stop_pct: float = 1.5
    default_target_pct: float = 3.0


@dataclass(frozen=True)
class SimTrade:
    symbol: str
    entry_ts: str
    exit_ts: str
    entry_price: float
    exit_price: float
    qty: int
    gross_pnl: float
    net_pnl: float
    return_pct: float
    hold_minutes: float
    exit_reason: str  # TARGET / STOP / EOD


@dataclass(frozen=True)
class PortfolioSimResult:
    initial_capital: float
    final_equity: float
    total_return_pct: float
    total_trades: int
    trading_days: int
    win_rate: float | None
    avg_win: float | None
    avg_loss: float | None
    payoff_ratio: float | None
    expectancy: float | None
    profit_factor: float | None
    max_drawdown_pct: float | None
    max_drawdown_krw: float | None
    daily_avg_trades: float | None
    daily_avg_return_pct: float | None
    weekly_avg_return_pct: float | None
    monthly_avg_return_pct: float | None
    worst_day_pnl: float | None
    worst_day_return_pct: float | None
    worst_consecutive_losses: int
    avg_hold_minutes: float | None
    skipped_no_cash: int
    skipped_max_positions: int
    equity_curve: tuple[tuple[str, float], ...]
    monthly_returns: dict[str, float]
    per_symbol: tuple[dict[str, Any], ...]
    # 안전 불변값.
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    do_not_auto_apply: bool = True
    contains_secret: bool = False
    no_profit_guarantee: bool = True
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.is_live_authorization or self.broker_order_sent or self.order_created:
            raise ValueError("unsafe invariant True on PortfolioSimResult")
        if not self.do_not_auto_apply or not self.no_profit_guarantee:
            raise ValueError("do_not_auto_apply / no_profit_guarantee must be True")
        if self.contains_secret:
            raise ValueError("contains_secret must be False")


def _precompute_buy_signals(
    bars: Sequence[Any], cfg: SimConfig,
) -> dict[tuple[str, str], dict[str, float]]:
    """(symbol, ts_iso) → {'confidence','stop_pct','target_pct'} (BUY 신호만).

    council 판단은 포트폴리오 상태와 무관 — 1회만 계산해 캐시.
    """
    from app.agents.agent_council import run_agent_council
    from app.backtest.strategy_council_backtest import _build_input, _group_by_day

    signals: dict[tuple[str, str], dict[str, float]] = {}
    days = _group_by_day(bars)
    prev_day_close: dict[str, float | None] = {}
    warmup = cfg.opening_range_bars + cfg.recent_closes_window
    for day_bars in days:
        sym = day_bars[0].symbol
        prev_close = prev_day_close.get(sym)
        day_end = len(day_bars) - 1
        for i in range(len(day_bars)):
            if i >= day_end or i < warmup:
                continue  # 마지막 bar / warmup 구간은 신규 진입 안 함.
            mi = _build_input(
                day_bars, i, opening_range_bars=cfg.opening_range_bars,
                recent_closes_window=cfg.recent_closes_window, prev_close=prev_close)
            decision = run_agent_council(mi, risk_profile=cfg.risk_profile, held_position=None)
            if decision.final_action.value != "BUY":
                continue
            if float(decision.confidence) < cfg.min_confidence:
                continue
            ep = decision.exit_plan or {}
            stop_pct = float(ep.get("stop_loss_pct") or cfg.default_stop_pct)
            target_pct = float(ep.get("take_profit_pct") or cfg.default_target_pct)
            signals[(sym, day_bars[i].timestamp.isoformat())] = {
                "confidence": float(decision.confidence),
                "stop_pct": stop_pct, "target_pct": target_pct}
        prev_day_close[sym] = day_bars[day_end].close
    return signals


def run_portfolio_capital_sim(
    bars: Sequence[Any], cfg: SimConfig | None = None,
    *, signals: dict[tuple[str, str], dict[str, float]] | None = None,
) -> PortfolioSimResult:
    """공유 자본 포트폴리오 시뮬레이션. bars 는 여러 종목의 OHLCVBar 혼합.

    `signals` 를 주입하면 council 재계산을 생략(테스트/재현용). None 이면 council 로 계산.
    """
    cfg = cfg or SimConfig()
    bars = list(bars)
    if signals is None:
        signals = _precompute_buy_signals(bars, cfg)

    # 글로벌 타임라인: (ts, symbol) 정렬. 같은 ts 면 symbol 순 — 결정론적.
    from app.backtest.strategy_council_backtest import _group_by_day
    days = _group_by_day(bars)
    # 종목·날짜별 마지막 bar 인덱스 + prev_close 매핑.
    events: list[tuple[datetime, str, Any, bool]] = []  # (ts, symbol, bar, is_last_of_day)
    for day_bars in days:
        last_i = len(day_bars) - 1
        for i, b in enumerate(day_bars):
            events.append((b.timestamp, b.symbol, b, i == last_i))
    events.sort(key=lambda e: (e[0], e[1]))

    fee = cfg.fee_bps_per_side / 10_000.0
    tax = cfg.sell_tax_bps / 10_000.0
    slip = cfg.slippage_bps / 10_000.0

    cash = cfg.initial_capital
    positions: dict[str, dict[str, Any]] = {}
    last_price: dict[str, float] = {}
    trades: list[SimTrade] = []
    skipped_no_cash = 0
    skipped_max = 0
    trades_today: dict[Any, int] = defaultdict(int)

    # 일별 자금곡선 (장마감 강제청산이므로 date 말 equity = cash).
    daily_equity: list[tuple[Any, float]] = []
    cur_date: Any = None

    def _close(sym: str, price: float, ts: str, reason: str) -> None:
        nonlocal cash
        pos = positions.pop(sym)
        exit_px = price * (1 - slip)
        proceeds = pos["qty"] * exit_px
        sell_cost = proceeds * (fee + tax)
        net_proceeds = proceeds - sell_cost
        cash += net_proceeds
        gross = (exit_px - pos["entry_px"]) * pos["qty"]
        net = net_proceeds - pos["cost_basis"]
        ret = (net / pos["cost_basis"] * 100.0) if pos["cost_basis"] else 0.0
        hold_min = (datetime.fromisoformat(ts) - datetime.fromisoformat(pos["entry_ts"])
                    ).total_seconds() / 60.0
        trades.append(SimTrade(
            symbol=sym, entry_ts=pos["entry_ts"], exit_ts=ts, entry_price=pos["entry_px"],
            exit_price=exit_px, qty=pos["qty"], gross_pnl=gross, net_pnl=net,
            return_pct=ret, hold_minutes=hold_min, exit_reason=reason))

    for ts, sym, bar, is_last in events:
        date = ts.astimezone(_KST).date()
        if cur_date is not None and date != cur_date:
            daily_equity.append((cur_date, cash))  # 전일 마감 (모든 포지션 청산됨)
        cur_date = date
        last_price[sym] = bar.close

        # 1) 보유 중이면 청산 검사 (비관적: stop 우선).
        if sym in positions:
            pos = positions[sym]
            if bar.low <= pos["stop"]:
                _close(sym, pos["stop"], ts.isoformat(), "STOP")
            elif bar.high >= pos["target"]:
                _close(sym, pos["target"], ts.isoformat(), "TARGET")
            elif is_last:
                _close(sym, bar.close, ts.isoformat(), "EOD")
            continue  # 같은 bar 에서 청산 후 즉시 재진입 안 함.

        # 2) 진입 검사.
        if is_last:
            continue
        sig = signals.get((sym, ts.isoformat()))
        if sig is None:
            continue
        if cfg.max_trades_per_day and trades_today[date] >= cfg.max_trades_per_day:
            continue
        if len(positions) >= cfg.max_positions:
            skipped_max += 1
            continue
        entry_px = bar.close * (1 + slip)
        notional = min(cfg.max_position_notional, cash)
        if notional < cfg.min_position_notional:
            skipped_no_cash += 1
            continue
        qty = int(math.floor(notional / entry_px))
        if qty <= 0:
            skipped_no_cash += 1
            continue
        gross_cost = qty * entry_px
        buy_fee = gross_cost * fee
        cost_basis = gross_cost + buy_fee
        if cost_basis > cash:
            qty = int(math.floor(cash / (entry_px * (1 + fee))))
            if qty <= 0 or qty * entry_px < cfg.min_position_notional:
                skipped_no_cash += 1
                continue
            gross_cost = qty * entry_px
            cost_basis = gross_cost + gross_cost * fee
        cash -= cost_basis
        trades_today[date] += 1
        positions[sym] = {
            "entry_px": entry_px, "qty": qty, "entry_ts": ts.isoformat(),
            "cost_basis": cost_basis,
            "stop": entry_px * (1 - sig["stop_pct"] / 100.0),
            "target": entry_px * (1 + sig["target_pct"] / 100.0)}

    # 타임라인 종료 — 남은 포지션은 마지막 가격으로 청산 (안전).
    for sym in list(positions):
        px = last_price.get(sym, positions[sym]["entry_px"])
        _close(sym, px, events[-1][0].isoformat() if events else "", "EOD")
    if cur_date is not None:
        daily_equity.append((cur_date, cash))

    return _summarize(cfg, trades, daily_equity, skipped_no_cash, skipped_max)


def _summarize(
    cfg: SimConfig, trades: list[SimTrade], daily_equity: list[tuple[Any, float]],
    skipped_no_cash: int, skipped_max: int,
) -> PortfolioSimResult:
    final_equity = daily_equity[-1][1] if daily_equity else cfg.initial_capital
    total_ret = (final_equity - cfg.initial_capital) / cfg.initial_capital * 100.0
    n = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    win_rate = round(len(wins) / n, 4) if n else None
    avg_win = round(sum(t.net_pnl for t in wins) / len(wins), 2) if wins else None
    avg_loss = round(sum(t.net_pnl for t in losses) / len(losses), 2) if losses else None
    payoff = round(abs(avg_win / avg_loss), 4) if (avg_win and avg_loss) else None
    expectancy = round(sum(t.net_pnl for t in trades) / n, 2) if n else None
    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    pf = round(gross_profit / gross_loss, 4) if gross_loss else None

    # MDD (일별 equity 기준).
    eq = [e for _, e in daily_equity] or [cfg.initial_capital]
    peak = eq[0]
    mdd_pct = 0.0
    mdd_krw = 0.0
    for v in eq:
        peak = max(peak, v)
        dd = peak - v
        if peak > 0 and dd / peak > mdd_pct:
            mdd_pct = dd / peak
            mdd_krw = dd
    mdd_pct = round(mdd_pct * 100.0, 2)
    mdd_krw = round(mdd_krw, 2)

    # 일/주/월 수익률.
    daily_rets: list[float] = []
    prev = cfg.initial_capital
    for _, e in daily_equity:
        if prev > 0:
            daily_rets.append((e - prev) / prev * 100.0)
        prev = e
    trading_days = len(daily_equity)
    daily_avg_trades = round(n / trading_days, 3) if trading_days else None
    daily_avg_ret = round(statistics.mean(daily_rets), 4) if daily_rets else None

    # 월별 compounded.
    monthly: dict[str, float] = {}
    weekly: dict[str, float] = {}
    prev = cfg.initial_capital
    m_start: dict[str, float] = {}
    w_start: dict[str, float] = {}
    m_end: dict[str, float] = {}
    w_end: dict[str, float] = {}
    for d, e in daily_equity:
        mk = f"{d.year}-{d.month:02d}"
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        if mk not in m_start:
            m_start[mk] = prev
        m_end[mk] = e
        if wk not in w_start:
            w_start[wk] = prev
        w_end[wk] = e
        prev = e
    for mk in m_start:
        monthly[mk] = round((m_end[mk] - m_start[mk]) / m_start[mk] * 100.0, 4) if m_start[mk] else 0.0
    for wk in w_start:
        weekly[wk] = round((w_end[wk] - w_start[wk]) / w_start[wk] * 100.0, 4) if w_start[wk] else 0.0
    monthly_avg = round(statistics.mean(monthly.values()), 4) if monthly else None
    weekly_avg = round(statistics.mean(weekly.values()), 4) if weekly else None

    # 일별 PnL (worst day).
    day_pnl: dict[Any, float] = defaultdict(float)
    for t in trades:
        day_pnl[datetime.fromisoformat(t.exit_ts).astimezone(_KST).date()] += t.net_pnl
    worst_day_pnl = round(min(day_pnl.values()), 2) if day_pnl else None
    # worst day return % (vs 직전 equity) — daily_rets 최소.
    worst_day_ret = round(min(daily_rets), 4) if daily_rets else None

    # worst consecutive losses (시간순).
    streak = worst_streak = 0
    for t in sorted(trades, key=lambda x: x.exit_ts):
        if t.net_pnl < 0:
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0

    avg_hold = round(statistics.mean([t.hold_minutes for t in trades]), 2) if trades else None

    # 종목별 집계 (grading 용).
    by_sym: dict[str, list[SimTrade]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
    per_symbol: list[dict[str, Any]] = []
    for sym, ts_list in by_sym.items():
        w = [t for t in ts_list if t.net_pnl > 0]
        loss = [t for t in ts_list if t.net_pnl < 0]
        gp = sum(t.net_pnl for t in w)
        gl = abs(sum(t.net_pnl for t in loss))
        net = sum(t.net_pnl for t in ts_list)
        per_symbol.append({
            "symbol": sym, "trades": len(ts_list), "net_pnl": round(net, 2),
            "win_rate": round(len(w) / len(ts_list), 4) if ts_list else None,
            "profit_factor": round(gp / gl, 4) if gl else None,
            "expectancy": round(net / len(ts_list), 2) if ts_list else None,
            "avg_hold_minutes": round(statistics.mean([t.hold_minutes for t in ts_list]), 1),
        })
    per_symbol.sort(key=lambda p: p["net_pnl"], reverse=True)

    return PortfolioSimResult(
        initial_capital=cfg.initial_capital, final_equity=round(final_equity, 2),
        total_return_pct=round(total_ret, 4), total_trades=n, trading_days=trading_days,
        win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss, payoff_ratio=payoff,
        expectancy=expectancy, profit_factor=pf, max_drawdown_pct=mdd_pct,
        max_drawdown_krw=mdd_krw, daily_avg_trades=daily_avg_trades,
        daily_avg_return_pct=daily_avg_ret, weekly_avg_return_pct=weekly_avg,
        monthly_avg_return_pct=monthly_avg, worst_day_pnl=worst_day_pnl,
        worst_day_return_pct=worst_day_ret, worst_consecutive_losses=worst_streak,
        avg_hold_minutes=avg_hold, skipped_no_cash=skipped_no_cash,
        skipped_max_positions=skipped_max,
        equity_curve=tuple((str(d), round(e, 2)) for d, e in daily_equity),
        monthly_returns=monthly, per_symbol=tuple(per_symbol))


def to_dict(r: PortfolioSimResult) -> dict[str, Any]:
    return {
        "initial_capital": r.initial_capital, "final_equity": r.final_equity,
        "total_return_pct": r.total_return_pct, "total_trades": r.total_trades,
        "trading_days": r.trading_days, "win_rate": r.win_rate, "avg_win": r.avg_win,
        "avg_loss": r.avg_loss, "payoff_ratio": r.payoff_ratio, "expectancy": r.expectancy,
        "profit_factor": r.profit_factor, "max_drawdown_pct": r.max_drawdown_pct,
        "max_drawdown_krw": r.max_drawdown_krw, "daily_avg_trades": r.daily_avg_trades,
        "daily_avg_return_pct": r.daily_avg_return_pct,
        "weekly_avg_return_pct": r.weekly_avg_return_pct,
        "monthly_avg_return_pct": r.monthly_avg_return_pct, "worst_day_pnl": r.worst_day_pnl,
        "worst_day_return_pct": r.worst_day_return_pct,
        "worst_consecutive_losses": r.worst_consecutive_losses,
        "avg_hold_minutes": r.avg_hold_minutes, "skipped_no_cash": r.skipped_no_cash,
        "skipped_max_positions": r.skipped_max_positions,
        "equity_curve": [list(x) for x in r.equity_curve], "monthly_returns": r.monthly_returns,
        "per_symbol": list(r.per_symbol),
        "is_live_authorization": r.is_live_authorization, "broker_order_sent": r.broker_order_sent,
        "order_created": r.order_created, "do_not_auto_apply": r.do_not_auto_apply,
        "contains_secret": r.contains_secret, "no_profit_guarantee": r.no_profit_guarantee,
    }
