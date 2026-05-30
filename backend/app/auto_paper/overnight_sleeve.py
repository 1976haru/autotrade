"""오버나이트 전략 모의 sleeve — 단타와 *완전 분리* 추적 (계산 전용, advisory).

운영자가 "실패에서 배우는 경험" 목적으로 *모의로만* 돌려보는 오버나이트 전략.

**★중요: 이 전략은 과거 백테스트에서 *탈락*했다.** (비용 후 PF 0.68~0.84, 평균
−20~−40bps, OOS 전·후반 PF<1 — `reports/monday_ready_20260530.md` PART 2.)
따라서 본 sleeve 의 모든 산출물은 `VALIDATION_FAILED_LABEL` 을 *항상* 달고 다닌다 —
운영자가 "이건 검증 안 된 학습용"임을 잊지 않도록.

분리 추적:
- 단타(주간 단타+Council)는 `trade_reason="kis_paper_auto"`.
- 오버나이트는 `trade_reason="overnight_paper"` (본 모듈 `OVERNIGHT_TRADE_REASON`).
- 손익·체결·집계가 단타와 *섞이지 않게* — 리포트에서 각각 따로 집계.
- 자금도 분리 가능: caller 가 *오버나이트 전용 시드*를 주입(단타 시드와 별도).

신호(백테스트와 동일, lookahead 0):
- 전날 미국시장(S&P/나스닥/SOX) 평균 등락 > 임계 AND 전날 거래량/20일평균 ≥ 임계
  → 다음날 *아침 매수* 후보. 청산은 같은 날 종가(또는 익일 시가) — 운영자 트리거.

**본 모듈은 *주문 리스트를 계산만* 한다 — 주문을 생성/전송하지 않는다.**
- broker / OrderExecutor / route_order / KIS API import 0건 (정적 grep 가드).
- `OvernightPlan.is_live_authorization` / `broker_order_sent` / `order_created` /
  `is_order_signal` 항상 False, `validation_passed` 항상 False (dataclass 가드).
실제 모의주문은 별도 sanctioned 경로(execute_kis_paper_auto_order 에 trade_reason
="overnight_paper" 로 위임)가 운영자 트리거 시 수행 — 본 plan 은 그 입력 후보일 뿐.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from app.backtest.cost_model import BacktestCostModel, DEFAULT_COST

# 분리 추적용 태그 — 단타("kis_paper_auto")와 절대 겹치지 않음.
OVERNIGHT_TRADE_REASON = "overnight_paper"
DAYTRADE_TRADE_REASON = "kis_paper_auto"

# ★검증 미통과 라벨 — 화면/기록에 항상 노출. 빼면 안 됨.
VALIDATION_FAILED_LABEL = (
    "검증 미통과 — 과거 백테스트에서 비용을 넘지 못해 탈락한 전략입니다. "
    "수익 목적이 아니라 *학습·경험* 목적의 모의 실행입니다."
)

# 신호 기본 파라미터 (백테스트와 동일; 모의 학습용이라 가장 단순한 조합).
DEFAULT_US_THRESHOLD = 0.0      # 전날 미국시장 평균 등락 > 0 (상승)
DEFAULT_VOLUME_MIN = 1.0        # 전날 거래량 / 20일평균 ≥ 1.0
DEFAULT_VOL_LOOKBACK = 20
DEFAULT_MAX_NEW_POSITIONS = 5   # 아침 매수 후보 상한 (학습용 소량)
DEFAULT_MIN_TRADE_KRW = 50_000


@dataclass(frozen=True)
class OvernightConfig:
    us_threshold: float = DEFAULT_US_THRESHOLD
    volume_min: float = DEFAULT_VOLUME_MIN
    vol_lookback: int = DEFAULT_VOL_LOOKBACK
    max_new_positions: int = DEFAULT_MAX_NEW_POSITIONS
    exit_mode: str = "close"          # "close"(같은날 종가) | "nextopen"(익일 시가)
    overnight_seed_krw: float = 0.0   # 오버나이트 전용 시드 (단타와 분리)
    per_symbol_krw: float = 0.0       # 종목당 배정 (0이면 seed/max_new 균등)
    min_trade_krw: float = DEFAULT_MIN_TRADE_KRW

    def __post_init__(self) -> None:
        if self.max_new_positions < 1:
            raise ValueError("max_new_positions must be >= 1")
        if self.exit_mode not in ("close", "nextopen"):
            raise ValueError("exit_mode must be 'close' or 'nextopen'")


@dataclass(frozen=True)
class OvernightSignalInputs:
    """as-of(전날 종가) 까지의 역사적 데이터만 — lookahead 0.

    price_history: symbol -> 시간순 종가(최근이 마지막).
    volume_history: symbol -> 시간순 거래량(최근이 마지막).
    us_prev_return: 전날 미국시장 평균 일간수익(이미 KR 개장 전 확정된 값).
    prices: symbol -> 다음날 아침 *예상* 진입 참고가(전날 종가 proxy; 월요일 실시세로 교체).
    """
    price_history: Mapping[str, Sequence[float]]
    volume_history: Mapping[str, Sequence[float]]
    us_prev_return: float | None
    prices: Mapping[str, float]


@dataclass(frozen=True)
class OvernightOrderItem:
    symbol: str
    side: str                # 항상 "BUY" (아침 매수)
    quantity: int
    price: float
    notional: float
    est_cost_krw: float
    volume_ratio: float
    reason: str


@dataclass(frozen=True)
class OvernightPlan:
    """월요일 아침 *오버나이트 매수 후보* 리스트. **실제 주문 아님 + 검증 미통과.**"""
    as_of: str
    trade_reason: str
    validation_failed_label: str
    us_prev_return: float | None
    signal_active: bool
    overnight_seed_krw: float
    orders: tuple[OvernightOrderItem, ...]
    total_buy_krw: float
    total_est_cost_krw: float
    notes: tuple[str, ...] = ()
    # 안전 invariant — 항상 고정
    is_order_signal: bool = False
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    validation_passed: bool = False        # ★ 절대 True 안 됨

    def __post_init__(self) -> None:
        for flag in ("is_order_signal", "is_live_authorization", "broker_order_sent", "order_created"):
            if getattr(self, flag) is not False:
                raise ValueError(f"{flag} must be False — overnight sleeve computes order lists only")
        if self.validation_passed is not False:
            raise ValueError("validation_passed must be False — overnight strategy FAILED backtest")
        if self.trade_reason != OVERNIGHT_TRADE_REASON:
            raise ValueError(f"trade_reason must be {OVERNIGHT_TRADE_REASON!r} (separate from day-trade)")
        if not self.validation_failed_label:
            raise ValueError("validation_failed_label must be present — do not strip the label")


def _volume_ratio(volumes: Sequence[float], lookback: int) -> float | None:
    """전날 거래량 / 직전 lookback 평균. as-of 데이터만 사용."""
    if len(volumes) < lookback + 1:
        return None
    window = volumes[-1 - lookback:-1]   # 전날 *이전* lookback개 (전날 제외 평균)
    avg = sum(window) / len(window) if window else 0.0
    prev_vol = volumes[-1]
    if avg <= 0:
        return None
    return prev_vol / avg


def build_overnight_plan(
    *,
    as_of: str,
    signals: OvernightSignalInputs,
    cfg: OvernightConfig | None = None,
    cost_model: BacktestCostModel | None = None,
) -> OvernightPlan:
    """전날 미국↑ + 거래량 신호로 *아침 매수 후보* 계산. 계산만, 발사 안 함."""
    cfg = cfg or OvernightConfig()
    cm = cost_model or DEFAULT_COST
    notes: list[str] = []

    # 1) 시장 신호: 전날 미국시장 상승?
    us = signals.us_prev_return
    if us is None:
        signal_active = False
        notes.append("전날 미국시장 데이터 없음 → 신호 비활성(매수 후보 0)")
    elif us <= cfg.us_threshold:
        signal_active = False
        notes.append(f"전날 미국시장 평균 {us*100:.2f}% ≤ 임계 {cfg.us_threshold*100:.2f}% → 매수 안 함")
    else:
        signal_active = True
        notes.append(f"전날 미국시장 평균 +{us*100:.2f}% > 임계 → 거래량 통과 종목 아침 매수 후보")

    orders: list[OvernightOrderItem] = []
    total_buy = 0.0
    total_cost = 0.0

    if signal_active:
        # 2) 종목별 거래량 필터 → 후보 점수(거래량비율 큰 순)
        candidates = []
        for sym, vols in signals.volume_history.items():
            vr = _volume_ratio(vols, cfg.vol_lookback)
            px = signals.prices.get(sym)
            if vr is None or px is None or px <= 0:
                continue
            if vr >= cfg.volume_min:
                candidates.append((sym, vr, px))
        candidates.sort(key=lambda x: x[1], reverse=True)
        picks = candidates[: cfg.max_new_positions]

        # 3) 종목당 배정액
        if cfg.per_symbol_krw > 0:
            per_symbol = cfg.per_symbol_krw
        elif cfg.overnight_seed_krw > 0 and picks:
            per_symbol = cfg.overnight_seed_krw / max(1, min(len(picks), cfg.max_new_positions))
        else:
            per_symbol = 0.0
            notes.append("오버나이트 시드/종목당 배정 미지정 → 수량 0 (월요일 시드 입력 필요)")

        commission = cm.commission_bps / 1e4
        slippage = cm.slippage_bps / 1e4
        for sym, vr, px in picks:
            if per_symbol < cfg.min_trade_krw:
                continue
            qty = int(per_symbol // px)
            if qty <= 0:
                notes.append(f"{sym}: 배정 {per_symbol:,.0f}원으로 1주({px:,.0f}원) 미달 → 제외")
                continue
            notional = qty * px
            est_cost = notional * (commission + slippage)  # 매수레그 비용
            total_buy += notional
            total_cost += est_cost
            orders.append(OvernightOrderItem(
                symbol=sym, side="BUY", quantity=qty, price=float(px),
                notional=round(notional, 1), est_cost_krw=round(est_cost, 1),
                volume_ratio=round(vr, 3),
                reason=f"전날 미국↑+거래량 {vr:.2f}배 → 아침 매수 (검증 미통과·학습용)",
            ))

    return OvernightPlan(
        as_of=as_of,
        trade_reason=OVERNIGHT_TRADE_REASON,
        validation_failed_label=VALIDATION_FAILED_LABEL,
        us_prev_return=us,
        signal_active=signal_active,
        overnight_seed_krw=float(cfg.overnight_seed_krw),
        orders=tuple(orders),
        total_buy_krw=round(total_buy, 1),
        total_est_cost_krw=round(total_cost, 1),
        notes=tuple(notes),
    )


def plan_to_dict(plan: OvernightPlan) -> dict:
    """리포트/UI 직렬화 (broker 필드 0건, 검증 미통과 라벨 carry)."""
    return {
        "as_of": plan.as_of,
        "trade_reason": plan.trade_reason,
        "validation_failed_label": plan.validation_failed_label,
        "validation_passed": plan.validation_passed,
        "us_prev_return": plan.us_prev_return,
        "signal_active": plan.signal_active,
        "overnight_seed_krw": plan.overnight_seed_krw,
        "exit_trigger": "운영자 트리거 — 같은 날 종가 또는 익일 시가 청산",
        "orders": [
            {"symbol": o.symbol, "side": o.side, "quantity": o.quantity, "price": o.price,
             "notional": o.notional, "est_cost_krw": o.est_cost_krw,
             "volume_ratio": o.volume_ratio, "reason": o.reason}
            for o in plan.orders
        ],
        "total_buy_krw": plan.total_buy_krw,
        "total_est_cost_krw": plan.total_est_cost_krw,
        "notes": list(plan.notes),
        "is_order_signal": plan.is_order_signal,
        "is_live_authorization": plan.is_live_authorization,
        "broker_order_sent": plan.broker_order_sent,
        "order_created": plan.order_created,
    }
