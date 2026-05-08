"""PullbackRebreakStrategy 신호 로직 단위 테스트 (#30).

impulse → 거래량 감소 pullback → rebreak 구조 인식 + 추격 가드 (impulse/
pullback hard-cap, VWAP 격차, intraday runup, open cooldown) + 운영 가드
(stale, blocked regime, liquidity) + 일중 1회 진입.

본 전략은 *주문을 직접 만들지 않는다* (CLAUDE.md 절대 원칙 2). 모든
StrategySignal은 `is_order_intent=False`이며, broker / risk / permission /
execution / governance 어떤 모듈도 import하지 않는다.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.types import Bar, Signal
from app.strategies.base import (
    SignalAction,
    StrategyContext,
)
from app.strategies.concrete.pullback_rebreak import PullbackRebreakStrategy


_DAY1_OPEN = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)
_DAY2_OPEN = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)


def _bar(
    ts: datetime,
    *,
    o: int = 100, h: int = 102, lo: int = 99, c: int = 100, v: int = 1000,
    symbol: str = "X",
) -> Bar:
    return Bar(symbol=symbol, timestamp=ts, open=o, high=h, low=lo, close=c, volume=v)


def _ctx(bars: list[Bar], **kwargs) -> StrategyContext:
    return StrategyContext(bars=bars, symbol="X", **kwargs)


def _build_pattern(
    *,
    n_baseline: int = 20,  # total ≥ 31 bars to satisfy default min_bars_required=30
    baseline_close: int = 100,
    baseline_volume: int = 1000,
    impulse_bars: int = 5,
    impulse_top: int = 105,            # peak close
    impulse_volume: int = 5000,        # impulse 구간 거래량 (높음)
    pullback_bars: int = 5,
    pullback_low: int = 102,           # pullback 저점 close
    pullback_volume: int = 1500,       # pullback 거래량 (낮음 → fade)
    rebreak_close: int = 106,          # 현재 봉 close (peak 위)
    rebreak_volume: int = 4000,        # 현재 봉 volume (재돌파 참여)
    base_ts: datetime = _DAY1_OPEN,
    interval_minutes: int = 1,
) -> list[Bar]:
    """impulse_low(=baseline) → impulse → peak → pullback → rebreak 구조 생성.

    구조:
      [baseline N봉, close=100] →
      [impulse N봉, close 100→impulse_top 등반] →
      [pullback N봉, peak→pullback_low 하락] →
      [현재 봉(rebreak), close=rebreak_close]
    """
    bars: list[Bar] = []
    idx = 0

    # baseline (low / impulse_low)
    for _ in range(n_baseline):
        bars.append(_bar(
            base_ts + timedelta(minutes=idx * interval_minutes),
            o=baseline_close, h=baseline_close + 1, lo=baseline_close - 1,
            c=baseline_close, v=baseline_volume,
        ))
        idx += 1

    # impulse — 등반 (선형 보간)
    for i in range(1, impulse_bars + 1):
        c = baseline_close + (impulse_top - baseline_close) * i // impulse_bars
        bars.append(_bar(
            base_ts + timedelta(minutes=idx * interval_minutes),
            o=c, h=c + 1, lo=c - 1, c=c, v=impulse_volume,
        ))
        idx += 1

    # pullback — 하락 (선형 보간 후 마지막 봉이 pullback_low)
    for i in range(1, pullback_bars + 1):
        c = impulse_top + (pullback_low - impulse_top) * i // pullback_bars
        bars.append(_bar(
            base_ts + timedelta(minutes=idx * interval_minutes),
            o=c, h=c + 1, lo=c - 1, c=c, v=pullback_volume,
        ))
        idx += 1

    # 현재 봉 (rebreak)
    bars.append(_bar(
        base_ts + timedelta(minutes=idx * interval_minutes),
        o=pullback_low, h=rebreak_close + 1, lo=pullback_low - 1,
        c=rebreak_close, v=rebreak_volume,
    ))
    return bars


# ---------- 입력 검증 ----------

def test_rejects_invalid_params():
    with pytest.raises(ValueError):
        PullbackRebreakStrategy(min_bars_required=1)
    with pytest.raises(ValueError):
        PullbackRebreakStrategy(impulse_lookback_bars=1)
    with pytest.raises(ValueError):
        PullbackRebreakStrategy(min_impulse_pct=0)
    with pytest.raises(ValueError):
        PullbackRebreakStrategy(min_impulse_pct=5, max_impulse_pct=3)
    with pytest.raises(ValueError):
        PullbackRebreakStrategy(pullback_min_pct=2, pullback_max_pct=1)
    with pytest.raises(ValueError):
        PullbackRebreakStrategy(pullback_volume_fade_ratio=1.0)
    with pytest.raises(ValueError):
        PullbackRebreakStrategy(rebreak_volume_min_ratio=0)
    with pytest.raises(ValueError):
        PullbackRebreakStrategy(open_cooldown_bars=-1)
    with pytest.raises(ValueError):
        PullbackRebreakStrategy(stop_loss_below_pullback_low_pct=0)


# ---------- 1. bars 부족 ----------

def test_bars_insufficient_returns_no_signal():
    s = PullbackRebreakStrategy(min_bars_required=30)
    bars = _build_pattern(n_baseline=2)  # too short
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.is_order_intent is False
    assert out.explanation.indicators.get("decision_kind") in (None, "NO_SIGNAL")


# ---------- 2. impulse 없음 (평탄 데이터) ----------

def test_no_impulse_returns_no_signal_or_watch():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3)
    # 평탄 데이터 — impulse 없음
    bars = []
    for i in range(40):
        bars.append(_bar(_DAY1_OPEN + timedelta(minutes=i),
                         o=100, h=101, lo=99, c=100, v=1000))
    out = s.generate_signal(_ctx(bars))
    assert out.action != SignalAction.BUY
    assert out.is_order_intent is False


# ---------- 3. impulse 약함 ----------

def test_impulse_too_weak_returns_no_signal():
    """impulse_pct가 min_impulse_pct 미만 — NO_SIGNAL."""
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  min_impulse_pct=5.0, max_impulse_pct=20.0,
                                  pullback_min_pct=0.5, pullback_max_pct=4.0)
    # impulse만 1% (100→101) — min 5% 미달
    bars = _build_pattern(impulse_top=101, pullback_low=100, rebreak_close=102)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert any("impulse" in r and "min_impulse_pct" in r for r in out.explanation.reasons)


# ---------- 4. impulse 너무 강함 → REJECT ----------

def test_impulse_too_strong_rejects():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_impulse_pct=8.0,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    # impulse 30% (100→130) — max 8% 초과
    bars = _build_pattern(impulse_top=130, pullback_low=125, rebreak_close=131)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("max_impulse_pct" in r or "추격" in r for r in out.explanation.reasons)


# ---------- 5. pullback 없으면 NO_SIGNAL/WATCH ----------

def test_no_pullback_when_low_equals_peak():
    """impulse만 있고 pullback이 없으면 (peak가 가장 오른쪽) NO_SIGNAL."""
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    # 모든 봉이 단조 증가 — peak는 직전 봉, pullback 없음.
    bars = []
    for i in range(40):
        c = 100 + i  # 100, 101, 102, ..., 139
        bars.append(_bar(_DAY1_OPEN + timedelta(minutes=i),
                         o=c, h=c + 1, lo=c - 1, c=c, v=2000))
    out = s.generate_signal(_ctx(bars))
    # 마지막 봉이 곧 peak이므로 _find_pivots는 None — runup이 max보다 작으면 NO_SIGNAL.
    # max_intraday_runup_pct=50으로 runup 가드는 통과했으므로 패턴 미식별 메시지가 나와야 함.
    assert out.action != SignalAction.BUY
    assert out.is_order_intent is False


# ---------- 6. pullback 너무 얕음 → WATCH ----------

def test_pullback_too_shallow_returns_watch():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  pullback_min_pct=2.0, pullback_max_pct=5.0,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    # pullback 1% — min 2% 미달
    bars = _build_pattern(impulse_top=110, pullback_low=109, rebreak_close=112)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.WATCH
    assert any("pullback_min_pct" in r or "눌림 미형성" in r for r in out.explanation.reasons)


# ---------- 7. pullback 너무 깊음 → REJECT ----------

def test_pullback_too_deep_rejects():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  pullback_min_pct=0.5, pullback_max_pct=3.0,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    # pullback 9% (110 → 100). max 3% 초과.
    bars = _build_pattern(impulse_top=110, pullback_low=100, rebreak_close=112)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("pullback_max_pct" in r or "깊은 눌림" in r for r in out.explanation.reasons)


# ---------- 8. pullback 거래량 fade 없음 → NO_SIGNAL ----------

def test_no_volume_fade_returns_no_signal():
    """pullback 구간 거래량이 impulse 대비 작지 않으면 패턴 무효."""
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0,
                                  pullback_volume_fade_ratio=0.85)
    # impulse_volume=2000, pullback_volume=2000 → fade 없음
    bars = _build_pattern(impulse_volume=2000, pullback_volume=2000)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert any("거래량 fade" in r or "volume fade" in r.lower() for r in out.explanation.reasons)


# ---------- 9. VWAP 아래이면 BUY 불가 ----------

def test_below_vwap_does_not_buy():
    """impulse + pullback + rebreak이 모두 충족돼도 close < VWAP이면 WATCH."""
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    # 첫 봉을 huge volume + super high price로 세션 VWAP을 끌어올림
    bars: list[Bar] = []
    bars.append(_bar(_DAY1_OPEN, o=1000, h=1000, lo=1000, c=1000, v=10_000_000))
    # 이후 normal pattern
    base_ts = _DAY1_OPEN + timedelta(minutes=1)
    bars.extend(_build_pattern(base_ts=base_ts))
    out = s.generate_signal(_ctx(bars))
    assert out.action != SignalAction.BUY
    assert out.is_order_intent is False


# ---------- 10. rebreak 없음 → WATCH ----------

def test_no_rebreak_returns_watch():
    """impulse + pullback은 형성됐으나 현재 봉이 peak를 재돌파하지 못함."""
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  pullback_max_pct=10.0,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    # peak=110, pullback_low=107 (2.7% pullback within 10% cap), rebreak_close=109 < peak=110
    bars = _build_pattern(impulse_top=110, pullback_low=107, rebreak_close=109,
                          rebreak_volume=5000)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.WATCH
    assert any("재돌파 대기" in r for r in out.explanation.reasons)


# ---------- 11. 정상 BUY ----------

def test_full_pattern_returns_buy():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0,
                                  rebreak_volume_min_ratio=1.2)
    bars = _build_pattern(
        impulse_top=110, pullback_low=106, rebreak_close=112,
        impulse_volume=5000, pullback_volume=1500, rebreak_volume=4000,
    )
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.BUY, out.explanation.reasons
    assert out.is_order_intent is False
    assert out.sizing_hint is not None
    assert out.exit_plan is not None
    assert out.explanation.confidence is not None
    assert 0 <= out.explanation.confidence <= 100
    assert 0 <= out.explanation.indicators["quality_score"] <= 100


# ---------- 12. rebreak 거래량 부족 → WATCH ----------

def test_rebreak_low_volume_returns_watch():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0,
                                  rebreak_volume_min_ratio=2.0)
    # rebreak volume이 pullback과 동일 → 1.0x < 2.0x 임계
    bars = _build_pattern(
        impulse_top=110, pullback_low=106, rebreak_close=112,
        impulse_volume=5000, pullback_volume=1500, rebreak_volume=1500,
    )
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.WATCH
    assert any("거래량" in r and "rebreak_volume_min_ratio" in r for r in out.explanation.reasons)


# ---------- 13. open cooldown ----------

def test_open_cooldown_rejects():
    """bars_in_session ≤ open_cooldown_bars면 REJECT."""
    s = PullbackRebreakStrategy(min_bars_required=30,
                                  open_cooldown_bars=50,  # 31 봉 < 50 → REJECT
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    bars = _build_pattern()  # 31봉
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("open cooldown" in r for r in out.explanation.reasons)


# ---------- 14. blocked regime ----------

def test_blocked_regime_rejects():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    bars = _build_pattern(impulse_top=110, pullback_low=106, rebreak_close=112)
    out = s.generate_signal(_ctx(bars, regime="trending_down"))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("blocked regime" in r for r in out.explanation.reasons)


# ---------- 15. stale data ----------

def test_stale_data_rejects():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0,
                                  stale_max_age_seconds=30)
    bars = _build_pattern()
    out = s.generate_signal(_ctx(bars, extra={"data_age_seconds": 120}))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("stale data" in r for r in out.explanation.reasons)


def test_stale_via_now_timestamp_rejects():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0,
                                  stale_max_age_seconds=30)
    bars = _build_pattern()
    last_ts = bars[-1].timestamp
    out = s.generate_signal(_ctx(bars, extra={"now": last_ts + timedelta(minutes=5)}))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"


# ---------- 16. VWAP 격차 너무 멀면 REJECT ----------

def test_vwap_distance_too_far_rejects():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=1.0)  # 매우 작은 cap
    # 정상 pattern — close=112, VWAP은 ≈ 102~104 범위 → 격차 > 1%
    bars = _build_pattern(impulse_top=110, pullback_low=106, rebreak_close=112)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("VWAP 격차" in r for r in out.explanation.reasons)


# ---------- 17. intraday runup 과도 → REJECT ----------

def test_intraday_runup_excessive_rejects():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=3.0,
                                  max_vwap_distance_pct=50.0)
    # session open=100, rebreak close=120 → runup 20% > 3% cap
    bars = _build_pattern(baseline_close=100, impulse_top=115, pullback_low=110,
                          rebreak_close=120)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("intraday runup" in r for r in out.explanation.reasons)


# ---------- 18. volume 0 안전 처리 ----------

def test_zero_volume_current_bar_rejects():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    bars = _build_pattern(rebreak_volume=0)
    out = s.generate_signal(_ctx(bars))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.explanation.indicators.get("decision_kind") == "REJECT"
    assert any("volume == 0" in r for r in out.explanation.reasons)


# ---------- 19. 일중 1회 진입 invariant ----------

def test_only_first_buy_fires_within_session():
    """첫 BUY 후 같은 세션에서 새로운 impulse-pullback-rebreak 구조가 형성돼도
    fired_today로 차단."""
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    bars = _build_pattern(impulse_top=110, pullback_low=106, rebreak_close=112,
                          rebreak_volume=4000)
    out1 = s.generate_signal(_ctx(bars))
    assert out1.action == SignalAction.BUY

    # 첫 BUY 이후 다시 가격이 내려가서 새로운 pullback이 형성된 뒤 또 rebreak.
    last_ts = bars[-1].timestamp
    # pullback again: 112 → 108
    for i, c in enumerate([110, 108, 107, 108, 109], start=1):
        bars.append(_bar(last_ts + timedelta(minutes=i),
                         o=c, h=c + 1, lo=c - 1, c=c, v=1500))
    # rebreak again: close=113 (위 110 peak를 재돌파)
    bars.append(_bar(last_ts + timedelta(minutes=6),
                     o=110, h=115, lo=109, c=113, v=5000))
    out2 = s.generate_signal(_ctx(bars))
    assert out2.action == SignalAction.NO_SIGNAL
    assert any("already fired today" in r for r in out2.explanation.reasons)


# ---------- 20. 일별 reset ----------

def test_resets_state_at_day_boundary():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    day1 = _build_pattern(base_ts=_DAY1_OPEN, impulse_top=110, pullback_low=106,
                          rebreak_close=112, rebreak_volume=4000)
    out1 = s.generate_signal(_ctx(day1))
    assert out1.action == SignalAction.BUY

    # day2 — 새 세션 시작, fired_today reset
    day2 = _build_pattern(base_ts=_DAY2_OPEN, baseline_close=200, impulse_top=210,
                          pullback_low=206, rebreak_close=212,
                          impulse_volume=5000, pullback_volume=1500, rebreak_volume=4000)
    out2 = s.generate_signal(_ctx(day1 + day2))
    assert out2.action == SignalAction.BUY


# ---------- 21. 직접 주문 invariant ----------

def test_strategy_does_not_import_broker_or_risk():
    """pullback_rebreak.py는 broker/risk/permission/execution import 0건."""
    import inspect

    from app.strategies.concrete import pullback_rebreak as mod
    src = inspect.getsource(mod)
    forbidden = (
        "from app.brokers", "from app.risk", "from app.permission",
        "from app.execution", "from app.governance",
    )
    for f in forbidden:
        assert f not in src, f"forbidden import: {f}"


def test_strategy_signal_dict_has_no_order_fields():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    bars = _build_pattern(impulse_top=110, pullback_low=106, rebreak_close=112)
    out = s.generate_signal(_ctx(bars))
    d = out.to_dict()
    forbidden = ("side", "order_type", "limit_price", "decision",
                 "broker_order_id", "client_order_id", "quantity_to_execute")
    for f in forbidden:
        assert f not in d
    assert d["is_order_intent"] is False


def test_signal_is_not_order_intent_for_all_branches():
    """모든 분기에서 is_order_intent=False."""
    s_short = PullbackRebreakStrategy(min_bars_required=99)
    out = s_short.generate_signal(_ctx(_build_pattern()))
    assert out.action == SignalAction.NO_SIGNAL
    assert out.is_order_intent is False

    # WATCH — rebreak 미발생
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    bars_watch = _build_pattern(impulse_top=110, pullback_low=106, rebreak_close=104)
    out_w = s.generate_signal(_ctx(bars_watch))
    assert out_w.action == SignalAction.WATCH
    assert out_w.is_order_intent is False

    # REJECT — runup 과도
    s_strict = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                         max_intraday_runup_pct=2.0,
                                         max_vwap_distance_pct=50.0)
    bars_rej = _build_pattern(baseline_close=100, impulse_top=115,
                              pullback_low=110, rebreak_close=120)
    out_r = s_strict.generate_signal(_ctx(bars_rej))
    assert out_r.is_order_intent is False
    assert out_r.explanation.indicators.get("decision_kind") == "REJECT"

    # BUY
    bars_buy = _build_pattern(impulse_top=110, pullback_low=106, rebreak_close=112,
                               rebreak_volume=4000)
    out_b = s.generate_signal(_ctx(bars_buy))
    assert out_b.is_order_intent is False
    if out_b.action == SignalAction.BUY:
        assert out_b.sizing_hint is not None
        assert out_b.exit_plan is not None


# ---------- 22. 계산: impulse_pct / pullback_pct / volume_fade_ratio / pivots ----------

def test_pivot_indices_are_correct():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    # default n_baseline=20, impulse 5봉, pullback 5봉, rebreak 1봉 → 31 bars
    bars = _build_pattern(n_baseline=20, impulse_bars=5, pullback_bars=5,
                          impulse_top=110, pullback_low=106, rebreak_close=112,
                          rebreak_volume=4000)
    out = s.generate_signal(_ctx(bars))
    ind = out.explanation.indicators
    # baseline 20봉(0..19), impulse 5봉(20..24), pullback 5봉(25..29), 현재(30)
    assert ind["peak_idx"] == 24
    # pullback_low: argmin in [25, 30) — pullback closes [109, 108, 107, 106, 106],
    # min은 첫 발생 idx 28.
    assert ind["pullback_low_idx"] in (28, 29)
    # impulse_low_idx는 baseline 영역 (close=100 동일).
    assert 0 <= ind["impulse_low_idx"] <= 23


def test_impulse_pct_and_pullback_pct_calculation():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    bars = _build_pattern(impulse_top=110, pullback_low=105, rebreak_close=112,
                          rebreak_volume=4000)
    out = s.generate_signal(_ctx(bars))
    ind = out.explanation.indicators
    # impulse: 100 → 110 = 10%
    assert 9.5 <= ind["impulse_pct"] <= 10.5
    # pullback: 110 → 105 = 4.55%
    assert 4.0 <= ind["pullback_pct"] <= 5.0


def test_volume_fade_ratio_calculation():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    bars = _build_pattern(impulse_volume=5000, pullback_volume=1000)
    out = s.generate_signal(_ctx(bars))
    ind = out.explanation.indicators
    # pullback 1000 / impulse 5000 = 0.2 (대략 — 가격 가중이라 미세 차이)
    assert 0.0 < ind["volume_fade_ratio"] < 0.5


# ---------- 23. exit_rule / calculate_size ----------

def test_exit_rule_uses_pullback_low_for_dynamic_stop():
    s = PullbackRebreakStrategy(stop_loss_below_pullback_low_pct=1.0,
                                  take_profit_pct=4.0, trailing_pct=1.5,
                                  time_exit_bars=30)
    plan = s.exit_rule(None, position_context={
        "pullback_low_close": 100,
        "current_close":      105,
    })
    # stop = 100 * 0.99 = 99 → (105 - 99)/105 ≈ 5.71%
    assert 5.0 <= plan.stop_loss_pct <= 6.5
    assert plan.take_profit_pct == 4.0
    assert plan.time_exit_bars == 30
    assert "pullback_low" in plan.invalidation
    assert "trailing" in plan.invalidation


def test_exit_rule_falls_back_to_baseline_without_context():
    s = PullbackRebreakStrategy()
    plan = s.exit_rule(None)
    # fallback to risk_profile.stop_loss_pct
    assert plan.stop_loss_pct == s.risk_profile["stop_loss_pct"]
    assert plan.time_exit_bars == s.risk_profile["time_exit_bars"]


def test_calculate_size_reduces_for_low_confidence():
    s = PullbackRebreakStrategy()
    base_pct = float(s.risk_profile["position_size_pct"])
    h_high = s.calculate_size(None, risk_context={"confidence": 80})
    h_low  = s.calculate_size(None, risk_context={"confidence": 30})
    assert h_high.position_size_pct == base_pct
    assert h_low.position_size_pct  < base_pct


def test_calculate_size_reduces_for_wide_stop():
    s = PullbackRebreakStrategy(stop_loss_below_pullback_low_pct=1.0)
    base_pct = float(s.risk_profile["position_size_pct"])
    # pullback_low far from current_close → wide stop
    h = s.calculate_size(None, risk_context={
        "confidence":         80,
        "pullback_low_close": 80,
        "current_close":      110,
    })
    assert h.position_size_pct < base_pct
    assert h.note is not None


# ---------- 24. legacy on_bar ----------

def test_on_bar_returns_buy_for_full_pattern():
    s = PullbackRebreakStrategy(min_bars_required=30, open_cooldown_bars=3,
                                  max_intraday_runup_pct=50.0,
                                  max_vwap_distance_pct=50.0)
    bars = _build_pattern(impulse_top=110, pullback_low=106, rebreak_close=112,
                          rebreak_volume=4000)
    sigs = [s.on_bar(bars[:i + 1]) for i in range(len(bars))]
    assert sigs[-1] == Signal.BUY
    # 워밍업 동안은 BUY 미발생
    assert all(x != Signal.BUY for x in sigs[:25])


def test_on_bar_holds_on_flat_data():
    """평탄한 데이터 — 어떤 BUY도 발생하지 않음 (registry 테스트 호환)."""
    s = PullbackRebreakStrategy()
    bars = []
    for i in range(60):
        bars.append(_bar(_DAY1_OPEN + timedelta(minutes=i),
                         o=100, h=101, lo=99, c=100, v=1000))
    for i in range(1, len(bars) + 1):
        sig = s.on_bar(bars[:i])
        assert sig == Signal.HOLD


# ---------- 25. metadata / contract ----------

def test_metadata_is_complete():
    s = PullbackRebreakStrategy()
    assert s.entry
    assert s.exit
    assert s.invalidation
    assert s.required_regime != "any"
    assert s.risk_profile
    assert s.risk_profile["stop_loss_pct"] == 2.0
    assert s.risk_profile["take_profit_pct"] == 4.0
    assert s.risk_profile["trailing_pct"] == 1.5
