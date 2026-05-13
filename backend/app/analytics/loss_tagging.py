"""Loss Reason Tagger (#79) — 손실 거래 원인 *추정* 분류.

CLAUDE.md 절대 원칙:
- 본 모듈은 *순수 분류 / 추정*만 한다 — broker / OrderExecutor / route_order /
  외부 HTTP / AI provider 호출 0건.
- 결과 태그는 *추정값*이며 *확정 원인이 아니다* (`is_estimated=True` 영구).
- 태그는 주문 차단 / 실행 트리거로 사용 금지 (advisory only,
  `is_order_signal=False` invariant).
- DB write 0건 — evaluator 는 입력 DTO만 받는다 (저장은 별도 collector / API).

핵심 invariant (코드 단 강제):
- `LossEstimateResult.is_estimated=True` 항상 (False 시 ValueError).
- `LossEstimateResult.is_order_signal=False` 항상.
- `LossEstimateResult.is_investment_advice=False` 항상.

태그 카테고리 (7개) — 각 카테고리에 여러 tag value:
- strategy   : STOP_LOSS_HIT / FAILED_BREAKOUT / FALSE_REBREAK / VWAP_LOSS /
              TARGET_NOT_REACHED / TIME_STOP / REVERSAL_SIGNAL
- market     : MARKET_SELLOFF / SECTOR_DROP / REGIME_CHANGE / VOLATILITY_SPIKE
- execution  : LOW_LIQUIDITY / HIGH_SLIPPAGE / PARTIAL_FILL / PRICE_GAP
- risk       : RISK_LIMIT_HIT / EMERGENCY_STOP / OVER_EXPOSURE
- data       : DATA_STALE / BAD_QUOTE / MISSING_BAR
- agent      : AI_OVERCONFIDENCE / AI_LOW_CONFIDENCE / NEWS_THEME_FADED
- unknown    : UNKNOWN
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ---------- enums ----------


class LossReasonCategory(StrEnum):
    STRATEGY  = "strategy"
    MARKET    = "market"
    EXECUTION = "execution"
    RISK      = "risk"
    DATA      = "data"
    AGENT     = "agent"
    UNKNOWN   = "unknown"


class LossReasonTag(StrEnum):
    # strategy
    STOP_LOSS_HIT       = "stop_loss_hit"
    FAILED_BREAKOUT     = "failed_breakout"
    FALSE_REBREAK       = "false_rebreak"
    VWAP_LOSS           = "vwap_loss"
    TARGET_NOT_REACHED  = "target_not_reached"
    TIME_STOP           = "time_stop"
    REVERSAL_SIGNAL     = "reversal_signal"
    # market
    MARKET_SELLOFF      = "market_selloff"
    SECTOR_DROP         = "sector_drop"
    REGIME_CHANGE       = "regime_change"
    VOLATILITY_SPIKE    = "volatility_spike"
    # execution
    LOW_LIQUIDITY       = "low_liquidity"
    HIGH_SLIPPAGE       = "high_slippage"
    PARTIAL_FILL        = "partial_fill"
    PRICE_GAP           = "price_gap"
    # risk
    RISK_LIMIT_HIT      = "risk_limit_hit"
    EMERGENCY_STOP      = "emergency_stop"
    OVER_EXPOSURE       = "over_exposure"
    # data
    DATA_STALE          = "data_stale"
    BAD_QUOTE           = "bad_quote"
    MISSING_BAR         = "missing_bar"
    # agent
    AI_OVERCONFIDENCE   = "ai_overconfidence"
    AI_LOW_CONFIDENCE   = "ai_low_confidence"
    NEWS_THEME_FADED    = "news_theme_faded"
    # unknown
    UNKNOWN             = "unknown"


_TAG_TO_CATEGORY: dict[LossReasonTag, LossReasonCategory] = {
    LossReasonTag.STOP_LOSS_HIT:      LossReasonCategory.STRATEGY,
    LossReasonTag.FAILED_BREAKOUT:    LossReasonCategory.STRATEGY,
    LossReasonTag.FALSE_REBREAK:      LossReasonCategory.STRATEGY,
    LossReasonTag.VWAP_LOSS:          LossReasonCategory.STRATEGY,
    LossReasonTag.TARGET_NOT_REACHED: LossReasonCategory.STRATEGY,
    LossReasonTag.TIME_STOP:          LossReasonCategory.STRATEGY,
    LossReasonTag.REVERSAL_SIGNAL:    LossReasonCategory.STRATEGY,
    LossReasonTag.MARKET_SELLOFF:     LossReasonCategory.MARKET,
    LossReasonTag.SECTOR_DROP:        LossReasonCategory.MARKET,
    LossReasonTag.REGIME_CHANGE:      LossReasonCategory.MARKET,
    LossReasonTag.VOLATILITY_SPIKE:   LossReasonCategory.MARKET,
    LossReasonTag.LOW_LIQUIDITY:      LossReasonCategory.EXECUTION,
    LossReasonTag.HIGH_SLIPPAGE:      LossReasonCategory.EXECUTION,
    LossReasonTag.PARTIAL_FILL:       LossReasonCategory.EXECUTION,
    LossReasonTag.PRICE_GAP:          LossReasonCategory.EXECUTION,
    LossReasonTag.RISK_LIMIT_HIT:     LossReasonCategory.RISK,
    LossReasonTag.EMERGENCY_STOP:     LossReasonCategory.RISK,
    LossReasonTag.OVER_EXPOSURE:      LossReasonCategory.RISK,
    LossReasonTag.DATA_STALE:         LossReasonCategory.DATA,
    LossReasonTag.BAD_QUOTE:          LossReasonCategory.DATA,
    LossReasonTag.MISSING_BAR:        LossReasonCategory.DATA,
    LossReasonTag.AI_OVERCONFIDENCE:  LossReasonCategory.AGENT,
    LossReasonTag.AI_LOW_CONFIDENCE:  LossReasonCategory.AGENT,
    LossReasonTag.NEWS_THEME_FADED:   LossReasonCategory.AGENT,
    LossReasonTag.UNKNOWN:            LossReasonCategory.UNKNOWN,
}


def category_of(tag: LossReasonTag) -> LossReasonCategory:
    return _TAG_TO_CATEGORY[tag]


# ---------- DTO ----------


@dataclass(frozen=True)
class LossEstimateInput:
    """손실 거래 원인 추정 입력.

    필수:
    - entry_price / exit_price / quantity / side: 거래 손익 계산
    - is_loss는 본 모듈이 entry/exit로 *직접 계산* — caller가 강제할 필요 없음.

    옵션 (지정 시 더 정확한 추정):
    - stop_price        : 손절가 — exit가 stop_price 근처면 STOP_LOSS_HIT.
    - target_price      : 익절가 — 미달 + 손실이면 TARGET_NOT_REACHED.
    - entry_vwap        : 진입 시점 VWAP — exit < entry_vwap이면 VWAP_LOSS.
    - hold_minutes      : 보유 분 — 일정 이상이면 TIME_STOP.
    - entry_volume / exit_volume: 거래량 — exit 시점 급감이면 LOW_LIQUIDITY.
    - slippage_bps      : 슬리피지 (bps) — 임계 초과면 HIGH_SLIPPAGE.
    - partial_fill_ratio: 부분 체결 비율 — 1.0 미만이면 PARTIAL_FILL.
    - gap_ratio         : 시가 갭 (전일종가 대비) — 임계 초과면 PRICE_GAP.
    - kospi_return      : 시장 수익률 — 일정 이하면 MARKET_SELLOFF.
    - sector_return     : 섹터 수익률 — 일정 이하면 SECTOR_DROP.
    - regime_at_entry / regime_at_exit: regime 변경 감지.
    - volatility_pct    : 변동성 (당일 ATR / 가격) — 임계 초과면 VOLATILITY_SPIKE.
    - daily_loss_limit_breached: bool — true면 RISK_LIMIT_HIT.
    - emergency_stop_active:   bool — true면 EMERGENCY_STOP.
    - over_exposure:           bool — true면 OVER_EXPOSURE.
    - data_stale_at_entry / data_stale_at_exit: bool — true면 DATA_STALE.
    - bad_quote_count          : int — > 0이면 BAD_QUOTE.
    - missing_bar_count        : int — > 0이면 MISSING_BAR.
    - ai_entry_confidence      : 0~100 — 너무 높은데 손실이면 AI_OVERCONFIDENCE.
    - news_theme_active_at_entry / faded_at_exit: bool — 소멸이면 NEWS_THEME_FADED.
    - reverse_signal_at_exit:   bool — true면 REVERSAL_SIGNAL.
    - failed_breakout_pattern:  bool — true면 FAILED_BREAKOUT.
    - false_rebreak_pattern:    bool — true면 FALSE_REBREAK.
    """
    symbol:                          str
    side:                            str    # "BUY"=long, "SELL"=short
    entry_price:                     float
    exit_price:                      float
    quantity:                        int

    # strategy / pattern signals.
    stop_price:                      float | None = None
    target_price:                    float | None = None
    entry_vwap:                      float | None = None
    hold_minutes:                    int   | None = None
    failed_breakout_pattern:         bool        = False
    false_rebreak_pattern:           bool        = False
    reverse_signal_at_exit:          bool        = False
    time_stop_threshold_minutes:     int         = 180   # 3h

    # execution.
    entry_volume:                    int   | None = None
    exit_volume:                     int   | None = None
    slippage_bps:                    float | None = None
    partial_fill_ratio:              float | None = None     # 1.0=full
    gap_ratio:                       float | None = None     # |gap| / prev_close

    # market.
    kospi_return:                    float | None = None     # day return
    sector_return:                   float | None = None
    regime_at_entry:                 str   | None = None
    regime_at_exit:                  str   | None = None
    volatility_pct:                  float | None = None

    # risk.
    daily_loss_limit_breached:       bool        = False
    emergency_stop_active:           bool        = False
    over_exposure:                   bool        = False

    # data.
    data_stale_at_entry:             bool        = False
    data_stale_at_exit:              bool        = False
    bad_quote_count:                 int         = 0
    missing_bar_count:               int         = 0

    # agent.
    ai_entry_confidence:             int   | None = None     # 0~100
    news_theme_active_at_entry:      bool        = False
    news_theme_faded_at_exit:        bool        = False

    @property
    def trade_pnl(self) -> int:
        """단위 손익. side='BUY'=long → (exit-entry)*qty, 'SELL'=short → 반대."""
        s = (self.side or "").upper()
        if s == "SELL":
            return int((self.entry_price - self.exit_price) * self.quantity)
        return int((self.exit_price - self.entry_price) * self.quantity)

    @property
    def is_loss(self) -> bool:
        return self.trade_pnl < 0


@dataclass(frozen=True)
class LossEstimateThresholds:
    """추정 임계. 운영자 override 가능."""
    stop_loss_proximity_pct:    float = 0.01    # exit가 stop_price의 ±1% 이내면 STOP_LOSS_HIT
    high_slippage_bps:          float = 50.0
    partial_fill_threshold:     float = 0.95
    low_liquidity_drop_ratio:   float = 0.3     # exit_volume / entry_volume < 0.3
    price_gap_threshold:        float = 0.02    # 2%
    market_selloff_threshold:   float = -0.015  # KOSPI -1.5%
    sector_drop_threshold:      float = -0.02   # sector -2%
    volatility_spike_threshold: float = 0.05    # 일일 변동성 5% 이상
    ai_overconfidence_threshold: int  = 80      # AI confidence ≥ 80인데 손실 → overconfidence
    ai_low_confidence_threshold: int  = 40      # AI confidence ≤ 40인데 진입했고 손실


@dataclass
class LossEstimateResult:
    """추정 결과 — *확정 원인이 아니다*."""
    symbol:                  str
    is_loss:                 bool
    trade_pnl:               int
    tags:                    list[LossReasonTag] = field(default_factory=list)
    primary_tag:             LossReasonTag | None = None
    confidence:              int = 0    # 0~100 (휴리스틱 확신도)
    rationale:               list[str] = field(default_factory=list)
    is_estimated:            bool = True       # invariant: 항상 True
    is_order_signal:         bool = False      # invariant: 항상 False
    is_investment_advice:    bool = False
    generated_at:            datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_estimated is not True:
            raise ValueError(
                "LossEstimateResult.is_estimated must be True — "
                "loss tags are estimates, not confirmed causes."
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "LossEstimateResult.is_order_signal must be False — "
                "loss tags do not produce BUY/SELL/HOLD signals."
            )
        if self.is_investment_advice is not False:
            raise ValueError(
                "LossEstimateResult.is_investment_advice must be False — "
                "loss tags are system analysis material, not investment advice."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol":               self.symbol,
            "is_loss":              self.is_loss,
            "trade_pnl":            self.trade_pnl,
            "tags":                 [t.value for t in self.tags],
            "categories":           sorted({category_of(t).value for t in self.tags}),
            "primary_tag":          self.primary_tag.value if self.primary_tag else None,
            "primary_category": (
                category_of(self.primary_tag).value if self.primary_tag else None
            ),
            "confidence":           self.confidence,
            "rationale":            list(self.rationale),
            "is_estimated":         self.is_estimated,
            "is_order_signal":      self.is_order_signal,
            "is_investment_advice": self.is_investment_advice,
            "live_flag_changed":    False,
            "mode_changed":         False,
            "generated_at":         self.generated_at.isoformat(),
        }


# ---------- tagger ----------


def estimate_loss_reasons(
    inp: LossEstimateInput,
    thresholds: LossEstimateThresholds | None = None,
) -> LossEstimateResult:
    """단일 거래 손실 원인 추정. 외부 시스템 영향 0건.

    SELL이 손실인 거래(즉 *손익 < 0*)면 카테고리별 휴리스틱으로 태그를 모음.
    손실이 아니면 빈 태그.
    """
    th = thresholds or LossEstimateThresholds()
    tags: list[LossReasonTag] = []
    rationale: list[str] = []

    is_loss = inp.is_loss

    if not is_loss:
        return LossEstimateResult(
            symbol=inp.symbol,
            is_loss=False,
            trade_pnl=inp.trade_pnl,
            tags=[],
            primary_tag=None,
            confidence=0,
            rationale=["거래가 손실이 아닙니다 — 태깅 대상이 아닙니다."],
        )

    # ---- risk (highest priority — 운영 차단 사유) ----
    if inp.emergency_stop_active:
        tags.append(LossReasonTag.EMERGENCY_STOP)
        rationale.append("emergency_stop 활성 중 청산.")
    if inp.daily_loss_limit_breached:
        tags.append(LossReasonTag.RISK_LIMIT_HIT)
        rationale.append("일일 손실한도 위반 감지.")
    if inp.over_exposure:
        tags.append(LossReasonTag.OVER_EXPOSURE)
        rationale.append("과도한 노출(over_exposure) 감지.")

    # ---- data ----
    if inp.data_stale_at_entry or inp.data_stale_at_exit:
        tags.append(LossReasonTag.DATA_STALE)
        rationale.append("진입 또는 청산 시점 데이터 stale.")
    if inp.bad_quote_count > 0:
        tags.append(LossReasonTag.BAD_QUOTE)
        rationale.append(f"비정상 호가 {inp.bad_quote_count}건.")
    if inp.missing_bar_count > 0:
        tags.append(LossReasonTag.MISSING_BAR)
        rationale.append(f"봉 누락 {inp.missing_bar_count}건.")

    # ---- market ----
    if inp.kospi_return is not None and inp.kospi_return <= th.market_selloff_threshold:
        tags.append(LossReasonTag.MARKET_SELLOFF)
        rationale.append(
            f"KOSPI 수익률 {inp.kospi_return:.2%} ≤ "
            f"{th.market_selloff_threshold:.2%} — 시장 급락."
        )
    if inp.sector_return is not None and inp.sector_return <= th.sector_drop_threshold:
        tags.append(LossReasonTag.SECTOR_DROP)
        rationale.append(
            f"섹터 수익률 {inp.sector_return:.2%} ≤ "
            f"{th.sector_drop_threshold:.2%} — 섹터 급락."
        )
    if (inp.regime_at_entry is not None and inp.regime_at_exit is not None
            and inp.regime_at_entry != inp.regime_at_exit):
        tags.append(LossReasonTag.REGIME_CHANGE)
        rationale.append(
            f"market regime 변경 ({inp.regime_at_entry} → {inp.regime_at_exit})."
        )
    if inp.volatility_pct is not None and inp.volatility_pct >= th.volatility_spike_threshold:
        tags.append(LossReasonTag.VOLATILITY_SPIKE)
        rationale.append(
            f"변동성 {inp.volatility_pct:.2%} ≥ "
            f"{th.volatility_spike_threshold:.2%}."
        )

    # ---- execution ----
    if inp.partial_fill_ratio is not None and inp.partial_fill_ratio < th.partial_fill_threshold:
        tags.append(LossReasonTag.PARTIAL_FILL)
        rationale.append(
            f"부분 체결 비율 {inp.partial_fill_ratio:.1%} < "
            f"{th.partial_fill_threshold:.1%}."
        )
    if inp.slippage_bps is not None and inp.slippage_bps >= th.high_slippage_bps:
        tags.append(LossReasonTag.HIGH_SLIPPAGE)
        rationale.append(
            f"슬리피지 {inp.slippage_bps:.0f}bps ≥ {th.high_slippage_bps:.0f}bps."
        )
    if (inp.entry_volume is not None and inp.exit_volume is not None
            and inp.entry_volume > 0
            and inp.exit_volume / inp.entry_volume < th.low_liquidity_drop_ratio):
        tags.append(LossReasonTag.LOW_LIQUIDITY)
        rationale.append(
            f"청산 시점 거래량 {inp.exit_volume / inp.entry_volume:.1%} of entry — 유동성 부족."
        )
    if inp.gap_ratio is not None and abs(inp.gap_ratio) >= th.price_gap_threshold:
        tags.append(LossReasonTag.PRICE_GAP)
        rationale.append(
            f"시가 갭 {inp.gap_ratio:.2%} (|gap| ≥ {th.price_gap_threshold:.2%})."
        )

    # ---- strategy / pattern ----
    if inp.stop_price is not None and inp.stop_price > 0:
        proximity = abs(inp.exit_price - inp.stop_price) / inp.stop_price
        if proximity <= th.stop_loss_proximity_pct:
            tags.append(LossReasonTag.STOP_LOSS_HIT)
            rationale.append(
                f"청산가 {inp.exit_price} 이 stop_price {inp.stop_price} 근처 "
                f"({proximity:.1%} 이내) — 손절 hit."
            )
    if inp.failed_breakout_pattern:
        tags.append(LossReasonTag.FAILED_BREAKOUT)
        rationale.append("돌파 실패 (failed_breakout) 패턴 표시.")
    if inp.false_rebreak_pattern:
        tags.append(LossReasonTag.FALSE_REBREAK)
        rationale.append("재돌파 실패 (false_rebreak) 패턴 표시.")
    if (inp.entry_vwap is not None and inp.entry_vwap > 0
            and inp.exit_price < inp.entry_vwap
            # SELL(short)은 반대 방향이라 VWAP 의미가 다름 — long만 적용.
            and (inp.side or "").upper() != "SELL"):
        tags.append(LossReasonTag.VWAP_LOSS)
        rationale.append(
            f"청산가 {inp.exit_price} < 진입시 VWAP {inp.entry_vwap}."
        )
    if (inp.target_price is not None
            and inp.target_price > 0
            and (inp.side or "").upper() != "SELL"
            and inp.exit_price < inp.target_price):
        tags.append(LossReasonTag.TARGET_NOT_REACHED)
        rationale.append(
            f"target_price {inp.target_price} 미도달 후 손실 청산."
        )
    if (inp.hold_minutes is not None
            and inp.hold_minutes >= inp.time_stop_threshold_minutes):
        tags.append(LossReasonTag.TIME_STOP)
        rationale.append(
            f"보유 {inp.hold_minutes}분 ≥ {inp.time_stop_threshold_minutes}분 — time stop."
        )
    if inp.reverse_signal_at_exit:
        tags.append(LossReasonTag.REVERSAL_SIGNAL)
        rationale.append("청산 시점 reverse signal 감지.")

    # ---- agent ----
    if (inp.ai_entry_confidence is not None
            and inp.ai_entry_confidence >= th.ai_overconfidence_threshold):
        tags.append(LossReasonTag.AI_OVERCONFIDENCE)
        rationale.append(
            f"진입 시 AI confidence {inp.ai_entry_confidence} ≥ "
            f"{th.ai_overconfidence_threshold} 인데 손실 — overconfidence 의심."
        )
    if (inp.ai_entry_confidence is not None
            and inp.ai_entry_confidence <= th.ai_low_confidence_threshold):
        tags.append(LossReasonTag.AI_LOW_CONFIDENCE)
        rationale.append(
            f"진입 시 AI confidence {inp.ai_entry_confidence} ≤ "
            f"{th.ai_low_confidence_threshold} — 낮은 신뢰도 진입."
        )
    if inp.news_theme_active_at_entry and inp.news_theme_faded_at_exit:
        tags.append(LossReasonTag.NEWS_THEME_FADED)
        rationale.append("진입 시 테마/뉴스 활성이었으나 청산 시 소멸.")

    # ---- fallback ----
    if not tags:
        tags.append(LossReasonTag.UNKNOWN)
        rationale.append("자동 분류 불가 — 운영자 검토 필요.")

    # ---- primary tag 선정 ----
    # 우선순위: risk > data > market > execution > strategy > agent > unknown
    priority = [
        LossReasonCategory.RISK,
        LossReasonCategory.DATA,
        LossReasonCategory.MARKET,
        LossReasonCategory.EXECUTION,
        LossReasonCategory.STRATEGY,
        LossReasonCategory.AGENT,
        LossReasonCategory.UNKNOWN,
    ]
    primary: LossReasonTag | None = None
    for cat in priority:
        for t in tags:
            if category_of(t) == cat:
                primary = t
                break
        if primary is not None:
            break

    # confidence: tag 수에 비례 (max 6 → 100).
    confidence = min(100, max(15, len(tags) * 18))

    return LossEstimateResult(
        symbol=inp.symbol,
        is_loss=True,
        trade_pnl=inp.trade_pnl,
        tags=tags,
        primary_tag=primary,
        confidence=confidence,
        rationale=rationale,
    )


# ---------- summary helpers ----------


@dataclass(frozen=True)
class LossReasonSummaryRow:
    tag:        str
    category:   str
    count:      int
    pnl_sum:    int


def summarize_tag_counts(
    results: list[LossEstimateResult],
) -> list[LossReasonSummaryRow]:
    """집계 — 태그별 발생 횟수 + 손익 합산."""
    bucket: dict[str, dict[str, Any]] = {}
    for r in results:
        if not r.is_loss:
            continue
        for t in r.tags:
            key = t.value
            slot = bucket.setdefault(key, {
                "category": category_of(t).value,
                "count": 0, "pnl_sum": 0,
            })
            slot["count"]   += 1
            slot["pnl_sum"] += r.trade_pnl
    rows = [
        LossReasonSummaryRow(
            tag=k, category=v["category"],
            count=v["count"], pnl_sum=v["pnl_sum"],
        )
        for k, v in bucket.items()
    ]
    rows.sort(key=lambda x: (-x.count, x.tag))
    return rows


def summarize_for_daily_report(
    results: list[LossEstimateResult],
    *,
    top_n: int = 5,
) -> dict[str, Any]:
    """DailyReportAgent 가 read-only로 carry할 수 있는 dict.

    본 모듈은 DailyReportAgent를 *직접 호출하지 않는다* — 단지 dict를 반환.
    Agent 측 호출자가 markdown 섹션 생성 시 본 dict를 *재사용*.
    """
    loss_results = [r for r in results if r.is_loss]
    if not loss_results:
        return {
            "loss_count":          0,
            "top_tags":            [],
            "by_category":         {},
            "note":                "기간 내 손실 거래 없음 — 손실 태깅 대상 0건.",
            "is_estimated":        True,
        }

    rows = summarize_tag_counts(loss_results)
    by_cat: dict[str, int] = {}
    for r in rows:
        by_cat[r.category] = by_cat.get(r.category, 0) + r.count
    return {
        "loss_count":   len(loss_results),
        "top_tags":     [
            {"tag": r.tag, "category": r.category,
             "count": r.count, "pnl_sum": r.pnl_sum}
            for r in rows[:top_n]
        ],
        "by_category":  by_cat,
        "note":         (
            "본 요약은 *추정* 손실 원인이며 확정 원인이 아닙니다. "
            "운영자 검토 + 사후 분석 권장."
        ),
        "is_estimated": True,
    }


def summarize_for_strategy_researcher(
    results: list[LossEstimateResult],
) -> dict[str, Any]:
    """StrategyResearcherAgent 가 read-only로 carry — 반복 패턴 표시.

    반복 손실 태그 비율을 통해 strategy 개선 후보 발굴 보조.
    """
    rows = summarize_tag_counts([r for r in results if r.is_loss])
    total = sum(r.count for r in rows)
    if total == 0:
        return {
            "repeated_tags": [],
            "note":          "손실 표본 0 — 분석 불가.",
            "is_estimated":  True,
        }
    return {
        "repeated_tags": [
            {
                "tag": r.tag, "category": r.category, "count": r.count,
                "share": round(r.count / total, 3), "pnl_sum": r.pnl_sum,
            }
            for r in rows if r.count >= 2
        ],
        "note":         (
            "반복 손실 태그는 *추정*입니다. 전략 변경 / 파라미터 튜닝은 "
            "StrategyResearcherAgent / 운영자 검토 + 별도 PR 필요."
        ),
        "is_estimated": True,
    }
