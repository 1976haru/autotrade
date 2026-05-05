from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/ai", tags=["ai"])


class AnalyzeRequest(BaseModel):
    ticker: str
    extra: str | None = None
    activeStrats: list[str] = []
    risk: dict = {}


@router.post("/analyze")
def analyze(payload: AnalyzeRequest) -> dict:
    """초기 단계 placeholder. 실제 AI 호출은 backend에서만 구현한다."""
    text = (
        '{"tech":0,"trend":0,"news":0,"flow":0,"total":0,'
        '"signal":"관망","conf":0,"entry":0,"target":0,"stop":0}\n'
        f"{payload.ticker} 분석 라우트는 아직 placeholder입니다. "
        "AI는 현재 주문 권한이 없으며, 리포트/보조 분석에만 사용됩니다."
    )
    return {"text": text, "can_execute_order": False}
