"""P-20: Paper capital ↔ Live capital 분리 — 문서 / 가드 / API 테스트.

검증:
 - docs/capital_allocation_policy.md 분리 섹션 + 핵심 문구
 - live_capital_guard: 비-live 모드 / live 모드 / paper payload 감지 / reason_code
 - LiveCapitalReviewResult invariants (live_capital_approved / is_live_authorization
   항상 False)
 - capital-config API safety flags
 - 정적 grep: guard 모듈 broker/route_order/OrderExecutor import 0건,
   안전 flag mutation 0건, paper capital 을 live notional 계산에 사용 0건
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.permission.live_capital_guard as guard
from app.core.modes import OperationMode
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.permission.live_capital_guard import (
    LIVE_CAPITAL_PERMISSION_DENIED,
    LIVE_CAPITAL_REVIEW_REQUIRED,
    LIVE_ORDER_NOTIONAL_NOT_CONFIGURED,
    PAPER_CAPITAL_NOT_LIVE_CAPITAL,
    PAPER_MODE_PAPER_CAPITAL_OK,
    LiveCapitalReviewResult,
    detect_paper_capital_in_payload,
    evaluate_live_capital_authorization,
    is_live_order_mode,
)

_DOC = Path(__file__).resolve().parents[2] / "docs" / "capital_allocation_policy.md"
_GUARD = Path(guard.__file__).resolve()


# ─────────────────────────────────────────────────────────────────────────────
# 1. 문서
# ─────────────────────────────────────────────────────────────────────────────


class TestDoc:
    def test_doc_exists(self):
        assert _DOC.exists()

    def test_separation_section_present(self):
        text = _DOC.read_text(encoding="utf-8")
        assert "Paper Capital vs Live Capital Separation" in text

    def test_doc_core_phrases(self):
        text = _DOC.read_text(encoding="utf-8")
        assert "Paper 자금 설정은 실전 주문 한도가 아닙니다" in text
        assert "Live 자금 검토" in text

    def test_doc_reason_codes(self):
        text = _DOC.read_text(encoding="utf-8")
        for code in (PAPER_CAPITAL_NOT_LIVE_CAPITAL, LIVE_CAPITAL_REVIEW_REQUIRED,
                     LIVE_ORDER_NOTIONAL_NOT_CONFIGURED, LIVE_CAPITAL_PERMISSION_DENIED):
            assert code in text


# ─────────────────────────────────────────────────────────────────────────────
# 2. detect + mode helper
# ─────────────────────────────────────────────────────────────────────────────


class TestDetectAndMode:
    def test_detect_paper_fields(self):
        assert detect_paper_capital_in_payload(
            {"total_paper_capital": 1, "symbol": "x"}) == ["total_paper_capital"]
        assert detect_paper_capital_in_payload(
            {"per_symbol_allocation": 1, "max_daily_buy_amount": 2}) == [
            "max_daily_buy_amount", "per_symbol_allocation"]

    def test_detect_clean_payload(self):
        assert detect_paper_capital_in_payload({"symbol": "005930", "qty": 3}) == []
        assert detect_paper_capital_in_payload(None) == []

    def test_is_live_order_mode(self):
        assert is_live_order_mode(OperationMode.LIVE_MANUAL_APPROVAL)
        assert is_live_order_mode(OperationMode.LIVE_AI_ASSIST)
        assert is_live_order_mode(OperationMode.LIVE_AI_EXECUTION)
        assert not is_live_order_mode(OperationMode.SIMULATION)
        assert not is_live_order_mode(OperationMode.PAPER)
        assert not is_live_order_mode(OperationMode.LIVE_SHADOW)
        assert not is_live_order_mode(None)


# ─────────────────────────────────────────────────────────────────────────────
# 3. evaluate_live_capital_authorization
# ─────────────────────────────────────────────────────────────────────────────


class TestEvaluate:
    def test_paper_mode_allows_paper_capital(self):
        r = evaluate_live_capital_authorization(
            mode=OperationMode.PAPER,
            order_payload={"total_paper_capital": 10_000_000})
        assert r.allowed is True
        assert r.reason_code == PAPER_MODE_PAPER_CAPITAL_OK
        assert r.paper_capital_ignored is False
        assert r.is_live_authorization is False

    def test_simulation_mode_ok(self):
        r = evaluate_live_capital_authorization(mode=OperationMode.SIMULATION)
        assert r.allowed is True
        assert r.reason_code == PAPER_MODE_PAPER_CAPITAL_OK

    def test_shadow_mode_not_live_order(self):
        r = evaluate_live_capital_authorization(mode=OperationMode.LIVE_SHADOW)
        assert r.reason_code == PAPER_MODE_PAPER_CAPITAL_OK
        assert r.paper_capital_ignored is False

    def test_live_mode_paper_payload_blocked(self):
        r = evaluate_live_capital_authorization(
            mode=OperationMode.LIVE_MANUAL_APPROVAL,
            order_payload={"per_symbol_allocation": 1_000_000})
        assert r.allowed is False
        assert r.reason_code == PAPER_CAPITAL_NOT_LIVE_CAPITAL
        assert r.paper_capital_ignored is True
        assert "per_symbol_allocation" in r.detected_paper_fields
        assert r.reason_message == "Paper 자금 설정은 실전 주문 한도가 아닙니다."

    def test_live_mode_clean_not_configured(self):
        r = evaluate_live_capital_authorization(
            mode=OperationMode.LIVE_AI_EXECUTION, order_payload={"symbol": "005930"})
        assert r.allowed is False
        assert r.reason_code == LIVE_ORDER_NOTIONAL_NOT_CONFIGURED

    def test_live_mode_clean_review_required(self):
        r = evaluate_live_capital_authorization(
            mode=OperationMode.LIVE_MANUAL_APPROVAL,
            order_payload={"symbol": "005930"},
            live_order_notional_configured=True)
        assert r.allowed is False
        assert r.reason_code == LIVE_CAPITAL_REVIEW_REQUIRED
        assert r.reason_message == "실전 주문에는 별도 Live 자금 검토가 필요합니다."

    def test_live_mode_blocked_even_if_approved_flag(self):
        # approved=True 입력이어도 결과 invariant 는 live_capital_approved=False.
        r = evaluate_live_capital_authorization(
            mode=OperationMode.LIVE_MANUAL_APPROVAL,
            order_payload={"symbol": "x"},
            live_order_notional_configured=True,
            live_capital_approved=True)
        assert r.live_capital_approved is False
        assert r.is_live_authorization is False
        # approved 입력 시 도달 불가 분기 — PERMISSION_DENIED (안전 측).
        assert r.reason_code == LIVE_CAPITAL_PERMISSION_DENIED

    def test_result_invariants_to_dict(self):
        r = evaluate_live_capital_authorization(
            mode=OperationMode.LIVE_AI_EXECUTION,
            order_payload={"total_paper_capital": 1})
        d = r.to_dict()
        assert d["live_capital_approved"] is False
        assert d["is_live_authorization"] is False
        assert d["is_order_signal"] is False
        assert d["paper_capital_ignored"] is True

    def test_cannot_construct_approved_result(self):
        with pytest.raises(ValueError):
            LiveCapitalReviewResult(
                allowed=True, reason_code="x", reason_message="y",
                mode="LIVE_MANUAL_APPROVAL", paper_capital_ignored=True,
                live_capital_review_status="APPROVED",
                live_capital_approved=True)

    def test_cannot_construct_live_authorization(self):
        with pytest.raises(ValueError):
            LiveCapitalReviewResult(
                allowed=True, reason_code="x", reason_message="y",
                mode="LIVE_MANUAL_APPROVAL", paper_capital_ignored=True,
                live_capital_review_status="APPROVED",
                is_live_authorization=True)


# ─────────────────────────────────────────────────────────────────────────────
# 4. capital-config API safety flags
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                      expire_on_commit=False)

    def odb():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = odb
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


class TestCapitalConfigApi:
    def test_safety_flags_present(self, api_client):
        b = api_client.get("/api/auto-paper/capital-config").json()
        assert b["is_paper_capital"] is True
        assert b["is_live_capital"] is False
        assert b["is_live_authorization"] is False
        assert b["is_order_signal"] is False
        assert b["paper_capital_not_live_capital"] is True
        assert b["warning_message"] == "Paper 자금 설정은 실전 주문 한도가 아닙니다."

    def test_notice_mentions_live_review(self, api_client):
        b = api_client.get("/api/auto-paper/capital-config").json()
        assert "Live 자금 검토" in b["notice"]

    def test_no_secret_fields(self, api_client):
        raw = api_client.get("/api/auto-paper/capital-config").text.lower()
        for banned in ("api_key", "app_secret", "account_no", "access_token"):
            assert banned not in raw


# ─────────────────────────────────────────────────────────────────────────────
# 5. static guards
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_guard_module_no_broker_executor_import(self):
        text = _GUARD.read_text(encoding="utf-8")
        for pat in (
            r"from app\.brokers", r"import app\.brokers",
            r"from app\.execution", r"route_order\s*\(",
            r"OrderExecutor\s*\(", r"\bbroker\.place_order\s*\(",
            r"import httpx", r"import requests", r"import anthropic", r"import openai",
        ):
            assert not re.search(pat, text), f"live_capital_guard.py 금지 패턴: /{pat}/"

    def test_guard_module_no_safety_flag_mutation(self):
        text = _GUARD.read_text(encoding="utf-8")
        for pat in (
            r"enable_live_trading\s*=", r"enable_ai_execution\s*=",
            r"enable_futures_live_trading\s*=",
        ):
            assert not re.search(pat, text), (
                f"live_capital_guard.py 안전 flag mutation 의심: /{pat}/"
            )

    def test_guard_does_not_compute_live_notional_from_paper(self):
        """paper capital 필드를 곱/연산해 live notional 을 만드는 코드 0건.

        guard 는 paper 필드를 *감지(detect)* 만 하며 값을 *사용* 하지 않는다 —
        detected 는 키 *이름* list 일 뿐 값 연산 0건.
        """
        text = _GUARD.read_text(encoding="utf-8")
        # paper capital 값으로 notional/quantity 를 산출하는 패턴 금지.
        for pat in (
            r"total_paper_capital\s*[*/]",
            r"per_symbol_allocation\s*[*/]",
            r"max_daily_buy_amount\s*[*/]",
            r"live_notional\s*=\s*.*paper",
        ):
            assert not re.search(pat, text), (
                f"paper capital 을 live notional 계산에 사용 의심: /{pat}/"
            )

    def test_permission_gate_does_not_read_paper_capital(self):
        """PermissionGate.approve 가 paper capital 설정을 읽지 않음 (분리 lock)."""
        gate_path = (Path(__file__).resolve().parents[1]
                     / "app" / "permission" / "gate.py")
        text = gate_path.read_text(encoding="utf-8")
        for pat in (
            r"total_paper_capital", r"per_symbol_allocation",
            r"get_paper_capital_config", r"paper_capital_settings",
        ):
            assert not re.search(pat, text), (
                f"PermissionGate 가 paper capital 참조 의심: /{pat}/"
            )
