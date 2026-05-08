"""Emergency Stop / Kill Switch — 3 levels (#37).

기존 RiskManager.emergency_stop은 단일 boolean 토글이었다(`#060` / `#153`).
체크리스트 #37은 같은 *stop everything* 의미를 *3단계*로 분해해 운영자가
상황에 맞춰 단계적으로 가드를 강화할 수 있게 한다.

3단계:
- **LEVEL_1** — 신규 매수(BUY) 즉시 차단. 기존 보유 포지션 / 미체결 / 청산은
  운영자가 그대로 보유 가능. 가벼운 위험 신호용.
- **LEVEL_2** — LEVEL_1 + 미체결(`PendingApproval` PENDING / `OrderAuditLog`
  NEEDS_APPROVAL) **취소 후보 표시**. 자동 취소 X — read-only candidate list.
- **LEVEL_3** — LEVEL_2 + 보유 포지션 **청산 후보 표시**. 자동 전량청산 X —
  운영자가 후보를 보고 수동 승인. 호가 공백 / 급락 상황에서 시장가 전량
  청산은 위험하기 때문 (CLAUDE.md '손실 방어 우선' 원칙).

본 모듈은 *주문을 만들지 않는다*. broker / OrderExecutor / route_order 어떤
함수도 호출하지 않으며, 모든 candidate는 read-only DB / broker snapshot에서
산출. 실제 취소 / 청산은 별도 옵트인 PR (수동승인 흐름).

기존 호환성:
- `RiskManager.emergency_stop` boolean은 그대로 유지. LEVEL_1+ 일 때 True.
- `POST /risk/emergency-stop` (enabled=True/False)도 그대로 — `enabled=True`
  + level 미지정은 LEVEL_1로 매핑.
- 기존 EmergencyStopEvent.history 응답도 그대로 — 새 `level` 컬럼은 옵셔널,
  legacy row(NULL)는 LEVEL_1 (기존 의미)로 표시.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EmergencyStopEvent, OrderAuditLog, PendingApproval


class KillSwitchLevel(StrEnum):
    """3단계 + OFF.

    OFF = 정상 운영. LEVEL_1+ = `RiskManager.emergency_stop=True`로 매핑되어
    기존 hard-stop 가드가 활성화 (모든 주문 evaluate_order가 즉시 REJECTED).
    """
    OFF      = "OFF"
    LEVEL_1  = "LEVEL_1"   # 신규 BUY 차단
    LEVEL_2  = "LEVEL_2"   # + 미체결 취소 후보 표시
    LEVEL_3  = "LEVEL_3"   # + 청산 후보 표시 (자동 전량청산 X)


# legacy enabled=True row를 표시할 때 사용하는 default level.
LEGACY_LEVEL: KillSwitchLevel = KillSwitchLevel.LEVEL_1


def normalize_level(raw: str | None) -> KillSwitchLevel:
    """audit row / API 입력의 level 문자열을 정규화. None/빈값 = OFF."""
    if not raw:
        return KillSwitchLevel.OFF
    try:
        return KillSwitchLevel(raw)
    except ValueError:
        return KillSwitchLevel.OFF


def normalize_legacy_level(level: str | None, *, enabled: bool) -> KillSwitchLevel:
    """history row의 level/enabled를 함께 보고 정규화.

    - level 컬럼이 채워져 있으면 그대로.
    - 비어 있고 enabled=True면 legacy → LEVEL_1.
    - 비어 있고 enabled=False면 OFF.
    """
    parsed = normalize_level(level)
    if parsed != KillSwitchLevel.OFF:
        return parsed
    return KillSwitchLevel.LEVEL_1 if enabled else KillSwitchLevel.OFF


@dataclass(frozen=True)
class KillSwitchStatus:
    """현재 kill switch 상태 + 후보 카운트 스냅샷."""
    level:                 KillSwitchLevel
    emergency_stop:        bool                # 기존 boolean과 동기화
    reason_code:           str | None
    decided_by:            str | None
    note:                  str | None
    active_since:          str | None          # ISO format
    cancel_candidate_count:      int
    liquidation_candidate_count: int

    def to_dict(self) -> dict:
        return {
            "level":          self.level.value,
            "emergency_stop": self.emergency_stop,
            "reason_code":    self.reason_code,
            "decided_by":     self.decided_by,
            "note":           self.note,
            "active_since":   self.active_since,
            "cancel_candidate_count":      self.cancel_candidate_count,
            "liquidation_candidate_count": self.liquidation_candidate_count,
        }


# ---------- candidate 산출 (read-only) ----------


@dataclass(frozen=True)
class CancelCandidate:
    """미체결 / 승인 대기 주문 — Kill Switch LEVEL_2가 표시.

    실제 취소는 본 dataclass로 surface만 — 수동 승인 후 별도 cancel route 사용.
    """
    audit_id:    int
    approval_id: int | None
    symbol:      str
    side:        str
    quantity:    int
    order_type:  str
    limit_price: int | None
    mode:        str
    decision:    str
    created_at:  str | None
    note:        str | None
    source:      str   # "pending_approval" | "audit_needs_approval"

    def to_dict(self) -> dict:
        return {
            "audit_id":    self.audit_id,
            "approval_id": self.approval_id,
            "symbol":      self.symbol,
            "side":        self.side,
            "quantity":    self.quantity,
            "order_type":  self.order_type,
            "limit_price": self.limit_price,
            "mode":        self.mode,
            "decision":    self.decision,
            "created_at":  self.created_at,
            "note":        self.note,
            "source":      self.source,
        }


@dataclass(frozen=True)
class LiquidationCandidate:
    """현재 보유 포지션 — Kill Switch LEVEL_3가 표시.

    *자동 청산하지 않는다* — 운영자가 후보를 보고 수동 청산 승인. 호가 공백 /
    급락 상황에서 자동 시장가 전량청산은 위험.
    """
    symbol:        str
    quantity:      int
    avg_price:     int
    market_price:  int
    unrealized_pnl: int       # (market_price - avg_price) × quantity

    def to_dict(self) -> dict:
        return {
            "symbol":         self.symbol,
            "quantity":       self.quantity,
            "avg_price":      self.avg_price,
            "market_price":   self.market_price,
            "unrealized_pnl": self.unrealized_pnl,
        }


def compute_cancel_candidates(db: Session) -> list[CancelCandidate]:
    """LEVEL_2가 표시할 미체결/승인 대기 주문 list. read-only.

    소스:
    1. `PendingApproval` 중 status=PENDING — 운영자 승인 대기 큐.
    2. `OrderAuditLog` 중 decision=NEEDS_APPROVAL이지만 PendingApproval row가
       없는 row (희귀 — race / migration 이전 row).

    실제 취소는 별도 cancel API에서 처리 (수동 승인). 본 함수는 표시 후보만
    반환한다.
    """
    out: list[CancelCandidate] = []
    pending_rows = db.execute(
        select(PendingApproval).where(PendingApproval.status == "PENDING")
        .order_by(PendingApproval.id.desc())
    ).scalars().all()
    pending_audit_ids = set()
    for pa in pending_rows:
        pending_audit_ids.add(pa.audit_id)
        out.append(CancelCandidate(
            audit_id=pa.audit_id, approval_id=pa.id,
            symbol=pa.symbol, side=pa.side, quantity=pa.quantity,
            order_type=pa.order_type, limit_price=pa.limit_price,
            mode=pa.mode, decision="NEEDS_APPROVAL",
            created_at=pa.created_at.isoformat() if pa.created_at else None,
            note=pa.note, source="pending_approval",
        ))

    # NEEDS_APPROVAL audit row 중 PendingApproval에 안 잡힌 것 (drift detector).
    audit_rows = db.execute(
        select(OrderAuditLog).where(OrderAuditLog.decision == "NEEDS_APPROVAL")
        .order_by(OrderAuditLog.id.desc())
    ).scalars().all()
    for ar in audit_rows:
        if ar.id in pending_audit_ids:
            continue
        out.append(CancelCandidate(
            audit_id=ar.id, approval_id=None,
            symbol=ar.symbol, side=ar.side, quantity=ar.quantity,
            order_type=ar.order_type, limit_price=ar.limit_price,
            mode=ar.mode, decision=ar.decision,
            created_at=ar.created_at.isoformat() if ar.created_at else None,
            note=None, source="audit_needs_approval",
        ))
    return out


def compute_liquidation_candidates(positions: list[Any]) -> list[LiquidationCandidate]:
    """LEVEL_3가 표시할 청산 후보 list. read-only.

    `positions`는 broker.get_positions() 또는 virtual position store의
    스냅샷. 본 함수는 단순 데이터 변환 — broker 호출은 호출자(route)가 한다.
    quantity > 0인 포지션만 후보로 surface (잔량 0/음수는 무시).
    """
    out: list[LiquidationCandidate] = []
    for p in positions:
        qty = getattr(p, "quantity", None)
        avg = getattr(p, "avg_price", None)
        mkt = getattr(p, "market_price", None)
        sym = getattr(p, "symbol", None)
        if qty is None or qty <= 0 or sym is None:
            continue
        avg_v = int(avg) if avg is not None else 0
        mkt_v = int(mkt) if mkt is not None else avg_v
        out.append(LiquidationCandidate(
            symbol=sym, quantity=int(qty),
            avg_price=avg_v, market_price=mkt_v,
            unrealized_pnl=(mkt_v - avg_v) * int(qty),
        ))
    return out


# ---------- 상태 빌드 ----------


def build_status(
    *,
    risk,                                   # RiskManager (forward typing)
    db: Session,
    cancel_candidates:      list[CancelCandidate]      | None = None,
    liquidation_candidates: list[LiquidationCandidate] | None = None,
) -> KillSwitchStatus:
    """런타임 RiskManager 상태 + DB last-event metadata + 후보 카운트를 묶어
    `KillSwitchStatus`로 반환.

    `cancel_candidates` / `liquidation_candidates` 둘 다 None이면 카운트 0으로
    채운다 — 호출자가 endpoint별로 어느 카테고리를 surface할지 결정.
    """
    level = getattr(risk, "kill_switch_level", None) or KillSwitchLevel.OFF
    if isinstance(level, str):
        level = normalize_level(level)
    # emergency_stop boolean과 동기화 — 외부에서 boolean만 토글한 경우
    # legacy 처리 (LEVEL_1 mapping).
    emergency_stop = bool(getattr(risk, "emergency_stop", False))
    if emergency_stop and level == KillSwitchLevel.OFF:
        level = LEGACY_LEVEL
    if not emergency_stop and level != KillSwitchLevel.OFF:
        level = KillSwitchLevel.OFF

    last_active = None
    if emergency_stop:
        last_active = db.execute(
            select(EmergencyStopEvent)
            .where(EmergencyStopEvent.enabled.is_(True))
            .order_by(EmergencyStopEvent.id.desc()).limit(1)
        ).scalar_one_or_none()

    cc = list(cancel_candidates) if cancel_candidates is not None else []
    lc = list(liquidation_candidates) if liquidation_candidates is not None else []

    return KillSwitchStatus(
        level=level,
        emergency_stop=emergency_stop,
        reason_code=getattr(last_active, "reason_code", None) if last_active else None,
        decided_by=getattr(last_active, "decided_by", None) if last_active else None,
        note=getattr(last_active, "note", None) if last_active else None,
        active_since=(
            last_active.created_at.isoformat()
            if last_active and last_active.created_at else None
        ),
        cancel_candidate_count=len(cc),
        liquidation_candidate_count=len(lc),
    )


def apply_kill_switch_to_risk(risk, level: KillSwitchLevel) -> None:
    """RiskManager에 level을 설정하고 emergency_stop boolean을 동기화.

    - LEVEL_1+ → emergency_stop=True (기존 hard-stop 가드 활성화).
    - OFF → emergency_stop=False.

    in-memory 토글이라 process restart 시 OFF로 reset (기존 emergency_stop과
    동일 정책 — audit row가 영구화 source of truth).
    """
    risk.kill_switch_level = level
    risk.emergency_stop = (level != KillSwitchLevel.OFF)


# ---------- module invariants (코드 단 안전 보장) ----------
#
# 본 모듈은 broker.place_order, broker.cancel_order, route_order 어떤 함수도
# 호출 형태로 작성하지 않는다 — 자동 취소 / 자동 청산을 절대 만들지 않기
# 위함. 테스트가 본 invariant를 grep으로 강제 (`tests/test_emergency_stop_
# kill_switch.py::TestSafety`).
