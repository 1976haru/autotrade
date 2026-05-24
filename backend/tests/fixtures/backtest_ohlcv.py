"""#46 / 6-01: 백테스트용 deterministic OHLCV fixture 빌더.

외부 네트워크 / KIS API 호출 0건 — 순수 파이썬으로 시나리오별 bar 를 생성한다.
포함 시나리오: 상승추세 / 하락추세 / 횡보 / gap up / gap down / opening range
breakout / vwap 상회·하회 / volume 변화 / 중복 timestamp / 데이터 부족.

모든 bar 는 KST 장중(09:00~15:30) 1분봉 가정. 생성 결과는 deterministic
(난수 미사용) — 테스트 재현성 보장.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))


def _bar(symbol, ts, o, h, lo, c, vol, **extra):
    rec = {
        "symbol": symbol, "timestamp": ts.isoformat(),
        "open": o, "high": h, "low": lo, "close": c, "volume": vol,
    }
    rec.update(extra)
    return rec


def _day_start(date_str: str) -> datetime:
    d = datetime.fromisoformat(date_str)
    return d.replace(hour=9, minute=0, tzinfo=_KST)


def make_trend_day(symbol: str, date_str: str, *, direction: str = "up",
                   bars: int = 80, start_price: int = 70_000,
                   prev_close: int | None = None, step: int = 60) -> list[dict]:
    """추세 일봉 (direction: up / down / flat). gap 은 prev_close 대비 open 으로 표현."""
    t0 = _day_start(date_str)
    out: list[dict] = []
    price = start_price
    delta = {"up": step, "down": -step, "flat": 0}[direction]
    for i in range(bars):
        ts = t0 + timedelta(minutes=i)
        o = price
        c = price + delta
        # 횡보는 작은 톱니로 흔든다 (deterministic).
        if direction == "flat":
            c = price + (step if i % 2 == 0 else -step) // 2
        h = max(o, c) + 30
        lo = min(o, c) - 30
        vol = 8_000_000 + (i % 5) * 200_000
        out.append(_bar(symbol, ts, o, h, lo, c, vol))
        price = c
    return out


def make_gap_day(symbol: str, date_str: str, *, prev_close: int, gap_pct: float,
                 bars: int = 80, follow_through: bool = True) -> list[dict]:
    """gap up/down 일봉 — open 이 prev_close 대비 gap_pct 만큼 점프."""
    t0 = _day_start(date_str)
    open_price = int(round(prev_close * (1 + gap_pct)))
    out: list[dict] = []
    price = open_price
    drift = 50 if (gap_pct > 0 and follow_through) else (-50 if gap_pct < 0 else 0)
    for i in range(bars):
        ts = t0 + timedelta(minutes=i)
        o = price
        c = price + drift
        h = max(o, c) + 40
        lo = min(o, c) - 40
        vol = 9_000_000 + (i % 4) * 300_000
        out.append(_bar(symbol, ts, o, h, lo, c, vol, gap_pct=round(gap_pct, 4)))
        price = c
    return out


def make_orb_breakout_day(symbol: str, date_str: str, *, base: int = 70_000,
                          bars: int = 80) -> list[dict]:
    """opening range 형성 후 상단 돌파 — ORB BUY 유도."""
    t0 = _day_start(date_str)
    out: list[dict] = []
    # 첫 3 bar = opening range (좁은 박스).
    box = [base, base + 100, base + 50]
    for i, c in enumerate(box):
        ts = t0 + timedelta(minutes=i)
        out.append(_bar(symbol, ts, base, base + 150, base - 50, c, 8_000_000))
    # 이후 상단 돌파 후 상승.
    price = base + 150
    for i in range(3, bars):
        ts = t0 + timedelta(minutes=i)
        o = price
        c = price + 70
        out.append(_bar(symbol, ts, o, c + 30, o - 20, c, 9_500_000 + (i % 3) * 100_000))
        price = c
    return out


def make_low_volume_day(symbol: str, date_str: str, *, base: int = 70_000,
                        bars: int = 80) -> list[dict]:
    """거래량 급감 일봉 — low_volume risk_flag 유도."""
    t0 = _day_start(date_str)
    out: list[dict] = []
    price = base
    for i in range(bars):
        ts = t0 + timedelta(minutes=i)
        o = price
        c = price + (40 if i % 2 == 0 else -20)
        vol = 1_000_000 if i > 5 else 9_000_000   # 평균 대비 급감.
        out.append(_bar(symbol, ts, o, max(o, c) + 20, min(o, c) - 20, c, vol))
        price = c
    return out


def make_choppy_day(symbol: str, date_str: str, *, base: int = 70_000,
                    bars: int = 80) -> list[dict]:
    """급등락 반복 일봉 — BUY 신호 일부가 close horizon 에서 손실(혼합 성과)."""
    t0 = _day_start(date_str)
    out: list[dict] = []
    price = base
    for i in range(bars):
        ts = t0 + timedelta(minutes=i)
        o = price
        # 큰 폭 상승 후 더 큰 하락 반복 → 고점 매수는 손실.
        c = price + (700 if i % 2 == 0 else -800)
        h = max(o, c) + 80
        lo = min(o, c) - 80
        out.append(_bar(symbol, ts, o, h, lo, c, 8_500_000 + (i % 3) * 150_000))
        price = c
    return out


def full_scenario_records(symbol: str = "TEST") -> list[dict]:
    """상승+하락+횡보+gap up/down+ORB 돌파를 여러 날에 걸쳐 합친 레코드.

    여러 날을 이어 prev_close(전일 종가) 와 gap 평가가 가능하게 한다.
    """
    recs: list[dict] = []
    # day 1: 상승추세.
    d1 = make_trend_day(symbol, "2026-05-11", direction="up", start_price=70_000)
    recs += d1
    prev_close = d1[-1]["close"]
    # day 2: gap up + follow-through.
    d2 = make_gap_day(symbol, "2026-05-12", prev_close=prev_close, gap_pct=0.03)
    recs += d2
    prev_close = d2[-1]["close"]
    # day 3: 하락추세.
    d3 = make_trend_day(symbol, "2026-05-13", direction="down", start_price=prev_close)
    recs += d3
    prev_close = d3[-1]["close"]
    # day 4: gap down.
    d4 = make_gap_day(symbol, "2026-05-14", prev_close=prev_close, gap_pct=-0.03)
    recs += d4
    prev_close = d4[-1]["close"]
    # day 5: 횡보.
    recs += make_trend_day(symbol, "2026-05-15", direction="flat", start_price=prev_close)
    # day 6: ORB 돌파.
    d6 = make_orb_breakout_day(symbol, "2026-05-18", base=prev_close)
    recs += d6
    prev_close = d6[-1]["close"]
    # day 7: 급등락 반복 (혼합 성과 — BUY 일부 손실).
    recs += make_choppy_day(symbol, "2026-05-19", base=prev_close)
    return recs


def insufficient_records(symbol: str = "TEST") -> list[dict]:
    """데이터 부족 케이스 — bar 몇 개만."""
    t0 = _day_start("2026-05-11")
    return [_bar(symbol, t0 + timedelta(minutes=i), 70_000, 70_100, 69_900, 70_050, 8_000_000)
            for i in range(4)]


# ── #47 / 6-02 walk-forward 용 multi-day fixture ─────────────────────────────

_WF_DATES = [
    "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14",
    "2026-05-15", "2026-05-18", "2026-05-19", "2026-05-20",
]


def _day_by_kind(symbol: str, date_str: str, kind: str, start_price: int) -> list[dict]:
    if kind == "choppy":
        return make_choppy_day(symbol, date_str, base=start_price)
    return make_trend_day(symbol, date_str, direction=kind, start_price=start_price)


def multi_day_records(symbol: str, kinds: list[str], *, start_price: int = 70_000,
                      dates: list[str] | None = None) -> list[dict]:
    """여러 거래일을 kind(up/down/flat/choppy) 순서대로 이어 붙인다 (가격 연속)."""
    dts = dates or _WF_DATES
    recs: list[dict] = []
    price = start_price
    for i, kind in enumerate(kinds):
        date_str = dts[i % len(dts)]
        day = _day_by_kind(symbol, date_str, kind, price)
        recs += day
        price = day[-1]["close"]
    return recs


def walk_forward_stable_records(symbol: str = "TEST") -> list[dict]:
    """8 거래일 모두 상승추세 — train/validation/test 성과 유지(안정)."""
    return multi_day_records(symbol, ["up"] * 8)


def walk_forward_overfit_records(symbol: str = "TEST") -> list[dict]:
    """train 구간(앞)은 상승, validation/test 구간(뒤)은 급등락 → 과최적화 의심."""
    return multi_day_records(symbol, ["up", "up", "up", "up", "up",
                                      "choppy", "choppy", "choppy"])


def walk_forward_insufficient_records(symbol: str = "TEST") -> list[dict]:
    """walk-forward split 불가 — 2 거래일만."""
    return multi_day_records(symbol, ["up", "up"], dates=_WF_DATES[:2])


def duplicate_timestamp_records(symbol: str = "TEST") -> list[dict]:
    """중복 timestamp 포함 — 로더가 dedup 하는지 검증용."""
    base = make_trend_day(symbol, "2026-05-11", direction="up", bars=10)
    return base + [dict(base[0]), dict(base[1])]   # 처음 2개 중복.


def to_csv_text(records: list[dict]) -> str:
    """records → CSV 문자열 (헤더: timestamp,open,high,low,close,volume[,vwap,...])."""
    import csv as _csv
    import io
    cols = ["symbol", "timestamp", "open", "high", "low", "close", "volume",
            "vwap", "market_regime", "time_phase", "gap_pct"]
    buf = io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in records:
        w.writerow(r)
    return buf.getvalue()
