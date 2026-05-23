"""P-25: 사후 성과 라벨링 — decision episode 의 판단 후 실제 결과 평가.

AI Agent 가 BUY/SELL/HOLD 판단을 내린 뒤 5분/10분/30분/60분/종가 시점의 실제
결과를 계산해 episode.outcome 에 저장한다. "Agent 가 맞았는지/틀렸는지" 분석용.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *기록/평가 전용* — broker / OrderExecutor / route_order / 외부 HTTP
  import 0건, 주문을 만들지 않는다.
- secret / API key / 계좌번호 carry 0건.
- `OutcomeLabel.is_live_authorization=False` / `is_order_signal=False` /
  `contains_secret=False` 영구.
- 결정적(deterministic) — 같은 입력 → 같은 결과. 시장 데이터 부족 시 PENDING /
  UNAVAILABLE 로 남기며 *episode 저장을 실패시키지 않는다*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── 상태 ──
STATUS_PENDING     = "PENDING"      # 아직 평가 시간이 안 됨 / 데이터 대기
STATUS_PARTIAL     = "PARTIAL"      # 일부 horizon 만 평가됨
STATUS_COMPLETE    = "COMPLETE"     # 종가까지 평가 완료
STATUS_UNAVAILABLE = "UNAVAILABLE"  # 장 마감 후에도 데이터 없음

# ── 라벨 ──
LABEL_PROFITABLE         = "PROFITABLE"
LABEL_LOSS               = "LOSS"
LABEL_NEUTRAL            = "NEUTRAL"
LABEL_MISSED_OPPORTUNITY = "MISSED_OPPORTUNITY"   # HOLD 인데 이후 상승
LABEL_AVOIDED_LOSS       = "AVOIDED_LOSS"         # HOLD 인데 이후 하락
LABEL_PENDING            = "OUTCOME_PENDING"
LABEL_UNAVAILABLE        = "OUTCOME_UNAVAILABLE"

HORIZONS = (5, 10, 30, 60)            # 분
# 수익/손실 판정 임계 (%) — |return| < threshold 는 NEUTRAL.
DEFAULT_FLAT_THRESHOLD_PCT = 0.1


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class OutcomeLabel:
    """사후 성과 — episode.outcome 에 저장되는 안전 payload."""

    status:        str
    label:         str | None = None
    entry_price:   float | None = None
    entry_basis:   str | None = None   # avg_fill_price / request_price / market_snapshot
    return_5m:     float | None = None
    return_10m:    float | None = None
    return_30m:    float | None = None
    return_60m:    float | None = None
    return_close:  float | None = None
    max_favorable_excursion: float | None = None
    max_adverse_excursion:   float | None = None
    realized_pnl:  float | None = None
    evaluated_action: str | None = None
    horizons_available: list[int] = field(default_factory=list)
    note:          str = ""

    contains_secret:       bool = False
    is_live_authorization: bool = False
    is_order_signal:       bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status":        self.status,
            "label":         self.label,
            "entry_price":   self.entry_price,
            "entry_basis":   self.entry_basis,
            "return_5m":     self.return_5m,
            "return_10m":    self.return_10m,
            "return_30m":    self.return_30m,
            "return_60m":    self.return_60m,
            "return_close":  self.return_close,
            "max_favorable_excursion": self.max_favorable_excursion,
            "max_adverse_excursion":   self.max_adverse_excursion,
            "realized_pnl":  self.realized_pnl,
            "evaluated_action": self.evaluated_action,
            "horizons_available": list(self.horizons_available),
            "note":          self.note,
            "contains_secret":       False,
            "is_live_authorization": False,
            "is_order_signal":       False,
        }


def _resolve_entry_price(episode: dict[str, Any]) -> tuple[float | None, str | None]:
    """entry_price 우선순위: order_quality.avg_fill_price → request_price →
    market_snapshot.price."""
    kor = episode.get("kis_order_result") if isinstance(episode, dict) else None
    oq = kor.get("order_quality") if isinstance(kor, dict) else None
    if isinstance(oq, dict):
        v = _f(oq.get("avg_fill_price"))
        if v and v > 0:
            return v, "avg_fill_price"
        v = _f(oq.get("request_price"))
        if v and v > 0:
            return v, "request_price"
    ms = episode.get("market_snapshot") if isinstance(episode, dict) else None
    if isinstance(ms, dict):
        v = _f(ms.get("price"))
        if v and v > 0:
            return v, "market_snapshot"
    return None, None


def _pct(future: float, entry: float) -> float:
    return round((future - entry) / entry * 100.0, 4)


def evaluate_outcome(
    *,
    episode: dict[str, Any],
    future_prices: dict[int, float] | None = None,
    close_price: float | None = None,
    realized_pnl: float | None = None,
    market_closed: bool = False,
    flat_threshold_pct: float = DEFAULT_FLAT_THRESHOLD_PCT,
) -> OutcomeLabel:
    """episode + 사후 시장 데이터 → OutcomeLabel (deterministic, 예외 0).

    `future_prices`: {5: price5, 10: price10, 30: ..., 60: ...} (분: 가격). 일부만
    있으면 PARTIAL. `close_price`: 당일 종가. `market_closed`: 장 마감 여부 —
    데이터 없고 마감이면 UNAVAILABLE, 마감 전이면 PENDING.

    BUY: return = (future - entry)/entry*100, MFE/MAE = max/min excursion.
    SELL(보유 청산): realized_pnl 우선; price 기반은 (entry - future)/entry*100
      (청산 후 하락이 favorable). HOLD: reference=entry, 상승→MISSED_OPPORTUNITY,
      하락→AVOIDED_LOSS.
    """
    action = str(episode.get("final_action", "") or "").upper()
    future_prices = future_prices or {}
    entry, basis = _resolve_entry_price(episode)

    have_any = bool(future_prices) or close_price is not None
    if entry is None or entry <= 0 or not have_any:
        status = STATUS_UNAVAILABLE if market_closed else STATUS_PENDING
        return OutcomeLabel(
            status=status,
            label=(LABEL_UNAVAILABLE if status == STATUS_UNAVAILABLE else LABEL_PENDING),
            entry_price=entry, entry_basis=basis, evaluated_action=action,
            realized_pnl=_f(realized_pnl),
            note=("시장 데이터 부족" if entry is None or entry <= 0
                  else "사후 가격 데이터 대기 중"),
        )

    # 방향성: BUY/HOLD 는 (price-entry), SELL(청산)은 (entry-price) 가 favorable.
    sign = -1.0 if action == "SELL" else 1.0

    horizon_returns: dict[int, float] = {}
    excursions: list[float] = []
    for h in HORIZONS:
        fp = _f(future_prices.get(h))
        if fp is None or fp <= 0:
            continue
        r = _pct(fp, entry) * sign
        horizon_returns[h] = r
        excursions.append(r)

    return_close = None
    cp = _f(close_price)
    if cp is not None and cp > 0:
        return_close = _pct(cp, entry) * sign
        excursions.append(return_close)

    mfe = round(max(excursions), 4) if excursions else None
    mae = round(min(excursions), 4) if excursions else None

    # 상태.
    horizons_available = sorted(horizon_returns.keys())
    if return_close is not None:
        status = STATUS_COMPLETE
    elif horizons_available:
        status = STATUS_PARTIAL
    else:
        status = STATUS_UNAVAILABLE if market_closed else STATUS_PENDING

    # 최종 판정용 return — 종가 우선, 없으면 가장 긴 horizon.
    final_return = return_close
    if final_return is None and horizons_available:
        final_return = horizon_returns[horizons_available[-1]]

    # 라벨.
    label: str | None = None
    if status in (STATUS_PENDING, STATUS_UNAVAILABLE) and final_return is None:
        label = (LABEL_UNAVAILABLE if status == STATUS_UNAVAILABLE else LABEL_PENDING)
    elif final_return is not None:
        if action == "HOLD":
            # HOLD: 기준가 대비 *실제 가격 방향* (sign=+1) 기준.
            if final_return > flat_threshold_pct:
                label = LABEL_MISSED_OPPORTUNITY    # 안 샀는데 올랐다
            elif final_return < -flat_threshold_pct:
                label = LABEL_AVOIDED_LOSS          # 안 샀는데 내렸다
            else:
                label = LABEL_NEUTRAL
        else:
            # BUY/SELL: favorable(sign 적용) 기준.
            if final_return > flat_threshold_pct:
                label = LABEL_PROFITABLE
            elif final_return < -flat_threshold_pct:
                label = LABEL_LOSS
            else:
                label = LABEL_NEUTRAL

    # SELL 은 realized_pnl 우선 — label 보정 (현물 청산).
    rp = _f(realized_pnl)
    if action == "SELL" and rp is not None and status in (STATUS_COMPLETE, STATUS_PARTIAL):
        if rp > 0:
            label = LABEL_PROFITABLE
        elif rp < 0:
            label = LABEL_LOSS
        else:
            label = LABEL_NEUTRAL

    return OutcomeLabel(
        status=status, label=label, entry_price=entry, entry_basis=basis,
        return_5m=horizon_returns.get(5), return_10m=horizon_returns.get(10),
        return_30m=horizon_returns.get(30), return_60m=horizon_returns.get(60),
        return_close=return_close, max_favorable_excursion=mfe,
        max_adverse_excursion=mae, realized_pnl=rp, evaluated_action=action,
        horizons_available=horizons_available,
        note=("종가까지 평가 완료" if status == STATUS_COMPLETE
              else "일부 horizon 만 평가됨" if status == STATUS_PARTIAL
              else "평가 대기/불가"),
    )


def outcome_summary(outcome: dict[str, Any] | None) -> dict[str, Any]:
    """outcome dict → 목록 표시용 요약."""
    if not isinstance(outcome, dict):
        return {"status": STATUS_PENDING, "label": LABEL_PENDING}
    return {
        "status":       outcome.get("status"),
        "label":        outcome.get("label"),
        "return_5m":    outcome.get("return_5m"),
        "return_10m":   outcome.get("return_10m"),
        "return_30m":   outcome.get("return_30m"),
        "return_60m":   outcome.get("return_60m"),
        "return_close": outcome.get("return_close"),
        "max_favorable_excursion": outcome.get("max_favorable_excursion"),
        "max_adverse_excursion":   outcome.get("max_adverse_excursion"),
    }


__all__ = [
    "OutcomeLabel", "evaluate_outcome", "outcome_summary",
    "STATUS_PENDING", "STATUS_PARTIAL", "STATUS_COMPLETE", "STATUS_UNAVAILABLE",
    "LABEL_PROFITABLE", "LABEL_LOSS", "LABEL_NEUTRAL", "LABEL_MISSED_OPPORTUNITY",
    "LABEL_AVOIDED_LOSS", "LABEL_PENDING", "LABEL_UNAVAILABLE", "HORIZONS",
]
