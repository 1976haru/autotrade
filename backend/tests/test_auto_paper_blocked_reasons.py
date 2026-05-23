"""P-17: 매수 불가 사유 집계 — module + API 테스트.

검증:
 - normalize_reason_code (alias / unknown)
 - extract_block_record (REJECTED / FILLED 제외 / HOLD 제외)
 - summarize_blocked_reasons (total / by_reason / recent / last_block)
 - 정규 reason_code 별 KO title
 - secret detail 미노출 (allowlist)
 - API: read-only, total_blocked / by_reason, invariants, broker 호출 0건
 - 정적 grep: 모듈 broker / OrderExecutor / route_order import 0건, DB write 0건
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.auto_paper.blocked_reasons as br
from app.auto_paper.blocked_reasons import (
    BUY_BLOCK_REASON_TITLES_KO,
    extract_block_record,
    is_block_code,
    normalize_reason_code,
    summarize_blocked_reasons,
    title_for,
)
from app.auto_paper.events import DecisionAction, PaperFillStatus
from app.auto_paper.ledger import record_paper_event, reset_ledger_for_tests
from app.db.base import Base
from app.db.session import get_db
from app.main import app


_MODULE_PATH = Path(br.__file__).resolve()


# ─────────────────────────────────────────────────────────────────────────────
# normalize / title
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalize:
    @pytest.mark.parametrize("raw,expected", [
        ("INSUFFICIENT_CASH", "INSUFFICIENT_PAPER_CASH"),
        ("PAPER_GUARD_DUPLICATE", "DUPLICATE_POSITION_BUY_BLOCKED"),
        ("PAPER_GUARD_DAILY_LIMIT", "DAILY_BUY_LIMIT_EXCEEDED"),
        ("PAPER_GUARD_SYMBOL_WEIGHT", "SYMBOL_WEIGHT_LIMIT_EXCEEDED"),
        ("PRICE_OVER_CAP", "MIN_LOT_NOT_AFFORDABLE"),
        ("PRICE_MISSING", "NO_MARKET_DATA"),
        ("STALE_DATA", "PRICE_STALE"),
        ("UNKNOWN_ERROR", "UNKNOWN"),
        ("min_lot_not_affordable", "MIN_LOT_NOT_AFFORDABLE"),
    ])
    def test_alias_and_case(self, raw, expected):
        assert normalize_reason_code(raw) == expected

    def test_unknown_and_none(self):
        assert normalize_reason_code("WHATEVER") == "UNKNOWN"
        assert normalize_reason_code(None) == "UNKNOWN"
        assert normalize_reason_code("") == "UNKNOWN"

    def test_required_titles_present(self):
        for code in [
            "MIN_LOT_NOT_AFFORDABLE", "INSUFFICIENT_PAPER_CASH",
            "DAILY_BUY_LIMIT_EXCEEDED", "SYMBOL_WEIGHT_LIMIT_EXCEEDED",
            "DUPLICATE_POSITION_BUY_BLOCKED", "PRICE_STALE", "INVALID_PRICE",
            "ABNORMAL_PRICE_MOVE", "PAPER_EXECUTION_DISABLED",
            "BLOCKED_BY_PERMISSION_GATE", "BLOCKED_BY_RISK_MANAGER",
            "MARKET_CLOSED", "NO_MARKET_DATA", "NO_STRATEGY_SIGNAL", "NO_CANDIDATE",
        ]:
            assert code in BUY_BLOCK_REASON_TITLES_KO
            assert title_for(code)

    def test_specific_titles(self):
        assert title_for("MIN_LOT_NOT_AFFORDABLE") == "1주 가격이 투자한도 초과로 제외"
        assert title_for("INSUFFICIENT_PAPER_CASH") == "남은 Paper 현금이 부족하여 매수 차단"
        assert title_for("DUPLICATE_POSITION_BUY_BLOCKED") == "이미 보유 중인 종목이라 추가 매수 차단"

    def test_is_block_code(self):
        assert is_block_code("INSUFFICIENT_PAPER_CASH")
        assert not is_block_code("OK")
        assert not is_block_code("VIRTUAL_ORDER_CANDIDATE_CREATED")


# ─────────────────────────────────────────────────────────────────────────────
# extract / summarize
# ─────────────────────────────────────────────────────────────────────────────


def _ev(symbol, action, fill, *, flags=None, reason="", meta=None, ts="2026-05-23T01:00:00+00:00"):
    return {
        "symbol": symbol, "strategy": "sma", "timestamp": ts,
        "decision_action": action, "paper_fill_status": fill,
        "risk_flags": flags or [], "reason": reason, "metadata": meta or {},
    }


class TestExtract:
    def test_rejected_buy_with_flag(self):
        rec = extract_block_record(
            _ev("005930", "BUY", "PAPER_REJECTED", flags=["INSUFFICIENT_PAPER_CASH"]))
        assert rec["reason_code"] == "INSUFFICIENT_PAPER_CASH"
        assert rec["title"] == "남은 Paper 현금이 부족하여 매수 차단"
        assert rec["category"] == "capital"
        assert rec["symbol"] == "005930"

    def test_filled_buy_not_block(self):
        assert extract_block_record(_ev("X", "BUY", "PAPER_FILLED")) is None

    def test_plain_hold_not_block(self):
        assert extract_block_record(_ev("X", "HOLD", "NA", reason="ok")) is None

    def test_rejected_without_code_is_unknown(self):
        rec = extract_block_record(_ev("Y", "BUY", "PAPER_REJECTED"))
        assert rec is not None
        assert rec["reason_code"] == "UNKNOWN"

    def test_reason_text_paper_flow_pattern(self):
        rec = extract_block_record(_ev(
            "Z", "BUY", "PAPER_REJECTED",
            reason="[paper-flow] DAILY_BUY_LIMIT_EXCEEDED: over"))
        assert rec["reason_code"] == "DAILY_BUY_LIMIT_EXCEEDED"

    def test_secret_in_metadata_not_leaked(self):
        rec = extract_block_record(_ev(
            "S", "BUY", "PAPER_REJECTED", flags=["INSUFFICIENT_PAPER_CASH"],
            meta={"app_secret": "sk-leak", "required_amount": 500000}))
        assert "app_secret" not in rec["detail"]
        assert rec["detail"]["required_amount"] == 500000


class TestSummarize:
    def test_counts_and_recent(self):
        events = [
            _ev("A", "BUY", "PAPER_REJECTED", flags=["INSUFFICIENT_PAPER_CASH"],
                ts="2026-05-23T01:00:00+00:00"),
            _ev("B", "BUY", "PAPER_REJECTED", flags=["MIN_LOT_NOT_AFFORDABLE"],
                ts="2026-05-23T01:05:00+00:00"),
            _ev("C", "HOLD", "NA", reason="ok", ts="2026-05-23T01:06:00+00:00"),
            _ev("D", "BUY", "PAPER_FILLED", ts="2026-05-23T01:07:00+00:00"),
        ]
        s = summarize_blocked_reasons(events).to_dict()
        assert s["total_blocked"] == 2
        assert s["by_reason"]["INSUFFICIENT_PAPER_CASH"] == 1
        assert s["by_reason"]["MIN_LOT_NOT_AFFORDABLE"] == 1
        # recent 는 최신순 — 가장 최근 차단(B) 이 먼저.
        assert s["recent"][0]["symbol"] == "B"
        assert s["last_block"]["symbol"] == "B"

    def test_empty(self):
        s = summarize_blocked_reasons([]).to_dict()
        assert s["total_blocked"] == 0
        assert s["by_reason"] == {}
        assert s["recent"] == []
        assert s["last_block"] is None

    def test_invariants(self):
        s = summarize_blocked_reasons([_ev("A", "BUY", "PAPER_REJECTED",
                                           flags=["INVALID_PRICE"])])
        d = s.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    reset_ledger_for_tests()
    test_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False,
    )

    def _override_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)
        reset_ledger_for_tests()


def _seed_blocked():
    # RUNNING 에서 BUY + PAPER_REJECTED 로 차단 기록 (record() state-aware 허용).
    for code, sym in [
        ("MIN_LOT_NOT_AFFORDABLE", "373220"),
        ("INSUFFICIENT_PAPER_CASH", "005930"),
        ("INSUFFICIENT_PAPER_CASH", "000660"),
        ("DAILY_BUY_LIMIT_EXCEEDED", "035720"),
        ("SYMBOL_WEIGHT_LIMIT_EXCEEDED", "051910"),
        ("PRICE_STALE", "207940"),
        ("ABNORMAL_PRICE_MOVE", "068270"),
    ]:
        record_paper_event(
            loop_state="RUNNING", strategy="sma", symbol=sym,
            decision_action=DecisionAction.BUY,
            reason=f"[paper-flow] {code}: blocked",
            risk_flags=[code], paper_fill_status=PaperFillStatus.PAPER_REJECTED,
        )


class TestApi:
    def test_empty_summary_when_no_events(self, api_client):
        r = api_client.get("/api/auto-paper/blocked-reasons/today")
        assert r.status_code == 200
        b = r.json()
        assert b["total_blocked"] == 0
        assert b["by_reason"] == {}
        assert b["is_order_signal"] is False
        assert b["is_live_authorization"] is False
        assert b["auto_apply_allowed"] is False

    def test_summary_after_seed(self, api_client):
        _seed_blocked()
        r = api_client.get("/api/auto-paper/blocked-reasons/today?limit=10")
        assert r.status_code == 200
        b = r.json()
        assert b["total_blocked"] == 7
        assert b["by_reason"]["INSUFFICIENT_PAPER_CASH"] == 2
        assert b["by_reason"]["MIN_LOT_NOT_AFFORDABLE"] == 1
        # 다양한 reason_code 가 포함됐는지.
        for code in ("DAILY_BUY_LIMIT_EXCEEDED", "SYMBOL_WEIGHT_LIMIT_EXCEEDED",
                     "PRICE_STALE", "ABNORMAL_PRICE_MOVE"):
            assert code in b["by_reason"]
        assert b["last_block"]["reason_code"] in BUY_BLOCK_REASON_TITLES_KO

    def test_response_has_no_secret_fields(self, api_client):
        _seed_blocked()
        raw = api_client.get("/api/auto-paper/blocked-reasons/today").text.lower()
        for banned in ("app_secret", "api_key", "account_no", "access_token"):
            assert banned not in raw

    def test_disclaimer_and_scope(self, api_client):
        r = api_client.get("/api/auto-paper/blocked-reasons/today")
        b = r.json()
        assert "표시 전용" in b["advisory_disclaimer"]
        assert b["scope"] == "today"


# ─────────────────────────────────────────────────────────────────────────────
# static guards
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_module_no_broker_or_executor_import(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in (
            r"from app\.brokers", r"import app\.brokers",
            r"from app\.execution", r"route_order\s*\(",
            r"OrderExecutor\s*\(", r"\bbroker\.place_order\s*\(",
            r"import httpx", r"import requests", r"import anthropic", r"import openai",
        ):
            assert not re.search(pat, text), f"blocked_reasons.py 금지 패턴: /{pat}/"

    def test_module_no_db_write(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in (r"\.add\(", r"\.commit\(", r"db\.delete\(", r"session\.add\("):
            assert not re.search(pat, text), f"blocked_reasons.py DB write 의심: /{pat}/"
