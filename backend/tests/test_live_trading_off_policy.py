"""#70 / 9-01: 실매매 기본 OFF 정책 테스트.

핵심 invariant:
- config default: ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION /
  ENABLE_FUTURES_LIVE_TRADING = False, KIS_IS_PAPER = True.
- .env.example 의 live flag default false / KIS_IS_PAPER true.
- 정책 결과: live_path_gated=True, is_live_authorization/broker_order_sent/
  order_created = False, 필수 reason_code 포함.
- 문서 안전성 (수익 보장 / 실전 전환 승인 문구 없음).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.permission import live_trading_off_policy as lop
from app.permission.live_trading_off_policy import (
    LIVE_NOT_AVAILABLE_IN_DEFAULT_EXE,
    LIVE_ORDER_BLOCKED_BY_DEFAULT,
    LIVE_PATH_GATED,
    LIVE_REQUIRES_EXPLICIT_APPROVAL,
    LIVE_TRADING_DISABLED_BY_DEFAULT,
    evaluate_live_off_policy,
)

_REPO = Path(__file__).resolve().parents[2]
_ENV_EXAMPLE = _REPO / "backend" / ".env.example"
_DOC = _REPO / "docs" / "live_trading_off_policy.md"


# ── 1~4. config defaults ─────────────────────────────────────────────────────


def test_config_default_live_off():
    from app.core.config import Settings
    s = Settings()
    assert s.enable_live_trading is False
    assert s.enable_ai_execution is False
    assert s.enable_futures_live_trading is False
    assert s.kis_is_paper is True


# ── 5. .env.example defaults ─────────────────────────────────────────────────


def _env_pairs() -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        pairs[k.strip()] = v.strip()
    return pairs


def test_env_example_live_flags_false():
    p = _env_pairs()
    assert p.get("ENABLE_LIVE_TRADING") == "false"
    assert p.get("ENABLE_AI_EXECUTION") == "false"
    assert p.get("ENABLE_FUTURES_LIVE_TRADING") == "false"


def test_env_example_kis_is_paper_true():
    assert _env_pairs().get("KIS_IS_PAPER") == "true"


# ── 6~8. 정책 결과 invariants ────────────────────────────────────────────────


def test_policy_default_gated_and_blocked():
    r = evaluate_live_off_policy()
    assert r.live_path_gated is True
    assert r.live_order_blocked is True
    assert r.reason_code == LIVE_ORDER_BLOCKED_BY_DEFAULT
    assert r.is_live_authorization is False
    assert r.broker_order_sent is False
    assert r.order_created is False


def test_policy_reason_codes_present():
    r = evaluate_live_off_policy()
    for code in (LIVE_ORDER_BLOCKED_BY_DEFAULT, LIVE_PATH_GATED,
                 LIVE_REQUIRES_EXPLICIT_APPROVAL, LIVE_TRADING_DISABLED_BY_DEFAULT,
                 LIVE_NOT_AVAILABLE_IN_DEFAULT_EXE):
        assert code in r.reason_codes


def test_policy_gated_even_if_live_flag_true():
    # 어떤 flag 하나만으로도 실전 허용 안 됨 — 여전히 gated/blocked.
    r = evaluate_live_off_policy(enable_live_trading=True, kis_is_paper=False)
    assert r.live_path_gated is True
    assert r.is_live_authorization is False
    assert r.live_order_blocked is True


def test_policy_invariant_enforced():
    with pytest.raises(ValueError):
        lop.LiveOffPolicyResult(
            enable_live_trading=False, enable_ai_execution=False,
            enable_futures_live_trading=False, kis_is_paper=True,
            default_mode="SIMULATION", live_path_gated=True, live_order_blocked=True,
            reason_code=LIVE_ORDER_BLOCKED_BY_DEFAULT, reason_codes=(), message_ko="",
            is_live_authorization=True)


def test_policy_gated_must_be_true():
    with pytest.raises(ValueError):
        lop.LiveOffPolicyResult(
            enable_live_trading=False, enable_ai_execution=False,
            enable_futures_live_trading=False, kis_is_paper=True,
            default_mode="SIMULATION", live_path_gated=False, live_order_blocked=True,
            reason_code=LIVE_ORDER_BLOCKED_BY_DEFAULT, reason_codes=(), message_ko="")


# ── 9~10. 문서 안전성 ────────────────────────────────────────────────────────


def test_doc_exists_and_safe():
    assert _DOC.exists(), "docs/live_trading_off_policy.md must exist"
    txt = _DOC.read_text(encoding="utf-8")
    assert "실전매매" in txt and "기본 OFF" in txt
    for forbidden in ("수익 보장", "실전 전환 승인", "지금 매수", "지금 매도", "LIVE ON"):
        assert forbidden not in txt, f"forbidden phrase in doc: {forbidden}"


def test_no_secret_in_policy_dict():
    import json
    text = json.dumps(evaluate_live_off_policy().to_dict(), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-"):
        assert forbidden not in text


# ── import 가드 ──────────────────────────────────────────────────────────────


def test_no_forbidden_imports():
    src = open(lop.__file__, encoding="utf-8").read()
    for tok in ("from app.brokers", "from app.execution.order_router",
                "from app.execution.executor", "from app.execution.paper_trader",
                "import anthropic", "import openai", "import httpx", "import requests",
                ".place_order(", "route_order(",
                "from app.core.config import get_settings"):
        assert tok not in src, f"forbidden token: {tok}"


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_live_safety_status(client):
    r = client.get("/api/status/live-safety")
    assert r.status_code == 200
    body = r.json()
    assert body["is_live_authorization"] is False
    lp = body["live_policy"]
    assert lp["live_path_gated"] is True
    assert lp["enable_live_trading"] is False
    assert lp["is_live_authorization"] is False
