"""Position reconciliation endpoint (212, MUST).

운영자 / dashboard가 broker view vs audit view drift를 확인하는 read-only
endpoint. backlog #2 — KIS LIVE 활성화 시 두 view 사이의 drift를 즉시 감지
하기 위한 안전 메커니즘.

CLAUDE.md 준수:
- 새 broker 호출 가드 우회 0건. broker.get_positions() 단일 호출.
- 새 RiskManager / PermissionGate 분기 0건. SELECT만.
- 새 주문 경로 0건.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_broker
from app.brokers.base import BrokerAdapter
from app.db.session import get_db
from app.reconciliation.position_checker import reconcile, report_to_dict

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


class PositionMismatchOut(BaseModel):
    symbol:           str
    broker_quantity:  int
    audit_quantity:   int
    quantity_diff:    int
    kind:             str


class ReconciliationStatusOut(BaseModel):
    in_sync:              bool
    broker_symbol_count:  int
    audit_symbol_count:   int
    matched_count:        int
    mismatches:           list[PositionMismatchOut]


@router.get("/status", response_model=ReconciliationStatusOut)
async def get_reconciliation_status(
    broker: BrokerAdapter = Depends(get_broker),
    db:     Session       = Depends(get_db),
) -> ReconciliationStatusOut:
    """broker view vs audit view 비교 결과.

    broker.get_positions가 실패하면 502 (운영자가 broker 연결 점검).
    """
    try:
        report = await reconcile(db, broker)
    except NotImplementedError as e:
        # KIS LIVE place_order 등은 NotImplementedError이지만 get_positions는
        # 일반적으로 구현됨. 혹시 미구현 broker라면 502로 표면화.
        raise HTTPException(status_code=502, detail=f"broker not available: {e}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"broker error: {e}")
    return ReconciliationStatusOut.model_validate(report_to_dict(report))
