from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult, OrderSide, OrderType
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval
from app.execution.executor import OrderExecutor
from app.risk.daily_pnl import compute_today_realized_pnl
from app.risk.risk_manager import RiskDecision, RiskManager


class ApprovalNotFoundError(LookupError):
    pass


class ApprovalAlreadyDecidedError(RuntimeError):
    pass


class ApprovalRiskCheckFailedError(RuntimeError):
    """Re-evaluation at approve time surfaced violations that didn't exist at
    submit. Approval stays PENDING so the operator can retry once conditions
    improve (price reverts, cash arrives, emergency stop clears, etc.)."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(f"approve-time risk check failed: {reasons}")
        self.reasons = reasons


STATUS_PENDING   = "PENDING"
STATUS_APPROVED  = "APPROVED"
STATUS_REJECTED  = "REJECTED"
STATUS_CANCELLED = "CANCELLED"
# 167: TTL 만료. PENDING이 너무 오래 머물면 시세 stale + 시장 변동으로 의도가
# 어긋나므로 자동 만료 — 운영자 명시적 결정과 구분.
STATUS_EXPIRED   = "EXPIRED"

# evaluate_order returns NEEDS_APPROVAL with this reason for the queueing modes
# (LIVE_MANUAL_APPROVAL / LIVE_AI_ASSIST). At approve time it's the *expected*
# reason — we strip it before deciding whether re-eval found real violations.
_MODE_REQUIRES_APPROVAL_REASON = "manual approval required by operation mode"


def _approve_re_eval_blocks_execution(result) -> bool:
    """True iff the re-evaluation reasons indicate a real violation, not just
    the mode-driven NEEDS_APPROVAL marker that would always show up for
    LIVE_MANUAL_APPROVAL / LIVE_AI_ASSIST."""
    if result.decision == RiskDecision.REJECTED:
        return True
    # APPROVED at SIM-like re-eval → proceed.
    # NEEDS_APPROVAL → only proceed if every reason is the mode marker;
    # accumulated limit/state reasons mean conditions changed since submit.
    return any(r != _MODE_REQUIRES_APPROVAL_REASON for r in result.reasons)


class PermissionGate:
    """RiskManager가 NEEDS_APPROVAL을 반환한 주문을 큐잉하고 결정 흐름을 관리.

    재현은 PendingApproval 행에 저장된 스냅샷에서 OrderRequest를 재구성해 수행한다.
    제출 시점과 승인 시점 사이의 잔고/가격 변화에 대한 재평가는 향후 PR에서 추가.
    """

    def __init__(self, db: Session):
        self.db = db

    def submit(
        self,
        *,
        audit: OrderAuditLog,
        order: OrderRequest,
        mode:  OperationMode,
    ) -> PendingApproval:
        if audit.id is None:
            self.db.flush()
        approval = PendingApproval(
            audit_id=audit.id,
            symbol=order.symbol,
            side=order.side.value,
            quantity=order.quantity,
            order_type=order.order_type.value,
            limit_price=order.limit_price,
            mode=mode.value,
            status=STATUS_PENDING,
        )
        self.db.add(approval)
        self.db.commit()
        self.db.refresh(approval)
        return approval

    def list_pending(self, *, ttl_seconds: int = 0) -> list[PendingApproval]:
        """현재 PENDING approvals. 167: ttl_seconds > 0이면 호출 시점에 lazy
        expire — created_at이 ttl_seconds 초과로 오래된 PENDING은 EXPIRED로
        전환된 후 제외된다."""
        if ttl_seconds > 0:
            self.expire_stale_approvals(ttl_seconds)
        return list(self.db.execute(
            select(PendingApproval)
            .where(PendingApproval.status == STATUS_PENDING)
            .order_by(PendingApproval.created_at)
        ).scalars().all())

    def expire_stale_approvals(
        self,
        ttl_seconds: int,
        *,
        now:        datetime | None = None,
    ) -> list[PendingApproval]:
        """167: PENDING이 ttl_seconds 초과 오래된 approvals를 EXPIRED로 일괄 전환.

        한 번에 여러 row를 EXPIRED로 표시 + decided_at = 현재시각 + reason
        annotation. 호출자(list_pending lazy / 운영자 명시 cleanup / cron)가
        결정. ttl_seconds <= 0이면 no-op (안전 측 — 0 의미는 '검사 비활성').

        반환: 이번 호출에서 EXPIRED된 row 리스트 (audit / 알림 용도).
        """
        if ttl_seconds <= 0:
            return []
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=ttl_seconds)
        stale = self.db.execute(
            select(PendingApproval).where(
                PendingApproval.status == STATUS_PENDING,
                PendingApproval.created_at < cutoff,
            )
        ).scalars().all()
        for approval in stale:
            approval.status     = STATUS_EXPIRED
            approval.decided_at = now
            approval.note       = f"auto-expired after {ttl_seconds}s TTL"
        if stale:
            self.db.commit()
        return list(stale)

    def list_decided(
        self,
        *,
        limit:  int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[PendingApproval]:
        """Return decided approvals (APPROVED / REJECTED / CANCELLED).

        Most recent first by decided_at. Pending rows are excluded — that's
        what list_pending is for. status filter narrows further when set.
        """
        stmt = select(PendingApproval).where(PendingApproval.status != STATUS_PENDING)
        if status is not None:
            stmt = stmt.where(PendingApproval.status == status)
        stmt = (
            stmt.order_by(PendingApproval.decided_at.desc())
                .limit(limit)
                .offset(offset)
        )
        return list(self.db.execute(stmt).scalars().all())

    def get(self, approval_id: int) -> PendingApproval:
        approval = self.db.execute(
            select(PendingApproval).where(PendingApproval.id == approval_id)
        ).scalar_one_or_none()
        if approval is None:
            raise ApprovalNotFoundError(approval_id)
        return approval

    async def approve(
        self,
        approval_id: int,
        broker:      BrokerAdapter,
        risk:        RiskManager,
        *,
        decided_by:  str | None = None,
        note:        str | None = None,
    ) -> tuple[PendingApproval, OrderResult]:
        approval = self.get(approval_id)
        if approval.status != STATUS_PENDING:
            raise ApprovalAlreadyDecidedError(
                f"approval {approval_id} is already {approval.status}"
            )

        audit = self.db.get(OrderAuditLog, approval.audit_id)
        if audit is None:
            raise RuntimeError(
                f"audit {approval.audit_id} for approval {approval_id} not found"
            )

        # 160: AI invariants(158/159)도 approve 시점 re-eval에 적용되려면
        # OrderRequest 재구성 시 audit row에서 AI 메타데이터를 복원해야 한다.
        # PendingApproval 자체엔 carry되지 않지만 audit row에 영구화되어 있다.
        order = OrderRequest(
            symbol=approval.symbol,
            side=OrderSide(approval.side),
            quantity=approval.quantity,
            order_type=OrderType(approval.order_type),
            limit_price=approval.limit_price,
            signal_strength=audit.signal_strength,
            signal_confidence=audit.signal_confidence,
            ai_decision_meta=audit.ai_decision_meta,
        )

        # 070 hardening: between submit and approve the broker state can drift
        # (price moves, cash spent on another order, emergency_stop toggled,
        # ENABLE_LIVE_TRADING flag flipped off). Re-evaluate against the
        # current state before executing — if conditions broke, raise so the
        # caller can hold the approval as PENDING for retry.
        quote     = await broker.get_price(order.symbol)
        balance   = await broker.get_balance()
        positions = await broker.get_positions()

        # 146: re-eval은 route_order와 동일한 가드를 적용해야 한다 — 그렇지 않으면
        # submit 시점엔 차단됐을 주문이 approve 시점에 빠져나간다.
        # (a) 143 stale price: Quote.timestamp 파싱 후 RiskManager에 carry. 파싱
        #     실패는 안전 측 None (검사 우회).
        # (b) 145 daily realized PnL: max_daily_loss를 강제하려면 audit log 기반
        #     으로 카운터를 채워야 한다 — submit 후 다른 거래로 손실이 누적된
        #     상황에서 approve 시점에는 한도 초과일 수 있다.
        quote_ts = None
        try:
            quote_ts = datetime.fromisoformat(quote.timestamp)
        except (TypeError, ValueError):
            quote_ts = None
        risk.daily_realized_pnl = compute_today_realized_pnl(self.db)

        re_eval   = risk.evaluate_order(
            order=order,
            mode=OperationMode(approval.mode),
            balance=balance,
            positions=positions,
            latest_price=quote.price,
            latest_price_timestamp=quote_ts,
            # 160: 원래 주문이 AI 발신이면 그 invariant(158/159)도 approve 시점에
            # 다시 검사. audit row가 source of truth.
            requested_by_ai=audit.requested_by_ai,
        )
        if _approve_re_eval_blocks_execution(re_eval):
            # 076: persist the failed attempt on the row before raising.
            # JSON column needs reassignment for the change to be detected —
            # mutating in place isn't always picked up by the SQLAlchemy
            # change-tracker.
            attempts = list(approval.attempts or [])
            attempts.append({
                "at":         datetime.now(timezone.utc).isoformat(),
                "decided_by": decided_by,
                "reasons":    list(re_eval.reasons),
            })
            approval.attempts = attempts
            self.db.commit()
            self.db.refresh(approval)
            # Approval stays PENDING — operator retries when conditions improve.
            raise ApprovalRiskCheckFailedError(list(re_eval.reasons))

        result = await OrderExecutor(broker, self.db).execute(order, audit)

        approval.status = STATUS_APPROVED
        approval.decided_at = datetime.now(timezone.utc)
        approval.decided_by = decided_by
        approval.note = note
        self.db.commit()
        self.db.refresh(approval)
        return approval, result

    def reject(
        self,
        approval_id: int,
        *,
        decided_by:  str | None = None,
        note:        str | None = None,
    ) -> PendingApproval:
        approval = self.get(approval_id)
        if approval.status != STATUS_PENDING:
            raise ApprovalAlreadyDecidedError(
                f"approval {approval_id} is already {approval.status}"
            )
        approval.status = STATUS_REJECTED
        approval.decided_at = datetime.now(timezone.utc)
        approval.decided_by = decided_by
        approval.note = note
        self.db.commit()
        self.db.refresh(approval)
        return approval

    def cancel(
        self,
        approval_id: int,
        *,
        decided_by:  str | None = None,
        note:        str | None = None,
    ) -> PendingApproval:
        """Operator dismissal — neutral disposition distinct from reject.

        Use when the order is no longer relevant (signal stale, made by
        mistake, will re-evaluate later) rather than actively refused.
        Preserves audit clarity: REJECTED == "operator said no", CANCELLED ==
        "operator dismissed without judgement". Same already-decided guard as
        approve/reject so a settled item cannot be reopened.
        """
        approval = self.get(approval_id)
        if approval.status != STATUS_PENDING:
            raise ApprovalAlreadyDecidedError(
                f"approval {approval_id} is already {approval.status}"
            )
        approval.status = STATUS_CANCELLED
        approval.decided_at = datetime.now(timezone.utc)
        approval.decided_by = decided_by
        approval.note = note
        self.db.commit()
        self.db.refresh(approval)
        return approval
