"""MULTI-TF-SHADOW-V1 — 5분봉 entry + 60분봉 confirm(Agent Council) 콤보 관측.

results/multi_timeframe/design.md 의 최우수 콤보(5m entry AGENT_COUNCIL BUY + 60m
confirm AGENT_COUNCIL BUY)를 실시간으로 read-only 관측한다. 목적:
  1) 이 콤보가 5분봉 빈도로 실제 하루 몇 번 신호를 내는지
  2) 신호 확정 시점(P1) → 60분 확인평가 완료 직후 재조회 시점(P2) 사이의
     실제 슬리피지(results/multi_timeframe/adverse_selection_measurement.md 의
     "판단가→체결가" 슬리피지를 이 콤보 자체의 신호빈도로 재측정)
  3) 5분 빈도 스캔이 봇의 30분 빈도 스캔보다 레이트리밋(EGW00201) 경합이
     얼마나 심한지

★절대 원칙 준수:
  - broker.place_order / route_order / OrderExecutor / RiskManager 호출 0건.
    `fetch_realtime_quote`(read-only 시세), `_fetch_full_day_minutes`(read-only
    분봉), `run_agent_council`(순수 함수, 부작용 0)만 사용.
  - 봇의 실제 신호 경로(kis_paper/driver_bridge.py, market_data/kis_realtime.py
    의 라이브 BAR_INTERVAL_MINUTES 분기)는 1줄도 수정하지 않는다 — 이 모듈은
    *별도의 읽기 전용 파라미터화 재구현*이다(전역 상수 BAR_INTERVAL_MINUTES를
    건드리면 봇의 동시 실행 중인 신호 계산과 경합할 위험이 있어 의도적으로
    분리했다).
  - 모든 기록은 is_order_signal=False / is_live_authorization=False 고정.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.agents.agent_council import (
    CouncilAction,
    StrategyMarketInput,
    run_agent_council,
)
from app.core.runtime_config import effective_active_profile
from app.market_data.kis_realtime import (
    KIS_MARKET_DATA_UNAVAILABLE,
    _fetch_full_day_minutes,
    _fetch_full_day_minutes_cached,
    fetch_realtime_quote,
)
from app.scheduler.market_clock import is_market_open, to_kst

_log = logging.getLogger("autotrade.shadow.multi_tf")

_SESSION_MIN = 390          # 09:00~15:30
_RECENT_BARS = 30           # kis_realtime.py 와 동일 룩백(봉 수)
_ORB_START, _ORB_END = "090000", "093000"

ENTRY_N = 5     # entry TF(분) — results/multi_timeframe/design.md 콤보 그대로
CONFIRM_N = 60  # confirm TF(분)

# 종목별 과거일 원시 1분봉 캐시(N무관, 하루 1회만 채움) — kis_realtime.py 의
# _prior_bars_cache 와는 별개 dict(전역 상수 공유 0, 경합 위험 0).
_prior_minute_cache: dict[tuple[str, str], list[dict]] = {}


def _bars_per_day(n: int) -> int:
    return max(1, _SESSION_MIN // n)


def _prior_days_needed(n: int) -> int:
    return max(1, -(-_RECENT_BARS // _bars_per_day(n)) + 1)


def _resample_to_n(minute_bars: list[dict], n: int) -> list[dict]:
    """1분봉 → n분봉. kis_realtime._resample_bars 와 동일 로직의 파라미터화 사본
    (전역 BAR_INTERVAL_MINUTES 를 읽는 원본을 건드리지 않기 위해 별도 구현)."""
    buckets: dict[tuple[str, int], dict] = {}
    order: list[tuple[str, int]] = []
    for b in minute_bars:
        t = b["t"] or "000000"
        slot = (int(t[:2]) * 60 + int(t[2:4])) // n
        key = (b["d"], slot)
        if key not in buckets:
            hhmm = f"{(slot * n) // 60:02d}{(slot * n) % 60:02d}00"
            buckets[key] = {"d": b["d"], "t": hhmm,
                            "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}
            order.append(key)
        else:
            agg = buckets[key]
            agg["h"] = max(agg["h"], b["h"]); agg["l"] = min(agg["l"], b["l"])
            agg["c"] = b["c"]; agg["v"] += b["v"]
    return [buckets[k] for k in order]


async def _prior_days_raw_minutes(client, symbol: str, now: datetime, n: int) -> list[dict]:
    """과거 거래일의 *원시 1분봉*(N무관) — 심볼당 하루 1회만 채움(자체 캐시)."""
    need = _prior_days_needed(n)
    asof = to_kst(now)
    out: list[dict] = []
    collected = 0
    for back in range(1, need * 3 + 1):
        if collected >= need:
            break
        from datetime import timedelta
        d = (asof - timedelta(days=back)).strftime("%Y%m%d")
        ck = (symbol, d)
        if ck in _prior_minute_cache:
            mins = _prior_minute_cache[ck]
        else:
            try:
                mins = await _fetch_full_day_minutes(client, symbol, d)
            except Exception as exc:  # noqa: BLE001 — 하루 실패는 skip, 다른 날로.
                _log.info("[shadow-mtf] prior %s %s unavailable: %s", symbol, d, type(exc).__name__)
                continue
            if mins:
                _prior_minute_cache[ck] = mins
        if not mins:
            continue
        out.extend(mins)
        collected += 1
    out.sort(key=lambda b: (b["d"], b["t"]))
    return out


@dataclass(frozen=True)
class ComboEvalResult:
    symbol:          str
    entry_signal:    str    # BUY/SELL/HOLD (5m Council)
    confirm_signal:  str    # BUY/SELL/HOLD (60m Council, as-of)
    entry_confidence: float
    p1_price:        float | None
    prev_close:      float | None
    quote_ok:        bool
    rate_limited:    bool
    fetch_error:     bool


async def evaluate_combo(client, symbol: str, now: datetime) -> ComboEvalResult:
    """5m entry + 60m confirm Council 신호를 한 번 평가(주문 0, read-only 시세만).

    `fetch_realtime_quote` 자체는 절대 raise 하지 않는 계약(실패 시 status 로
    반환)이라 그 앞은 try/except 로 감싸지 않는다 — 대신 status 로 구분한다.
    단, 이 경로는 실패 원인(EGW00201 등)이 status_message_ko 의 일반 문구로
    뭉개져 rate_limited 판정은 불가(=바 조회 경로만 EGW00201 세분 가능 — 정직히
    한계로 남긴다, results/multi_timeframe 문서 참고).
    """
    quote = await fetch_realtime_quote(symbol, client=client, now=now, market_is_open=True)
    if not quote.ok:
        fetch_error = quote.status == KIS_MARKET_DATA_UNAVAILABLE
        return ComboEvalResult(symbol, "HOLD", "HOLD", 0.0, None, quote.prev_close, False, False, fetch_error)

    date = to_kst(now).strftime("%Y%m%d")
    try:
        today_min = await _fetch_full_day_minutes_cached(client, symbol, date)
    except Exception as exc:  # noqa: BLE001
        fetch_error = True
        rate_limited = "EGW00201" in str(exc)
        return ComboEvalResult(symbol, "HOLD", "HOLD", 0.0, quote.price, quote.prev_close,
                                True, rate_limited, fetch_error)
    if not today_min:
        return ComboEvalResult(symbol, "HOLD", "HOLD", 0.0, quote.price, quote.prev_close,
                                True, False, False)

    orb_min = [b for b in today_min if _ORB_START <= b["t"] < _ORB_END]
    orh = max((b["h"] for b in orb_min), default=quote.high)
    orl = min((b["l"] for b in orb_min), default=quote.low)

    profile = effective_active_profile()

    today_5 = _resample_to_n(today_min, ENTRY_N)
    today_60 = _resample_to_n(today_min, CONFIRM_N)
    prior_5 = await _prior_days_raw_minutes(client, symbol, now, ENTRY_N)
    prior_60_raw = await _prior_days_raw_minutes(client, symbol, now, CONFIRM_N)
    prior_60 = _resample_to_n(prior_60_raw, CONFIRM_N)
    prior_5_r = _resample_to_n(prior_5, ENTRY_N)

    def _build(bars_today: list[dict], bars_prior: list[dict]) -> StrategyMarketInput | None:
        bars = bars_prior + bars_today
        if len(bars) < _RECENT_BARS:
            return None
        recent_closes = tuple(b["c"] for b in bars[-_RECENT_BARS:])
        vwap = quote.vwap if quote.vwap else (sum(recent_closes) / len(recent_closes))
        return StrategyMarketInput(
            symbol=symbol, current_price=quote.price, prev_close=quote.prev_close,
            open_price=quote.open_price, vwap=vwap,
            opening_range_high=orh, opening_range_low=orl,
            recent_closes=recent_closes, current_volume=quote.volume,
            avg_volume=quote.volume,
        )

    mi5 = _build(today_5, prior_5_r)
    mi60 = _build(today_60, prior_60)
    if mi5 is None or mi60 is None:
        return ComboEvalResult(symbol, "HOLD", "HOLD", 0.0, quote.price, quote.prev_close,
                                True, False, False)

    dec5 = run_agent_council(mi5, risk_profile=profile, held_position=False)
    dec60 = run_agent_council(mi60, risk_profile=profile, held_position=False)

    return ComboEvalResult(
        symbol=symbol,
        entry_signal=dec5.final_action.value,
        confirm_signal=dec60.final_action.value,
        entry_confidence=float(dec5.confidence),
        p1_price=quote.price,
        prev_close=quote.prev_close,
        quote_ok=True,
        rate_limited=False,
        fetch_error=False,
    )


@dataclass
class ShadowTickSummary:
    symbols_scanned:     int
    combo_signals_found: int
    fetch_errors:        int
    rate_limited_count:  int
    tick_duration_seconds: float
    signals: list[dict]   # 각 신호 detail(테이블 insert 용 dict)


async def run_shadow_tick(*, client, symbols: list[str], now: datetime | None = None) -> ShadowTickSummary:
    """1회 tick — symbols 전부에 대해 콤보 평가, BUY+BUY 매치만 slippage(P2) 추가 측정.

    market_is_open=False(장외)면 스캔 자체를 skip(빈 summary) — 장외 시세는
    STALE 이라 의사결정용이 아니므로 신호 0건 기록(불필요한 호출도 0).
    """
    now = now or datetime.now(timezone.utc)
    t0 = _time.perf_counter()
    if not is_market_open(now):
        return ShadowTickSummary(0, 0, 0, 0, 0.0, [])

    signals: list[dict] = []
    fetch_errors = 0
    rate_limited_count = 0
    for symbol in symbols:
        try:
            res = await evaluate_combo(client, symbol, now)
        except Exception as exc:  # noqa: BLE001 — 심볼 하나 실패가 tick 전체를 막지 않음.
            fetch_errors += 1
            rate_limited_count += 1 if "EGW00201" in str(exc) else 0
            _log.warning("[shadow-mtf] %s 평가 실패: %s", symbol, type(exc).__name__)
            continue
        if res.fetch_error:
            fetch_errors += 1
        if res.rate_limited:
            rate_limited_count += 1
        if res.entry_signal == CouncilAction.BUY.value and res.confirm_signal == CouncilAction.BUY.value:
            p1_ts = now
            # P2: 60분 확인평가까지 실제로 소요된 시간 뒤 재조회 — 인위적 sleep 0,
            # 지금까지의 순차 await(분봉 조회 등)가 이미 만든 자연 경과시간을 그대로 씀.
            p2_ts = datetime.now(timezone.utc)
            try:
                quote2 = await fetch_realtime_quote(symbol, client=client, now=p2_ts, market_is_open=True)
                p2_price = quote2.price if quote2.ok else None
            except Exception as exc:  # noqa: BLE001
                p2_price = None
                fetch_errors += 1
                if "EGW00201" in str(exc):
                    rate_limited_count += 1
            elapsed = (p2_ts - p1_ts).total_seconds()
            slippage_bps = None
            if p2_price and res.p1_price:
                slippage_bps = (p2_price - res.p1_price) / res.p1_price * 10000.0
            signals.append({
                "symbol": symbol, "entry_tf": f"{ENTRY_N}m", "confirm_tf": f"{CONFIRM_N}m",
                "entry_signal": res.entry_signal, "confirm_signal": res.confirm_signal,
                "p1_price": res.p1_price, "p1_timestamp": p1_ts,
                "p2_price": p2_price, "p2_timestamp": p2_ts,
                "elapsed_seconds": elapsed, "slippage_bps": slippage_bps,
                "prev_close": res.prev_close, "confidence": res.entry_confidence,
                "is_order_signal": False, "is_live_authorization": False,
            })
    duration = _time.perf_counter() - t0
    return ShadowTickSummary(
        symbols_scanned=len(symbols), combo_signals_found=len(signals),
        fetch_errors=fetch_errors, rate_limited_count=rate_limited_count,
        tick_duration_seconds=round(duration, 3), signals=signals,
    )


def persist_tick(db, summary: ShadowTickSummary) -> None:
    """ShadowTickSummary → DB row(들). broker/route_order 0건, 순수 기록."""
    from app.db.models import MultiTfShadowSignal, MultiTfShadowTick

    db.add(MultiTfShadowTick(
        symbols_scanned=summary.symbols_scanned,
        combo_signals_found=summary.combo_signals_found,
        fetch_errors=summary.fetch_errors,
        rate_limited_count=summary.rate_limited_count,
        tick_duration_seconds=summary.tick_duration_seconds,
    ))
    for s in summary.signals:
        db.add(MultiTfShadowSignal(**s))
    db.commit()


class MultiTfShadowRunner:
    """Background task — 고정 주기로 run_shadow_tick + persist_tick 반복.

    app/execution/fill_poller.py::FillPoller 와 동일 구조(broker_factory /
    session_factory 를 매 tick 마다 호출해 최신 broker·독립 DB 세션 사용).
    broker.place_order 호출 0건 — broker.client(read-only KIS client)만 사용.
    """

    def __init__(
        self,
        broker_factory,
        session_factory,
        symbols: list[str],
        interval: int = 300,
        now_factory=None,
    ):
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.broker_factory = broker_factory
        self.session_factory = session_factory
        self.symbols = list(symbols)
        self.interval = interval
        self.now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self._task = None
        self._stop_event = None

    def start(self) -> bool:
        if not self.symbols:
            return False
        if self._task is not None and not self._task.done():
            return False
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())
        return True

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                broker = self.broker_factory()
                client = getattr(broker, "client", None)
                if client is not None:
                    summary = await run_shadow_tick(
                        client=client, symbols=self.symbols, now=self.now_factory(),
                    )
                    with self.session_factory() as db:
                        persist_tick(db, summary)
            except Exception as exc:  # noqa: BLE001 — tick 실패는 다음 tick 재시도.
                _log.warning("[shadow-mtf] tick 실패: %s: %s", type(exc).__name__, exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:  # noqa: BLE001
                pass
