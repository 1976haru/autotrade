"""분산70 + 모멘텀30 + 위험장치 blend — 리밸런싱 *주문 리스트 계산* 모듈 (advisory).

검증 산출물(`reports/backtest/momentum_blend_20260530.md`)의 추천 설계를 *실행 가능한
계산 로직*으로 옮긴 것:

  목표 = 분산 70% (universe 균등분산) + 모멘텀 30% tilt (12개월 순위 상위 N)
         × 하락장 방어 (KOSPI MA200, 전일 t-1 기준 — bear면 모멘텀 sleeve off)
         × 변동성 타게팅 15% (trailing 변동성으로 전체 노출 scale, ≤100%)

**본 모듈은 *주문 리스트를 계산만* 한다 — 실제 주문을 생성/전송하지 않는다.**
- broker / OrderExecutor / route_order / KIS API / `app.execution.*` import 0건 (정적 grep 가드).
- `RebalancePlan.is_live_authorization` / `broker_order_sent` / `order_created` /
  `is_order_signal` 항상 False (dataclass `__post_init__` ValueError 가드).
- lookahead 0: 모멘텀 순위·regime·변동성은 모두 *as-of 날짜의 종가까지만* 사용.
- 비용은 `app.backtest.cost_model`(순수) 재사용 — 33bps 왕복 추정.

실제 주문은 *별도의 sanctioned 경로*(route_order → RiskManager → PermissionGate →
OrderExecutor)가 운영자 승인 하에 수행한다. 본 plan은 그 입력 *후보*일 뿐이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from app.backtest.cost_model import BacktestCostModel, DEFAULT_COST

# 추천 설계 기본 파라미터 (reports/backtest/momentum_blend_20260530.md)
DEFAULT_DIVERSIFIED_WEIGHT = 0.70
DEFAULT_MOMENTUM_WEIGHT = 0.30
DEFAULT_MOMENTUM_TOP_N = 10
DEFAULT_MOMENTUM_LOOKBACK = 252      # 12개월
DEFAULT_MOMENTUM_GAP = 21           # 최근 1개월 제외 (12-1 모멘텀)
DEFAULT_TARGET_VOL = 0.15           # 변동성 타게팅 15%
DEFAULT_VOL_LOOKBACK = 60
DEFAULT_REGIME_MA = 200
TRADING_DAYS = 252
DEFAULT_MIN_TRADE_KRW = 50_000      # 이보다 작은 리밸런싱 주문은 노이즈 → 생략


@dataclass(frozen=True)
class AllocationConfig:
    diversified_weight: float = DEFAULT_DIVERSIFIED_WEIGHT
    momentum_weight: float = DEFAULT_MOMENTUM_WEIGHT
    momentum_top_n: int = DEFAULT_MOMENTUM_TOP_N
    momentum_lookback: int = DEFAULT_MOMENTUM_LOOKBACK
    momentum_gap: int = DEFAULT_MOMENTUM_GAP
    target_vol: float = DEFAULT_TARGET_VOL
    vol_lookback: int = DEFAULT_VOL_LOOKBACK
    regime_ma: int = DEFAULT_REGIME_MA
    regime_defense: bool = True
    vol_targeting: bool = True
    min_trade_krw: float = DEFAULT_MIN_TRADE_KRW

    def __post_init__(self) -> None:
        if abs(self.diversified_weight + self.momentum_weight - 1.0) > 1e-9:
            raise ValueError("diversified_weight + momentum_weight must equal 1.0")
        if not (0.0 <= self.diversified_weight <= 1.0):
            raise ValueError("weights must be in [0,1]")
        if self.momentum_top_n < 1:
            raise ValueError("momentum_top_n must be >= 1")


@dataclass(frozen=True)
class SignalInputs:
    """as-of 날짜까지의 *역사적* 종가만 담는다 (lookahead 0).

    price_history: symbol -> 시간순 종가 시퀀스(가장 최근이 마지막). as-of 종가 포함.
    index_history: 시장지수(KOSPI 등) 종가 시퀀스 — 하락장 방어 판정용.
    """
    price_history: Mapping[str, Sequence[float]]
    index_history: Sequence[float] = field(default_factory=tuple)


@dataclass(frozen=True)
class TargetAllocation:
    """계산된 목표 비중 (현금 + 종목별). 합 ≤ 1.0 (변동성 타게팅으로 현금 보유 가능)."""
    weights: Mapping[str, float]          # symbol -> target weight (0..1)
    cash_weight: float                    # 0..1
    gross_exposure: float                 # 1 - cash_weight
    regime_is_bull: bool
    vol_scale: float                      # 변동성 타게팅 배수 (0..1)
    momentum_picks: tuple[str, ...]
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        total = sum(self.weights.values()) + self.cash_weight
        if total - 1.0 > 1e-6:
            raise ValueError(f"weights+cash must be <= 1.0 (got {total})")
        for s, w in self.weights.items():
            if w < -1e-9:
                raise ValueError(f"negative weight for {s}: {w}")


@dataclass(frozen=True)
class OrderLineItem:
    symbol: str
    side: str            # "BUY" | "SELL"
    quantity: int        # 정수 주(株)
    price: float         # 참고 시세 (as-of 종가)
    notional: float      # quantity * price
    est_cost_krw: float  # 추정 거래비용
    reason: str


@dataclass(frozen=True)
class RebalancePlan:
    """월요일 장 열리면 *검토할* 리밸런싱 주문 리스트. **실제 주문 아님.**"""
    as_of: str
    total_equity_krw: float
    target: TargetAllocation
    orders: tuple[OrderLineItem, ...]
    total_buy_krw: float
    total_sell_krw: float
    total_est_cost_krw: float
    warnings: tuple[str, ...] = ()
    # 안전 invariant — 항상 False (계산 전용)
    is_order_signal: bool = False
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False

    def __post_init__(self) -> None:
        for flag in ("is_order_signal", "is_live_authorization", "broker_order_sent", "order_created"):
            if getattr(self, flag) is not False:
                raise ValueError(f"{flag} must be False — this module only computes order lists")


# ----------------------------------------------------------------------------
# signal helpers (lookahead-free: only history up to and including as-of close)
# ----------------------------------------------------------------------------
def _pct_returns(closes: Sequence[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev and prev > 0:
            out.append(closes[i] / prev - 1.0)
    return out


def _std(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return var ** 0.5


def momentum_score(closes: Sequence[float], lookback: int, gap: int) -> float | None:
    """12-1 모멘텀: (close[-1-gap] / close[-1-gap-lookback]) - 1. as-of 종가까지만 사용."""
    need = gap + lookback + 1
    if len(closes) < need:
        return None
    recent = closes[-1 - gap]
    old = closes[-1 - gap - lookback]
    if old is None or old <= 0:
        return None
    return recent / old - 1.0


def rank_momentum(history: Mapping[str, Sequence[float]], cfg: AllocationConfig) -> list[tuple[str, float]]:
    scores = []
    for sym, closes in history.items():
        sc = momentum_score(closes, cfg.momentum_lookback, cfg.momentum_gap)
        if sc is not None:
            scores.append((sym, sc))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores


def regime_is_bull(index_history: Sequence[float], ma: int) -> bool | None:
    """KOSPI as-of 종가 > MA(ma) 이면 bull. 데이터 부족 시 None (보수적으로 bull 취급 X)."""
    if len(index_history) < ma:
        return None
    window = index_history[-ma:]
    moving_avg = sum(window) / ma
    return index_history[-1] > moving_avg


def vol_target_scale(closes_index: Sequence[float], cfg: AllocationConfig) -> float:
    """전체 포트폴리오 노출 배수 = target_vol / trailing_vol(연율), ≤1.0. 데이터 부족 시 1.0."""
    rets = _pct_returns(closes_index[-(cfg.vol_lookback + 1):]) if len(closes_index) > 1 else []
    if len(rets) < max(20, cfg.vol_lookback // 2):
        return 1.0
    ann_vol = _std(rets) * (TRADING_DAYS ** 0.5)
    if ann_vol <= 0:
        return 1.0
    return min(1.0, cfg.target_vol / ann_vol)


# ----------------------------------------------------------------------------
# target allocation
# ----------------------------------------------------------------------------
def build_target_allocation(
    universe: Sequence[str],
    signals: SignalInputs,
    cfg: AllocationConfig | None = None,
) -> TargetAllocation:
    """추천 설계의 목표 비중 계산 (현금 + 종목별). 주문 아님 — 비중만."""
    cfg = cfg or AllocationConfig()
    notes: list[str] = []

    # 1) 분산 sleeve: universe 균등분산 (가격 데이터 있는 종목만)
    div_syms = [s for s in universe if s in signals.price_history and len(signals.price_history[s]) > 0]
    if not div_syms:
        raise ValueError("no symbols with price history in universe")
    div_w = {s: 1.0 / len(div_syms) for s in div_syms}

    # 2) 모멘텀 sleeve: 상위 N 균등 (lookahead-free 순위)
    ranked = rank_momentum(signals.price_history, cfg)
    picks = [s for s, _ in ranked[: cfg.momentum_top_n]]
    if picks:
        mom_w = {s: 1.0 / len(picks) for s in picks}
    else:
        mom_w = {}
        notes.append("모멘텀 순위 산출 불가(데이터 부족) → 모멘텀 sleeve 0, 분산만")

    # 3) 하락장 방어: bear 면 모멘텀 비중 → 분산 sleeve 로 흡수 (모멘텀 off)
    bull = regime_is_bull(signals.index_history, cfg.regime_ma)
    mom_weight = cfg.momentum_weight
    if cfg.regime_defense:
        if bull is None:
            mom_weight = 0.0
            notes.append("지수 데이터 부족 → 하락장 방어 보수적 발동(모멘텀 off)")
        elif not bull:
            mom_weight = 0.0
            notes.append("하락장(지수<MA200) → 모멘텀 sleeve off, 분산만 보유")
    div_weight = 1.0 - mom_weight

    # 4) blend (sleeve 합성)
    combined: dict[str, float] = {}
    for s, w in div_w.items():
        combined[s] = combined.get(s, 0.0) + div_weight * w
    for s, w in mom_w.items():
        combined[s] = combined.get(s, 0.0) + mom_weight * w

    # 5) 변동성 타게팅: 전체 노출 scale (현금으로 나머지)
    scale = vol_target_scale(signals.index_history, cfg) if cfg.vol_targeting else 1.0
    if scale < 1.0:
        notes.append(f"변동성 타게팅: 노출 {scale*100:.0f}% (나머지 현금)")
    scaled = {s: w * scale for s, w in combined.items()}
    gross = sum(scaled.values())
    cash = max(0.0, 1.0 - gross)

    return TargetAllocation(
        weights=scaled, cash_weight=cash, gross_exposure=gross,
        regime_is_bull=bool(bull) if bull is not None else False,
        vol_scale=scale, momentum_picks=tuple(picks), notes=tuple(notes),
    )


# ----------------------------------------------------------------------------
# rebalance plan (order LIST — calculation only)
# ----------------------------------------------------------------------------
def build_rebalance_plan(
    *,
    as_of: str,
    total_equity_krw: float,
    current_holdings: Mapping[str, int],       # symbol -> 현재 보유 수량(주)
    prices: Mapping[str, float],               # symbol -> as-of 종가
    target: TargetAllocation,
    cost_model: BacktestCostModel | None = None,
    min_trade_krw: float = DEFAULT_MIN_TRADE_KRW,
) -> RebalancePlan:
    """현재 보유 vs 목표 비중 차이 → 정수주 BUY/SELL 주문 리스트. **계산만, 발사 안 함.**"""
    cm = cost_model or DEFAULT_COST
    warnings: list[str] = []
    if total_equity_krw <= 0:
        raise ValueError("total_equity_krw must be > 0")

    # 목표 금액
    target_value = {s: w * total_equity_krw for s, w in target.weights.items()}
    # 모든 관련 종목 (목표 ∪ 보유)
    all_syms = set(target_value) | set(current_holdings)

    orders: list[OrderLineItem] = []
    total_buy = 0.0
    total_sell = 0.0
    total_cost = 0.0
    commission = cm.commission_bps / 1e4
    slippage = cm.slippage_bps / 1e4
    tax = cm.tax_bps / 1e4

    for sym in sorted(all_syms):
        px = prices.get(sym)
        cur_qty = int(current_holdings.get(sym, 0))
        if px is None or px <= 0:
            if cur_qty > 0:
                warnings.append(f"{sym}: 시세 없음 — 매도 계산 보류(수동 확인)")
            continue
        cur_value = cur_qty * px
        tgt_value = target_value.get(sym, 0.0)
        diff = tgt_value - cur_value
        if abs(diff) < min_trade_krw:
            continue
        qty = int(abs(diff) // px)   # 정수주, 보수적으로 내림
        if qty <= 0:
            continue
        notional = qty * px
        if diff > 0:
            side = "BUY"
            est_cost = notional * (commission + slippage)
            total_buy += notional
            reason = "목표 비중까지 매수" + (" (모멘텀 상위)" if sym in target.momentum_picks else " (분산)")
        else:
            side = "SELL"
            est_cost = notional * (commission + slippage + tax)
            total_sell += notional
            reason = "목표 비중까지 매도/축소"
        total_cost += est_cost
        orders.append(OrderLineItem(
            symbol=sym, side=side, quantity=qty, price=float(px),
            notional=round(notional, 1), est_cost_krw=round(est_cost, 1), reason=reason,
        ))

    # 검증 경고
    gross = target.gross_exposure
    if gross > 1.0 + 1e-6:
        warnings.append(f"목표 노출 {gross:.2f} > 1.0 (레버리지 없음 원칙 위반 의심)")
    return RebalancePlan(
        as_of=as_of, total_equity_krw=float(total_equity_krw), target=target,
        orders=tuple(orders),
        total_buy_krw=round(total_buy, 1), total_sell_krw=round(total_sell, 1),
        total_est_cost_krw=round(total_cost, 1), warnings=tuple(warnings),
    )


def plan_to_dict(plan: RebalancePlan) -> dict:
    """리포트/JSON 직렬화용 (broker 필드 0건)."""
    return {
        "as_of": plan.as_of,
        "total_equity_krw": plan.total_equity_krw,
        "target": {
            "cash_weight": round(plan.target.cash_weight, 4),
            "gross_exposure": round(plan.target.gross_exposure, 4),
            "regime_is_bull": plan.target.regime_is_bull,
            "vol_scale": round(plan.target.vol_scale, 4),
            "momentum_picks": list(plan.target.momentum_picks),
            "weights": {s: round(w, 4) for s, w in plan.target.weights.items()},
            "notes": list(plan.target.notes),
        },
        "orders": [
            {"symbol": o.symbol, "side": o.side, "quantity": o.quantity,
             "price": o.price, "notional": o.notional, "est_cost_krw": o.est_cost_krw,
             "reason": o.reason}
            for o in plan.orders
        ],
        "total_buy_krw": plan.total_buy_krw,
        "total_sell_krw": plan.total_sell_krw,
        "total_est_cost_krw": plan.total_est_cost_krw,
        "warnings": list(plan.warnings),
        "is_order_signal": plan.is_order_signal,
        "is_live_authorization": plan.is_live_authorization,
        "broker_order_sent": plan.broker_order_sent,
        "order_created": plan.order_created,
    }
