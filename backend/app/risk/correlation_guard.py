"""Correlation Guard (#78) — 동일 sector / theme 종목 과다 보유 방지.

같은 테마가 급락하면 보유 종목들이 *동시에* 손실로 들어갈 수 있다. 본 모듈은
신규 BUY가 sector / theme 익스포저를 *너무 집중*시키지 않도록 pre-trade
guard로 작동한다.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / 외부 HTTP / AI provider import 0건.
- DB write 0건 — read-only 평가 + (선택) read-only SELECT 만.
- *신규 BUY 만 제한* — SELL/EXIT은 *리스크 축소* 목적이므로 본 가드가
  허용한다 (정책 invariant).
- RiskManager 의 *하위 pre-trade guard* 로만 사용 — 본 모듈은 RiskManager /
  PermissionGate / OrderExecutor 우회 0건. 상위 가드 체인을 *대체*하지 않고
  *보강*한다.

초기 구현 (본 PR):
- **sector / theme 태그 기반** 단순 한도 검사.
- 후속 PR에서 MarketBar 수익률 correlation 기반 확장 가능 (`compute_return_correlation`
  helper 자리).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ---------- enums / DTOs ----------


class CorrelationGuardVerdict(StrEnum):
    """4단계 판정.

    - PASS         : 정책 임계 통과, 정상 진행.
    - WARN         : 임계 근접 — 운영자 surface 권장.
    - REJECT       : BUY 차단.
    - SKIP_NON_BUY : SELL/EXIT 등 위험 축소 주문 — 가드 우회.
    """
    PASS         = "PASS"
    WARN         = "WARN"
    REJECT       = "REJECT"
    SKIP_NON_BUY = "SKIP_NON_BUY"


@dataclass(frozen=True)
class CorrelationGuardPolicy:
    """가드 임계.

    0 또는 빈 값은 *해당 검사 비활성*. 모든 한도는 *상한* — 운영자가 RiskPolicy
    어댑터로 보수적으로 set.
    """
    # 섹터별 한도.
    max_symbols_per_sector:        int = 0     # 0=무제한
    max_sector_exposure:           int = 0     # 절대 노출 (KRW) — 0=비활성
    max_sector_exposure_pct:       float = 0.0 # equity 대비 (0.30 = 30%)

    # 테마별 한도.
    max_symbols_per_theme:         int = 0
    max_theme_exposure:            int = 0
    max_theme_exposure_pct:        float = 0.0

    # WARN 임계 — REJECT 임계의 N% 이상이면 WARN.
    warn_ratio:                    float = 0.8

    # 후속 PR — 상관계수 한도 (현 PR에서는 helper만).
    max_pairwise_correlation:      float = 0.0   # 0=비활성
    correlation_lookback_bars:     int   = 0


@dataclass(frozen=True)
class SymbolMeta:
    """평가 대상 종목의 sector / theme 메타.

    `sector`는 WatchlistItem.sector 등에서 carry. `themes`는 ThemeSignal
    related_symbols → 역인덱스에서 carry.

    sector/themes 미지정 시 빈 문자열 / 빈 튜플 — 검사 통과 (PASS).
    """
    symbol:   str
    sector:   str = ""
    themes:   tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class HeldPosition:
    """현 보유 종목 + 명목 노출 (KRW).

    `meta.sector` / `meta.themes` 가 없으면 sector/theme exposure 계산에서
    *제외* (집계되지 않음 — 보수적으로 BUY 차단을 약화하지 않도록).
    """
    meta:        SymbolMeta
    notional:    int       # 절대 노출 (KRW)


@dataclass(frozen=True)
class CandidateOrder:
    """평가 대상 주문 (신규 또는 추가 매수)."""
    symbol:     str
    side:       str     # "BUY" | "SELL"
    notional:   int     # 의도 주문 금액 (KRW)
    meta:       SymbolMeta


@dataclass(frozen=True)
class CorrelationGuardInput:
    """가드 평가 입력 — 외부 시스템 영향 0건."""
    candidate:        CandidateOrder
    held_positions:   tuple[HeldPosition, ...] = field(default_factory=tuple)
    equity_krw:       int = 0


@dataclass
class CorrelationGuardResult:
    """가드 평가 결과.

    invariants (코드 단 강제):
    - `is_order_signal=False` 항상.
    - `auto_apply_allowed=False` 항상 — *제안*만.
    """
    verdict:                 CorrelationGuardVerdict
    blocked_reasons:         list[str] = field(default_factory=list)
    warnings:                list[str] = field(default_factory=list)
    sector_exposure:         dict[str, int]      = field(default_factory=dict)
    theme_exposure:          dict[str, int]      = field(default_factory=dict)
    projected_sector:        str | None          = None
    projected_themes:        list[str]           = field(default_factory=list)
    projected_sector_exposure: int               = 0
    projected_theme_exposure:  dict[str, int]    = field(default_factory=dict)
    sector_symbol_count:     int                 = 0
    projected_sector_symbol_count: int           = 0
    is_order_signal:         bool = False
    auto_apply_allowed:      bool = False
    generated_at:            datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError(
                "CorrelationGuardResult.is_order_signal must be False — "
                "guard does not produce BUY/SELL/HOLD signals."
            )
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "CorrelationGuardResult.auto_apply_allowed must be False — "
                "guard outputs are advisory only."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":                 self.verdict.value,
            "blocked_reasons":         list(self.blocked_reasons),
            "warnings":                list(self.warnings),
            "sector_exposure":         dict(self.sector_exposure),
            "theme_exposure":          dict(self.theme_exposure),
            "projected_sector":        self.projected_sector,
            "projected_themes":        list(self.projected_themes),
            "projected_sector_exposure": self.projected_sector_exposure,
            "projected_theme_exposure":  dict(self.projected_theme_exposure),
            "sector_symbol_count":     self.sector_symbol_count,
            "projected_sector_symbol_count": self.projected_sector_symbol_count,
            "is_order_signal":         self.is_order_signal,
            "auto_apply_allowed":      self.auto_apply_allowed,
            "live_flag_changed":       False,
            "mode_changed":            False,
            "generated_at":            self.generated_at.isoformat(),
        }


# ---------- rule ----------


@dataclass(frozen=True)
class CorrelationGuardRule:
    """sector / theme 익스포저 한도를 BUY 사전 검사하는 rule.

    SELL/EXIT 은 *리스크 축소* 목적이므로 invariant상 SKIP_NON_BUY 로 통과.
    """
    policy: CorrelationGuardPolicy

    def evaluate(self, inp: CorrelationGuardInput) -> CorrelationGuardResult:
        cand = inp.candidate

        # ---- SELL/EXIT 우회 ----
        if str(cand.side).upper() != "BUY":
            sector, themes = _build_exposure(inp.held_positions)
            return CorrelationGuardResult(
                verdict=CorrelationGuardVerdict.SKIP_NON_BUY,
                sector_exposure=sector,
                theme_exposure=themes,
            )

        sector_exp, theme_exp = _build_exposure(inp.held_positions)
        cand_sector = cand.meta.sector or ""
        cand_themes = list(cand.meta.themes or [])

        # ---- 신규 노출 / 심볼 카운트 ----
        # 같은 심볼 재매수면 *추가* 노출, 신규면 신규.
        already_holds = any(
            h.meta.symbol == cand.symbol for h in inp.held_positions
        )
        proj_sector_exp = sector_exp.get(cand_sector, 0) + cand.notional if cand_sector else 0
        proj_theme_exp = {
            t: theme_exp.get(t, 0) + cand.notional for t in cand_themes
        }
        # 같은 sector 내 종목 수 (cand 미포함 현재 카운트).
        cur_sector_count = (
            sum(1 for h in inp.held_positions
                if (h.meta.sector or "") == cand_sector and cand_sector)
        )
        proj_sector_count = cur_sector_count + (0 if already_holds else 1)

        blocked: list[str] = []
        warnings: list[str] = []

        # ---- sector 종목 수 한도 ----
        p = self.policy
        if (p.max_symbols_per_sector and cand_sector
                and proj_sector_count > p.max_symbols_per_sector):
            blocked.append(
                f"sector '{cand_sector}' 종목 수 {proj_sector_count} > "
                f"{p.max_symbols_per_sector} — 동일 섹터 과집중."
            )
        elif (p.max_symbols_per_sector and cand_sector
                and proj_sector_count >= p.max_symbols_per_sector * p.warn_ratio
                and proj_sector_count > cur_sector_count):
            warnings.append(
                f"sector '{cand_sector}' 종목 수 {proj_sector_count}/"
                f"{p.max_symbols_per_sector} — WARN 임계 근접."
            )

        # ---- sector 절대 노출 ----
        if (p.max_sector_exposure and cand_sector
                and proj_sector_exp > p.max_sector_exposure):
            blocked.append(
                f"sector '{cand_sector}' 노출 {proj_sector_exp:,} > "
                f"{p.max_sector_exposure:,} — 섹터 노출 한도 초과."
            )
        elif (p.max_sector_exposure and cand_sector
                and proj_sector_exp >= int(p.max_sector_exposure * p.warn_ratio)):
            warnings.append(
                f"sector '{cand_sector}' 노출 {proj_sector_exp:,} ≥ "
                f"{int(p.max_sector_exposure * p.warn_ratio):,} (WARN)."
            )

        # ---- sector % of equity ----
        if (p.max_sector_exposure_pct and cand_sector and inp.equity_krw > 0):
            limit = int(inp.equity_krw * p.max_sector_exposure_pct)
            if proj_sector_exp > limit:
                blocked.append(
                    f"sector '{cand_sector}' 노출 비율 "
                    f"{proj_sector_exp / inp.equity_krw:.1%} > "
                    f"{p.max_sector_exposure_pct:.1%} — equity 한도 초과."
                )
            elif proj_sector_exp >= int(limit * p.warn_ratio):
                warnings.append(
                    f"sector '{cand_sector}' equity 비율 "
                    f"{proj_sector_exp / inp.equity_krw:.1%} (WARN)."
                )

        # ---- theme 종목 수 ----
        if p.max_symbols_per_theme and cand_themes:
            for t in cand_themes:
                cur = sum(
                    1 for h in inp.held_positions
                    if t in (h.meta.themes or ())
                )
                proj = cur + (0 if already_holds else 1)
                if proj > p.max_symbols_per_theme:
                    blocked.append(
                        f"theme '{t}' 종목 수 {proj} > "
                        f"{p.max_symbols_per_theme} — 테마 과집중."
                    )
                elif (proj >= p.max_symbols_per_theme * p.warn_ratio
                      and proj > cur):
                    warnings.append(
                        f"theme '{t}' 종목 수 {proj}/{p.max_symbols_per_theme} (WARN)."
                    )

        # ---- theme 절대 노출 ----
        if p.max_theme_exposure and cand_themes:
            for t in cand_themes:
                exp = proj_theme_exp.get(t, 0)
                if exp > p.max_theme_exposure:
                    blocked.append(
                        f"theme '{t}' 노출 {exp:,} > {p.max_theme_exposure:,}."
                    )
                elif exp >= int(p.max_theme_exposure * p.warn_ratio):
                    warnings.append(
                        f"theme '{t}' 노출 {exp:,} ≥ "
                        f"{int(p.max_theme_exposure * p.warn_ratio):,} (WARN)."
                    )

        # ---- theme % of equity ----
        if p.max_theme_exposure_pct and cand_themes and inp.equity_krw > 0:
            limit_pct = p.max_theme_exposure_pct
            for t in cand_themes:
                exp = proj_theme_exp.get(t, 0)
                limit = int(inp.equity_krw * limit_pct)
                if exp > limit:
                    blocked.append(
                        f"theme '{t}' 노출 비율 "
                        f"{exp / inp.equity_krw:.1%} > {limit_pct:.1%}."
                    )
                elif exp >= int(limit * p.warn_ratio):
                    warnings.append(
                        f"theme '{t}' equity 비율 "
                        f"{exp / inp.equity_krw:.1%} (WARN)."
                    )

        verdict = (
            CorrelationGuardVerdict.REJECT if blocked
            else CorrelationGuardVerdict.WARN if warnings
            else CorrelationGuardVerdict.PASS
        )

        return CorrelationGuardResult(
            verdict=verdict,
            blocked_reasons=blocked,
            warnings=warnings,
            sector_exposure=sector_exp,
            theme_exposure=theme_exp,
            projected_sector=cand_sector or None,
            projected_themes=cand_themes,
            projected_sector_exposure=proj_sector_exp,
            projected_theme_exposure=proj_theme_exp,
            sector_symbol_count=cur_sector_count,
            projected_sector_symbol_count=proj_sector_count,
        )


# ---------- helpers ----------


def _build_exposure(
    positions: tuple[HeldPosition, ...],
) -> tuple[dict[str, int], dict[str, int]]:
    """sector / theme 노출 집계."""
    sec: dict[str, int] = {}
    th:  dict[str, int] = {}
    for h in positions:
        if h.meta.sector:
            sec[h.meta.sector] = sec.get(h.meta.sector, 0) + h.notional
        for t in (h.meta.themes or ()):
            th[t] = th.get(t, 0) + h.notional
    return sec, th


def compute_return_correlation(
    series_a: list[float],
    series_b: list[float],
    *,
    min_bars: int = 20,
) -> float | None:
    """수익률 시계열 두 개의 Pearson 상관계수.

    표본이 `min_bars` 미만이면 None 반환 — 데이터 부족 시 가드는 *통과*시켜야
    한다 (보수적으로 BUY 차단을 약화하지 않는 방향이 아니라, "상관계수 추정
    불가" 시 본 검사를 skip 한다는 의미).

    본 함수는 외부 import 0건 — 순수 list[float] 입력만. MarketBar 등은 호출자
    가 정렬된 series 로 변환해서 전달.
    """
    n = min(len(series_a), len(series_b))
    if n < max(2, min_bars):
        return None
    a = series_a[-n:]
    b = series_b[-n:]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    num = sum((ai - mean_a) * (bi - mean_b) for ai, bi in zip(a, b))
    var_a = sum((ai - mean_a) ** 2 for ai in a)
    var_b = sum((bi - mean_b) ** 2 for bi in b)
    if var_a <= 0 or var_b <= 0:
        return None
    return num / ((var_a ** 0.5) * (var_b ** 0.5))


def returns_from_closes(closes: list[float]) -> list[float]:
    """종가 시계열 → 단순 수익률. 0 또는 음수 close는 skip (전이 close 사용)."""
    out: list[float] = []
    prev: float | None = None
    for c in closes:
        if c is None or c <= 0:
            continue
        if prev is not None and prev > 0:
            out.append((c - prev) / prev)
        prev = c
    return out
