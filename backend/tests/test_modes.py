from app.core.modes import (
    MODE_CAPABILITIES,
    OperationMode,
    can_ai_execute,
    can_place_live_order,
)


def test_capabilities_defined_for_every_mode():
    for mode in OperationMode:
        assert mode in MODE_CAPABILITIES


def test_simulation_blocks_real_market_data_and_orders():
    caps = MODE_CAPABILITIES[OperationMode.SIMULATION]
    assert caps["real_market_data"] is False
    assert caps["paper_order"] is False
    assert caps["live_order"] is False
    assert caps["ai_can_execute"] is False


def test_live_shadow_does_not_allow_any_order():
    caps = MODE_CAPABILITIES[OperationMode.LIVE_SHADOW]
    assert caps["real_market_data"] is True
    assert caps["paper_order"] is False
    assert caps["live_order"] is False


def test_manual_approval_requires_user_and_blocks_ai_execution():
    caps = MODE_CAPABILITIES[OperationMode.LIVE_MANUAL_APPROVAL]
    assert caps["requires_user_approval"] is True
    assert caps["ai_can_execute"] is False


def test_only_ai_execution_mode_lets_ai_execute_when_flag_on():
    """LIVE_AI_EXECUTION + flag=True 또는 VIRTUAL_AI_EXECUTION(152, flag 무관)
    만 ai_can_execute=True. 152에서 VIRTUAL_AI_EXECUTION 모드가 추가됨."""
    for mode in OperationMode:
        expected = mode in (
            OperationMode.LIVE_AI_EXECUTION,
            OperationMode.VIRTUAL_AI_EXECUTION,
        )
        assert can_ai_execute(mode, enable_ai_execution=True) is expected


def test_ai_execution_blocked_when_flag_off():
    assert can_ai_execute(OperationMode.LIVE_AI_EXECUTION, enable_ai_execution=False) is False


def test_live_order_capability_gated_by_global_flag():
    assert can_place_live_order(OperationMode.LIVE_MANUAL_APPROVAL, enable_live_trading=False) is False
    assert can_place_live_order(OperationMode.LIVE_MANUAL_APPROVAL, enable_live_trading=True) is True
    assert can_place_live_order(OperationMode.SIMULATION, enable_live_trading=True) is False
    assert can_place_live_order(OperationMode.LIVE_SHADOW, enable_live_trading=True) is False
