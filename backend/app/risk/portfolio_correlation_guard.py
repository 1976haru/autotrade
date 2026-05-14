"""Portfolio Correlation Guard (#95) — *포지션 간 수익률 상관관계* advisory.

본 모듈은 현재 보유 포지션 + 신규 진입 후보 종목 간의 *수익률 상관관계 매트릭스*
를 계산해, 포트폴리오 전체가 동일 시장 리스크에 *과도하게 노출*되었는지 advisory
검사한다. 동일한 시장 충격 (예: KOSPI 전체 하락, 특정 섹터 sell-off) 으로 다수
포지션이 *동시에* 손실을 입는 위험을 사전 경고.

본 모듈은 #78 `app/risk/correlation_guard.py` 의 *sector / theme 노출 cap*
검사와는 별개 분석:

| 항목 | #78 correlation_guard | #95 본 모듈 (portfolio_correlation_guard) |
|---|---|---|
| 분석 | sector / theme 메타 기반 노출 cap | 종목 간 *historical return* Pearson correlation |
| 입력 | 포지션 + 신규 후보 + sector/theme 메타 | 위 + 종목별 close 시계열 (또는 returns) |
| verdict | PASS / WARN / REJECT / SKIP_NON_BUY | HEALTHY / WATCH / WARN / BLOCK / INSUFFICIENT_DATA |
| 차단 트리거 | sector/theme 비중 임계 초과 | 종목 간 상관계수 임계 초과 |
| 데이터 부족 시 | SKIP — 차단 안 함 | INSUFFICIENT_DATA — 차단 안 함 |

두 분석은 *상호 보완* — 운영자는 두 카드 모두 확인. 본 모듈은 #78 의 helper
함수 `compute_return_correlation` / `returns_from_closes` 를 *재사용*한다.

**적용 범위**: 본 모듈은 *asset-class agnostic* — 국내주식, 해외주식, 선물,
crypto 등 어떤 자산이든 *수익률 시계열*만 있으면 동작한다. 본 프로젝트 1차
배포는 국내주식 단타이며, 다음 모든 종목 군에 적용 가능:
- KOSPI 200 다중 보유 시 상호 correlation
- 동일 sector (예: 반도체) 다중 보유 시 sector beta 노출
- 페어트레이딩 / 변동성 돌파 전략의 신호 클러스터링

CLAUDE.md 절대 원칙 — 본 모듈은 외부 시스템과 완전 분리된 *순수 함수*:

- broker / executor / 외부 HTTP / AI SDK / settings reader 직접 사용 0건.
- 다른 governance gate evaluator 함수 직접 호출 0건.
- 데이터베이스 쓰기 작업 0건.
- 안전 flag (실거래 / AI 자동실행 / 선물 LIVE) 변경 0건.
- PortfolioCorrelationResult 생성 시 invariants ValueError 강제:
  is_live_authorization / auto_apply_allowed / is_order_signal 모두 False 만 허용.

verdict 5단계:
- HEALTHY        : max pairwise corr < `warn_threshold` — 진입 허용
- WATCH          : `warn_threshold` ≤ corr < `caution_threshold` — informational
- WARN           : `caution_threshold` ≤ corr < `block_threshold` — sizing 감소 권장
- BLOCK          : corr ≥ `block_threshold` — *신규 진입 차단 권장*
- INSUFFICIENT_DATA: 표본 부족 / 입력 없음 — 본 가드 미적용 (차단 안 함, advisory)

**본 모듈은 실거래 실행 기능을 추가하지 않으며**, RiskManager / OrderGuard 를
우회하지 않는다. BLOCK verdict 도 *권고* 수준 — 실제 차단은 별도 RiskRule
(후속 PR + 운영자 명시 옵트인) 에서 처리.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

# #78 모듈의 helper 재사용 — 표본 부족 시 None 반환 (skip).
from app.risk.correlation_guard import (
    compute_return_correlation,
    returns_from_closes,
)


# ---------- enums ----------


class PortfolioCorrelationVerdict(StrEnum):
    """5단계 verdict. BUY/SELL/HOLD 값 0개 — 본 verdict 는 주문 신호가 아니다."""
    HEALTHY            = "HEALTHY"
    WATCH              = "WATCH"
    WARN               = "WARN"
    BLOCK              = "BLOCK"
    INSUFFICIENT_DATA  = "INSUFFICIENT_DATA"


class PairSeverity(StrEnum):
    """단일 종목 쌍의 severity. BUY/SELL/HOLD 값 0개."""
    LOW     = "LOW"
    MEDIUM  = "MEDIUM"
    HIGH    = "HIGH"
    EXTREME = "EXTREME"


# ---------- thresholds / policy ----------


@dataclass(frozen=True)
class PortfolioCorrelationThresholds:
    """Pearson 상관계수 임계. 0~1 범위. 1 에 가까울수록 같은 방향 movement.

    절댓값 기준 — 음의 상관관계 (-0.85 등) 도 *반대 방향이지만 강한 결합*
    이므로 동일하게 advisory 발생.

    기본값은 *국내주식 단타* 의 보수적 권장값:
    - warn_threshold=0.50 : 0.5 이상이면 informational (WATCH)
    - caution_threshold=0.70 : 0.7 이상이면 sizing 권장 감소 (WARN)
    - block_threshold=0.85 : 0.85 이상이면 신규 진입 차단 권장 (BLOCK)
    - min_bars=20 : Pearson 계산 최소 표본 수 (compute_return_correlation 호출)
    """
    warn_threshold:    float = 0.50
    caution_threshold: float = 0.70
    block_threshold:   float = 0.85
    min_bars:          int   = 20
    # 한 portfolio 안에서 *몇 쌍이 임계를 넘으면* aggregate verdict 가 격상되는지.
    max_block_pairs_for_healthy: int = 0   # > 0 이면 verdict 격상.

    def __post_init__(self) -> None:
        for name, v in (
            ("warn_threshold", self.warn_threshold),
            ("caution_threshold", self.caution_threshold),
            ("block_threshold", self.block_threshold),
        ):
            if not (0.0 <= v <= 1.0):
                raise ValueError(
                    f"{name} must be in [0.0, 1.0], got {v}"
                )
        if not (self.warn_threshold <= self.caution_threshold
                <= self.block_threshold):
            raise ValueError(
                "thresholds must be ordered: "
                "warn <= caution <= block"
            )


# ---------- position / pair ----------


@dataclass(frozen=True)
class PortfolioPositionInput:
    """단일 보유 포지션 또는 신규 진입 후보.

    `notional_krw` 는 정수 (KRW 원 단위). `direction` 는 LONG / SHORT 등 (advisory
    표시용, 본 모듈은 *상관관계*만 계산하므로 direction 자체에 따른 차단 차이 없음).
    """
    symbol:        str
    notional_krw:  int  = 0
    direction:     str  = "LONG"

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be non-empty")


@dataclass(frozen=True)
class CorrelatedPair:
    """결과: 단일 종목 쌍의 상관계수 + severity."""
    symbol_a:    str
    symbol_b:    str
    correlation: float
    severity:    PairSeverity
    sample_size: int
    note:        str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol_a":    self.symbol_a,
            "symbol_b":    self.symbol_b,
            "correlation": self.correlation,
            "severity":    self.severity.value,
            "sample_size": self.sample_size,
            "note":        self.note,
        }


# ---------- input DTO ----------


@dataclass(frozen=True)
class PortfolioCorrelationInput:
    """평가 입력. 호출자가 현재 포지션 + 후보 + 시계열 채워서 전달.

    `return_series_by_symbol` 또는 `close_series_by_symbol` 둘 중 하나만 제공:
    - return_series: 이미 계산된 수익률 시계열 (사용 권장 — 빠름)
    - close_series : 종가 시계열 → 본 evaluator 가 returns_from_closes 로 변환

    둘 다 제공 시 return_series 우선 사용.
    """
    positions:               tuple[PortfolioPositionInput, ...] = ()
    candidate:               PortfolioPositionInput | None = None
    return_series_by_symbol: dict[str, tuple[float, ...]] = field(
        default_factory=dict,
    )
    close_series_by_symbol:  dict[str, tuple[float, ...]] = field(
        default_factory=dict,
    )
    strict:                  bool = False


# ---------- result DTO ----------


@dataclass
class PortfolioCorrelationResult:
    """평가 결과.

    invariants (코드 단 강제):
    - `is_order_signal=False` 항상.
    - `auto_apply_allowed=False` 항상.
    - `is_live_authorization=False` 항상.
    """
    verdict:                       PortfolioCorrelationVerdict = (
        PortfolioCorrelationVerdict.INSUFFICIENT_DATA
    )
    pairs:                         list[CorrelatedPair] = field(default_factory=list)
    portfolio_correlation_score:   float = 0.0   # 0~100, higher = more risk
    max_pairwise_correlation:      float = 0.0
    mean_pairwise_correlation:     float = 0.0
    high_correlation_pair_count:   int = 0
    candidate_max_correlation:     float | None = None
    new_entry_allowed:             bool = True
    warnings:                      list[str] = field(default_factory=list)
    advice:                        list[str] = field(default_factory=list)
    insufficient_data:             bool = False
    is_order_signal:               bool = False
    auto_apply_allowed:            bool = False
    is_live_authorization:         bool = False
    generated_at:                  datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError(
                "PortfolioCorrelationResult.is_order_signal must be False — "
                "this module does not produce BUY/SELL/HOLD signals."
            )
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "PortfolioCorrelationResult.auto_apply_allowed must be False — "
                "this module never auto-applies settings or rules."
            )
        if self.is_live_authorization is not False:
            raise ValueError(
                "PortfolioCorrelationResult.is_live_authorization must be False — "
                "this module is not a live trading authorization gate."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":                     self.verdict.value,
            "pairs":                       [p.to_dict() for p in self.pairs],
            "portfolio_correlation_score": self.portfolio_correlation_score,
            "max_pairwise_correlation":    self.max_pairwise_correlation,
            "mean_pairwise_correlation":   self.mean_pairwise_correlation,
            "high_correlation_pair_count": self.high_correlation_pair_count,
            "candidate_max_correlation":   self.candidate_max_correlation,
            "new_entry_allowed":           self.new_entry_allowed,
            "warnings":                    list(self.warnings),
            "advice":                      list(self.advice),
            "insufficient_data":           self.insufficient_data,
            "is_order_signal":             self.is_order_signal,
            "auto_apply_allowed":          self.auto_apply_allowed,
            "is_live_authorization":       self.is_live_authorization,
            "generated_at":                self.generated_at.isoformat(),
        }


# ---------- helpers ----------


def _severity_for_corr(
    abs_corr: float, th: PortfolioCorrelationThresholds,
) -> PairSeverity:
    """절댓값 corr → severity."""
    if abs_corr >= th.block_threshold:
        return PairSeverity.EXTREME
    if abs_corr >= th.caution_threshold:
        return PairSeverity.HIGH
    if abs_corr >= th.warn_threshold:
        return PairSeverity.MEDIUM
    return PairSeverity.LOW


def _get_returns_for(
    symbol: str,
    inp: PortfolioCorrelationInput,
) -> tuple[float, ...] | None:
    """입력에서 symbol 의 수익률 시계열 추출.

    return_series 가 있으면 그대로, 없으면 close_series 에서 변환.
    둘 다 없으면 None.
    """
    if symbol in inp.return_series_by_symbol:
        return inp.return_series_by_symbol[symbol]
    if symbol in inp.close_series_by_symbol:
        closes = list(inp.close_series_by_symbol[symbol])
        return tuple(returns_from_closes(closes))
    return None


def _aggregate_correlation_score(pairs: list[CorrelatedPair]) -> float:
    """전체 pair 들의 평균 |corr| 를 0~100 으로 변환 (clamp)."""
    if not pairs:
        return 0.0
    avg = sum(abs(p.correlation) for p in pairs) / len(pairs)
    return max(0.0, min(100.0, avg * 100.0))


def evaluate_portfolio_correlation(
    inp: PortfolioCorrelationInput,
    thresholds: PortfolioCorrelationThresholds | None = None,
) -> PortfolioCorrelationResult:
    """portfolio correlation 평가. read-only — 외부 시스템 영향 0건.

    표본 / 입력 부족 시 `insufficient_data=True` 로 INSUFFICIENT_DATA verdict.
    """
    th = thresholds or PortfolioCorrelationThresholds()
    all_positions: list[PortfolioPositionInput] = list(inp.positions)
    candidate = inp.candidate

    # 후보 포함한 *전체* symbol 집합 — 분석 대상.
    symbols: list[str] = []
    seen: set[str] = set()
    for p in all_positions:
        if p.symbol not in seen:
            symbols.append(p.symbol)
            seen.add(p.symbol)
    if candidate is not None and candidate.symbol not in seen:
        symbols.append(candidate.symbol)
        seen.add(candidate.symbol)

    if len(symbols) < 2:
        return PortfolioCorrelationResult(
            verdict=PortfolioCorrelationVerdict.INSUFFICIENT_DATA,
            pairs=[],
            insufficient_data=True,
            new_entry_allowed=True,
            warnings=["분석 대상 종목이 2개 미만 — 상관관계 검사 skip"],
            advice=[
                "포지션 1개 또는 후보 1개만 있는 경우 본 가드는 적용 안 됨"
            ],
        )

    # pairwise correlation 계산.
    pairs: list[CorrelatedPair] = []
    candidate_symbol = candidate.symbol if candidate is not None else None
    candidate_max_corr: float | None = None

    for i, sym_a in enumerate(symbols):
        ret_a = _get_returns_for(sym_a, inp)
        if ret_a is None or len(ret_a) < th.min_bars:
            continue
        for j in range(i + 1, len(symbols)):
            sym_b = symbols[j]
            ret_b = _get_returns_for(sym_b, inp)
            if ret_b is None or len(ret_b) < th.min_bars:
                continue
            corr = compute_return_correlation(
                list(ret_a), list(ret_b), min_bars=th.min_bars,
            )
            if corr is None:
                continue
            severity = _severity_for_corr(abs(corr), th)
            pairs.append(CorrelatedPair(
                symbol_a=sym_a, symbol_b=sym_b,
                correlation=corr,
                severity=severity,
                sample_size=min(len(ret_a), len(ret_b)),
            ))
            # candidate 와의 쌍은 별도 추적.
            if candidate_symbol in (sym_a, sym_b):
                if candidate_max_corr is None or abs(corr) > abs(candidate_max_corr):
                    candidate_max_corr = corr

    if not pairs:
        return PortfolioCorrelationResult(
            verdict=PortfolioCorrelationVerdict.INSUFFICIENT_DATA,
            pairs=[],
            insufficient_data=True,
            new_entry_allowed=True,
            warnings=[
                "유효한 종목 쌍 0개 — 시계열 부족 또는 min_bars 미달"
            ],
            advice=[
                f"각 종목 최소 {th.min_bars} bar 의 수익률 시계열 필요"
            ],
        )

    # 집계 통계.
    abs_corrs = [abs(p.correlation) for p in pairs]
    max_corr = max(abs_corrs)
    mean_corr = sum(abs_corrs) / len(abs_corrs)
    high_count = sum(
        1 for p in pairs if abs(p.correlation) >= th.block_threshold
    )
    score = _aggregate_correlation_score(pairs)

    # candidate 가 있으면 *후보 vs 기존* 의 max 로 verdict 우선 판단.
    decisive_corr = (
        candidate_max_corr if candidate is not None and candidate_max_corr is not None
        else max_corr
    )
    decisive_abs = abs(decisive_corr)

    # verdict 결정.
    if decisive_abs >= th.block_threshold or high_count > th.max_block_pairs_for_healthy:
        verdict = PortfolioCorrelationVerdict.BLOCK
        new_entry_allowed = False
    elif decisive_abs >= th.caution_threshold:
        verdict = PortfolioCorrelationVerdict.WARN
        # WARN 은 신규 진입 *권장하지 않음* 이지만 차단은 아님.
        new_entry_allowed = True
    elif decisive_abs >= th.warn_threshold:
        verdict = PortfolioCorrelationVerdict.WATCH
        new_entry_allowed = True
    else:
        verdict = PortfolioCorrelationVerdict.HEALTHY
        new_entry_allowed = True

    # strict 모드 — WARN 이상이면 new_entry_allowed=False.
    if inp.strict and verdict in (
        PortfolioCorrelationVerdict.WARN, PortfolioCorrelationVerdict.BLOCK,
    ):
        new_entry_allowed = False

    warnings: list[str] = []
    advice: list[str] = []

    if high_count > 0:
        warnings.append(
            f"{high_count}개 종목 쌍에서 |corr| ≥ {th.block_threshold:.2f} — "
            "동일 시장 충격에 *과도하게 노출*"
        )
    if verdict is PortfolioCorrelationVerdict.WARN:
        advice.append(
            f"max |corr| = {decisive_abs:.2f} — 신규 진입 시 *보수적 사이즈* 권장"
        )
    if verdict is PortfolioCorrelationVerdict.BLOCK:
        advice.append(
            f"max |corr| = {decisive_abs:.2f} ≥ {th.block_threshold:.2f} — "
            "*신규 진입 차단 권장* (다른 자산군 / 무상관 종목 검토)"
        )
    if candidate is not None and candidate_max_corr is not None:
        advice.append(
            f"후보 {candidate.symbol} 의 기존 포지션 max |corr| = "
            f"{abs(candidate_max_corr):.2f}"
        )
    if inp.strict and verdict in (
        PortfolioCorrelationVerdict.WARN, PortfolioCorrelationVerdict.BLOCK,
    ):
        advice.append(
            "strict=true 입력 — WARN / BLOCK 모두 new_entry_allowed=False 로 격하"
        )

    return PortfolioCorrelationResult(
        verdict=verdict,
        pairs=pairs,
        portfolio_correlation_score=score,
        max_pairwise_correlation=max_corr,
        mean_pairwise_correlation=mean_corr,
        high_correlation_pair_count=high_count,
        candidate_max_correlation=candidate_max_corr,
        new_entry_allowed=new_entry_allowed,
        warnings=warnings,
        advice=advice,
        insufficient_data=False,
    )


# ---------- markdown ----------


def render_markdown_report(result: PortfolioCorrelationResult) -> str:
    """markdown 리포트 — 운영자 / Daily Report 에 carry 가능."""
    lines: list[str] = []
    lines.append("# Portfolio Correlation Guard")
    lines.append("")
    lines.append(f"_생성: {result.generated_at.isoformat()}_")
    lines.append("")
    lines.append(
        "> ⚠️ 본 보고서는 *포트폴리오 상관관계 advisory* 입니다. 본 모듈은 어떤 "
        "주문도 발행하지 않으며, BLOCK verdict 도 권고 수준입니다. 실제 차단은 "
        "별도 RiskRule (후속 PR + 운영자 명시 옵트인) 에서 처리합니다."
    )
    lines.append("")
    lines.append(f"## 판정: **{result.verdict.value}**")
    lines.append("")
    lines.append(
        f"- portfolio_correlation_score: **{result.portfolio_correlation_score:.1f}** / 100"
    )
    lines.append(
        f"- max_pairwise_correlation: **{result.max_pairwise_correlation:.3f}**"
    )
    lines.append(
        f"- mean_pairwise_correlation: **{result.mean_pairwise_correlation:.3f}**"
    )
    lines.append(
        f"- high_correlation_pair_count: **{result.high_correlation_pair_count}**"
    )
    lines.append(
        f"- new_entry_allowed: **{result.new_entry_allowed}**"
    )
    if result.candidate_max_correlation is not None:
        lines.append(
            f"- candidate_max_correlation: **{result.candidate_max_correlation:.3f}**"
        )
    if result.insufficient_data:
        lines.append("- ⚠️ insufficient_data=True (표본 부족 또는 입력 누락)")

    if result.warnings:
        lines.append("")
        lines.append("## 경고")
        for w in result.warnings:
            lines.append(f"- ⚠️ {w}")

    if result.advice:
        lines.append("")
        lines.append("## 권고")
        for a in result.advice:
            lines.append(f"- 📝 {a}")

    if result.pairs:
        lines.append("")
        lines.append("## 종목 쌍 상관계수 (severity 순)")
        lines.append("")
        lines.append(
            "| symbol_a | symbol_b | correlation | severity | samples |"
        )
        lines.append("|---|---|---|---|---|")
        for p in sorted(result.pairs,
                        key=lambda x: abs(x.correlation), reverse=True):
            lines.append(
                f"| `{p.symbol_a}` | `{p.symbol_b}` | "
                f"{p.correlation:+.3f} | {p.severity.value} | "
                f"{p.sample_size} |"
            )

    lines.append("")
    lines.append(
        "---\n본 보고서는 advisory — broker / OrderExecutor / route_order / "
        "안전 flag 변경 0건. 실거래 실행 기능을 추가하지 않습니다."
    )
    return "\n".join(lines)
