"""Pre-market Check (#80) — evaluator + API + CLI + invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.governance.pre_market_check import (
    CheckStatus,
    PreMarketCheckInput,
    PreMarketCheckResult,
    PreMarketVerdict,
    evaluate_pre_market_check,
    render_markdown_report,
)


def _ready_paper_input(**kw) -> PreMarketCheckInput:
    """PAPER 모드에서 모든 기본 항목 통과하는 입력."""
    defaults = dict(
        mode="PAPER",
        api_reachable=True,
        db_reachable=True,
        broker_ready=True,
        kis_is_paper=True,
        kis_credentials_present=True,
        market_data_provider="mock",
        data_freshness_ok=True,
        stale_symbol_count=0,
        watchlist_item_count=10,
        active_strategy_count=2,
        risk_policy_configured=True,
        daily_loss_limit_configured=True,
        daily_loss_used_ratio=0.2,
        emergency_stop_active=False,
        notification_configured=True,
        enable_live_trading=False,
    )
    defaults.update(kw)
    return PreMarketCheckInput(**defaults)


# ---------- DTO invariants ----------


def test_result_rejects_is_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        PreMarketCheckResult(
            mode="SIMULATION",
            verdict=PreMarketVerdict.READY_TO_START,
            start_allowed=True,
            is_order_signal=True,
        )


def test_result_rejects_live_flag_changed_true():
    with pytest.raises(ValueError, match="live_flag_changed"):
        PreMarketCheckResult(
            mode="SIMULATION",
            verdict=PreMarketVerdict.READY_TO_START,
            start_allowed=True,
            live_flag_changed=True,
        )


def test_result_rejects_mode_changed_true():
    with pytest.raises(ValueError, match="mode_changed"):
        PreMarketCheckResult(
            mode="SIMULATION",
            verdict=PreMarketVerdict.READY_TO_START,
            start_allowed=True,
            mode_changed=True,
        )


def test_to_dict_invariant_flags():
    r = evaluate_pre_market_check(_ready_paper_input())
    d = r.to_dict()
    assert d["is_order_signal"] is False
    assert d["live_flag_changed"] is False
    assert d["mode_changed"] is False


# ---------- happy / mode-specific paths ----------


def test_simulation_minimum_passes_with_watchlist():
    inp = PreMarketCheckInput(
        mode="SIMULATION",
        watchlist_item_count=3,
        active_strategy_count=1,
    )
    r = evaluate_pre_market_check(inp)
    assert r.start_allowed is True
    # SIMULATION mode → broker_sim SKIP.
    assert any(it.name == "broker_sim" and it.status is CheckStatus.SKIP for it in r.items)


def test_simulation_fails_when_watchlist_empty():
    inp = PreMarketCheckInput(mode="SIMULATION", watchlist_item_count=0)
    r = evaluate_pre_market_check(inp)
    assert r.start_allowed is False
    assert "watchlist" in r.failed_required


def test_paper_ready_passes():
    r = evaluate_pre_market_check(_ready_paper_input())
    assert r.start_allowed is True
    assert r.verdict in (
        PreMarketVerdict.READY_TO_START,
        PreMarketVerdict.WARN_BUT_START_ALLOWED,
    )


def test_paper_fails_when_broker_not_ready():
    r = evaluate_pre_market_check(_ready_paper_input(broker_ready=False))
    assert r.start_allowed is False
    assert "broker_paper" in r.failed_required


def test_paper_fails_when_kis_is_paper_false():
    """PAPER 모드인데 KIS_IS_PAPER=false 면 즉시 차단."""
    r = evaluate_pre_market_check(_ready_paper_input(kis_is_paper=False))
    assert r.start_allowed is False
    assert "broker_paper" in r.failed_required


def test_paper_fails_when_data_stale():
    r = evaluate_pre_market_check(_ready_paper_input(data_freshness_ok=False))
    assert r.start_allowed is False
    assert "data_freshness" in r.failed_required


def test_paper_warns_when_stale_symbols_but_freshness_ok():
    r = evaluate_pre_market_check(
        _ready_paper_input(data_freshness_ok=True, stale_symbol_count=2),
    )
    # WARN 이라도 start_allowed True.
    assert r.start_allowed is True
    assert r.verdict is PreMarketVerdict.WARN_BUT_START_ALLOWED


# ---------- kill switch ----------


def test_emergency_stop_blocks_start():
    r = evaluate_pre_market_check(
        _ready_paper_input(emergency_stop_active=True, kill_switch_level="LEVEL_1"),
    )
    assert r.start_allowed is False
    assert "kill_switch" in r.failed_required


# ---------- daily loss limit ----------


def test_daily_loss_limit_exceeded_fails():
    r = evaluate_pre_market_check(_ready_paper_input(daily_loss_used_ratio=1.0))
    assert r.start_allowed is False
    assert "daily_loss_limit" in r.failed_required


def test_daily_loss_limit_80pct_warns():
    r = evaluate_pre_market_check(_ready_paper_input(daily_loss_used_ratio=0.85))
    assert r.start_allowed is True
    assert any("일일 손실한도" in w for w in r.warnings)


# ---------- LIVE_MANUAL_APPROVAL — governance gate carry ----------


def _ready_live_manual(**kw) -> PreMarketCheckInput:
    defaults = dict(
        mode="LIVE_MANUAL_APPROVAL",
        api_reachable=True, db_reachable=True,
        broker_ready=True, kis_credentials_present=True,
        kis_is_paper=False,
        market_data_provider="mock",
        data_freshness_ok=True,
        watchlist_item_count=5,
        active_strategy_count=2,
        risk_policy_configured=True,
        daily_loss_limit_configured=True,
        daily_loss_used_ratio=0.1,
        emergency_stop_active=False,
        notification_configured=True,
        enable_live_trading=True,
        paper_gate_pass=True,
        live_manual_gate_pass=True,
    )
    defaults.update(kw)
    return PreMarketCheckInput(**defaults)


def test_live_manual_ready_passes():
    r = evaluate_pre_market_check(_ready_live_manual())
    assert r.start_allowed is True


def test_live_manual_fails_without_paper_gate():
    r = evaluate_pre_market_check(_ready_live_manual(paper_gate_pass=False))
    assert r.start_allowed is False
    assert "paper_gate" in r.failed_required


def test_live_manual_fails_without_live_manual_gate():
    r = evaluate_pre_market_check(_ready_live_manual(live_manual_gate_pass=False))
    assert r.start_allowed is False
    assert "live_manual_gate" in r.failed_required


def test_live_manual_fails_when_enable_live_trading_false():
    r = evaluate_pre_market_check(_ready_live_manual(enable_live_trading=False))
    assert r.start_allowed is False
    assert "live_trading_flag" in r.failed_required


# ---------- LIVE_AI_ASSIST / LIVE_AI_EXECUTION ----------


def test_live_ai_assist_requires_ai_assist_gate():
    inp = PreMarketCheckInput(
        mode="LIVE_AI_ASSIST",
        api_reachable=True, db_reachable=True,
        broker_ready=True, kis_credentials_present=True,
        data_freshness_ok=True, watchlist_item_count=5,
        active_strategy_count=2, daily_loss_limit_configured=True,
        ai_permission_gate_active=True, enable_live_trading=True,
        paper_gate_pass=True, live_manual_gate_pass=True,
        ai_assist_gate_pass=False,  # ← fail
    )
    r = evaluate_pre_market_check(inp)
    assert r.start_allowed is False
    assert "ai_assist_gate" in r.failed_required


def test_live_ai_execution_requires_execution_gate_ready():
    inp = PreMarketCheckInput(
        mode="LIVE_AI_EXECUTION",
        api_reachable=True, db_reachable=True,
        broker_ready=True, kis_credentials_present=True,
        data_freshness_ok=True, watchlist_item_count=5,
        active_strategy_count=2, daily_loss_limit_configured=True,
        ai_permission_gate_active=True, enable_live_trading=True,
        ai_execution_enabled=False,  # ← fail
        paper_gate_pass=True, live_manual_gate_pass=True,
        ai_assist_gate_pass=True, ai_execution_gate_ready=True,
    )
    r = evaluate_pre_market_check(inp)
    assert r.start_allowed is False
    assert "ai_execution_flag" in r.failed_required


def test_futures_live_flag_true_always_blocks():
    inp = PreMarketCheckInput(
        mode="SIMULATION",
        watchlist_item_count=3, active_strategy_count=1,
        enable_futures_live_trading=True,
    )
    r = evaluate_pre_market_check(inp)
    assert r.start_allowed is False
    assert "futures_live_flag" in r.failed_required


# ---------- manual ack non-bypass ----------


def test_manual_ack_does_not_bypass_fail():
    """manual_ack=True 라도 required FAIL 이 있으면 start_allowed=False."""
    inp = PreMarketCheckInput(
        mode="PAPER",
        watchlist_item_count=0,  # FAIL 요인
        manual_ack=True, manual_ack_by="operator",
        manual_ack_note="확인했음",
    )
    r = evaluate_pre_market_check(inp)
    assert r.start_allowed is False
    assert r.manual_ack_recorded is True
    assert r.manual_ack_by == "operator"
    # required FAIL 우회 불가 안내.
    assert any("manual_ack" in a for a in r.required_actions)


def test_manual_ack_recorded_when_pass():
    """PASS 시에도 manual_ack 는 단순 기록."""
    r = evaluate_pre_market_check(_ready_paper_input(
        manual_ack=True, manual_ack_by="operator",
    ))
    assert r.start_allowed is True
    assert r.manual_ack_recorded is True


# ---------- strict mode ----------


def test_strict_mode_treats_unknown_required_as_fail():
    inp = PreMarketCheckInput(
        mode="PAPER",
        watchlist_item_count=5, active_strategy_count=1,
        broker_ready=None,  # UNKNOWN
        strict=True,
    )
    r = evaluate_pre_market_check(inp)
    assert r.start_allowed is False
    assert "broker_paper" in r.failed_required


def test_non_strict_unknown_does_not_block():
    """UNKNOWN required 라도 non-strict 면 시작 허용 (warning 만)."""
    inp = PreMarketCheckInput(
        mode="PAPER",
        watchlist_item_count=5, active_strategy_count=1,
        broker_ready=None,
        strict=False,
        # required PAPER 항목 충족.
        data_freshness_ok=True, daily_loss_limit_configured=True,
    )
    r = evaluate_pre_market_check(inp)
    # required UNKNOWN 은 FAIL 아니므로 start_allowed True.
    assert r.start_allowed is True


# ---------- warnings ----------


def test_notification_missing_warns():
    r = evaluate_pre_market_check(_ready_paper_input(notification_configured=False))
    assert r.start_allowed is True
    assert any("Notification" in w for w in r.warnings)


def test_position_limits_warns():
    r = evaluate_pre_market_check(
        _ready_paper_input(position_limits_configured=False),
    )
    assert r.start_allowed is True
    assert any("PositionLimitRule" in w for w in r.warnings)


# ---------- markdown ----------


def test_markdown_report_contains_disclaimers_and_no_flag_changes():
    r = evaluate_pre_market_check(_ready_paper_input())
    text = render_markdown_report(r)
    assert "안전 점검" in text
    assert "안전 플래그를 변경하지 않습니다" in text
    for banned in ["매수 실행", "매도 실행", "BUY signal", "SELL signal", "HOLD signal"]:
        assert banned not in text


def test_markdown_lists_failed_required_and_actions():
    inp = _ready_paper_input(broker_ready=False)
    r = evaluate_pre_market_check(inp)
    text = render_markdown_report(r)
    assert "DO_NOT_START" in text
    assert "broker_paper" in text


# ---------- API ----------


def test_route_get_default_simulation(client):
    res = client.get("/api/governance/pre-market-check")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["mode"] == "SIMULATION"
    # default watchlist=0 → FAIL.
    assert data["start_allowed"] is False
    assert data["is_order_signal"] is False
    assert data["live_flag_changed"] is False
    assert data["mode_changed"] is False


def test_route_post_paper_ready_passes(client):
    body = {
        "mode": "PAPER",
        "broker_ready": True, "kis_is_paper": True,
        "kis_credentials_present": True,
        "data_freshness_ok": True,
        "watchlist_item_count": 5, "active_strategy_count": 1,
        "daily_loss_limit_configured": True,
    }
    res = client.post("/api/governance/pre-market-check", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["start_allowed"] is True


def test_route_post_blocks_emergency_stop(client):
    body = {
        "mode": "PAPER",
        "broker_ready": True, "kis_is_paper": True,
        "kis_credentials_present": True,
        "data_freshness_ok": True,
        "watchlist_item_count": 5, "active_strategy_count": 1,
        "emergency_stop_active": True, "kill_switch_level": "LEVEL_1",
    }
    res = client.post("/api/governance/pre-market-check", json=body)
    assert res.status_code == 200
    assert res.json()["start_allowed"] is False


def test_route_response_does_not_leak_secrets(client):
    res = client.get("/api/governance/pre-market-check")
    text = res.text.lower()
    for needle in [
        "kis_app_key", "kis_app_secret", "anthropic_api_key",
        "telegram_bot_token", "sk-", "bearer ",
    ]:
        assert needle not in text


# ---------- CLI smoke ----------


def test_cli_simulation_default_exits_1_due_to_empty_watchlist():
    import subprocess
    import sys
    project_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "scripts/pre_market_check.py",
         "--mode", "SIMULATION", "--format", "json"],
        cwd=str(project_root),
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=30,
    )
    # default --watchlist 0 → fail → exit 1.
    assert proc.returncode == 1
    assert "DO_NOT_START" in proc.stdout


def test_cli_paper_with_inputs_exits_0():
    import subprocess
    import sys
    project_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [
            sys.executable, "scripts/pre_market_check.py",
            "--mode", "PAPER", "--format", "json",
            "--broker-ready", "--kis-is-paper",
            "--kis-credentials-present",
            "--data-freshness-ok",
            "--watchlist", "5", "--strategies", "1",
            "--daily-loss-limit-configured",
        ],
        cwd=str(project_root),
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
    assert "READY_TO_START" in proc.stdout or "WARN_BUT_START_ALLOWED" in proc.stdout


# ---------- invariants — static grep guards ----------


_MODULE_PATH = Path("backend/app/governance/pre_market_check.py")
_CLI_PATH    = Path("scripts/pre_market_check.py")


def _resolve(p: Path) -> Path:
    return p if p.exists() else Path(__file__).resolve().parents[2] / p


def test_module_does_not_import_broker_or_executor():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.order_router",
        "from app.execution.order_executor",
        "from app.execution.executor",
        "from app.execution.paper_trader",
        "from app.ai.assist",
        "from app.ai.client",
        "import anthropic",
        "import openai",
        "import httpx",
        "import requests",
    ]
    for needle in forbidden:
        assert needle not in src, f"forbidden import: {needle!r}"


def test_module_does_not_call_broker_or_route_order():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        "OrderExecutor(",
        "submit_candidate(",
        "AiClient(",
    ]
    for needle in forbidden:
        assert needle not in src, f"forbidden call: {needle!r}"


def test_module_does_not_mutate_safety_flags():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "setattr(settings, \"enable_",
        "os.environ[\"ENABLE_LIVE_TRADING\"]",
        "os.environ[\"ENABLE_AI_EXECUTION\"]",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"]",
    ]
    for needle in forbidden:
        assert needle not in src, f"mutates safety flag: {needle!r}"


def test_module_does_not_read_settings():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "from app.core.config import",
        "get_settings(",
    ]
    for needle in forbidden:
        assert needle not in src, f"reads settings: {needle!r}"


def test_module_does_not_write_to_db():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "db.add(", "db.commit(", "db.flush(", "db.delete(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    for needle in forbidden:
        assert needle not in src, f"writes to DB: {needle!r}"


def test_cli_does_not_call_external_api_or_read_env():
    src = _resolve(_CLI_PATH).read_text(encoding="utf-8")
    forbidden = [
        "broker.place_order(", "route_order(",
        "import anthropic", "import openai",
        "httpx.post", "requests.post",
        "load_dotenv",
    ]
    for needle in forbidden:
        assert needle not in src, f"CLI forbidden: {needle!r}"


# ============================================================
# #91 — Desktop EXE / KIS Paper one-click extension
# ============================================================


from app.governance.pre_market_check import CheckCategory  # noqa: E402


def test_91_new_category_enum_values_exist():
    """#91 — DESKTOP / KIS_PAPER 카테고리 enum 값 추가 확인."""
    assert CheckCategory.DESKTOP.value == "desktop"
    assert CheckCategory.KIS_PAPER.value == "kis_paper"


# ---------- beginner safety flag proactive checks ----------


def test_91_kis_is_paper_safety_pass_in_simulation():
    inp = PreMarketCheckInput(
        mode="SIMULATION",
        watchlist_item_count=3, active_strategy_count=1,
        kis_is_paper=True,
    )
    r = evaluate_pre_market_check(inp)
    item = next(it for it in r.items if it.name == "kis_is_paper_safety")
    assert item.status is CheckStatus.PASS
    assert item.category is CheckCategory.KIS_PAPER
    assert item.required is True


def test_91_kis_is_paper_safety_fail_when_false_in_simulation():
    inp = PreMarketCheckInput(
        mode="SIMULATION",
        watchlist_item_count=3, active_strategy_count=1,
        kis_is_paper=False,
    )
    r = evaluate_pre_market_check(inp)
    item = next(it for it in r.items if it.name == "kis_is_paper_safety")
    assert item.status is CheckStatus.FAIL
    assert r.start_allowed is False
    assert "kis_is_paper_safety" in r.failed_required


def test_91_live_trading_safety_fail_in_paper_when_true():
    inp = _ready_paper_input(enable_live_trading=True)
    r = evaluate_pre_market_check(inp)
    item = next(it for it in r.items if it.name == "enable_live_trading_safety")
    assert item.status is CheckStatus.FAIL
    assert r.start_allowed is False
    assert "enable_live_trading_safety" in r.failed_required


def test_91_ai_execution_safety_fail_in_paper_when_true():
    inp = _ready_paper_input(ai_execution_enabled=True)
    r = evaluate_pre_market_check(inp)
    item = next(it for it in r.items if it.name == "enable_ai_execution_safety")
    assert item.status is CheckStatus.FAIL
    assert r.start_allowed is False


def test_91_futures_safety_pass_when_false_in_simulation():
    inp = PreMarketCheckInput(
        mode="SIMULATION",
        watchlist_item_count=3, active_strategy_count=1,
        enable_futures_live_trading=False,
    )
    r = evaluate_pre_market_check(inp)
    item = next(it for it in r.items if it.name == "enable_futures_safety")
    assert item.status is CheckStatus.PASS


def test_91_safety_flag_checks_skipped_in_live_ai_execution_mode():
    """LIVE_AI_EXECUTION 같은 비-초보자 모드에서는 본 4개 safety check 미포함."""
    inp = PreMarketCheckInput(
        mode="LIVE_AI_EXECUTION",
        watchlist_item_count=3, active_strategy_count=1,
    )
    r = evaluate_pre_market_check(inp)
    names = {it.name for it in r.items}
    assert "kis_is_paper_safety" not in names
    assert "enable_live_trading_safety" not in names
    assert "enable_ai_execution_safety" not in names
    assert "enable_futures_safety" not in names


# ---------- desktop sidecar / status endpoint ----------


def test_91_desktop_mode_off_does_not_add_desktop_items():
    """기본 desktop_mode=False 면 DESKTOP / KIS_PAPER capability 항목 미추가."""
    inp = _ready_paper_input(desktop_mode=False)
    r = evaluate_pre_market_check(inp)
    names = {it.name for it in r.items}
    assert "desktop_sidecar" not in names
    assert "desktop_status_endpoint" not in names
    assert "kis_paper_readiness" not in names
    assert "kis_paper_capability" not in names


def test_91_desktop_mode_adds_sidecar_and_status_checks():
    inp = _ready_paper_input(
        desktop_mode=True,
        desktop_sidecar_connected=True,
        desktop_status_endpoint_ok=True,
        kis_paper_ready=True,
        kis_paper_can_run_mock=True,
        kis_paper_can_run_kis=True,
    )
    r = evaluate_pre_market_check(inp)
    item_sidecar = next(it for it in r.items if it.name == "desktop_sidecar")
    item_status  = next(it for it in r.items if it.name == "desktop_status_endpoint")
    assert item_sidecar.status is CheckStatus.PASS
    assert item_sidecar.category is CheckCategory.DESKTOP
    assert item_status.status is CheckStatus.PASS
    assert item_status.category is CheckCategory.DESKTOP


def test_91_desktop_sidecar_disconnected_fails():
    inp = _ready_paper_input(
        desktop_mode=True,
        desktop_sidecar_connected=False,
        desktop_status_endpoint_ok=True,
    )
    r = evaluate_pre_market_check(inp)
    item = next(it for it in r.items if it.name == "desktop_sidecar")
    assert item.status is CheckStatus.FAIL
    assert "desktop_sidecar" in r.failed_required
    assert r.start_allowed is False


def test_91_desktop_status_endpoint_unknown_in_strict_mode_fails():
    inp = _ready_paper_input(
        desktop_mode=True,
        desktop_sidecar_connected=True,
        desktop_status_endpoint_ok=None,  # UNKNOWN
        strict=True,
    )
    r = evaluate_pre_market_check(inp)
    assert r.start_allowed is False
    assert "desktop_status_endpoint" in r.failed_required


# ---------- KIS Paper readiness ----------


def test_91_kis_paper_readiness_blocked_reasons_carry_as_labels():
    """blocked_reasons 는 *라벨*만 carry — secret 원문 0건."""
    inp = _ready_paper_input(
        desktop_mode=True,
        desktop_sidecar_connected=True,
        desktop_status_endpoint_ok=True,
        kis_paper_ready=False,
        kis_paper_blocked_reasons=("ENABLE_LIVE_TRADING_TRUE", "KIS_IS_PAPER_FALSE"),
    )
    r = evaluate_pre_market_check(inp)
    item = next(it for it in r.items if it.name == "kis_paper_readiness")
    assert item.status is CheckStatus.FAIL
    assert item.category is CheckCategory.KIS_PAPER
    # label 만 carry — Secret / API key 원문 검사.
    assert "ENABLE_LIVE_TRADING_TRUE" in item.message
    assert "KIS_IS_PAPER_FALSE" in item.message
    text = item.message.lower()
    for needle in [
        "sk-", "bearer ", "kis_app_key=", "kis_app_secret=", "anthropic_api_key=",
    ]:
        assert needle not in text


def test_91_kis_paper_capability_warn_when_only_mock_available():
    inp = _ready_paper_input(
        desktop_mode=True,
        desktop_sidecar_connected=True,
        desktop_status_endpoint_ok=True,
        kis_paper_ready=True,
        kis_paper_can_run_kis=False,
        kis_paper_can_run_mock=True,
    )
    r = evaluate_pre_market_check(inp)
    item = next(it for it in r.items if it.name == "kis_paper_capability")
    assert item.status is CheckStatus.WARN
    assert item.required is False


def test_91_kis_paper_capability_fail_when_neither_available():
    inp = _ready_paper_input(
        desktop_mode=True,
        desktop_sidecar_connected=True,
        desktop_status_endpoint_ok=True,
        kis_paper_ready=True,
        kis_paper_can_run_kis=False,
        kis_paper_can_run_mock=False,
    )
    r = evaluate_pre_market_check(inp)
    item = next(it for it in r.items if it.name == "kis_paper_capability")
    assert item.status is CheckStatus.FAIL
    assert "kis_paper_capability" in r.failed_required


# ---------- One-click paper test 활성화 게이트 ----------


def test_91_kis_paper_test_allowed_false_when_desktop_mode_off():
    """desktop_mode=False 면 kis_paper_test_allowed 는 항상 False."""
    inp = _ready_paper_input()  # desktop_mode 기본 False
    r = evaluate_pre_market_check(inp)
    assert r.kis_paper_test_allowed is False


def test_91_kis_paper_test_allowed_true_when_all_pass():
    inp = _ready_paper_input(
        desktop_mode=True,
        desktop_sidecar_connected=True,
        desktop_status_endpoint_ok=True,
        kis_paper_ready=True,
        kis_paper_can_run_mock=True,
        kis_paper_can_run_kis=True,
    )
    r = evaluate_pre_market_check(inp)
    assert r.start_allowed is True
    assert r.kis_paper_test_allowed is True


def test_91_kis_paper_test_allowed_false_when_start_blocked():
    """start_allowed=False 면 kis_paper_test_allowed 도 False."""
    inp = _ready_paper_input(
        desktop_mode=True,
        desktop_sidecar_connected=True,
        desktop_status_endpoint_ok=True,
        kis_paper_ready=True,
        kis_paper_can_run_mock=True,
        kis_paper_can_run_kis=True,
        emergency_stop_active=True,   # kill_switch FAIL → start_allowed=False
    )
    r = evaluate_pre_market_check(inp)
    assert r.start_allowed is False
    assert r.kis_paper_test_allowed is False


def test_91_kis_paper_test_allowed_false_when_no_capability():
    """start_allowed=True 이지만 모든 capability 차단 시 False."""
    inp = _ready_paper_input(
        desktop_mode=True,
        desktop_sidecar_connected=True,
        desktop_status_endpoint_ok=True,
        kis_paper_ready=True,
        kis_paper_can_run_mock=False,
        kis_paper_can_run_kis=False,
    )
    r = evaluate_pre_market_check(inp)
    # capability FAIL 로 start_allowed 도 False.
    assert r.start_allowed is False
    assert r.kis_paper_test_allowed is False


def test_91_kis_paper_test_allowed_true_with_mock_only():
    """mock 모드만 가능해도 kis_paper_test_allowed=True (mock 도 유효한 시작 경로)."""
    inp = _ready_paper_input(
        desktop_mode=True,
        desktop_sidecar_connected=True,
        desktop_status_endpoint_ok=True,
        kis_paper_ready=True,
        kis_paper_can_run_mock=True,
        kis_paper_can_run_kis=False,
    )
    r = evaluate_pre_market_check(inp)
    # capability 는 WARN 이지만 required=False 라 start_allowed=True.
    assert r.start_allowed is True
    assert r.kis_paper_test_allowed is True


# ---------- to_dict / API ----------


def test_91_to_dict_carries_kis_paper_test_allowed_field():
    r = evaluate_pre_market_check(_ready_paper_input())
    d = r.to_dict()
    assert "kis_paper_test_allowed" in d
    assert d["kis_paper_test_allowed"] is False


def test_91_route_post_with_desktop_mode_returns_kis_paper_test_allowed(client):
    body = {
        "mode": "PAPER",
        "broker_ready": True, "kis_is_paper": True,
        "kis_credentials_present": True,
        "data_freshness_ok": True,
        "watchlist_item_count": 5, "active_strategy_count": 1,
        "daily_loss_limit_configured": True,
        "desktop_mode": True,
        "desktop_sidecar_connected": True,
        "desktop_status_endpoint_ok": True,
        "kis_paper_ready": True,
        "kis_paper_can_run_mock": True,
        "kis_paper_can_run_kis": True,
    }
    res = client.post("/api/governance/pre-market-check", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["start_allowed"] is True
    assert data["kis_paper_test_allowed"] is True
    # invariant 유지.
    assert data["is_order_signal"] is False
    assert data["live_flag_changed"] is False
    assert data["mode_changed"] is False
    # DESKTOP / KIS_PAPER 카테고리 항목 포함.
    cats = {it["category"] for it in data["items"]}
    assert "desktop" in cats
    assert "kis_paper" in cats


def test_91_route_post_does_not_leak_secrets_with_desktop_payload(client):
    body = {
        "mode": "PAPER",
        "broker_ready": True, "kis_is_paper": True,
        "watchlist_item_count": 5, "active_strategy_count": 1,
        "desktop_mode": True,
        "desktop_sidecar_connected": True,
        "desktop_status_endpoint_ok": False,
        "kis_paper_ready": False,
        "kis_paper_blocked_reasons": [
            "ENABLE_LIVE_TRADING_TRUE", "KIS_IS_PAPER_FALSE",
        ],
    }
    res = client.post("/api/governance/pre-market-check", json=body)
    assert res.status_code == 200, res.text
    text = res.text.lower()
    for needle in [
        "kis_app_key=", "kis_app_secret=", "anthropic_api_key=",
        "telegram_bot_token=", "sk-", "bearer ",
    ]:
        assert needle not in text
