"""Paper Trading read-only status API (#42).

운영자/UI가 "현재 paper 상태인가? 어느 broker가 paper로 라우팅되는가?
LIVE 차단이 켜져 있는가?"를 한 endpoint에서 조회. 본 라우트는 *상태 표시
만* — 주문 / broker 호출 / mode 변경 어떤 부수효과도 만들지 않는다.
"""

from fastapi import APIRouter

from app.execution.paper_trader import build_paper_status


router = APIRouter(prefix="/paper", tags=["paper"])


@router.get("/status")
def paper_status() -> dict:
    """현재 paper mode 상태 + 안전 flag 스냅샷.

    응답:
    - `mode`: 현재 운용모드 (DEFAULT_MODE).
    - `is_paper_mode`: SIMULATION/PAPER 여부.
    - `paper_broker_kind`: MOCK / KIS_PAPER (paper에서 사용할 broker).
    - `kis_is_paper`: KIS_IS_PAPER env 값.
    - `enable_live_trading` / `enable_ai_execution` / `enable_futures_live_
      trading`: 세 LIVE flag (모두 False = 안전).
    - `fill_polling_enabled`: 백그라운드 체결 갱신 여부.
    - `notice`: 운영자 안내 — 모의투자 체결 품질 주의사항.

    read-only. broker 호출 0건. mode/flag 변경 0건.
    """
    status = build_paper_status().to_dict()
    status["notice"] = (
        "Paper(모의투자) 체결 품질은 실제 체결과 다를 수 있습니다. "
        "체결 시간 / 슬리피지 / 부분체결 패턴이 실제 시장과 차이가 있을 수 "
        "있으므로 LIVE 활성화 전 충분한 paper 운용과 reconciliation이 필수입니다."
    )
    return status
