"""BOT/MANUAL 보유 출처 분류 (설계 B — 조각 1). *read-only 집계*.

`OrderAuditLog.trade_reason` 로 봇/수동 주문을 구분해 종목별 net 수량을 분리 집계한다.
KIS 는 합산 보유만 주므로, 출처 구분은 우리 DB(주문 기록)에서 파생한다.

★조각 1 범위: *표시/분류 전용*. 봇의 보유 스캔(driver_bridge._kis_held_map)은 **미접촉** —
봇 격리(MANUAL 차감)는 조각 2(월요일 R-A 검증 후). 본 모듈은 broker / route_order /
driver_bridge / OrderExecutor 를 import 하지 않는다(정적 가드). 주문 0건, 안전플래그 변경 0건.
"""

from __future__ import annotations

from typing import Any

# 출처별 trade_reason 분류 (설계 B §1-2).
BOT_TRADE_REASONS: tuple[str, ...] = ("kis_paper_auto",)
MANUAL_BUY_REASONS: tuple[str, ...] = ("manual_buy",)
MANUAL_SELL_REASONS: tuple[str, ...] = ("manual_sell", "manual_sell_all")
MANUAL_TRADE_REASONS: tuple[str, ...] = (*MANUAL_BUY_REASONS, *MANUAL_SELL_REASONS)

# 분류 라벨.
SOURCE_BOT = "BOT"          # 봇이 운용(단타 — 손절/익절 대상)
SOURCE_MANUAL = "MANUAL"    # 운영자 직접 보유(봇 청산 대상 아님)
SOURCE_MIXED = "MIXED"      # 같은 종목을 봇·수동 둘 다 보유
SOURCE_UNTAGGED = "UNTAGGED"  # 출처 미상(외부거래·캐리오버) → 보수적으로 직접 취급


def _net_by_source(rows: list[Any]) -> dict[str, dict[str, int]]:
    """OrderAuditLog row 목록 → 종목별 {bot_net, manual_net}. BUY +qty, SELL −qty.
    broker_status REJECTED 는 잔고를 바꾸지 않으므로 제외(net 계산 일관성)."""
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        sym = getattr(r, "symbol", None)
        if not sym:
            continue
        if str(getattr(r, "broker_status", "") or "").upper() == "REJECTED":
            continue
        reason = str(getattr(r, "trade_reason", "") or "").lower()
        side = str(getattr(r, "side", "") or "").upper()
        qty = int(getattr(r, "quantity", 0) or 0)
        signed = qty if side in ("BUY", "B") else (-qty if side in ("SELL", "S") else 0)
        if signed == 0:
            continue
        cell = out.setdefault(sym, {"bot_net": 0, "manual_net": 0})
        if reason in BOT_TRADE_REASONS:
            cell["bot_net"] += signed
        elif reason in MANUAL_TRADE_REASONS:
            cell["manual_net"] += signed
        # 그 외 trade_reason 은 untagged — net 에 안 넣음(kis_total 과의 차액으로 파생).
    return out


def compute_holding_source(db: Any) -> dict[str, dict[str, int]]:
    """종목별 {bot_net, manual_net} 전체기간 net (출처는 일별 리셋 아님).

    read-only SELECT 만. INSERT/UPDATE/DELETE 0건.
    """
    try:
        from app.db.models import OrderAuditLog
        rows = db.query(OrderAuditLog).filter(OrderAuditLog.executed.is_(True)).all()
    except Exception:  # noqa: BLE001 — 집계 실패는 빈 dict(보수적: 분류 불가 → UNTAGGED 처리).
        return {}
    return _net_by_source(rows)


def classify(bot_net: int, manual_net: int, kis_qty: int | None = None) -> dict[str, Any]:
    """보유 1종목의 출처 분류 + 봇/수동 수량 분해.

    kis_qty(브로커 진실)가 주어지면 untagged 차액 = kis_qty − bot_net − manual_net 도 계산.
    untagged 는 **보수적으로 직접(MANUAL 측)** — "출처 모르면 봇이 안 건드림"(손실방어 안전측).
    """
    bot = max(0, int(bot_net or 0))
    man = max(0, int(manual_net or 0))
    untagged = 0
    if kis_qty is not None:
        untagged = max(0, int(kis_qty) - bot - man)
    manual_side = man + untagged   # 직접 보유 = 수동 + 출처미상

    if bot > 0 and manual_side > 0:
        source = SOURCE_MIXED
    elif bot > 0:
        source = SOURCE_BOT
    elif manual_side > 0:
        source = SOURCE_UNTAGGED if (man == 0 and untagged > 0) else SOURCE_MANUAL
    else:
        source = SOURCE_BOT if bot_net or manual_net else SOURCE_UNTAGGED

    return {
        "source": source,
        "bot_qty": bot,
        "manual_qty": manual_side,     # 수동 + untagged (봇 비대상)
        "untagged_qty": untagged,
        # ★조각 1 안내: 봇 격리(조각 2)는 아직 — 표시 전용.
        "bot_isolated": False,
    }


def classify_positions(db: Any, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """positions(각 {symbol, quantity, ...})에 출처 분류를 붙여 반환(표시용)."""
    src = compute_holding_source(db)
    out: list[dict[str, Any]] = []
    for p in (positions or []):
        sym = str(p.get("symbol") or "")
        cell = src.get(sym, {"bot_net": 0, "manual_net": 0})
        kis_qty = int(p.get("quantity", 0) or 0)
        cls = classify(cell["bot_net"], cell["manual_net"], kis_qty=kis_qty)
        out.append({**p, **cls})
    return out
