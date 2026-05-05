from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult
from app.db.models import OrderAuditLog


class OrderExecutor:
    """RiskManager + PermissionGate를 통과한 주문의 마지막 단계.

    브로커로 주문을 보내고, 동일한 OrderAuditLog 행을 체결 결과로 갱신한다.
    OrderAuditLog 한 행이 주문 요청부터 체결까지의 전체 라이프사이클을 담는다.

    트랜잭션 경계는 호출자가 가진다 — execute()는 변경을 stage만 하고 commit하지 않는다.
    한 흐름에서 audit 외 다른 행(예: PendingApproval.status)을 같이 갱신해야 할 때를
    위해 단일 commit 시점을 호출자가 결정할 수 있게 한다.
    """

    def __init__(self, broker: BrokerAdapter, db: Session):
        self.broker = broker
        self.db = db

    async def execute(self, order: OrderRequest, audit: OrderAuditLog) -> OrderResult:
        if audit is None:
            raise ValueError("audit row is required to execute an order")
        result = await self.broker.place_order(order)
        audit.executed = True
        audit.broker_order_id = result.order_id
        audit.broker_status = result.status.value
        audit.filled_quantity = result.filled_quantity
        audit.avg_fill_price = result.avg_fill_price
        audit.message = result.message
        return result
