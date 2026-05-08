"""AI Permission Gate 단위 테스트 (#39).

체크리스트 #39: AI 주문 권한을 단계별로 명확히 분리. 5 level × 5 action
매트릭스 + flag-driven 강등 + audit_note 생성 + read-only status API.

핵심 invariant:
- AI API Key는 권한 조건이 아니다 (모듈 입력에 포함되지 않음).
- broker / OrderExecutor / route_order 호출 0건.
"""

import inspect

import pytest

from app.core.modes import OperationMode
from app.risk.ai_permission_gate import (
    AiAction,
    AiPermissionFlags,
    AiPermissionLevel,
    build_permission_matrix,
    build_status,
    current_ai_level,
    evaluate_ai_permission,
)


def _flags(**overrides) -> AiPermissionFlags:
    base = dict(
        enable_live_trading=False,
        enable_ai_execution=False,
        enable_futures_live_trading=False,
        emergency_stop=False,
        disable_ai_orders=False,
    )
    base.update(overrides)
    return AiPermissionFlags(**base)


# ====================================================================
# Enum sanity
# ====================================================================


class TestEnums:
    def test_levels(self):
        assert {lvl.value for lvl in AiPermissionLevel} == {
            "FULL_STOP", "RECOMMEND_ONLY", "APPROVAL_REQUIRED",
            "VIRTUAL_EXECUTION", "LIMITED_LIVE_EXECUTION",
        }

    def test_actions(self):
        assert {a.value for a in AiAction} == {
            "RECOMMEND", "SUBMIT_FOR_APPROVAL", "VIRTUAL_EXECUTE",
            "LIVE_EXECUTE", "FUTURES_LIVE_EXECUTE",
        }


# ====================================================================
# current_ai_level — flag-driven 강등
# ====================================================================


class TestCurrentLevel:
    def test_emergency_stop_yields_full_stop(self):
        for mode in OperationMode:
            assert current_ai_level(mode, _flags(emergency_stop=True)) == \
                   AiPermissionLevel.FULL_STOP

    def test_disable_ai_orders_yields_full_stop(self):
        for mode in OperationMode:
            assert current_ai_level(mode, _flags(disable_ai_orders=True)) == \
                   AiPermissionLevel.FULL_STOP

    def test_simulation_yields_recommend_only(self):
        assert current_ai_level(OperationMode.SIMULATION, _flags()) == \
               AiPermissionLevel.RECOMMEND_ONLY

    def test_paper_yields_recommend_only(self):
        assert current_ai_level(OperationMode.PAPER, _flags()) == \
               AiPermissionLevel.RECOMMEND_ONLY

    def test_live_shadow_yields_recommend_only(self):
        assert current_ai_level(OperationMode.LIVE_SHADOW, _flags()) == \
               AiPermissionLevel.RECOMMEND_ONLY

    def test_live_manual_approval_yields_full_stop(self):
        """LIVE_MANUAL_APPROVAL은 운영자 수동 모드 — AI 추천도 차단."""
        assert current_ai_level(OperationMode.LIVE_MANUAL_APPROVAL, _flags()) == \
               AiPermissionLevel.FULL_STOP

    def test_live_ai_assist_yields_approval_required(self):
        assert current_ai_level(OperationMode.LIVE_AI_ASSIST, _flags()) == \
               AiPermissionLevel.APPROVAL_REQUIRED

    def test_virtual_ai_execution_yields_virtual_execution(self):
        assert current_ai_level(OperationMode.VIRTUAL_AI_EXECUTION, _flags()) == \
               AiPermissionLevel.VIRTUAL_EXECUTION

    def test_live_ai_execution_requires_both_flags(self):
        # 두 flag 모두 True여야 LIMITED_LIVE_EXECUTION
        assert current_ai_level(
            OperationMode.LIVE_AI_EXECUTION,
            _flags(enable_live_trading=True, enable_ai_execution=True),
        ) == AiPermissionLevel.LIMITED_LIVE_EXECUTION

    def test_live_ai_execution_demoted_when_live_off(self):
        assert current_ai_level(
            OperationMode.LIVE_AI_EXECUTION,
            _flags(enable_live_trading=False, enable_ai_execution=True),
        ) == AiPermissionLevel.APPROVAL_REQUIRED

    def test_live_ai_execution_demoted_when_ai_off(self):
        assert current_ai_level(
            OperationMode.LIVE_AI_EXECUTION,
            _flags(enable_live_trading=True, enable_ai_execution=False),
        ) == AiPermissionLevel.APPROVAL_REQUIRED


# ====================================================================
# evaluate_ai_permission — 모드별 행동 매트릭스
# ====================================================================


class TestEvaluateMatrix:
    def test_simulation_recommend_allowed(self):
        r = evaluate_ai_permission(
            action=AiAction.RECOMMEND, mode=OperationMode.SIMULATION, flags=_flags(),
        )
        assert r.allowed is True
        assert r.reasons == []
        assert r.level == AiPermissionLevel.RECOMMEND_ONLY

    def test_simulation_blocks_live_execute(self):
        r = evaluate_ai_permission(
            action=AiAction.LIVE_EXECUTE, mode=OperationMode.SIMULATION, flags=_flags(),
        )
        assert r.allowed is False
        assert any("not in level RECOMMEND_ONLY" in rs for rs in r.reasons)

    def test_simulation_blocks_virtual_execute(self):
        r = evaluate_ai_permission(
            action=AiAction.VIRTUAL_EXECUTE, mode=OperationMode.SIMULATION, flags=_flags(),
        )
        assert r.allowed is False

    def test_live_ai_assist_allows_submit_for_approval(self):
        r = evaluate_ai_permission(
            action=AiAction.SUBMIT_FOR_APPROVAL,
            mode=OperationMode.LIVE_AI_ASSIST, flags=_flags(),
        )
        assert r.allowed is True
        assert r.level == AiPermissionLevel.APPROVAL_REQUIRED

    def test_live_ai_assist_blocks_live_execute(self):
        r = evaluate_ai_permission(
            action=AiAction.LIVE_EXECUTE,
            mode=OperationMode.LIVE_AI_ASSIST, flags=_flags(),
        )
        assert r.allowed is False

    def test_virtual_ai_execution_allows_virtual_execute(self):
        r = evaluate_ai_permission(
            action=AiAction.VIRTUAL_EXECUTE,
            mode=OperationMode.VIRTUAL_AI_EXECUTION, flags=_flags(),
        )
        assert r.allowed is True
        assert r.level == AiPermissionLevel.VIRTUAL_EXECUTION

    def test_virtual_ai_execution_blocks_live_execute(self):
        r = evaluate_ai_permission(
            action=AiAction.LIVE_EXECUTE,
            mode=OperationMode.VIRTUAL_AI_EXECUTION, flags=_flags(),
        )
        assert r.allowed is False

    def test_live_ai_execution_with_flags_allows_live_execute(self):
        r = evaluate_ai_permission(
            action=AiAction.LIVE_EXECUTE,
            mode=OperationMode.LIVE_AI_EXECUTION,
            flags=_flags(enable_live_trading=True, enable_ai_execution=True),
        )
        assert r.allowed is True
        assert r.level == AiPermissionLevel.LIMITED_LIVE_EXECUTION

    def test_live_ai_execution_without_flags_blocks_live_execute(self):
        r = evaluate_ai_permission(
            action=AiAction.LIVE_EXECUTE,
            mode=OperationMode.LIVE_AI_EXECUTION,
            flags=_flags(),
        )
        # demoted to APPROVAL_REQUIRED → LIVE_EXECUTE blocked
        assert r.allowed is False
        assert r.level == AiPermissionLevel.APPROVAL_REQUIRED

    def test_live_manual_approval_blocks_everything(self):
        for action in AiAction:
            r = evaluate_ai_permission(
                action=action, mode=OperationMode.LIVE_MANUAL_APPROVAL, flags=_flags(),
            )
            assert r.allowed is False, action
            assert r.level == AiPermissionLevel.FULL_STOP


# ====================================================================
# Flag-driven hard stop
# ====================================================================


class TestHardStops:
    def test_emergency_stop_blocks_everything(self):
        for mode in OperationMode:
            for action in AiAction:
                r = evaluate_ai_permission(
                    action=action, mode=mode, flags=_flags(emergency_stop=True),
                )
                assert r.allowed is False
                assert r.level == AiPermissionLevel.FULL_STOP
                assert any("emergency_stop" in rs for rs in r.reasons)

    def test_disable_ai_orders_blocks_everything(self):
        for mode in OperationMode:
            for action in AiAction:
                r = evaluate_ai_permission(
                    action=action, mode=mode, flags=_flags(disable_ai_orders=True),
                )
                assert r.allowed is False
                assert r.level == AiPermissionLevel.FULL_STOP

    def test_futures_live_blocked_without_flag(self):
        r = evaluate_ai_permission(
            action=AiAction.FUTURES_LIVE_EXECUTE,
            mode=OperationMode.LIVE_AI_EXECUTION,
            flags=_flags(enable_live_trading=True, enable_ai_execution=True,
                         enable_futures_live_trading=False),
        )
        assert r.allowed is False
        assert any("futures live trading is disabled" in rs for rs in r.reasons)

    def test_futures_live_allowed_only_with_flag(self):
        r = evaluate_ai_permission(
            action=AiAction.FUTURES_LIVE_EXECUTE,
            mode=OperationMode.LIVE_AI_EXECUTION,
            flags=_flags(enable_live_trading=True, enable_ai_execution=True,
                         enable_futures_live_trading=True),
        )
        assert r.allowed is True


# ====================================================================
# AiPermissionDecision serialization + audit_note
# ====================================================================


class TestDecision:
    def test_audit_note_on_allow(self):
        r = evaluate_ai_permission(
            action=AiAction.RECOMMEND, mode=OperationMode.SIMULATION, flags=_flags(),
        )
        assert "AI permission OK" in r.audit_note
        assert "SIMULATION" in r.audit_note
        assert "RECOMMEND" in r.audit_note

    def test_audit_note_on_block(self):
        r = evaluate_ai_permission(
            action=AiAction.LIVE_EXECUTE, mode=OperationMode.SIMULATION, flags=_flags(),
        )
        assert "AI permission BLOCKED" in r.audit_note
        assert "SIMULATION" in r.audit_note
        assert "LIVE_EXECUTE" in r.audit_note

    def test_to_dict_round_trip(self):
        r = evaluate_ai_permission(
            action=AiAction.RECOMMEND, mode=OperationMode.SIMULATION, flags=_flags(),
        )
        d = r.to_dict()
        assert d["allowed"] is True
        assert d["level"] == "RECOMMEND_ONLY"
        assert d["action"] == "RECOMMEND"
        assert d["mode"] == "SIMULATION"
        assert "audit_note" in d


# ====================================================================
# Matrix surface
# ====================================================================


class TestMatrix:
    def test_matrix_includes_all_modes_and_actions(self):
        m = build_permission_matrix()
        assert set(m.keys()) == {mode.value for mode in OperationMode}
        for mode_val, actions in m.items():
            assert set(actions.keys()) == {a.value for a in AiAction}

    def test_default_flags_block_live_execute_everywhere(self):
        m = build_permission_matrix()
        for mode_val in m:
            assert m[mode_val]["LIVE_EXECUTE"] is False, mode_val

    def test_simulation_only_recommend_allowed(self):
        m = build_permission_matrix()
        sim = m["SIMULATION"]
        assert sim["RECOMMEND"] is True
        assert sim["SUBMIT_FOR_APPROVAL"] is False
        assert sim["VIRTUAL_EXECUTE"] is False


class TestStatus:
    def test_status_simulation(self):
        s = build_status(mode=OperationMode.SIMULATION, flags=_flags())
        assert s["mode"] == "SIMULATION"
        assert s["level"] == "RECOMMEND_ONLY"
        assert "RECOMMEND" in s["allowed_actions"]
        assert "LIVE_EXECUTE" in s["blocked_actions"]
        assert s["live_execution_disabled"] is True
        assert s["futures_live_disabled"] is True
        assert "AI API Key는 주문 권한이 아닙니다" in s["notice"]

    def test_status_live_ai_execution_with_flags(self):
        s = build_status(
            mode=OperationMode.LIVE_AI_EXECUTION,
            flags=_flags(enable_live_trading=True, enable_ai_execution=True),
        )
        assert s["level"] == "LIMITED_LIVE_EXECUTION"
        assert s["live_execution_disabled"] is False

    def test_status_emergency_stop(self):
        s = build_status(mode=OperationMode.LIVE_AI_EXECUTION,
                          flags=_flags(emergency_stop=True))
        assert s["level"] == "FULL_STOP"
        assert s["allowed_actions"] == []

    def test_status_includes_matrix(self):
        s = build_status(mode=OperationMode.SIMULATION, flags=_flags())
        assert "matrix" in s
        assert "SIMULATION" in s["matrix"]


# ====================================================================
# /api/risk/ai-permission/status endpoint
# ====================================================================


class TestStatusEndpoint:
    def test_status_endpoint_returns_payload(self, client):
        res = client.get("/api/risk/ai-permission/status")
        assert res.status_code == 200
        body = res.json()
        # basic shape
        assert "level" in body
        assert "mode" in body
        assert "allowed_actions" in body
        assert "blocked_actions" in body
        assert "matrix" in body
        assert "notice" in body
        assert "AI API Key" in body["notice"]
        # 기본 setting (default mode + default flags)에서 LIVE_EXECUTE 허용 X
        assert "LIVE_EXECUTE" in body["blocked_actions"]


# ====================================================================
# Safety invariants
# ====================================================================


class TestSafety:
    def test_module_does_not_take_api_key(self):
        """AiPermissionFlags에 api_key / secret 어떤 필드도 없다."""
        flag_attrs = AiPermissionFlags.__dataclass_fields__.keys()
        forbidden = ("api_key", "secret", "account_no", "kis_app_key",
                     "kis_app_secret", "anthropic_api_key", "openai_api_key")
        for f in forbidden:
            assert f not in flag_attrs, f"forbidden secret-like flag: {f}"

    def test_module_does_not_import_broker_or_executor(self):
        from app.risk import ai_permission_gate as mod
        src = inspect.getsource(mod)
        forbidden = (
            "from app.brokers", "from app.execution.executor",
            "from app.execution.order_router",
            "place_order(", "cancel_order(", "route_order(",
        )
        for f in forbidden:
            assert f not in src, f"forbidden symbol: {f}"

    def test_evaluate_signature_does_not_accept_api_key(self):
        sig = inspect.signature(evaluate_ai_permission)
        for p in sig.parameters:
            assert "api_key" not in p.lower()
            assert "secret" not in p.lower()

    def test_module_is_pure(self):
        """evaluate / current_ai_level / build_status 모두 input 변경 없이 결과만 반환."""
        f = _flags()
        # 같은 입력에 같은 출력 (deterministic)
        a = evaluate_ai_permission(action=AiAction.RECOMMEND,
                                     mode=OperationMode.SIMULATION, flags=f)
        b = evaluate_ai_permission(action=AiAction.RECOMMEND,
                                     mode=OperationMode.SIMULATION, flags=f)
        assert a == b

    def test_decision_is_frozen_dataclass(self):
        """결과 객체는 frozen — 호출자가 임의 변경 불가."""
        r = evaluate_ai_permission(
            action=AiAction.RECOMMEND, mode=OperationMode.SIMULATION, flags=_flags(),
        )
        with pytest.raises((AttributeError, Exception)):
            r.allowed = False  # type: ignore[misc]
        # frozen dataclass — to_dict로 직렬화는 가능
        assert isinstance(r.to_dict(), dict)
