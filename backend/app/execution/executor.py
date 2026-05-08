from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult
from app.db.models import OrderAuditLog
from app.risk.risk_manager import RiskDecision


class UnauthorizedOrderError(RuntimeError):
    """#34: audit.decision이 APPROVED가 아닌 상태에서 OrderExecutor.execute가
    호출됐을 때 raise. RiskManager 우회 방지를 위한 마지막 backstop — 정상
    경로에서는 발생하지 않아야 한다 (route_order가 NEEDS_APPROVAL/REJECTED/
    BLOCKED는 broker 호출 없이 분기 처리하므로). 본 예외가 발생하면 운영 사고로
    간주하고 즉시 조사 필요."""


# RiskManager 결과 중 broker.place_order로 진행 가능한 decision 집합.
#
# - APPROVED: route_order의 정상 직진 경로.
# - NEEDS_APPROVAL: PermissionGate.approve가 운영자 승인 + re-evaluation을
#   통과시킨 뒤 호출하는 경로. audit row의 decision은 RiskManager의 *원래*
#   판정(NEEDS_APPROVAL)을 그대로 보존하는 것이 기존 contract — operator
#   결정은 PendingApproval.status 별도 행에 기록된다 (test_virtual_flow_e2e
#   참조). PermissionGate가 직전에 다시 evaluate_order를 호출하므로 본 시점
#   에서 broker로 가는 것은 합법.
# - REDUCED는 호출자가 주문을 정규화한 뒤 다시 check_order를 거쳐 APPROVED로
#   들어와야 하므로 포함하지 않는다.
# - REJECTED / BLOCKED는 어떤 경로로도 broker에 도달하지 않아야 한다 — 본
#   가드가 마지막 backstop.
_EXECUTABLE_DECISIONS: frozenset[str] = frozenset({
    RiskDecision.APPROVED.value,
    RiskDecision.NEEDS_APPROVAL.value,
})


class OrderExecutor:
    """RiskManager + PermissionGate를 통과한 주문의 마지막 단계.

    브로커로 주문을 보내고, 동일한 OrderAuditLog 행을 체결 결과로 갱신한다.
    OrderAuditLog 한 행이 주문 요청부터 체결까지의 전체 라이프사이클을 담는다.

    트랜잭션 경계는 호출자가 가진다 — execute()는 변경을 stage만 하고 commit하지 않는다.
    한 흐름에서 audit 외 다른 행(예: PendingApproval.status)을 같이 갱신해야 할 때를
    위해 단일 commit 시점을 호출자가 결정할 수 있게 한다.

    #34 (RiskManager 표준화) 가드:
    - audit row가 반드시 존재해야 한다 (None은 ValueError).
    - audit.decision이 APPROVED여야 한다 (그 외면 UnauthorizedOrderError).
      route_order는 REJECTED/BLOCKED/NEEDS_APPROVAL은 broker 호출 없이 분기
      처리하므로 정상 경로에서 본 가드는 trip되지 않는다. 누군가 RiskManager를
      우회해 직접 OrderExecutor.execute를 호출하려 하면 본 가드가 즉시 차단.
    """

    def __init__(self, broker: BrokerAdapter, db: Session):
        self.broker = broker
        self.db = db

    async def execute(self, order: OrderRequest, audit: OrderAuditLog) -> OrderResult:
        if audit is None:
            raise ValueError("audit row is required to execute an order")
        # #34 backstop — RiskManager 우회 방지.
        if audit.decision not in _EXECUTABLE_DECISIONS:
            raise UnauthorizedOrderError(
                f"OrderExecutor refuses to call broker.place_order: "
                f"audit.decision={audit.decision!r} (only APPROVED is executable). "
                "route_order이 RiskManager.check_order를 통과시키지 않은 주문을 "
                "직접 실행하려는 시도일 가능성이 큽니다."
            )
        result = await self.broker.place_order(order)
        audit.executed = True
        audit.broker_order_id = result.order_id
        audit.broker_status = result.status.value
        audit.filled_quantity = result.filled_quantity
        audit.avg_fill_price = result.avg_fill_price
        audit.message = result.message
        return result
