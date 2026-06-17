"""트레일링 스탑 1·2단계 — 최고가 추적(측정) + 섀도 비교(로그만). *동작 변경 0*.

설계 docs/design/trailing_stop.md. ★봇 매도 결정에 **미사용** — 봇은 기존 고정 익절(+3%)/
손절(−1.5%)대로 그대로 거래한다. 본 모듈은 보유 종목의 장중 최고가를 영속 추적하고,
"트레일링이었다면 청산했을지"를 *계산·로그*만 한다(실제 매도 0).

broker / route_order / OrderExecutor / driver_bridge import 0건(정적 가드). 주문 0건.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.models import PositionHighWatermark, TrailingShadowOutcome

_log = logging.getLogger("autotrade.trailing.shadow")
_KST = timezone(timedelta(hours=9))

# 기본 파라미터(설계) — 활성선 +3%, 트레일링 폭 2%. 추후 런타임 스테퍼로 노출(3단계).
ACTIVATION_PCT_DEFAULT = 3.0
TRAILING_PCT_DEFAULT = 2.0


def _kst_date(dt: datetime | None) -> Any:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).date()


def _seed_hwm(entry: int, cur: int, activation_pct: float) -> int:
    """재시작/신규 폴백 — max(현재가, 진입가×(1+활성%)). 활성선 아래로 안 내려감."""
    return max(int(cur), int(round(entry * (1 + activation_pct / 100.0))))


@dataclass(frozen=True)
class ShadowTrailingSignal:
    """섀도 트레일링 청산 시그널 — *기록 전용*, 실제 매도 아님(is_order_signal=False)."""
    symbol:            str
    entry_price:       int
    high_watermark:    int
    current_price:     int
    activated:         bool
    trailing_would_exit: bool
    # 비교 지표(T3): 트레일링 청산 수익률 vs 고정 익절(+activation) 수익률 vs 최고점.
    trailing_return_pct: float       # 지금 트레일링 청산 시 (cur−entry)/entry
    fixed_tp_return_pct: float       # 고정 익절은 +activation 에서 팔았을 것 = activation_pct
    peak_return_pct:     float       # 최고점 (hwm−entry)/entry
    is_order_signal:   bool = False  # ★주문 신호 아님(영구 False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "entry_price": self.entry_price,
            "high_watermark": self.high_watermark, "current_price": self.current_price,
            "activated": self.activated, "trailing_would_exit": self.trailing_would_exit,
            "trailing_return_pct": round(self.trailing_return_pct, 3),
            "fixed_tp_return_pct": round(self.fixed_tp_return_pct, 3),
            "peak_return_pct": round(self.peak_return_pct, 3),
            "trailing_vs_fixed_diff_pct": round(self.trailing_return_pct - self.fixed_tp_return_pct, 3),
            "is_order_signal": False,
        }


def update_and_shadow(
    db: Any,
    positions: list[Any],
    *,
    now: datetime,
    activation_pct: float = ACTIVATION_PCT_DEFAULT,
    trailing_pct: float = TRAILING_PCT_DEFAULT,
) -> list[ShadowTrailingSignal]:
    """보유 종목별 최고가 갱신/영속 + 섀도 트레일링 시그널 계산·로그. **매도 0**.

    positions: broker.get_positions() 결과(각 .symbol/.avg_price/.market_price).
    반환: 이번 틱의 섀도 트레일링 청산 시그널 목록(would_exit=True 인 것).
    """
    held: set[str] = set()
    signals: list[ShadowTrailingSignal] = []
    try:
        for p in (positions or []):
            sym = str(getattr(p, "symbol", "") or "")
            entry = int(getattr(p, "avg_price", 0) or 0)
            cur = int(getattr(p, "market_price", 0) or 0)
            qty = int(getattr(p, "quantity", 0) or 0)
            if not sym or entry <= 0 or cur <= 0 or qty <= 0:
                continue
            held.add(sym)

            row = db.get(PositionHighWatermark, sym)
            fresh = row is not None and _kst_date(row.updated_at) == _kst_date(now)
            if fresh:
                hwm = max(int(row.high_watermark), cur)
                activated = bool(row.activated) or (cur >= entry * (1 + activation_pct / 100.0))
            else:
                # 행 없음/손상/전일치 → 폴백 재시드(활성선 아래로 안 내려감).
                hwm = _seed_hwm(entry, cur, activation_pct)
                activated = cur >= entry * (1 + activation_pct / 100.0)

            if row is None:
                db.add(PositionHighWatermark(
                    symbol=sym, entry_price=entry, high_watermark=hwm, activated=activated))
            else:
                row.entry_price = entry
                row.high_watermark = hwm
                row.activated = activated

            # 섀도 트레일링 판정 — activated AND cur ≤ hwm×(1−trailing%).
            trail_price = hwm * (1 - trailing_pct / 100.0)
            would_exit = bool(activated and cur <= trail_price)
            sig = ShadowTrailingSignal(
                symbol=sym, entry_price=entry, high_watermark=hwm, current_price=cur,
                activated=activated, trailing_would_exit=would_exit,
                trailing_return_pct=(cur - entry) / entry * 100.0,
                fixed_tp_return_pct=float(activation_pct),
                peak_return_pct=(hwm - entry) / entry * 100.0,
            )
            if would_exit:
                signals.append(sig)
                _log.info("[trailing-shadow] %s 섀도 트레일링 청산 시그널 — %s",
                          sym, sig.to_dict())

        # 청산(미보유)된 종목 행 정리 — T3 비교용 최고점 기록 후 DELETE(재매수 시 재시작).
        for row in db.query(PositionHighWatermark).all():
            if row.symbol not in held:
                ent = int(row.entry_price or 0)
                peak_ret = ((int(row.high_watermark) - ent) / ent * 100.0) if ent > 0 else 0.0
                db.add(TrailingShadowOutcome(
                    symbol=row.symbol, entry_price=ent,
                    peak_high_watermark=int(row.high_watermark),
                    peak_return_pct=peak_ret, activated=bool(row.activated), closed_at=now))
                db.delete(row)
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 측정 실패는 봇/거래에 영향 0(로그만).
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        _log.warning("[trailing-shadow] 측정 실패(무시): %s: %s", type(exc).__name__, exc)
    return signals


def summarize_trailing_shadow(
    db: Any, *, activation_pct: float = ACTIVATION_PCT_DEFAULT,
    trailing_pct: float = TRAILING_PCT_DEFAULT,
) -> dict[str, Any]:
    """T3 — 섀도 vs 고정 익절 비교 리포트 (read-only).

    청산된 포지션의 *최고점 도달*(TrailingShadowOutcome)을 집계해 "트레일링이었다면
    얼마까지 갔나" vs "고정 익절 +activation%"를 대조한다. 핵심: activated(=+3% 도달)
    포지션의 평균 최고점이 activation 보다 충분히 높으면 트레일링이 더 먹었을 것.

    ★한계(정직): 봇이 고정 익절 +3%에서 *실제로 팔기* 때문에, 그 이상 갔을 잠재 상승은
    관측이 잘려 peak_return 이 ~+3% 근처에 머문다(스캔 간 스파이크/체결 지연분만 초과 관측).
    완전한 트레일링 우위 검증은 3단계(실제로 더 들고 가기) 또는 과거봉 백테스트 필요.
    """
    outs = db.query(TrailingShadowOutcome).all()
    n = len(outs)
    act = [o for o in outs if o.activated]
    avg_peak_act = round(sum(o.peak_return_pct for o in act) / len(act), 3) if act else None
    # 트레일링이 고정익절보다 더 먹었을 후보 = 최고점이 (activation + trailing) 초과
    #   (그 정도 올랐어야 트레일링 청산가가 +activation 보다 위).
    edge = activation_pct + trailing_pct
    trailing_wins = [o for o in act if o.peak_return_pct > edge]
    return {
        "outcomes_total": n,
        "activated_count": len(act),
        "avg_peak_return_pct_activated": avg_peak_act,
        "fixed_take_profit_pct": activation_pct,
        "trailing_would_win_count": len(trailing_wins),
        "trailing_would_win_symbols": [o.symbol for o in trailing_wins][:20],
        "note": ("activated 포지션의 평균 최고점이 고정익절(+%.1f%%)+트레일폭(%.1f%%)=%.1f%% 를 "
                 "넘는 비율로 트레일링 우위 추정. 단 고정익절 실매도로 상승 관측이 잘림 — "
                 "완전검증은 3단계/백테스트." % (activation_pct, trailing_pct, edge)),
        "is_order_signal": False,
    }
