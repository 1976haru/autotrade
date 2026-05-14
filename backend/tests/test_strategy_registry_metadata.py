"""Strategy Registry beginner metadata (#81) — invariants + API."""

from __future__ import annotations

from pathlib import Path

from app.strategies.concrete import STRATEGY_REGISTRY
from app.strategies.registry_metadata import (
    BeginnerMetadata,
    RecommendedMode,
    RiskLevel,
    beginner_metadata,
    list_beginner_registry,
    validate_metadata,
)


# ---------- expected internal ids (코드에서 *직접* 가져옴) ----------


_EXPECTED_IDS = {
    "sma_crossover", "rsi_reversion", "vwap_strategy",
    "orb_vwap", "volume_breakout", "pullback_rebreak",
}


# ---------- inventory invariants ----------


def test_registry_contains_exactly_six_strategies():
    """*가짜 전략명 추가 금지* — 6개 외에는 등록 0개."""
    assert set(STRATEGY_REGISTRY.keys()) == _EXPECTED_IDS


def test_beginner_metadata_matches_registry_one_to_one():
    """기존 STRATEGY_REGISTRY 와 *완전 1:1* 매핑."""
    out = list_beginner_registry()
    ids = {e["strategy_id"] for e in out}
    assert ids == _EXPECTED_IDS
    # 6개 모두 메타 누락 없음 (validate_metadata 위반 0건).
    assert validate_metadata() == []


def test_validate_metadata_catches_fake_ids():
    """가짜 strategy_id 검출 — 본 테스트는 실제 메타 누출 없음."""
    from app.strategies import registry_metadata as rm
    # 백업 후 가짜 entry 임시 주입.
    backup = dict(rm._BEGINNER_METADATA)
    try:
        rm._BEGINNER_METADATA["fake_super_strategy"] = BeginnerMetadata(
            strategy_id="fake_super_strategy",
            display_name="fake", beginner_name="fake",
            description="fake",
            risk_level=RiskLevel.HIGH,
            recommended_mode=RecommendedMode.PAPER_RECOMMENDED,
        )
        v = validate_metadata()
        assert any("fake_super_strategy" in msg for msg in v)
    finally:
        rm._BEGINNER_METADATA.clear()
        rm._BEGINNER_METADATA.update(backup)


def test_validate_metadata_catches_missing_entry():
    """STRATEGY_REGISTRY 에 있지만 메타 누락 검출."""
    from app.strategies import registry_metadata as rm
    backup = dict(rm._BEGINNER_METADATA)
    try:
        rm._BEGINNER_METADATA.pop("sma_crossover")
        v = validate_metadata()
        assert any("missing beginner metadata" in msg for msg in v)
    finally:
        rm._BEGINNER_METADATA.clear()
        rm._BEGINNER_METADATA.update(backup)


# ---------- content invariants ----------


def test_every_entry_has_display_and_beginner_name():
    for entry in list_beginner_registry():
        assert entry["display_name"].strip(), entry["strategy_id"]
        assert entry["beginner_name"].strip(), entry["strategy_id"]
        assert entry["description"].strip(), entry["strategy_id"]


def test_risk_level_uses_known_enum_values():
    valid = {m.value for m in RiskLevel}
    for entry in list_beginner_registry():
        assert entry["risk_level"] in valid


def test_recommended_mode_uses_known_enum_values():
    valid = {m.value for m in RecommendedMode}
    for entry in list_beginner_registry():
        assert entry["recommended_mode"] in valid


def test_internal_name_matches_actual_class():
    """internal_name 이 코드의 실제 클래스명과 동일."""
    expected = {
        "sma_crossover":    "SmaCrossoverStrategy",
        "rsi_reversion":    "RsiReversionStrategy",
        "vwap_strategy":    "VWAPStrategy",
        "orb_vwap":         "OrbVwapStrategy",
        "volume_breakout":  "VolumeBreakoutStrategy",
        "pullback_rebreak": "PullbackRebreakStrategy",
    }
    for entry in list_beginner_registry():
        assert entry["internal_name"] == expected[entry["strategy_id"]]


# ---------- safety invariants ----------


def test_live_trading_unavailable_for_all_strategies():
    """KIS live place_order(is_paper=False) 가 NotImplementedError 인 한 *모든*
    전략에 대해 live_trading_available=False 영구."""
    for entry in list_beginner_registry():
        assert entry["live_trading_available"] is False, entry["strategy_id"]


def test_backtest_and_paper_available_for_all():
    """현재 6개 전략 모두 backtest + paper 가능."""
    for entry in list_beginner_registry():
        assert entry["backtest_available"] is True
        assert entry["paper_trading_available"] is True


def test_supported_modes_does_not_include_live_ai_execution():
    """LIVE_AI_EXECUTION 은 본 메타에서 *영구 미허용* — #75 와 일관."""
    for entry in list_beginner_registry():
        assert "LIVE_AI_EXECUTION" not in entry["supported_modes"]


def test_invariant_flags_all_false():
    for entry in list_beginner_registry():
        assert entry["is_order_signal"] is False
        assert entry["auto_apply_allowed"] is False
        assert entry["is_investment_advice"] is False


def test_no_competitor_or_fake_strategy_names_present():
    """경쟁 앱 / 가짜 전략명 노출 0건. 본 테스트는 모든 entry 의 text 필드를
    스캔해 *코드에 없는* 전략명 패턴이 들어가지 않는지 확인.

    *영구 차단* 패턴 (예시): "골든브릿지" / "트라이앵글 전설" / "다이아 전략" /
    "퀀텀 점프" 등 외부 앱 색깔. 본 prefix 가 등장하면 정책 위반.
    """
    forbidden_substrings = [
        # 가짜 / 외부 앱식 자극적 표현 (영구 차단).
        "골든브릿지", "트라이앵글 전설", "다이아 전략", "퀀텀 점프",
        "황금알", "초신성", "월급쟁이 비밀", "100% 승률",
        # 영문 hype.
        "guaranteed", "magic strategy", "secret formula", "100% win",
    ]
    out = list_beginner_registry()
    for entry in out:
        # 모든 텍스트 필드 합쳐 스캔.
        text = " ".join([
            entry["display_name"], entry["beginner_name"],
            entry["description"], " ".join(entry.get("notes") or []),
        ]).lower()
        for needle in forbidden_substrings:
            assert needle.lower() not in text, (
                f"forbidden / fake strategy name in '{entry['strategy_id']}': {needle!r}"
            )


# ---------- public API helpers ----------


def test_beginner_metadata_returns_none_for_unknown_id():
    assert beginner_metadata("definitely_not_a_real_strategy") is None


def test_beginner_metadata_returns_known_id():
    meta = beginner_metadata("sma_crossover")
    assert meta is not None
    assert meta.display_name


# ---------- API ----------


def test_route_beginner_registry_returns_six(client):
    res = client.get("/api/strategies/beginner-registry")
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, list)
    assert len(body) == 6
    ids = {e["strategy_id"] for e in body}
    assert ids == _EXPECTED_IDS


def test_route_beginner_registry_invariant_flags(client):
    res = client.get("/api/strategies/beginner-registry")
    body = res.json()
    for entry in body:
        assert entry["is_order_signal"] is False
        assert entry["auto_apply_allowed"] is False
        assert entry["is_investment_advice"] is False
        assert entry["live_trading_available"] is False


def test_route_response_does_not_leak_secrets(client):
    res = client.get("/api/strategies/beginner-registry")
    text = res.text.lower()
    for needle in [
        "kis_app_key", "kis_app_secret", "anthropic_api_key",
        "telegram_bot_token", "sk-", "bearer ",
    ]:
        assert needle not in text


def test_existing_registry_endpoint_still_works(client):
    """기존 /api/strategies/registry 호환 유지 — 본 PR이 기존 endpoint 손상 0건."""
    res = client.get("/api/strategies/registry")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert len(body) == 6


# ---------- invariants — static grep guards ----------


_MODULE_PATH = Path("backend/app/strategies/registry_metadata.py")


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
        "route_order(",
        "OrderExecutor(",
        "submit_candidate(",
        "AiClient(",
    ]
    for needle in forbidden:
        assert needle not in src, f"forbidden call: {needle!r}"


def test_module_does_not_mutate_safety_flags_or_strategies():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "STRATEGY_REGISTRY[",                # *읽기만* — mutate 0건
        "STRATEGY_REGISTRY.pop(",
        "STRATEGY_REGISTRY.update(",
        ".save_params(",
        ".apply_params(",
        "strategy.enabled = False",
    ]
    for needle in forbidden:
        assert needle not in src, f"forbidden mutation: {needle!r}"


def test_module_does_not_write_to_db():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "db.add(", "db.commit(", "db.flush(", "db.delete(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    for needle in forbidden:
        assert needle not in src, f"writes to DB: {needle!r}"
