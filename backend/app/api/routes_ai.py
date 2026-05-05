from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.client import AiClient, AiNotConfiguredError
from app.ai.service import analyze as run_analysis
from app.api.deps import get_ai_client
from app.db.models import AiAnalysisLog
from app.db.session import get_db

router = APIRouter(prefix="/ai", tags=["ai"])


class AnalyzeRequest(BaseModel):
    ticker:       str
    extra:        str | None = None
    activeStrats: list[str]  = []
    risk:         dict       = {}


class AnalyzeResponse(BaseModel):
    text:              str
    can_execute_order: bool        = False  # AI는 절대 주문 권한 없음 (CLAUDE.md)
    model:             str | None  = None
    score:             dict | None = None


_DISABLED_NOTICE = (
    "AI 분석이 비활성화되어 있습니다 (ANTHROPIC_API_KEY 미설정). "
    "주문 판단에 사용하지 마세요."
)


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_route(
    payload: AnalyzeRequest,
    client:  AiClient = Depends(get_ai_client),
    db:      Session  = Depends(get_db),
) -> AnalyzeResponse:
    if not payload.ticker.strip():
        raise HTTPException(status_code=400, detail="ticker must not be empty")

    log = AiAnalysisLog(
        ticker=payload.ticker,
        extra=payload.extra or "",
        active_strats=list(payload.activeStrats),
        risk_params=dict(payload.risk),
    )

    try:
        result = await run_analysis(
            ticker=payload.ticker,
            extra=payload.extra,
            active_strats=payload.activeStrats,
            risk=payload.risk,
            client=client,
        )
    except AiNotConfiguredError as e:
        log.error = str(e)
        db.add(log)
        db.commit()
        return AnalyzeResponse(text=_DISABLED_NOTICE, can_execute_order=False)
    except Exception as e:
        log.error = str(e)[:500]
        db.add(log)
        db.commit()
        raise HTTPException(status_code=502, detail=f"AI provider error: {e}")

    log.text = result.text
    log.model = result.model
    log.input_tokens = result.input_tokens
    log.output_tokens = result.output_tokens
    log.score = result.score
    db.add(log)
    db.commit()

    return AnalyzeResponse(
        text=result.text,
        can_execute_order=False,
        model=result.model,
        score=result.score,
    )
