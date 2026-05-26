"""KIS-INTRADAY-ROBUST-DATASET-COLLECTION-01 — robust 분봉 데이터셋 메타 (read-only).

정확한 *장기* 전략 검증을 위한 데이터셋의 **메타데이터**(종목군 / 시간분할 /
장세 라벨 / 품질 / manifest)를 *순수 결정론적 함수*로 생성한다. **백테스트를
실행하지 않는다** — 본 작업의 목표는 데이터 수집/품질검증/분할 메타데이터 구축이다.

설계 원칙 (CLAUDE.md 절대 원칙 상속):
- 본 모듈은 *순수 데이터 + 결정론적 함수* — broker / OrderExecutor / route_order /
  KIS 주문 API / Anthropic / OpenAI / httpx / requests import 0건. 외부 HTTP 호출 0건.
- 안전 flag mutate 0건. secret / 계좌 carry 0건.
- 모든 산출물은 검증 자료일 뿐 — 주문 신호 / 실전 승인 / 투자 추천이 아니다.
- **look-ahead 금지**: 장세 regime 은 해당 날짜까지의 정보로만 라벨링한다.

데이터 수집/검증 자체(KIS read-only 분봉 호출)는 scripts/ 의 collector 가 수행하고,
본 모듈은 수집 결과의 메타데이터만 구성한다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.market_data.intraday_ohlcv import check_intraday_quality

# ----------------------------------------------------------------------------
# 표준 경로 (기존 6개월 / 1년 데이터와 혼동 금지 — 별도 경로).
# ----------------------------------------------------------------------------
DATASET_NAME = "robust_intraday"
ROBUST_5M_DIR = "data/market/robust_intraday_5m"
ROBUST_1M_SUBSET_DIR = "data/market/robust_intraday_1m_subset"
REPORT_DIR = "reports/strategy_validation"

# 품질 / 준비 판정 기준 (5분봉).
MIN_DAYS_FOR_PASS = 220          # 종목별 거래일 — 대부분 종목이 이 이상이면 PASS
MIN_DAYS_FOR_SPLIT = 200         # train/validation/test 분할 가능 최소 거래일
MIN_SYMBOLS_FOR_READY = 30       # robust 백테스트 준비 최소 종목 수
MIN_TRADING_DAYS_FOR_READY = 200

_KST = timezone(timedelta(hours=9))

# ----------------------------------------------------------------------------
# 종목군 정의 (LARGE_CAP / MID_CAP / HIGH_VOL_THEME / ETF_PROXY)
# ----------------------------------------------------------------------------
LARGE_CAP = "LARGE_CAP"
MID_CAP = "MID_CAP"
HIGH_VOL_THEME = "HIGH_VOL_THEME"
ETF_PROXY = "ETF_PROXY"

GROUP_ORDER = (LARGE_CAP, MID_CAP, HIGH_VOL_THEME, ETF_PROXY)

# (code, 표시명, 선정 이유) — code 가 단일 진실. 정적 큐레이션 목록.
_LARGE_CAP: tuple[tuple[str, str, str], ...] = (
    ("005930", "삼성전자", "시총 1위 · 최고 유동성 대표 대형주"),
    ("000660", "SK하이닉스", "반도체 대형주 · 거래대금 상위"),
    ("005380", "현대차", "자동차 대형주 · 경기 민감 대표"),
    ("000270", "기아", "자동차 대형주 · 거래대금 충분"),
    ("051910", "LG화학", "화학/2차전지 대형주"),
    ("006400", "삼성SDI", "2차전지 대형주"),
    ("035420", "NAVER", "플랫폼 대형주"),
    ("035720", "카카오", "플랫폼 대형주 · 변동성 보유"),
    ("068270", "셀트리온", "바이오 대형주"),
    ("012330", "현대모비스", "자동차 부품 대형주"),
)

_MID_CAP: tuple[tuple[str, str, str], ...] = (
    ("105560", "KB금융", "금융 대표 중대형 · 업종 분산"),
    ("055550", "신한지주", "금융 중대형 · 유동성 충분"),
    ("034730", "SK", "지주/IT 중대형"),
    ("009540", "HD한국조선해양", "조선 중대형 · 업종 분산"),
    ("011200", "HMM", "해운 중형 · 거래대금 상위"),
    ("010130", "고려아연", "비철금속 중대형"),
    ("032830", "삼성생명", "보험 중대형 · 업종 분산"),
    ("066570", "LG전자", "전자 중대형 · 반복 선택 core"),
    ("015760", "한국전력", "유틸리티 중대형 · 저변동 대조군"),
    ("086790", "하나금융지주", "금융 중대형 · 업종 분산"),
)

_HIGH_VOL_THEME: tuple[tuple[str, str, str], ...] = (
    ("042700", "한미반도체", "반도체 장비 테마 · 고변동"),
    ("247540", "에코프로비엠", "2차전지 소재 테마 · 고변동"),
    ("086520", "에코프로", "2차전지 테마 · 고변동"),
    ("196170", "알테오젠", "바이오 테마 · 고변동"),
    ("028300", "HLB", "바이오 테마 · 고변동"),
    ("259960", "크래프톤", "게임 테마 · 변동성"),
    ("352820", "하이브", "엔터 테마 · 변동성"),
    ("293490", "카카오게임즈", "게임 테마 · 변동성"),
    ("012450", "한화에어로스페이스", "방산 테마 · 거래대금 상위"),
    ("042660", "한화오션", "조선 테마 · 고변동"),
)

# ETF / 지수 proxy (시장 상태 proxy 용). KIS 분봉 제공 여부는 *수집 단계*에서 확정.
_ETF_PROXY: tuple[tuple[str, str, str], ...] = (
    ("069500", "KODEX 200", "KOSPI200 시장 proxy"),
    ("229200", "KODEX 코스닥150", "KOSDAQ150 시장 proxy"),
    ("102110", "TIGER 200", "KOSPI200 보조 proxy"),
    ("122630", "KODEX 레버리지", "시장 방향 고변동 proxy"),
    ("114800", "KODEX 인버스", "하락장 proxy (방향 확인용)"),
)

# 1분봉 정밀 subset 대표 10종목 (task §6 — 체결순서/ORB/GAP 장초반 정밀 검증용).
REPRESENTATIVE_10: tuple[str, ...] = (
    "005930", "000660", "005380", "000270", "012330",
    "042700", "066570", "006400", "035420", "051910",
)

_GROUP_DEFS: dict[str, tuple[tuple[str, str, str], ...]] = {
    LARGE_CAP: _LARGE_CAP,
    MID_CAP: _MID_CAP,
    HIGH_VOL_THEME: _HIGH_VOL_THEME,
    ETF_PROXY: _ETF_PROXY,
}


@dataclass(frozen=True)
class SymbolGroupEntry:
    symbol: str
    name: str
    group: str
    reason: str


@dataclass(frozen=True)
class SymbolGroupManifest:
    entries: tuple[SymbolGroupEntry, ...]
    counts_by_group: dict[str, int]
    symbols_by_group: dict[str, list[str]]
    total_symbols: int
    etf_proxy_note: str
    is_order_signal: bool = False
    is_investment_advice: bool = False
    contains_secret: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal or self.is_investment_advice or self.contains_secret:
            raise ValueError("unsafe invariant True on SymbolGroupManifest")

    def all_symbols(self) -> list[str]:
        return [e.symbol for e in self.entries]


def build_robust_symbol_groups() -> SymbolGroupManifest:
    """4개 그룹(대형/중형/고변동·테마/ETF proxy)으로 종목군을 구성한다.

    그룹 간 중복은 *첫 그룹*(GROUP_ORDER 우선)에서만 유지한다(순서 보존 dedupe).
    종목 수를 무작정 늘리지 않고 유형별로 나눈다 — 추후 group별 성과 분리를 위해.
    """
    from app.market_data.kis_intraday_universe import validate_symbol

    seen: set[str] = set()
    entries: list[SymbolGroupEntry] = []
    for group in GROUP_ORDER:
        for code, name, reason in _GROUP_DEFS[group]:
            c = str(code).strip()
            if not validate_symbol(c) or c in seen:
                continue
            seen.add(c)
            entries.append(SymbolGroupEntry(symbol=c, name=name, group=group, reason=reason))

    counts = {g: sum(1 for e in entries if e.group == g) for g in GROUP_ORDER}
    by_group = {g: [e.symbol for e in entries if e.group == g] for g in GROUP_ORDER}
    return SymbolGroupManifest(
        entries=tuple(entries),
        counts_by_group=counts,
        symbols_by_group=by_group,
        total_symbols=len(entries),
        etf_proxy_note=(
            "ETF/지수 proxy 의 KIS 분봉 제공 여부는 수집 단계에서 확정한다. "
            "미제공 시 manifest 에 unavailable 로 기록하고, 50종목 equal-weight proxy 로 대체한다."
        ),
    )


# ----------------------------------------------------------------------------
# 시간 분할 (Train / Validation / Test/OOS)
# ----------------------------------------------------------------------------
SPLIT_2Y = "TWO_YEAR_50_25_25"
SPLIT_1Y = "ONE_YEAR_6_3_3_MONTHS"
SPLIT_MIN = "MIN_240D_50_25_25"
SPLIT_INSUFFICIENT = "INSUFFICIENT_FOR_SPLIT"


@dataclass(frozen=True)
class TimeSplitManifest:
    split_method: str
    split_quality_status: str          # PASS / WARN / FAIL
    train_start: str | None
    train_end: str | None
    validation_start: str | None
    validation_end: str | None
    test_start: str | None
    test_end: str | None
    train_days: int
    validation_days: int
    test_days: int
    total_days: int
    reasons: tuple[str, ...] = ()
    # 절대 원칙: test 구간은 selector / score / 파라미터 선택에 사용 금지.
    test_used_for_selection: bool = False
    is_order_signal: bool = False
    contains_secret: bool = False

    def __post_init__(self) -> None:
        if self.test_used_for_selection or self.is_order_signal or self.contains_secret:
            raise ValueError("unsafe invariant True on TimeSplitManifest")


def build_time_split(trading_days: list[str], *, method: str = "auto") -> TimeSplitManifest:
    """정렬된 거래일(YYYY-MM-DD) 리스트로 train/validation/test 경계를 만든다.

    원칙:
    - test(OOS) 는 항상 *마지막* 구간 — selector/파라미터 선택에 절대 사용하지 않는다.
    - validation 은 과최적화 탐지용(중간 구간).
    - train 은 규칙/파라미터 개발용(앞 구간).
    - 구간 경계는 겹치지 않는다(거래일 인덱스로 분할).
    """
    days = sorted({str(d)[:10] for d in trading_days if str(d).strip()})
    n = len(days)
    if n < MIN_DAYS_FOR_SPLIT:
        return TimeSplitManifest(
            split_method=SPLIT_INSUFFICIENT, split_quality_status="FAIL",
            train_start=None, train_end=None, validation_start=None,
            validation_end=None, test_start=None, test_end=None,
            train_days=0, validation_days=0, test_days=0, total_days=n,
            reasons=(f"거래일 {n} < 분할 최소 {MIN_DAYS_FOR_SPLIT} — train/validation/test 분할 불가",),
        )

    # method 결정 (auto 는 데이터 길이로).
    if method == "auto":
        if n >= 400:
            method = SPLIT_2Y
        elif n >= 220:
            method = SPLIT_1Y
        else:
            method = SPLIT_MIN

    # 50/25/25 (거래일 인덱스 기준). 1년안도 동일 비율로 6/3/3 개월에 근사.
    train_end_idx = int(n * 0.50)
    val_end_idx = int(n * 0.75)
    train = days[:train_end_idx]
    val = days[train_end_idx:val_end_idx]
    test = days[val_end_idx:]

    status = "PASS"
    reasons: list[str] = []
    if n < 240:
        status = "WARN"
        reasons.append(f"거래일 {n} — 분할 가능하나 기간이 짧음(240일 미만)")
    if not (train and val and test):
        status = "FAIL"
        reasons.append("일부 구간이 비어 분할 불가")

    return TimeSplitManifest(
        split_method=method, split_quality_status=status,
        train_start=train[0] if train else None,
        train_end=train[-1] if train else None,
        validation_start=val[0] if val else None,
        validation_end=val[-1] if val else None,
        test_start=test[0] if test else None,
        test_end=test[-1] if test else None,
        train_days=len(train), validation_days=len(val), test_days=len(test),
        total_days=n, reasons=tuple(reasons),
    )


# ----------------------------------------------------------------------------
# 장세 구분 (Market Regime Label) — look-ahead 금지
# ----------------------------------------------------------------------------
UPTREND = "UPTREND"
DOWNTREND = "DOWNTREND"
SIDEWAYS = "SIDEWAYS"
HIGH_VOLATILITY = "HIGH_VOLATILITY"
GAP_UP_DAY = "GAP_UP_DAY"
GAP_DOWN_DAY = "GAP_DOWN_DAY"
CRASH_LIKE_DAY = "CRASH_LIKE_DAY"
LOW_LIQUIDITY_DAY = "LOW_LIQUIDITY_DAY"

# regime_primary 우선순위 (위험/이벤트성 → 추세 → 박스).
_REGIME_PRIORITY = (
    CRASH_LIKE_DAY, GAP_DOWN_DAY, GAP_UP_DAY, HIGH_VOLATILITY,
    DOWNTREND, UPTREND, LOW_LIQUIDITY_DAY, SIDEWAYS,
)

# 단순하고 설명 가능한 임계값 (formula 는 report 에 명시).
REGIME_FORMULA = (
    "lookback=20거래일. ret20=close/close[-20]-1. ma20 기울기=(ma20-ma20[-5])/ma20[-5]. "
    "vol20=최근 20일 일간수익률 표준편차. gap=(open-prev_close)/prev_close. "
    "day_ret=close/prev_close-1. vol_ratio=volume/median(최근 20일 volume). "
    "모든 지표는 해당 날짜까지의 정보만 사용(look-ahead 금지). "
    "임계: UPTREND ret20>+3%&slope>0 / DOWNTREND ret20<-3%&slope<0 / SIDEWAYS |ret20|<=3% / "
    "HIGH_VOLATILITY vol20>2.5% / GAP_UP gap>+2% / GAP_DOWN gap<-2% / "
    "CRASH_LIKE day_ret<-4% / LOW_LIQUIDITY vol_ratio<0.4."
)


@dataclass(frozen=True)
class RegimeDay:
    date: str
    market_proxy_return: float | None
    volatility: float | None
    gap: float | None
    regime_labels: tuple[str, ...]
    regime_primary: str
    confidence: float


@dataclass(frozen=True)
class RegimeManifest:
    proxy_kind: str                    # ETF_PROXY:069500 / EQUAL_WEIGHT_50 / UNAVAILABLE
    formula: str
    days: tuple[RegimeDay, ...]
    counts_by_regime: dict[str, int]
    regime_label_status: str           # OK / WARN / FAIL
    reasons: tuple[str, ...] = ()
    no_look_ahead: bool = True
    is_order_signal: bool = False
    contains_secret: bool = False

    def __post_init__(self) -> None:
        if not self.no_look_ahead or self.is_order_signal or self.contains_secret:
            raise ValueError("unsafe invariant on RegimeManifest")


@dataclass(frozen=True)
class DailyPoint:
    """장세 라벨용 일봉 proxy 포인트."""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def daily_from_intraday_bars(bars: list[Any]) -> list[DailyPoint]:
    """분봉(OHLCVBar) 리스트를 일봉 DailyPoint 로 집계 (KST 날짜 기준)."""
    by_day: dict[str, list[Any]] = {}
    for b in bars:
        ts = getattr(b, "timestamp", None)
        try:
            d = ts.astimezone(_KST).date().isoformat()
        except Exception:  # noqa: BLE001
            d = str(ts)[:10]
        if not d:
            continue
        by_day.setdefault(d, []).append(b)
    out: list[DailyPoint] = []
    for d in sorted(by_day):
        day_bars = sorted(by_day[d], key=lambda x: getattr(x, "timestamp", 0))
        opens = float(getattr(day_bars[0], "open", 0) or 0)
        close = float(getattr(day_bars[-1], "close", 0) or 0)
        high = max(float(getattr(x, "high", 0) or 0) for x in day_bars)
        low = min(float(getattr(x, "low", 0) or 0) for x in day_bars if (getattr(x, "low", 0) or 0) > 0)
        vol = sum(float(getattr(x, "volume", 0) or 0) for x in day_bars)
        out.append(DailyPoint(date=d, open=opens, high=high, low=low, close=close, volume=vol))
    return out


def build_equal_weight_proxy(symbol_to_daily: dict[str, list[DailyPoint]]) -> list[DailyPoint]:
    """종목별 일봉을 equal-weight 시장 proxy 일봉으로 합성 (ETF proxy 부재 시 대체).

    각 거래일의 종목 평균 OHLC/volume (정규화 없이 단순 평균 — proxy 방향성만 사용).
    """
    by_day: dict[str, list[DailyPoint]] = {}
    for daily in symbol_to_daily.values():
        for p in daily:
            by_day.setdefault(p.date, []).append(p)
    out: list[DailyPoint] = []
    for d in sorted(by_day):
        pts = by_day[d]
        n = len(pts)
        out.append(DailyPoint(
            date=d,
            open=sum(p.open for p in pts) / n,
            high=sum(p.high for p in pts) / n,
            low=sum(p.low for p in pts) / n,
            close=sum(p.close for p in pts) / n,
            volume=sum(p.volume for p in pts) / n,
        ))
    return out


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def label_market_regimes(daily: list[DailyPoint], *, proxy_kind: str = "EQUAL_WEIGHT_50") -> RegimeManifest:
    """일봉 proxy 로 날짜별 장세 라벨을 생성한다 (look-ahead 금지).

    각 날짜 i 의 라벨은 0..i 데이터만 사용 — 미래를 보지 않는다.
    """
    if not daily:
        return RegimeManifest(
            proxy_kind="UNAVAILABLE", formula=REGIME_FORMULA, days=(),
            counts_by_regime={}, regime_label_status="FAIL",
            reasons=("proxy 일봉이 없어 regime 라벨 생성 불가",))

    pts = sorted(daily, key=lambda p: p.date)
    closes = [p.close for p in pts]
    vols = [p.volume for p in pts]
    days_out: list[RegimeDay] = []
    counts: dict[str, int] = {}

    for i, p in enumerate(pts):
        prev_close = closes[i - 1] if i >= 1 else None
        gap = ((p.open - prev_close) / prev_close) if (prev_close and prev_close > 0) else None
        day_ret = ((p.close - prev_close) / prev_close) if (prev_close and prev_close > 0) else None

        ret20 = None
        slope = None
        vol20 = None
        vol_ratio = None
        warmup = i < 20

        if i >= 20 and closes[i - 20] > 0:
            ret20 = closes[i] / closes[i - 20] - 1.0
            window = closes[i - 20: i + 1]
            ma20_now = sum(window) / len(window)
            if i >= 25:
                window_prev = closes[i - 25: i - 4]
                ma20_prev = sum(window_prev) / len(window_prev)
                if ma20_prev > 0:
                    slope = (ma20_now - ma20_prev) / ma20_prev
            rets = [closes[j] / closes[j - 1] - 1.0 for j in range(i - 19, i + 1) if closes[j - 1] > 0]
            if len(rets) >= 2:
                vol20 = statistics.pstdev(rets)
            med_vol = statistics.median(vols[i - 20: i + 1])
            if med_vol > 0:
                vol_ratio = p.volume / med_vol

        labels: list[str] = []
        if ret20 is not None and slope is not None:
            if ret20 > 0.03 and slope > 0:
                labels.append(UPTREND)
            elif ret20 < -0.03 and slope < 0:
                labels.append(DOWNTREND)
            elif abs(ret20) <= 0.03:
                labels.append(SIDEWAYS)
        if vol20 is not None and vol20 > 0.025:
            labels.append(HIGH_VOLATILITY)
        if gap is not None and gap > 0.02:
            labels.append(GAP_UP_DAY)
        if gap is not None and gap < -0.02:
            labels.append(GAP_DOWN_DAY)
        if day_ret is not None and day_ret < -0.04:
            labels.append(CRASH_LIKE_DAY)
        if vol_ratio is not None and vol_ratio < 0.4:
            labels.append(LOW_LIQUIDITY_DAY)
        if not labels:
            labels.append(SIDEWAYS)

        primary = next((r for r in _REGIME_PRIORITY if r in labels), SIDEWAYS)

        # confidence: 임계 초과 margin 기반 단순 추정, warmup 은 감점.
        conf = 0.5
        if primary == CRASH_LIKE_DAY and day_ret is not None:
            conf = _clamp01(0.5 + abs(day_ret + 0.04) * 5)
        elif primary in (GAP_UP_DAY, GAP_DOWN_DAY) and gap is not None:
            conf = _clamp01(0.4 + (abs(gap) - 0.02) * 10)
        elif primary == HIGH_VOLATILITY and vol20 is not None:
            conf = _clamp01(0.4 + (vol20 - 0.025) * 10)
        elif primary in (UPTREND, DOWNTREND) and ret20 is not None:
            conf = _clamp01(0.4 + (abs(ret20) - 0.03) * 5)
        if warmup:
            conf = min(conf, 0.25)

        counts[primary] = counts.get(primary, 0) + 1
        days_out.append(RegimeDay(
            date=p.date,
            market_proxy_return=round(day_ret, 6) if day_ret is not None else None,
            volatility=round(vol20, 6) if vol20 is not None else None,
            gap=round(gap, 6) if gap is not None else None,
            regime_labels=tuple(labels),
            regime_primary=primary,
            confidence=round(conf, 3),
        ))

    warmup_days = sum(1 for d in days_out if d.confidence <= 0.25)
    status = "OK"
    reasons: list[str] = []
    if len(days_out) < 60:
        status = "WARN"
        reasons.append(f"proxy 일수 {len(days_out)} — regime 표본이 짧음")
    if warmup_days:
        reasons.append(f"warmup(이력<20일) {warmup_days}일은 confidence 낮음")
    return RegimeManifest(
        proxy_kind=proxy_kind, formula=REGIME_FORMULA, days=tuple(days_out),
        counts_by_regime=counts, regime_label_status=status, reasons=tuple(reasons))


# ----------------------------------------------------------------------------
# 데이터 품질 검증 (종목별 / 그룹별)
# ----------------------------------------------------------------------------
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass(frozen=True)
class DatasetQualityReport:
    status: str                         # PASS / WARN / FAIL
    symbol_count: int
    symbols_present: int
    per_symbol: tuple[dict[str, Any], ...]
    group_quality: dict[str, dict[str, Any]]
    total_bars: int
    trading_days: int
    start_date: str | None
    end_date: str | None
    split_feasible: bool
    regime_feasible: bool
    reasons: tuple[str, ...] = ()
    is_order_signal: bool = False
    contains_secret: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal or self.contains_secret:
            raise ValueError("unsafe invariant True on DatasetQualityReport")


def evaluate_dataset_quality(
    group_manifest: SymbolGroupManifest,
    symbol_to_bars: dict[str, list[Any]],
    *,
    min_days_pass: int = MIN_DAYS_FOR_PASS,
) -> DatasetQualityReport:
    """종목군 manifest + 적재된 bars 로 데이터셋 품질을 종합 판정한다 (read-only).

    symbol_to_bars: {symbol: [OHLCVBar, ...]}. 누락 종목은 present=False.
    """
    per_symbol: list[dict[str, Any]] = []
    total_bars = 0
    all_dates: set[str] = set()
    present = 0
    fail_count = 0

    group_of = {e.symbol: e.group for e in group_manifest.entries}

    for sym in group_manifest.all_symbols():
        bars = symbol_to_bars.get(sym) or []
        grp = group_of.get(sym, "")
        if not bars:
            per_symbol.append({
                "symbol": sym, "group": grp, "present": False, "status": "MISSING",
                "bar_count": 0, "day_count": 0, "bars_per_day": 0.0,
                "bar_size_minutes": None, "reasons": ["수집되지 않음"]})
            continue
        present += 1
        q = check_intraday_quality(bars, min_bars=100, min_days=20)
        for b in bars:
            ts = getattr(b, "timestamp", None)
            try:
                all_dates.add(ts.astimezone(_KST).date().isoformat())
            except Exception:  # noqa: BLE001
                d = str(ts)[:10]
                if d:
                    all_dates.add(d)
        total_bars += q.bar_count
        if q.status == FAIL:
            fail_count += 1
        per_symbol.append({
            "symbol": sym, "group": grp, "present": True, "status": q.status,
            "bar_count": q.bar_count, "day_count": q.day_count,
            "bars_per_day": q.bars_per_day, "bar_size_minutes": q.bar_size_minutes,
            "intraday_detected": q.intraday_detected,
            "reasons": list(q.reasons)})

    group_quality: dict[str, dict[str, Any]] = {}
    for g in GROUP_ORDER:
        present_syms = [p for p in per_symbol if p["group"] == g and p["present"]]
        days_list = [p["day_count"] for p in present_syms]
        group_quality[g] = {
            "expected": group_manifest.counts_by_group.get(g, 0),
            "present": len(present_syms),
            "median_days": round(statistics.median(days_list), 1) if days_list else 0,
            "min_days": min(days_list) if days_list else 0,
            "max_days": max(days_list) if days_list else 0,
        }

    trading_days = len(all_dates)
    start_date = min(all_dates) if all_dates else None
    end_date = max(all_dates) if all_dates else None
    days_each = [p["day_count"] for p in per_symbol if p["present"]]
    median_days = statistics.median(days_each) if days_each else 0
    split_feasible = trading_days >= MIN_DAYS_FOR_SPLIT
    regime_feasible = trading_days >= 60

    reasons: list[str] = []
    if present == 0:
        status = FAIL
        reasons.append("수집된 종목이 0개 — 5분봉 데이터 부족")
    elif fail_count > present * 0.3:
        status = FAIL
        reasons.append(f"품질 FAIL 종목 {fail_count}/{present} — 다수 종목 품질 불량")
    elif not split_feasible:
        status = WARN
        reasons.append(f"거래일 {trading_days} < {MIN_DAYS_FOR_SPLIT} — split 생성 불가/짧음")
    elif median_days < min_days_pass or present < MIN_SYMBOLS_FOR_READY:
        status = WARN
        reasons.append(
            f"중앙 거래일 {median_days} / 종목 {present} — 일부 부족(대부분 {min_days_pass}일·"
            f"{MIN_SYMBOLS_FOR_READY}종목 권장)")
    else:
        status = PASS

    if not split_feasible:
        reasons.append("train/validation/test 분할 불가 — 거래일 부족")

    return DatasetQualityReport(
        status=status, symbol_count=group_manifest.total_symbols, symbols_present=present,
        per_symbol=tuple(per_symbol), group_quality=group_quality,
        total_bars=total_bars, trading_days=trading_days,
        start_date=start_date, end_date=end_date,
        split_feasible=split_feasible, regime_feasible=regime_feasible,
        reasons=tuple(reasons))


# ----------------------------------------------------------------------------
# Dataset Manifest
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class DatasetManifest:
    dataset_name: str
    created_at: str
    provider: str
    requested_period: str
    actual_period: str
    bar_intervals_available: tuple[str, ...]
    symbol_count: int
    symbols_by_group: dict[str, list[str]]
    total_bars_5m: int
    total_bars_1m_subset: int
    start_date: str | None
    end_date: str | None
    trading_days: int
    data_quality_status: str
    time_split_status: str
    regime_label_status: str
    one_minute_availability: str        # AVAILABLE / PARTIAL / UNAVAILABLE
    ready_for_robust_backtest: bool
    warnings: tuple[str, ...]
    next_recommended_task: str
    # 안전 불변값.
    do_not_auto_apply: bool = True
    no_profit_guarantee: bool = True
    live_trading_recommendation: bool = False
    real_order_allowed: bool = False
    is_live_authorization: bool = False
    is_order_signal: bool = False
    kis_order_api_called: bool = False
    broker_order_sent: bool = False
    exe_build_executed: bool = False
    contains_secret: bool = False

    def __post_init__(self) -> None:
        bad = (
            self.live_trading_recommendation or self.real_order_allowed
            or self.is_live_authorization or self.is_order_signal
            or self.kis_order_api_called or self.broker_order_sent
            or self.exe_build_executed or self.contains_secret
        )
        if bad or not self.do_not_auto_apply or not self.no_profit_guarantee:
            raise ValueError("unsafe invariant on DatasetManifest")


def build_dataset_manifest(
    *,
    group_manifest: SymbolGroupManifest,
    quality: DatasetQualityReport,
    time_split: TimeSplitManifest,
    regime: RegimeManifest,
    requested_period: str,
    one_minute_availability: str,
    total_bars_1m_subset: int = 0,
    provider: str = "KIS_INTRADAY_READONLY",
    created_at: str | None = None,
) -> DatasetManifest:
    """수집/품질/분할/regime 결과를 종합한 dataset manifest 를 만든다 (순수).

    ready_for_robust_backtest 기준:
    - 5분봉 품질 PASS 또는 WARN
    - train/validation/test split 생성 가능
    - 최소 30종목 이상
    - 최소 200거래일 이상
    - regime label 생성 가능
    """
    intervals: list[str] = []
    if quality.symbols_present > 0:
        intervals.append("5m")
    if total_bars_1m_subset > 0:
        intervals.append("1m_subset")

    warnings: list[str] = []
    warnings.extend(quality.reasons)
    warnings.extend(time_split.reasons)
    warnings.extend(regime.reasons)

    ready = (
        quality.status in (PASS, WARN)
        and time_split.split_quality_status in (PASS, WARN)
        and quality.symbols_present >= MIN_SYMBOLS_FOR_READY
        and quality.trading_days >= MIN_TRADING_DAYS_FOR_READY
        and regime.regime_label_status in ("OK", "WARN")
    )

    if ready:
        next_task = (
            "robust dataset 준비 완료 — 후속 작업에서 train/validation/test 분할로 "
            "고정 룰 검증(별도 작업, 본 작업은 수집/메타데이터만)."
        )
    else:
        missing: list[str] = []
        if quality.status == FAIL:
            missing.append("5분봉 품질")
        if time_split.split_quality_status == "FAIL":
            missing.append("시간 분할")
        if quality.symbols_present < MIN_SYMBOLS_FOR_READY:
            missing.append(f"종목 수(현재 {quality.symbols_present})")
        if quality.trading_days < MIN_TRADING_DAYS_FOR_READY:
            missing.append(f"거래일(현재 {quality.trading_days})")
        next_task = "추가 수집 필요 — 미충족: " + (", ".join(missing) if missing else "재수집/재검증")

    actual_period = (
        f"{quality.start_date or '?'} ~ {quality.end_date or '?'} ({quality.trading_days} 거래일)"
    )

    return DatasetManifest(
        dataset_name=DATASET_NAME,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        provider=provider,
        requested_period=requested_period,
        actual_period=actual_period,
        bar_intervals_available=tuple(intervals),
        symbol_count=group_manifest.total_symbols,
        symbols_by_group=group_manifest.symbols_by_group,
        total_bars_5m=quality.total_bars,
        total_bars_1m_subset=total_bars_1m_subset,
        start_date=quality.start_date,
        end_date=quality.end_date,
        trading_days=quality.trading_days,
        data_quality_status=quality.status,
        time_split_status=time_split.split_quality_status,
        regime_label_status=regime.regime_label_status,
        one_minute_availability=one_minute_availability,
        ready_for_robust_backtest=ready,
        warnings=tuple(warnings),
        next_recommended_task=next_task,
    )


# ----------------------------------------------------------------------------
# to_dict 직렬화 helper
# ----------------------------------------------------------------------------
def symbol_group_to_dict(m: SymbolGroupManifest) -> dict[str, Any]:
    return {
        "total_symbols": m.total_symbols,
        "counts_by_group": m.counts_by_group,
        "symbols_by_group": m.symbols_by_group,
        "entries": [
            {"symbol": e.symbol, "name": e.name, "group": e.group, "reason": e.reason}
            for e in m.entries
        ],
        "etf_proxy_note": m.etf_proxy_note,
        "is_order_signal": m.is_order_signal,
        "is_investment_advice": m.is_investment_advice,
        "contains_secret": m.contains_secret,
    }


def time_split_to_dict(s: TimeSplitManifest) -> dict[str, Any]:
    return {
        "split_method": s.split_method,
        "split_quality_status": s.split_quality_status,
        "train_start": s.train_start, "train_end": s.train_end,
        "validation_start": s.validation_start, "validation_end": s.validation_end,
        "test_start": s.test_start, "test_end": s.test_end,
        "train_days": s.train_days, "validation_days": s.validation_days,
        "test_days": s.test_days, "total_days": s.total_days,
        "test_used_for_selection": s.test_used_for_selection,
        "reasons": list(s.reasons),
        "is_order_signal": s.is_order_signal, "contains_secret": s.contains_secret,
    }


def regime_to_dict(r: RegimeManifest, *, include_days: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "proxy_kind": r.proxy_kind,
        "formula": r.formula,
        "counts_by_regime": r.counts_by_regime,
        "regime_label_status": r.regime_label_status,
        "day_count": len(r.days),
        "no_look_ahead": r.no_look_ahead,
        "reasons": list(r.reasons),
        "is_order_signal": r.is_order_signal, "contains_secret": r.contains_secret,
    }
    if include_days:
        out["days"] = [
            {"date": d.date, "market_proxy_return": d.market_proxy_return,
             "volatility": d.volatility, "gap": d.gap,
             "regime_labels": list(d.regime_labels), "regime_primary": d.regime_primary,
             "confidence": d.confidence}
            for d in r.days
        ]
    return out


def quality_to_dict(q: DatasetQualityReport) -> dict[str, Any]:
    return {
        "status": q.status, "symbol_count": q.symbol_count,
        "symbols_present": q.symbols_present, "total_bars": q.total_bars,
        "trading_days": q.trading_days, "start_date": q.start_date, "end_date": q.end_date,
        "split_feasible": q.split_feasible, "regime_feasible": q.regime_feasible,
        "group_quality": q.group_quality,
        "per_symbol": list(q.per_symbol),
        "reasons": list(q.reasons),
        "is_order_signal": q.is_order_signal, "contains_secret": q.contains_secret,
    }


def manifest_to_dict(m: DatasetManifest) -> dict[str, Any]:
    return {
        "dataset_name": m.dataset_name, "created_at": m.created_at, "provider": m.provider,
        "requested_period": m.requested_period, "actual_period": m.actual_period,
        "bar_intervals_available": list(m.bar_intervals_available),
        "symbol_count": m.symbol_count, "symbols_by_group": m.symbols_by_group,
        "total_bars_5m": m.total_bars_5m, "total_bars_1m_subset": m.total_bars_1m_subset,
        "start_date": m.start_date, "end_date": m.end_date, "trading_days": m.trading_days,
        "data_quality_status": m.data_quality_status, "time_split_status": m.time_split_status,
        "regime_label_status": m.regime_label_status,
        "one_minute_availability": m.one_minute_availability,
        "ready_for_robust_backtest": m.ready_for_robust_backtest,
        "warnings": list(m.warnings), "next_recommended_task": m.next_recommended_task,
        "do_not_auto_apply": m.do_not_auto_apply, "no_profit_guarantee": m.no_profit_guarantee,
        "live_trading_recommendation": m.live_trading_recommendation,
        "real_order_allowed": m.real_order_allowed,
        "is_live_authorization": m.is_live_authorization, "is_order_signal": m.is_order_signal,
        "kis_order_api_called": m.kis_order_api_called, "broker_order_sent": m.broker_order_sent,
        "exe_build_executed": m.exe_build_executed, "contains_secret": m.contains_secret,
    }
