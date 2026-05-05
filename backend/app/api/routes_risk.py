from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_risk_manager
from app.risk.risk_manager import RiskManager, RiskPolicy

router = APIRouter(prefix="/risk", tags=["risk"])


class EmergencyStopRequest(BaseModel):
    enabled: bool


@router.get("/policy")
def get_policy(risk: RiskManager = Depends(get_risk_manager)) -> RiskPolicy:
    return risk.policy


@router.post("/emergency-stop")
def set_emergency_stop(payload: EmergencyStopRequest, risk: RiskManager = Depends(get_risk_manager)) -> dict:
    risk.set_emergency_stop(payload.enabled)
    return {"emergency_stop": risk.emergency_stop}
