"""#49: Mock futures strategies — skeleton 구현.

`FuturesStrategyBase` 위에 동작하는 3개 mock 전략. **실제 거래에 사용하지
말 것** — 본 모듈의 모든 전략은:

- 결정론적 / 보수적 / 1계약 이하만 권장
- broker / OrderExecutor / route_order 호출 0건
- `FuturesSignal.is_order_intent = False` 불변
- 만기 임박 시 자동 롤오버 *주문*을 발신하지 않음 — advisory plan만 반환

전략 목록:

1. **`FuturesTrendFollowingStrategy`** — SMA crossover 기반 단순 추세추종
   (상승 → OPEN_LONG / 하락 → OPEN_SHORT / 부족 → WATCH).
2. **`FuturesVolatilityBreakoutStrategy`** — Bollinger-style 변동성 돌파
   (high vol risk면 REDUCE_SIZE / WATCH).
3. **`FuturesHedgeStrategy`** — equity 노출 보정 헤지 (양의 노출 → SHORT 헤지
   advisory).

본 mock 전략들은 *integration sandbox*용 — 실제 운영 단계에서는 운영자가
별도 PR로 검증된 전략을 추가한다.

자세한 contract: [`docs/futures_strategy_contract.md`](../../../../docs/futures_strategy_contract.md).
"""

from __future__ import annotations

from app.futures.strategies.base import (
    FuturesContractSizingHint,
    FuturesExitPlan,
    FuturesRolloverPlan,
    FuturesSignal,
    FuturesSignalAction,
    FuturesSignalExplanation,
    FuturesStrategyBase,
    FuturesStrategyContext,
    FuturesStrategyMetadata,
)


# ====================================================================
# Helpers (순수 산식 — broker 호출 0건)
# ====================================================================


def _sma(values: list[int], window: int) -> float | None:
    """단순 이동평균. 데이터 부족이면 None."""
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window


def _stddev(values: list[int], window: int) -> float | None:
    """표본 표준편차. 데이터 부족이면 None."""
    if window <= 1 or len(values) < window:
        return None
    sample = values[-window:]
    mean = sum(sample) / window
    var = sum((v - mean) ** 2 for v in sample) / (window - 1)
    return var ** 0.5


def _maybe_rollover(
    context: FuturesStrategyContext, *,
    threshold_days: int = 5,
) -> FuturesRolloverPlan | None:
    """만기 임박 advisory plan 생성. broker 호출 0건.

    `context.expiry`가 주어지면 잔존 일수를 근사 계산. 임계 이하면 plan을
    반환 — caller는 plan을 보고 *수동* 롤오버를 결정한다.
    """
    if context.expiry is None or context.contract is None:
        return None
    last_bar_ts = context.bars[-1].timestamp if context.bars else None
    if last_bar_ts is None:
        return None
    # naive arithmetic — 영업일 캘린더는 별도 PR에서 정밀화.
    delta = context.expiry - last_bar_ts
    days_left = max(0, int(delta.total_seconds() // 86400))
    if days_left > threshold_days:
        return None
    return FuturesRolloverPlan(
        close_contract=context.contract,
        open_contract=f"{context.contract}_NEXT",  # placeholder; 운영자가 차월물 code 매핑
        days_to_expiry=days_left,
        recommended_window=f"expiry-{threshold_days}d ~ expiry-2d",
        rule_summary=(
            f"근월물 {context.contract}이 {days_left}일 후 만기 — 차월물로 "
            "수동 롤오버 검토 필요. 자동 주문 발신 안 함."
        ),
    )


# ====================================================================
# 1. Trend Following
# ====================================================================


class FuturesTrendFollowingStrategy(FuturesStrategyBase):
    """SMA crossover 기반 추세추종 mock.

    `fast_window` SMA가 `slow_window` SMA 위면 LONG, 아래면 SHORT, 충분한
    데이터 없으면 WATCH. 1계약 이하 권장.
    """

    def __init__(
        self, *,
        fast_window: int = 5,
        slow_window: int = 20,
        risk_pct_of_equity: float = 0.5,
    ):
        if fast_window <= 0 or slow_window <= 0 or fast_window >= slow_window:
            raise ValueError(
                f"need 0 < fast_window < slow_window, "
                f"got fast={fast_window}, slow={slow_window}"
            )
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.risk_pct_of_equity = risk_pct_of_equity

    @property
    def metadata(self) -> FuturesStrategyMetadata:
        return FuturesStrategyMetadata(
            name=f"futures_trend_sma_{self.fast_window}_{self.slow_window}",
            kind="trend",
            description="SMA crossover trend following — mock skeleton (max 1 contract).",
            category={"horizon": "intraday", "market": "domestic_futures"},
        )

    def generate_signal(
        self, context: FuturesStrategyContext,
    ) -> FuturesSignal:
        bars = context.bars
        if len(bars) < self.slow_window:
            return FuturesSignal(
                action=FuturesSignalAction.WATCH,
                contract=context.contract,
                explanation=FuturesSignalExplanation(
                    summary=f"insufficient bars ({len(bars)} < {self.slow_window})",
                    reasons=["data not enough for SMA crossover"],
                ),
            )

        closes = [b.close for b in bars]
        fast = _sma(closes, self.fast_window)
        slow = _sma(closes, self.slow_window)
        assert fast is not None and slow is not None  # length checked above

        rollover = _maybe_rollover(context)

        # 명시적 LONG / SHORT 결정. 동일하면 WATCH.
        if fast > slow:
            action = FuturesSignalAction.OPEN_LONG
            note = f"SMA{self.fast_window}({fast:.0f}) > SMA{self.slow_window}({slow:.0f})"
        elif fast < slow:
            action = FuturesSignalAction.OPEN_SHORT
            note = f"SMA{self.fast_window}({fast:.0f}) < SMA{self.slow_window}({slow:.0f})"
        else:
            return FuturesSignal(
                action=FuturesSignalAction.WATCH,
                contract=context.contract,
                rollover=rollover,
                explanation=FuturesSignalExplanation(
                    summary="SMA crossover flat — no edge",
                    reasons=["fast == slow"],
                ),
            )

        # 만기 임박 + 신규 진입은 보수적으로 WATCH. 운영자가 롤오버 결정 후 다시 평가.
        if rollover is not None:
            return FuturesSignal(
                action=FuturesSignalAction.WATCH,
                contract=context.contract,
                rollover=rollover,
                explanation=FuturesSignalExplanation(
                    summary="trend signal suppressed — contract expiring soon",
                    reasons=[
                        note,
                        f"days_to_expiry={rollover.days_to_expiry} ≤ threshold",
                    ],
                    risk_note="만기 임박 — 신규 진입 회피, 차월물 수동 롤오버 후 재평가",
                ),
            )

        return FuturesSignal(
            action=action,
            contract=context.contract,
            contract_sizing=FuturesContractSizingHint(
                contracts=1, risk_pct_of_equity=self.risk_pct_of_equity,
                note="mock — max 1 contract",
            ),
            exit_plan=FuturesExitPlan(
                stop_loss_pct=1.5, take_profit_pct=3.0,
                liquidation_buffer_pct=7.0,  # LiquidationRiskRule warning band 참조
                rule_summary="ATR/Vol 기반 정밀 exit는 별도 PR — 본 mock은 % 기반",
            ),
            explanation=FuturesSignalExplanation(
                summary=note, reasons=[note],
                risk_note="leverage / margin은 FuturesRiskManager / FuturesMarginRule이 결정",
            ),
        )


# ====================================================================
# 2. Volatility Breakout
# ====================================================================


class FuturesVolatilityBreakoutStrategy(FuturesStrategyBase):
    """Bollinger-style 변동성 돌파 mock.

    최근 `lookback` 봉의 mean ± `band_k × stddev`를 채널로 가정. 종가가 상단
    돌파 → OPEN_LONG, 하단 돌파 → OPEN_SHORT. 변동성이 임계 이상이면 (high
    vol risk) REDUCE_SIZE 또는 WATCH로 강등.
    """

    def __init__(
        self, *,
        lookback: int = 20,
        band_k: float = 2.0,
        max_volatility_pct: float = 5.0,
        risk_pct_of_equity: float = 0.5,
    ):
        if lookback < 2:
            raise ValueError(f"lookback must be >= 2, got {lookback}")
        if band_k <= 0:
            raise ValueError(f"band_k must be positive, got {band_k}")
        if max_volatility_pct <= 0:
            raise ValueError(
                f"max_volatility_pct must be positive, got {max_volatility_pct}"
            )
        self.lookback = lookback
        self.band_k = band_k
        self.max_volatility_pct = max_volatility_pct
        self.risk_pct_of_equity = risk_pct_of_equity

    @property
    def metadata(self) -> FuturesStrategyMetadata:
        return FuturesStrategyMetadata(
            name=f"futures_volbreakout_{self.lookback}_{self.band_k}",
            kind="breakout",
            description="Volatility breakout — mock skeleton (max 1 contract).",
            category={"horizon": "intraday", "market": "domestic_futures"},
        )

    def generate_signal(
        self, context: FuturesStrategyContext,
    ) -> FuturesSignal:
        bars = context.bars
        if len(bars) < self.lookback:
            return FuturesSignal(
                action=FuturesSignalAction.WATCH,
                contract=context.contract,
                explanation=FuturesSignalExplanation(
                    summary=f"insufficient bars ({len(bars)} < {self.lookback})",
                    reasons=["data not enough for vol breakout"],
                ),
            )

        closes = [b.close for b in bars]
        mean   = _sma(closes, self.lookback)
        sd     = _stddev(closes, self.lookback)
        assert mean is not None and sd is not None
        last_close = closes[-1]
        # 변동성을 mean 대비 % 로 표현 — high vol 차단 임계.
        volatility_pct = (sd / mean * 100.0) if mean > 0 else 0.0

        rollover = _maybe_rollover(context)
        upper = mean + self.band_k * sd
        lower = mean - self.band_k * sd

        # 변동성이 임계 초과면 REDUCE_SIZE / WATCH.
        if volatility_pct > self.max_volatility_pct:
            return FuturesSignal(
                action=FuturesSignalAction.REDUCE_SIZE,
                contract=context.contract,
                rollover=rollover,
                explanation=FuturesSignalExplanation(
                    summary=(
                        f"volatility {volatility_pct:.2f}% > "
                        f"max {self.max_volatility_pct}% — reduce size"
                    ),
                    reasons=["high vol regime"],
                    indicators={"volatility_pct": volatility_pct,
                                 "mean": mean, "stddev": sd},
                    risk_note="고변동성 — 자동 진입 회피 권고",
                ),
            )

        if last_close > upper:
            action = FuturesSignalAction.OPEN_LONG
            note = f"close({last_close}) > upper({upper:.0f})"
        elif last_close < lower:
            action = FuturesSignalAction.OPEN_SHORT
            note = f"close({last_close}) < lower({lower:.0f})"
        else:
            return FuturesSignal(
                action=FuturesSignalAction.WATCH,
                contract=context.contract,
                rollover=rollover,
                explanation=FuturesSignalExplanation(
                    summary="inside vol band",
                    reasons=[
                        f"close={last_close} in [{lower:.0f}, {upper:.0f}]",
                    ],
                ),
            )

        if rollover is not None:
            return FuturesSignal(
                action=FuturesSignalAction.WATCH,
                contract=context.contract,
                rollover=rollover,
                explanation=FuturesSignalExplanation(
                    summary="breakout suppressed — contract expiring soon",
                    reasons=[note,
                              f"days_to_expiry={rollover.days_to_expiry}"],
                    risk_note="만기 임박 — 신규 진입 회피, 차월물 수동 롤오버 후 재평가",
                ),
            )

        return FuturesSignal(
            action=action,
            contract=context.contract,
            contract_sizing=FuturesContractSizingHint(
                contracts=1, risk_pct_of_equity=self.risk_pct_of_equity,
                note="mock — max 1 contract; high-vol band breakout",
            ),
            exit_plan=FuturesExitPlan(
                stop_loss_pct=1.0, take_profit_pct=2.5,
                liquidation_buffer_pct=7.0,
                rule_summary="vol-based exit는 별도 PR — 본 mock은 % 기반 단순 exit",
            ),
            explanation=FuturesSignalExplanation(
                summary=note, reasons=[note],
                indicators={"mean": mean, "upper": upper, "lower": lower,
                             "volatility_pct": volatility_pct},
            ),
        )


# ====================================================================
# 3. Hedge
# ====================================================================


class FuturesHedgeStrategy(FuturesStrategyBase):
    """Equity 노출 헤지 mock.

    `context.equity_exposure_krw`가 임계 이상이면 SHORT 헤지 advisory를 만든다.
    음수 노출(이미 short 포지션)은 LONG 헤지. 노출이 미약하면 NO_SIGNAL.

    **실제 hedge 주문을 *직접* 만들지 않는다** — 운영자가 본 advisory를 보고
    수동으로 주문을 결정한다.
    """

    def __init__(
        self, *,
        min_exposure_krw: int = 5_000_000,
        risk_pct_of_equity: float = 0.5,
    ):
        if min_exposure_krw <= 0:
            raise ValueError(
                f"min_exposure_krw must be positive, got {min_exposure_krw}"
            )
        self.min_exposure_krw = min_exposure_krw
        self.risk_pct_of_equity = risk_pct_of_equity

    @property
    def metadata(self) -> FuturesStrategyMetadata:
        return FuturesStrategyMetadata(
            name=f"futures_hedge_{self.min_exposure_krw}",
            kind="hedge",
            description="Equity exposure hedge — mock advisory (max 1 contract).",
            category={"horizon": "session", "market": "domestic_futures"},
        )

    def generate_signal(
        self, context: FuturesStrategyContext,
    ) -> FuturesSignal:
        exposure = context.equity_exposure_krw or 0
        rollover = _maybe_rollover(context)

        # |노출| < 임계면 헤지 무필요.
        if abs(exposure) < self.min_exposure_krw:
            return FuturesSignal(
                action=FuturesSignalAction.NO_SIGNAL,
                contract=context.contract,
                rollover=rollover,
                explanation=FuturesSignalExplanation(
                    summary=(
                        f"exposure {exposure} below hedge threshold "
                        f"{self.min_exposure_krw}"
                    ),
                    reasons=["no hedge needed"],
                ),
            )

        # 양의 equity 노출 → SHORT 헤지. 음의 노출 → LONG 헤지 (드물지만 일관성).
        # 모든 경우에 HEDGE action으로 반환 — 호출자가 contract spec과 매칭.
        return FuturesSignal(
            action=FuturesSignalAction.HEDGE,
            contract=context.contract,
            rollover=rollover,
            contract_sizing=FuturesContractSizingHint(
                contracts=1, risk_pct_of_equity=self.risk_pct_of_equity,
                note="mock hedge advisory — max 1 contract; 운영자 수동 검토",
            ),
            exit_plan=FuturesExitPlan(
                stop_loss_pct=2.0, take_profit_pct=None,
                liquidation_buffer_pct=7.0,
                rule_summary="hedge는 equity 노출이 줄어들면 청산 — 정밀 trigger 별도 PR",
            ),
            explanation=FuturesSignalExplanation(
                summary=(
                    f"hedge advisory: exposure={exposure} >= threshold"
                ),
                reasons=[
                    f"|exposure|={abs(exposure)} ≥ {self.min_exposure_krw}",
                    "direction: SHORT for positive exposure" if exposure > 0
                        else "direction: LONG for negative exposure",
                ],
                risk_note="실제 헤지 주문은 운영자 수동 승인 후만 가능 — 본 신호는 advisory.",
            ),
        )


# ====================================================================
# Module invariants
# ====================================================================
#
# - 본 모듈은 broker / OrderExecutor / route_order import 0건 (정적 grep 가드).
# - 모든 전략의 신호는 `is_order_intent = False` (FuturesSignal 자체 가드).
# - 모든 전략의 contract_sizing은 contracts ≤ 1 (FuturesContractSizingHint 가드).
# - 자동 롤오버 *주문* 발신 0건 — `_maybe_rollover`는 plan만 반환.
