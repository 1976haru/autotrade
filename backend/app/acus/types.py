"""ACUS 공유 타입 + 판정 상수 + 안전 invariant.

모든 stage 결과는 frozen dataclass + ``__post_init__`` 가드로 안전 불변을 강제한다:
``is_order_signal`` / ``auto_apply_allowed`` / ``applied_to_runtime`` /
``is_live_authorization`` / ``contains_secret`` 는 항상 False, ``no_profit_guarantee``
는 항상 True. (기존 ``app/system/*`` 리포트 패턴 미러링.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# 거래비용 (왕복) — 수수료 0.015%×2 + 세금 0.18% + 슬리피지 0.05% = 0.26% = 26bps
# ─────────────────────────────────────────────────────────────────────────────
COMMISSION_RATE = 0.00015          # 편도 수수료
TAX_RATE = 0.0018                  # 매도 거래세
SLIPPAGE_RATE = 0.0005             # 슬리피지(편도 추정)
ROUND_TRIP_COST_FRACTION = COMMISSION_RATE * 2 + TAX_RATE + SLIPPAGE_RATE  # 0.0026

# ─────────────────────────────────────────────────────────────────────────────
# 판정 상수
# ─────────────────────────────────────────────────────────────────────────────
# [B] BacktestAgent
BT_ROBUST = "ROBUST"
BT_DECAYED = "DECAYED"
BT_CONSISTENT = "CONSISTENT"
BT_REJECTED = "REJECTED"
BT_INSUFFICIENT = "INSUFFICIENT_DATA"

# [C] RegimeAgent
REGIME_ROBUST = "REGIME_ROBUST"
REGIME_WEAK = "REGIME_WEAK"
REGIME_UNKNOWN = "REGIME_UNKNOWN"

# [D] LiquidityAgent
LIQUID = "LIQUID"
ILLIQUID = "ILLIQUID"
LIQUIDITY_UNKNOWN = "LIQUIDITY_UNKNOWN"

# [E] NewsAgent
NEWS_STABLE = "NEWS_STABLE"
NEWS_RISKY = "NEWS_RISKY"
NEWS_UNKNOWN = "NEWS_UNKNOWN"

# [F] RiskAgent
RISK_OK = "RISK_OK"
RISK_HIGH = "RISK_HIGH"
RISK_UNKNOWN = "RISK_UNKNOWN"

# [H] 최종 verdict + 풀 분류
VERDICT_STRONG = "STRONG_CANDIDATE_POOL_FOUND"
VERDICT_WEAK = "WEAK"
VERDICT_INSUFFICIENT = "INSUFFICIENT"

POOL_STRONG = "STRONG_CANDIDATE_POOL"   # N >= 30
POOL_DATE = "DATE_POOL"                 # 10 <= N < 30
POOL_INSUFFICIENT = "INSUFFICIENT_POOL"  # N < 10

POOL_STRONG_MIN = 30
POOL_DATE_MIN = 10

DISCLAIMER_KO = (
    "본 결과는 다중 에이전트 교차검증 기반 종목 선별 *연구 자료*이며, 자동 적용 / "
    "실전 전환 승인 / 주문 신호가 아니다. 수익을 보장하지 않으며 실전 진입은 운영자 "
    "명시 승인 + 별도 절차(Paper 리허설 → 게이트)가 필요하다."
)


def _assert_safety(obj: Any) -> None:
    """공통 안전 invariant 검증 — 모든 ACUS 리포트 dataclass 가 호출."""
    for name in ("is_order_signal", "auto_apply_allowed", "applied_to_runtime",
                 "is_live_authorization", "contains_secret"):
        if getattr(obj, name, False) is not False:
            raise ValueError(f"{name} must be False (ACUS 는 분석 전용)")
    if getattr(obj, "no_profit_guarantee", True) is not True:
        raise ValueError("no_profit_guarantee must be True")


@dataclass(frozen=True)
class SafetyFlags:
    """모든 ACUS 리포트에 carry 되는 안전 불변 묶음."""

    is_order_signal: bool = False
    auto_apply_allowed: bool = False
    applied_to_runtime: bool = False
    is_live_authorization: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True

    def __post_init__(self) -> None:
        _assert_safety(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_order_signal": False,
            "auto_apply_allowed": False,
            "applied_to_runtime": False,
            "is_live_authorization": False,
            "contains_secret": False,
            "no_profit_guarantee": True,
        }


SAFE = SafetyFlags()


@dataclass(frozen=True)
class SymbolEvaluation:
    """한 종목의 5개 에이전트 종합 라벨 — 분석 전용(주문 신호 아님)."""

    symbol: str
    backtest_class: str = BT_INSUFFICIENT
    regime_class: str = REGIME_UNKNOWN
    liquidity_class: str = LIQUIDITY_UNKNOWN
    news_class: str = NEWS_UNKNOWN
    risk_class: str = RISK_UNKNOWN
    # 보조 점수 (랭킹/리포트용 — 주문 신호 아님)
    score: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    is_order_signal: bool = False
    auto_apply_allowed: bool = False
    applied_to_runtime: bool = False
    is_live_authorization: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True

    def __post_init__(self) -> None:
        _assert_safety(self)

    def passes_all(self) -> bool:
        """5중 교집합 통과 여부. NEWS_UNKNOWN 은 제외하지 않는다(spec E2)."""
        return (
            self.backtest_class in (BT_ROBUST, BT_CONSISTENT)
            and self.regime_class == REGIME_ROBUST
            and self.liquidity_class == LIQUID
            and self.news_class in (NEWS_STABLE, NEWS_UNKNOWN)
            and self.risk_class == RISK_OK
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "backtest_class": self.backtest_class,
            "regime_class": self.regime_class,
            "liquidity_class": self.liquidity_class,
            "news_class": self.news_class,
            "risk_class": self.risk_class,
            "score": self.score,
            "detail": dict(self.detail),
            **SAFE.to_dict(),
        }
