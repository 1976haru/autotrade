from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult, OrderSide, OrderType
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval
from app.execution.executor import OrderExecutor


class ApprovalNotFoundError(LookupError):
    pass


class ApprovalAlreadyDecidedError(RuntimeError):
    pass


STATUS_PENDING  = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"


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

    def list_pending(self) -> list[PendingApproval]:
        return list(self.db.execute(
            select(PendingApproval)
            .where(PendingApproval.status == STATUS_PENDING)
            .order_by(PendingApproval.created_at)
        ).scalars().all())

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

        order = OrderRequest(
            symbol=approval.symbol,
            side=OrderSide(approval.side),
            quantity=approval.quantity,
            order_type=OrderType(approval.order_type),
            limit_price=approval.limit_price,
        )
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
