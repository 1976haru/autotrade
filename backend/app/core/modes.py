from enum import StrEnum


class OperationMode(StrEnum):
    SIMULATION = "SIMULATION"
    PAPER = "PAPER"
    LIVE_SHADOW = "LIVE_SHADOW"
    LIVE_MANUAL_APPROVAL = "LIVE_MANUAL_APPROVAL"
    LIVE_AI_ASSIST = "LIVE_AI_ASSIST"
    LIVE_AI_EXECUTION = "LIVE_AI_EXECUTION"
    # 152: AI가 가상 주문을 자동 생성 — 실거래 broker endpoint는 호출하지 않는다.
    # LIVE_AI_EXECUTION과의 차이: live_order=False라 어떤 모드 capability 검사도
    # live broker 라우팅을 허용하지 않는다.
    VIRTUAL_AI_EXECUTION = "VIRTUAL_AI_EXECUTION"


MODE_CAPABILITIES: dict[OperationMode, dict[str, bool]] = {
    OperationMode.SIMULATION: {
        "real_market_data": False,
        "paper_order": False,
        "live_order": False,
        "requires_user_approval": False,
        "ai_can_recommend": True,
        "ai_can_execute": False,
    },
    OperationMode.VIRTUAL_AI_EXECUTION: {
        "real_market_data": False,    # MockBroker 시세
        "paper_order": False,
        "live_order": False,          # 라이브 broker 미사용 — 가상 환경에서만 작동
        "requires_user_approval": False,
        "ai_can_recommend": True,
        "ai_can_execute": True,       # 본 모드의 핵심 — AI가 가상 주문 자동 생성
    },
    OperationMode.PAPER: {
        "real_market_data": True,
        "paper_order": True,
        "live_order": False,
        "requires_user_approval": False,
        "ai_can_recommend": True,
        "ai_can_execute": False,
    },
    OperationMode.LIVE_SHADOW: {
        "real_market_data": True,
        "paper_order": False,
        "live_order": False,
        "requires_user_approval": False,
        "ai_can_recommend": True,
        "ai_can_execute": False,
    },
    OperationMode.LIVE_MANUAL_APPROVAL: {
        "real_market_data": True,
        "paper_order": False,
        "live_order": True,
        "requires_user_approval": True,
        "ai_can_recommend": False,
        "ai_can_execute": False,
    },
    OperationMode.LIVE_AI_ASSIST: {
        "real_market_data": True,
        "paper_order": False,
        "live_order": True,
        "requires_user_approval": True,
        "ai_can_recommend": True,
        "ai_can_execute": False,
    },
    OperationMode.LIVE_AI_EXECUTION: {
        "real_market_data": True,
        "paper_order": False,
        "live_order": True,
        "requires_user_approval": False,
        "ai_can_recommend": True,
        "ai_can_execute": True,
    },
}


def can_place_live_order(mode: OperationMode, *, enable_live_trading: bool) -> bool:
    return enable_live_trading and MODE_CAPABILITIES[mode]["live_order"]


def can_ai_execute(mode: OperationMode, *, enable_ai_execution: bool) -> bool:
    """AI가 자동 주문을 만들 수 있는지.

    LIVE_AI_EXECUTION은 `enable_ai_execution=True`(env opt-in) + capability 모두
    필요. VIRTUAL_AI_EXECUTION은 정의상 가상이므로 env flag와 무관하게 capability
    만으로 결정 — flag는 LIVE 경로의 추가 가드.
    """
    if mode == OperationMode.VIRTUAL_AI_EXECUTION:
        return MODE_CAPABILITIES[mode]["ai_can_execute"]
    return enable_ai_execution and MODE_CAPABILITIES[mode]["ai_can_execute"]
