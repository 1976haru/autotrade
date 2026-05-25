"""KIS-INTRADAY-50-6M-ROOT-CAUSE-REBUILD-01 — per-bar 신호 추출 (council + 4전략, 캐시).

원인분해 + 재설계 실험이 공유하는 *무거운* 단계: 50종목 × 6개월 5분봉의 각 bar 에 대해
Agent Council 결정 + 4전략(ORB/MOMENTUM/GAP/VWAP) vote + EOD 까지의 forward return 을
한 번 계산해 캐시한다(gitignored). 이후 모든 실험은 캐시를 재사용한다.

본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import·호출하지 않는다.
read-only 분석 전용.
"""

from __future__ import annotations

import hashlib
import pickle
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

_KST = timezone(timedelta(hours=9))
_CACHE = Path("reports/strategy_validation/_wf6m_signal_cache.pkl")

_BUCKETS = (
    ("09:00-09:30", 9 * 60, 9 * 60 + 30),
    ("09:30-10:30", 9 * 60 + 30, 10 * 60 + 30),
    ("10:30-13:30", 10 * 60 + 30, 13 * 60 + 30),
    ("13:30-14:50", 13 * 60 + 30, 14 * 60 + 50),
    ("14:50-EOD", 14 * 60 + 50, 24 * 60),
)


def time_bucket(minute_of_day: int) -> str:
    for name, lo, hi in _BUCKETS:
        if lo <= minute_of_day < hi:
            return name
    return "09:00-09:30" if minute_of_day < 9 * 60 else "14:50-EOD"


@dataclass(frozen=True)
class SignalMap:
    """(symbol, ts_iso) → 신호 dict. + 부가 통계."""
    signals: dict[tuple[str, str], dict[str, Any]]
    strategy_fwd_pf: dict[str, float | None]   # 단일전략 fwd-return PF (랭킹용)
    bar_count: int
    symbol_count: int
    contains_secret: bool = False
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.contains_secret:
            raise ValueError("contains_secret must be False")


def _signature(bars: Sequence[Any]) -> str:
    syms = sorted({getattr(b, "symbol", "") for b in bars})
    h = hashlib.sha256()
    h.update(("|".join(syms)).encode())
    h.update(f"|{len(bars)}".encode())
    if bars:
        h.update(f"|{bars[0].timestamp.isoformat()}|{bars[-1].timestamp.isoformat()}".encode())
    return h.hexdigest()[:16]


def extract_signal_map(
    bars: Sequence[Any], *, risk_profile: str = "BALANCED",
    opening_range_bars: int = 3, recent_closes_window: int = 5,
    use_cache: bool = True,
) -> SignalMap:
    """각 bar 의 council + 4전략 vote + fwd_eod return 을 계산(또는 캐시 로드)."""
    sig = _signature(bars)
    if use_cache and _CACHE.exists():
        try:
            cached = pickle.loads(_CACHE.read_bytes())
            if cached.get("sig") == sig:
                return SignalMap(**cached["data"])
        except Exception:  # noqa: BLE001
            pass

    from app.agents.agent_council import (
        CouncilAction,
        evaluate_gap,
        evaluate_momentum,
        evaluate_orb,
        evaluate_vwap,
        run_agent_council,
    )
    from app.backtest.strategy_council_backtest import _build_input, _group_by_day

    evaluators = (("ORB", evaluate_orb), ("MOMENTUM", evaluate_momentum),
                  ("GAP", evaluate_gap), ("VWAP", evaluate_vwap))
    warmup = opening_range_bars + recent_closes_window
    signals: dict[tuple[str, str], dict[str, Any]] = {}
    # 단일전략 fwd-return 누적 (PF 계산).
    strat_gain: dict[str, float] = {n: 0.0 for n, _ in evaluators}
    strat_loss: dict[str, float] = {n: 0.0 for n, _ in evaluators}

    days = _group_by_day(bars)
    prev_close: dict[str, float | None] = {}
    for day_bars in days:
        sym = day_bars[0].symbol
        pc = prev_close.get(sym)
        day_end = len(day_bars) - 1
        eod_close = day_bars[day_end].close
        for i in range(len(day_bars)):
            if i >= day_end or i < warmup:
                continue
            b = day_bars[i]
            mi = _build_input(day_bars, i, opening_range_bars=opening_range_bars,
                              recent_closes_window=recent_closes_window, prev_close=pc)
            fwd = (eod_close - b.close) / b.close if b.close else 0.0
            single_buys = []
            for name, ev in evaluators:
                v = ev(mi)
                if v.signal == CouncilAction.BUY:
                    single_buys.append(name)
                    if fwd >= 0:
                        strat_gain[name] += fwd
                    else:
                        strat_loss[name] += -fwd
            decision = run_agent_council(mi, risk_profile=risk_profile, held_position=None)
            action = decision.final_action.value
            if action != "BUY" and not single_buys:
                continue
            ep = decision.exit_plan or {}
            kst = b.timestamp.astimezone(_KST)
            mod = kst.hour * 60 + kst.minute
            signals[(sym, b.timestamp.isoformat())] = {
                "council_action": action,
                "confidence": float(decision.confidence),
                "quality_score": float(decision.quality_score),
                "stop_pct": float(ep.get("stop_loss_pct") or 1.5),
                "target_pct": float(ep.get("take_profit_pct") or 3.0),
                "selected": tuple(decision.selected_strategies),
                "single_buys": tuple(single_buys),
                "minute_of_day": mod,
                "time_bucket": time_bucket(mod),
                "fwd_eod_return": fwd,
            }
        prev_close[sym] = eod_close

    strat_pf: dict[str, float | None] = {}
    for n, _ in evaluators:
        gl = strat_loss[n]
        strat_pf[n] = round(strat_gain[n] / gl, 4) if gl else None

    sm = SignalMap(signals=signals, strategy_fwd_pf=strat_pf,
                   bar_count=len(bars), symbol_count=len({b.symbol for b in bars}))
    if use_cache:
        try:
            _CACHE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE.write_bytes(pickle.dumps({"sig": sig, "data": {
                "signals": sm.signals, "strategy_fwd_pf": sm.strategy_fwd_pf,
                "bar_count": sm.bar_count, "symbol_count": sm.symbol_count}}))
        except OSError:
            pass
    return sm
