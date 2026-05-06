from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.agent_stats import compute_ai_agent_stats
from app.ai.client import AiClient, AiNotConfiguredError
from app.ai.service import analyze as run_analysis
from app.api.deps import get_ai_client
from app.core.config import get_settings
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


def _is_rate_limit_error(exc: Exception) -> bool:
    """Anthropic SDK가 max_retries 후에도 429를 풀지 못했을 때 True.

    anthropic 패키지가 설치되지 않은 환경(테스트 등)에서도 안전하도록
    import 실패는 False로 간주한다.
    """
    try:
        from anthropic import APIStatusError, RateLimitError
    except ImportError:
        return False
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError) and getattr(exc, "status_code", None) == 429:
        return True
    return False


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
        # 123: 호출 시점의 운용모드 기록 — frontend ModeBadge가 timeline/AI
        # sub-tab에서 자동 표시. 미래 mode별 cost 분포 분석에도 활용.
        mode=get_settings().default_mode.value,
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
        if _is_rate_limit_error(e):
            # SDK가 max_retries회 backoff 후에도 풀지 못한 429.
            # 502가 아닌 429로 매핑해 호출자가 재시도 시점을 판단할 수 있게 한다.
            raise HTTPException(status_code=429, detail=f"AI provider rate limited: {e}")
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


@router.get("/agent-stats")
def agent_stats(
    lookback_days: int = Query(7, ge=0, le=365),
    db:            Session = Depends(get_db),
) -> dict:
    """162: AI 에이전트의 의사결정 품질 통계. read-only — 어떤 가드 / 결정에도
    영향 X. lookback_days=0이면 전체 기간."""
    return compute_ai_agent_stats(db, lookback_days=lookback_days)


@router.get("/agent-decisions")
def agent_decisions(
    limit:      int = Query(50, ge=1, le=200),
    chain_id:   str | None = Query(None, description="특정 chain_id의 모든 결정 조회"),
    agent_name: str | None = Query(None, description="206: agent별 narrow"),
    decision:   str | None = Query(None, description="206: 결정값별 narrow"),
    db:         Session = Depends(get_db),
) -> list[dict]:
    """187: AgentDecisionLog 조회. read-only.

    chain_id 지정 시 해당 chain의 모든 결정 (1 chief + 9 members) 일괄 조회.
    미지정 시 최근 N건 (created_at desc).

    206: agent_name + decision 필터 — 운영자가 "ChiefTradingAgent의 REJECT만"
    같은 narrow를 할 수 있도록. chain_id와 동시에 지정해도 작동 (해당 chain의
    해당 agent만).
    """
    from sqlalchemy import select
    from app.db.models import AgentDecisionLog
    stmt = select(AgentDecisionLog)
    if chain_id is not None:
        stmt = stmt.where(AgentDecisionLog.chain_id == chain_id).order_by(
            AgentDecisionLog.id
        )
    else:
        stmt = stmt.order_by(AgentDecisionLog.id.desc()).limit(limit)
    if agent_name is not None:
        stmt = stmt.where(AgentDecisionLog.agent_name == agent_name)
    if decision is not None:
        stmt = stmt.where(AgentDecisionLog.decision == decision)
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id":         r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "agent_name": r.agent_name,
            "symbol":     r.symbol,
            "mode":       r.mode,
            "decision":   r.decision,
            "confidence": r.confidence,
            "reasons":    r.reasons,
            "meta":       r.meta,
            "chain_id":   r.chain_id,
        }
        for r in rows
    ]


@router.get("/agent-decisions/summary")
def agent_decisions_summary(
    db: Session = Depends(get_db),
) -> dict:
    """205: AgentDecisionLog 집계.

    - `by_agent`: {agent_name: {decision: count}} — 각 agent별 결정 분포.
    - `total_decisions`: 전체 row 수.
    - `total_chains`: distinct chain_id 수 (None은 0개로 계산).
    - `recent_chains`: 최근 5개 chain의 (chain_id, chief_decision, created_at).

    Read-only — broker / order side effect 0건. CLAUDE.md 절대 원칙 준수.
    """
    from collections import defaultdict
    from sqlalchemy import distinct, func, select
    from app.db.models import AgentDecisionLog

    by_agent: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total_decisions = 0
    rows = db.execute(
        select(AgentDecisionLog.agent_name, AgentDecisionLog.decision,
               func.count(AgentDecisionLog.id))
        .group_by(AgentDecisionLog.agent_name, AgentDecisionLog.decision)
    ).all()
    for agent_name, decision, n in rows:
        c = int(n or 0)
        by_agent[agent_name][decision] += c
        total_decisions += c

    total_chains = db.execute(
        select(func.count(distinct(AgentDecisionLog.chain_id)))
        .where(AgentDecisionLog.chain_id.is_not(None))
    ).scalar_one() or 0

    # 최근 chain의 chief 결정 — frontend에서 history pin 형태로 보여줌.
    chief_rows = db.execute(
        select(AgentDecisionLog).where(
            AgentDecisionLog.agent_name == "ChiefTradingAgent"
        ).order_by(AgentDecisionLog.id.desc()).limit(5)
    ).scalars().all()
    recent_chains = [
        {
            "chain_id":   r.chain_id,
            "decision":   r.decision,
            "symbol":     r.symbol,
            "confidence": r.confidence,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in chief_rows
    ]

    return {
        "by_agent":        {k: dict(v) for k, v in by_agent.items()},
        "total_decisions": total_decisions,
        "total_chains":    int(total_chains),
        "recent_chains":   recent_chains,
    }
