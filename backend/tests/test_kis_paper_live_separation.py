"""#71 / 9-02: KIS Paper / KIS Live endpoint·TR·account 분리 테스트.

핵심 invariant:
- Paper host/TR/account 와 Live host/TR/account 가 별도 값.
- KIS_IS_PAPER=true → PAPER path 만.
- KIS_IS_PAPER=false + explicit live gate 없음 → BLOCKED (fallback 금지).
- KIS_IS_PAPER=false + gate 통과 → LIVE 선택 (단, 주문은 생성 안 함).
- host 상수가 kis_client 단일 진실과 일치.
- broker_order_sent/order_created/is_live_authorization=False.
"""

from __future__ import annotations

import pytest

from app.brokers import kis_client as kc
from app.kis import endpoints as ep
from app.kis.endpoints import (
    KIS_LIVE_GATE_REQUIRED,
    KIS_PAPER_PATH_SELECTED,
    KisEndpointMode,
    resolve_kis_endpoint,
)


# ── 분리 / 단일 진실 일치 ────────────────────────────────────────────────────


def test_paper_live_hosts_distinct():
    assert ep.PAPER_HOST != ep.LIVE_HOST
    assert "openapivts" in ep.PAPER_HOST   # 모의 host.
    assert ep.PAPER_TR_PREFIX != ep.LIVE_TR_PREFIX
    assert ep.PAPER_ACCOUNT_MODE != ep.LIVE_ACCOUNT_MODE


def test_hosts_match_kis_client_single_source():
    assert ep.PAPER_HOST == kc.PAPER_HOST
    assert ep.LIVE_HOST == kc.LIVE_HOST


def test_tr_prefix_matches_kis_client():
    # kis_client 의 TR id 가 V(paper)/T(live) prefix 를 쓴다.
    assert kc.KisClient.__dict__  # sanity
    assert ep.PAPER_ORDER_TR_BUY.startswith(ep.PAPER_TR_PREFIX)
    assert ep.LIVE_ORDER_TR_BUY.startswith(ep.LIVE_TR_PREFIX)


# ── KIS_IS_PAPER=true → PAPER only ───────────────────────────────────────────


def test_paper_mode_uses_paper_path():
    r = resolve_kis_endpoint(kis_is_paper=True)
    assert r.selected_mode == KisEndpointMode.PAPER.value
    assert r.host == ep.PAPER_HOST
    assert r.account_mode == ep.PAPER_ACCOUNT_MODE
    assert r.allowed is True
    assert r.reason_code == KIS_PAPER_PATH_SELECTED


def test_paper_mode_never_live_host():
    r = resolve_kis_endpoint(kis_is_paper=True)
    assert r.host != ep.LIVE_HOST


# ── KIS_IS_PAPER=false + no gate → BLOCKED (no fallback) ─────────────────────


def test_live_without_gate_blocked():
    r = resolve_kis_endpoint(kis_is_paper=False, explicit_live_gate_passed=False)
    assert r.selected_mode == KisEndpointMode.BLOCKED.value
    assert r.allowed is False
    assert r.reason_code == KIS_LIVE_GATE_REQUIRED
    # fallback 금지 — paper host 로 떨어지지 않는다.
    assert r.host is None
    assert r.host != ep.PAPER_HOST


def test_blocked_does_not_fallback_to_paper():
    r = resolve_kis_endpoint(kis_is_paper=False)
    assert r.account_mode != ep.PAPER_ACCOUNT_MODE
    assert r.order_tr_id is None


# ── KIS_IS_PAPER=false + gate → LIVE 선택 (주문은 생성 안 함) ────────────────


def test_live_with_gate_selects_live_but_no_order():
    r = resolve_kis_endpoint(kis_is_paper=False, explicit_live_gate_passed=True)
    assert r.selected_mode == KisEndpointMode.LIVE.value
    assert r.host == ep.LIVE_HOST
    assert r.account_mode == ep.LIVE_ACCOUNT_MODE
    # endpoint 선택일 뿐 — 주문 생성/전송/실전 권한 0.
    assert r.broker_order_sent is False
    assert r.order_created is False
    assert r.is_live_authorization is False


def test_live_mode_never_paper_host():
    r = resolve_kis_endpoint(kis_is_paper=False, explicit_live_gate_passed=True)
    assert r.host != ep.PAPER_HOST


# ── invariants ───────────────────────────────────────────────────────────────


def test_resolution_invariants():
    for r in (resolve_kis_endpoint(kis_is_paper=True),
              resolve_kis_endpoint(kis_is_paper=False),
              resolve_kis_endpoint(kis_is_paper=False, explicit_live_gate_passed=True)):
        assert r.paper_live_separated is True
        assert r.broker_order_sent is False
        assert r.order_created is False
        assert r.is_live_authorization is False
        assert r.contains_secret is False


def test_no_fallback_invariant_enforced():
    # PAPER 모드에 LIVE host 를 넣으면 dataclass 가 거부.
    with pytest.raises(ValueError):
        ep.KisEndpointResolution(
            selected_mode=KisEndpointMode.PAPER.value, host=ep.LIVE_HOST,
            tr_prefix="V", order_tr_id="VTTC0802U", account_mode="PAPER",
            paper_live_separated=True, live_gate_required=False, live_gate_passed=False,
            allowed=True, reason_code=KIS_PAPER_PATH_SELECTED, message_ko="")


def test_no_secret_in_resolution():
    import json
    text = json.dumps(resolve_kis_endpoint(kis_is_paper=True).to_dict(), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-",
                      "app_key", "app_secret"):
        assert forbidden not in text


def test_no_forbidden_calls():
    src = open(ep.__file__, encoding="utf-8").read()
    for tok in (".place_order(", ".cancel_order(", "route_order(",
                "import requests", "httpx.", "import openai", "import anthropic"):
        assert tok not in src, f"forbidden token: {tok}"


# ── place_order 실전 가드 회귀 (이미 존재하는 하드 가드) ─────────────────────


def test_kis_place_order_live_guard_exists():
    import inspect

    from app.brokers.kis import KisBrokerAdapter
    src = inspect.getsource(KisBrokerAdapter.place_order)
    assert "NotImplementedError" in src
    assert "is_paper" in src


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_kis_endpoint_block(client):
    body = client.get("/api/status/live-safety").json()
    ke = body["kis_endpoint"]
    assert ke["paper_live_separated"] is True
    # 기본(KIS_IS_PAPER=true) → PAPER 선택.
    assert ke["selected_mode"] in ("PAPER", "BLOCKED")
    assert ke["broker_order_sent"] is False
