from enum import StrEnum


class OperationMode(StrEnum):
    SIMULATION = "SIMULATION"
    PAPER = "PAPER"
    LIVE_SHADOW = "LIVE_SHADOW"
    LIVE_MANUAL_APPROVAL = "LIVE_MANUAL_APPROVAL"
    LIVE_AI_ASSIST = "LIVE_AI_ASSIST"
    LIVE_AI_EXECUTION = "LIVE_AI_EXECUTION"


MODE_CAPABILITIES: dict[OperationMode, dict[str, bool]] = {
    OperationMode.SIMULATION: {
        "real_market_data": False,
        "paper_order": False,
        "live_order": False,
        "requires_user_approval": False,
        "ai_can_recommend": True,
        "ai_can_execute": False,
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
    return enable_ai_execution and MODE_CAPABILITIES[mode]["ai_can_execute"]
