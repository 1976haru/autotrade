"""#3-09: 실제 데이터 백테스트 — *확장 가능한 universe + 필터 정책* skeleton.

본 모듈은 *현재 10종 sample → 향후 거래대금/유동성 상위 50/100/300종*
확장을 위한 **구조 정의** 만 제공한다. 실제 데이터 수집 (KRX listing /
거래대금 / 유동성 API) 은 후속 *별도 opt-in PR* 에서 wiring — 본 PR 시점에
LIQUIDITY_TOP* 옵션은 명시적 데이터 source 가 제공될 때만 동작.

## 핵심 정책

1. **10종 sample 은 1차 기능 검증용** — 실제 운용 후보 식별이 아니라
   *백테스트 / 최적화 / Walk-forward / Stress test 파이프라인 동작 확인*.
2. **최종 검증은 *필터 통과 종목* 대상** — 거래정지 / 관리종목 / ETF/ETN/
   SPAC / 신규상장 / 거래량 부족 / 데이터 결측 과다는 *모두 제외*.
3. **전체 종목 무조건 실행은 *왜곡 위험*** — 저유동성 / 상장폐지 / 데이터
   결측 종목이 결과를 오염시킬 수 있다. 옵트인 + 필터 통과 강제.
4. **유동성 상위 50 → 100 → 300 순서로 *단계적* 확장** — 한 번에 전체 점프
   금지. 50종 운용 결과를 운영자가 검토한 뒤 100/300 로 옵트인.
5. **후보가 없으면 *억지로 만들지 않는다*** — `paper_candidate_aggregator`
   (#3-07) 가 이미 `reasons_no_candidate` carry. 본 모듈은 빈 universe
   반환 가능.

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. **본 모듈은 *결정론적 universe resolver*** — 백테스트 결과 / 주문 신호 0건.
2. **broker / OrderExecutor / route_order import 0건** — 정적 grep 가드.
3. **외부 HTTP / AI SDK / KIS API 호출 0건** — universe 데이터 source 가
   *주입형* (`liquidity_source` callable). 본 모듈은 fetch 자체 수행 X.
4. **DB write 0건**.
5. **secret / API key / 계좌번호 carry 0건** — UniverseFilterPolicy / Universe
   결과에 그런 필드 존재 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from app.backtest.real_data.symbols import (
    REPRESENTATIVE_SYMBOLS,
    representative_symbol_codes,
)


# ─────────────────────────────────────────────────────────────────────────────
# Universe kind enum — CLI `--universe` 옵션과 1:1 매핑
# ─────────────────────────────────────────────────────────────────────────────


class UniverseKind(StrEnum):
    """선택 가능한 universe 종류 — *주문 방향* 값 0개.

    - `SAMPLE10`         — 1차 검증용 (`REPRESENTATIVE_SYMBOLS`, 즉시 사용 가능)
    - `LIQUIDITY_TOP50`  — 거래대금 상위 50종 (데이터 source 필요 — opt-in)
    - `LIQUIDITY_TOP100` — 거래대금 상위 100종 (데이터 source 필요)
    - `LIQUIDITY_TOP300` — 거래대금 상위 300종 (운영자 별도 검토 후 사용)
    - `CUSTOM`           — `--symbols` 로 운영자가 명시 지정
    """
    SAMPLE10         = "sample10"
    LIQUIDITY_TOP50  = "liquidity_top50"
    LIQUIDITY_TOP100 = "liquidity_top100"
    LIQUIDITY_TOP300 = "liquidity_top300"
    CUSTOM           = "custom"


# 거래대금 universe kind 매트릭스 — kind → 요구 top N.
_LIQUIDITY_TOP_N: dict[UniverseKind, int] = {
    UniverseKind.LIQUIDITY_TOP50:  50,
    UniverseKind.LIQUIDITY_TOP100: 100,
    UniverseKind.LIQUIDITY_TOP300: 300,
}


def is_liquidity_kind(kind: UniverseKind) -> bool:
    return kind in _LIQUIDITY_TOP_N


def liquidity_top_n(kind: UniverseKind) -> int | None:
    return _LIQUIDITY_TOP_N.get(kind)


# ─────────────────────────────────────────────────────────────────────────────
# 종목 필터 정책 — CLI 옵션과 1:1 매핑
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SymbolFilterPolicy:
    """종목 필터 정책 — 운영자가 CLI 로 조정 가능한 임계값 집합.

    *advisory only* — 본 정책은 universe 선정 기준일 뿐, 주문 결정에는
    사용되지 *않는다*.
    """

    # 최소 평균 거래량 (주식 수) — 0 = 비활성.
    min_avg_volume:         int   = 0
    # 최소 평균 거래대금 KRW — 0 = 비활성.
    min_avg_trading_value:  int   = 0
    # 거래정지 / 관리 / ETF/ETN / SPAC 제외 (default = 제외).
    exclude_suspended:      bool  = True
    exclude_managed:        bool  = True
    exclude_etf_etn:        bool  = True
    exclude_spac:           bool  = True
    # 상장일 minimum days — 0 = 비활성. default 180일 (6개월).
    min_listed_days:        int   = 180
    # 데이터 결측 비율 max — 0~1, 1.0 = 비활성. default 0.05 (5%).
    max_missing_ratio:      float = 0.05

    def __post_init__(self) -> None:
        if self.min_avg_volume < 0:
            raise ValueError("min_avg_volume must be >= 0")
        if self.min_avg_trading_value < 0:
            raise ValueError("min_avg_trading_value must be >= 0")
        if self.min_listed_days < 0:
            raise ValueError("min_listed_days must be >= 0")
        if not (0.0 <= self.max_missing_ratio <= 1.0):
            raise ValueError(
                f"max_missing_ratio must be in [0,1], got {self.max_missing_ratio}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_avg_volume":         int(self.min_avg_volume),
            "min_avg_trading_value":  int(self.min_avg_trading_value),
            "exclude_suspended":      bool(self.exclude_suspended),
            "exclude_managed":        bool(self.exclude_managed),
            "exclude_etf_etn":        bool(self.exclude_etf_etn),
            "exclude_spac":           bool(self.exclude_spac),
            "min_listed_days":        int(self.min_listed_days),
            "max_missing_ratio":      float(self.max_missing_ratio),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Universe resolver — symbols 결정 + filter 적용
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UniverseResolution:
    """resolver 결과 — symbols 리스트 + 적용된 정책 / 메타데이터."""

    kind:                  UniverseKind
    symbols:               list[str]
    policy:                SymbolFilterPolicy
    requested_top_n:       int | None = None
    available_before_filter: int      = 0
    excluded_reasons:      dict[str, int] = field(default_factory=dict)
    operator_note:         str | None = None
    advisory_disclaimer:   str = (
        "본 universe 는 *advisory* 백테스트 후보 — 주문 신호 / 실거래 활성화 "
        "0건. 후보가 없으면 억지로 만들지 않음 (빈 list 가능)."
    )

    @property
    def symbol_count(self) -> int:
        return len(self.symbols)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind":                    self.kind.value,
            "symbol_count":            self.symbol_count,
            "symbols":                 list(self.symbols),
            "policy":                  self.policy.to_dict(),
            "requested_top_n":         self.requested_top_n,
            "available_before_filter": int(self.available_before_filter),
            "excluded_reasons":        dict(self.excluded_reasons),
            "operator_note":           self.operator_note,
            "advisory_disclaimer":     self.advisory_disclaimer,
            # advisory invariant — JSON consumer 안전.
            "is_order_signal":         False,
            "auto_apply_allowed":      False,
            "is_live_authorization":   False,
        }


class UniverseDataNotAvailableError(RuntimeError):
    """LIQUIDITY_TOP* kind 요청 시 데이터 source 가 주입되지 않은 경우.

    *현재 본 PR 시점 default* — 운영자가 별도 PR 에서 KRX listing / 거래대금
    feed wiring 시점에만 사용 가능.
    """


@dataclass(frozen=True)
class SymbolMeta:
    """liquidity source 가 반환하는 종목 메타데이터 — universe filter 입력.

    *secret 필드 0건* — API key / 계좌번호 carry 0개 (테스트로 lock).
    """

    symbol:               str
    avg_volume:           int   = 0
    avg_trading_value:    int   = 0    # KRW
    is_suspended:         bool  = False
    is_managed:           bool  = False
    is_etf_or_etn:        bool  = False
    is_spac:              bool  = False
    listed_days:          int   = 9999
    missing_ratio:        float = 0.0


# liquidity_source signature — 운영자가 별도 PR 에서 KRX feed 로 wiring.
# Returns: kind 의 요구 top N 까지 (sorted by trading value desc) 의 SymbolMeta list.
LiquiditySource = Callable[[UniverseKind, int], list[SymbolMeta]]


def _apply_filter(
    metas: list[SymbolMeta], policy: SymbolFilterPolicy,
) -> tuple[list[SymbolMeta], dict[str, int]]:
    """단일 종목 메타 list → 통과 list + 사유별 제외 카운트."""
    passed: list[SymbolMeta] = []
    counts: dict[str, int] = {}

    def _drop(reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    for m in metas:
        if policy.exclude_suspended and m.is_suspended:
            _drop("suspended")
            continue
        if policy.exclude_managed and m.is_managed:
            _drop("managed")
            continue
        if policy.exclude_etf_etn and m.is_etf_or_etn:
            _drop("etf_or_etn")
            continue
        if policy.exclude_spac and m.is_spac:
            _drop("spac")
            continue
        if policy.min_listed_days > 0 and m.listed_days < policy.min_listed_days:
            _drop("listed_less_than_min_days")
            continue
        if policy.max_missing_ratio < 1.0 and m.missing_ratio > policy.max_missing_ratio:
            _drop("missing_ratio_too_high")
            continue
        if policy.min_avg_volume > 0 and m.avg_volume < policy.min_avg_volume:
            _drop("low_avg_volume")
            continue
        if policy.min_avg_trading_value > 0 and m.avg_trading_value < policy.min_avg_trading_value:
            _drop("low_avg_trading_value")
            continue
        passed.append(m)
    return passed, counts


def resolve_universe(
    kind:               UniverseKind,
    *,
    policy:             SymbolFilterPolicy | None = None,
    custom_symbols:     list[str]   | None      = None,
    liquidity_source:   LiquiditySource | None  = None,
) -> UniverseResolution:
    """universe kind + 정책 → resolved symbols.

    Args:
        kind:             SAMPLE10 / LIQUIDITY_TOP* / CUSTOM.
        policy:           필터 정책. None = `SymbolFilterPolicy()` (default).
        custom_symbols:   `kind=CUSTOM` 일 때 운영자 명시 지정.
        liquidity_source: `kind=LIQUIDITY_TOP*` 에서 종목 메타 공급 callable.
                          None 이면 `UniverseDataNotAvailableError` raise.

    Returns:
        UniverseResolution — symbols + 메타 + 제외 사유 카운트 carry.

    Raises:
        UniverseDataNotAvailableError — LIQUIDITY_TOP* + `liquidity_source` 미제공.
        ValueError — `kind=CUSTOM` 인데 `custom_symbols` 미제공.
    """
    pol = policy or SymbolFilterPolicy()

    if kind == UniverseKind.SAMPLE10:
        # sample10 은 이미 검증된 10종 — 필터 적용하지 않음 (운영자 의도).
        symbols = representative_symbol_codes()
        return UniverseResolution(
            kind=kind,
            symbols=symbols,
            policy=pol,
            available_before_filter=len(symbols),
            operator_note=(
                "SAMPLE10 — 1차 기능 검증용 (REPRESENTATIVE_SYMBOLS). "
                "최종 운용 후보 식별이 아닙니다. 필터 통과 종목 대상은 "
                "LIQUIDITY_TOP50 → 100 → 300 으로 단계적 확장."
            ),
        )

    if kind == UniverseKind.CUSTOM:
        if not custom_symbols:
            raise ValueError(
                "kind=CUSTOM requires custom_symbols list."
            )
        # 6-digit 검증.
        cleaned: list[str] = []
        excluded: dict[str, int] = {}
        for sym in custom_symbols:
            s = str(sym).strip()
            if len(s) == 6 and s.isdigit():
                cleaned.append(s)
            else:
                excluded["invalid_symbol_format"] = \
                    excluded.get("invalid_symbol_format", 0) + 1
        return UniverseResolution(
            kind=kind,
            symbols=cleaned,
            policy=pol,
            available_before_filter=len(custom_symbols),
            excluded_reasons=excluded,
            operator_note=(
                f"CUSTOM — 운영자 명시 지정 {len(cleaned)} 종. "
                "필터 정책은 본 list 에 자동 적용되지 않음 (운영자 책임)."
            ),
        )

    if is_liquidity_kind(kind):
        top_n = liquidity_top_n(kind) or 0
        if liquidity_source is None:
            raise UniverseDataNotAvailableError(
                f"{kind.value}: liquidity_source callable required. "
                "본 PR 시점에는 거래대금/유동성 데이터 feed 가 *별도 opt-in PR* 로 "
                "wiring 됩니다. 본 옵션을 사용하려면 KRX listing / volume / "
                "trading_value feed 를 주입하는 `liquidity_source` 콜백을 "
                "전달해야 합니다 (운영자 명시 PR 필요)."
            )
        metas = list(liquidity_source(kind, top_n))
        before = len(metas)
        passed, counts = _apply_filter(metas, pol)
        # 필터 후 trading_value 내림차순으로 정렬 — 운영자가 stable order 기대.
        passed_sorted = sorted(
            passed, key=lambda m: m.avg_trading_value, reverse=True,
        )[:top_n]
        symbols = [m.symbol for m in passed_sorted]
        return UniverseResolution(
            kind=kind,
            symbols=symbols,
            policy=pol,
            requested_top_n=top_n,
            available_before_filter=before,
            excluded_reasons=counts,
            operator_note=(
                f"{kind.value} — 필터 통과 {len(symbols)}/{before}종. "
                "유동성 상위 50 → 100 → 300 *단계적 확장* 정책. "
                "후보가 없으면 빈 list (억지 생성 X)."
            ),
        )

    # 알 수 없는 kind — 빈 universe.
    return UniverseResolution(
        kind=kind,
        symbols=[],
        policy=pol,
        operator_note=f"unknown universe kind: {kind!r}",
    )


# 운영자 안전 helper — sample10 의 hard-coded count carry.
SAMPLE10_SIZE = len(REPRESENTATIVE_SYMBOLS)


__all__ = [
    "LiquiditySource",
    "SAMPLE10_SIZE",
    "SymbolFilterPolicy",
    "SymbolMeta",
    "UniverseDataNotAvailableError",
    "UniverseKind",
    "UniverseResolution",
    "is_liquidity_kind",
    "liquidity_top_n",
    "resolve_universe",
]
