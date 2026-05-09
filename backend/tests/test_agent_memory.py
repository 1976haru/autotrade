"""Agent Memory tests.

본 메모리는 *주문 신호가 아니다*. API key / Secret / 계좌번호 / 개인정보
저장 차단 + 검색 / archive / ingest helper 검증.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.agent_memory import (
    MemoryRecord,
    MemorySearchFilter,
    MemorySeverity,
    MemoryType,
    MemoryWriteRequest,
    SecretLeakError,
    SourceKind,
    archive_memory,
    get_memory,
    memory_from_daily_report_markdown,
    memory_from_risk_audit_report,
    memory_from_strategy_research_report,
    sanitize_dict,
    sanitize_text,
    save_memory,
    search_memory,
)
from app.db.models import AgentMemory


_AGENT_PATH = Path(__file__).resolve().parents[1] / "app" / "agents" / "agent_memory.py"
_ROUTES_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "api" / "routes_agent_memory.py"
)


# ====================================================================
# Sanitization — 절대 원칙: 민감정보 저장 차단
# ====================================================================


class TestSanitization:
    def test_sanitize_passes_clean_text(self):
        assert sanitize_text("정상 텍스트입니다") == "정상 텍스트입니다"

    def test_sanitize_blocks_anthropic_key(self):
        with pytest.raises(SecretLeakError, match="api_key_long|anthropic_key"):
            sanitize_text("our key sk-ant-abc123def456ghi789jkl0")

    def test_sanitize_blocks_openai_key(self):
        with pytest.raises(SecretLeakError):
            sanitize_text("sk-abcdefghij1234567890ABCDEFGHIJKLMNOP12")

    def test_sanitize_blocks_kr_account_number(self):
        with pytest.raises(SecretLeakError, match="kr_account|credit_card"):
            sanitize_text("계좌 555-12345-67 입니다")

    def test_sanitize_blocks_kr_account_long(self):
        with pytest.raises(SecretLeakError):
            sanitize_text("계좌번호 12345678901234")

    def test_sanitize_blocks_resident_number(self):
        with pytest.raises(SecretLeakError, match="kr_resident|credit_card"):
            sanitize_text("주민번호 901020-1234567")

    def test_sanitize_blocks_kis_app_key_label(self):
        with pytest.raises(SecretLeakError, match="kis_app_key|api_key_long"):
            sanitize_text("app_key=PSdwjkl1234567890ABCDEF")

    def test_sanitize_blocks_jwt(self):
        with pytest.raises(SecretLeakError, match="jwt"):
            sanitize_text("token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                          "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
                          "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")

    def test_sanitize_blocks_email(self):
        with pytest.raises(SecretLeakError, match="email"):
            sanitize_text("contact me at user@example.com")

    def test_sanitize_blocks_phone(self):
        with pytest.raises(SecretLeakError, match="kr_phone"):
            sanitize_text("phone 010-1234-5678")

    def test_sanitize_dict_recursively(self):
        with pytest.raises(SecretLeakError):
            sanitize_dict({
                "outer": "ok",
                "nested": {"inner": "sk-ant-abc123def456ghi789jkl0"},
            })


# ====================================================================
# DB CRUD via save / search / archive
# ====================================================================


class TestSaveAndSearch:
    def test_save_basic_memory(self, client):
        db = client.test_db_factory()
        try:
            req = MemoryWriteRequest(
                memory_type=MemoryType.OPERATOR_NOTE,
                title="삼성전자 변동성 메모",
                summary="장초반 30분 변동성이 큰 날 진입을 주의.",
                strategy="sma_cross",
                symbol="005930",
                tags=("variability", "open-30m"),
            )
            rec = save_memory(db, req)
            assert rec.id is not None
            assert rec.memory_type == MemoryType.OPERATOR_NOTE
            assert rec.is_order_signal is False
            assert "variability" in rec.tags
        finally:
            db.close()

    def test_save_blocks_secret_in_summary(self, client):
        db = client.test_db_factory()
        try:
            req = MemoryWriteRequest(
                memory_type=MemoryType.OPERATOR_NOTE,
                title="ok",
                summary="my key is sk-ant-abc123def456ghi789jkl0",
            )
            with pytest.raises(SecretLeakError):
                save_memory(db, req)
        finally:
            db.close()

    def test_save_blocks_secret_in_meta(self, client):
        db = client.test_db_factory()
        try:
            req = MemoryWriteRequest(
                memory_type=MemoryType.OPERATOR_NOTE,
                title="ok", summary="ok",
                meta={"raw_quote": "phone 010-1234-5678"},
            )
            with pytest.raises(SecretLeakError):
                save_memory(db, req)
        finally:
            db.close()

    def test_search_by_memory_type(self, client):
        db = client.test_db_factory()
        try:
            save_memory(db, MemoryWriteRequest(
                memory_type=MemoryType.OPERATOR_NOTE,
                title="A", summary="A summary",
            ))
            save_memory(db, MemoryWriteRequest(
                memory_type=MemoryType.RISK_INCIDENT,
                title="B", summary="B summary",
            ))
            rows = search_memory(db, MemorySearchFilter(
                memory_type=MemoryType.RISK_INCIDENT,
            ))
            assert len(rows) == 1
            assert rows[0].title == "B"
        finally:
            db.close()

    def test_search_by_strategy(self, client):
        db = client.test_db_factory()
        try:
            save_memory(db, MemoryWriteRequest(
                memory_type=MemoryType.STRATEGY_RESEARCH,
                title="vwap memo", summary="VWAP loss pattern",
                strategy="vwap",
            ))
            save_memory(db, MemoryWriteRequest(
                memory_type=MemoryType.STRATEGY_RESEARCH,
                title="sma memo", summary="SMA loss pattern",
                strategy="sma_cross",
            ))
            rows = search_memory(db, MemorySearchFilter(strategy="vwap"))
            assert len(rows) == 1
            assert rows[0].strategy == "vwap"
        finally:
            db.close()

    def test_search_by_keyword_in_summary(self, client):
        db = client.test_db_factory()
        try:
            save_memory(db, MemoryWriteRequest(
                memory_type=MemoryType.LOSS_POST_MORTEM,
                title="A", summary="VWAP 전략에서 데이터 지연으로 손실",
            ))
            save_memory(db, MemoryWriteRequest(
                memory_type=MemoryType.LOSS_POST_MORTEM,
                title="B", summary="긴급정지 토글 사례",
            ))
            rows = search_memory(db, MemorySearchFilter(keyword="VWAP"))
            assert len(rows) == 1
        finally:
            db.close()

    def test_search_by_tag_in_memory(self, client):
        db = client.test_db_factory()
        try:
            save_memory(db, MemoryWriteRequest(
                memory_type=MemoryType.LESSON_LEARNED,
                title="A", summary="A",
                tags=("data_stale", "vwap"),
            ))
            save_memory(db, MemoryWriteRequest(
                memory_type=MemoryType.LESSON_LEARNED,
                title="B", summary="B",
                tags=("ai_overconfidence",),
            ))
            rows = search_memory(db, MemorySearchFilter(tag="vwap"))
            assert len(rows) == 1
            assert rows[0].title == "A"
        finally:
            db.close()

    def test_search_excludes_archived_by_default(self, client):
        db = client.test_db_factory()
        try:
            r = save_memory(db, MemoryWriteRequest(
                memory_type=MemoryType.OPERATOR_NOTE,
                title="A", summary="A",
            ))
            archive_memory(db, r.id, archived=True)
            rows_default = search_memory(db, MemorySearchFilter())
            assert len(rows_default) == 0
            rows_with = search_memory(db, MemorySearchFilter(include_archived=True))
            assert len(rows_with) == 1
            assert rows_with[0].archived is True
        finally:
            db.close()

    def test_get_memory_returns_record(self, client):
        db = client.test_db_factory()
        try:
            saved = save_memory(db, MemoryWriteRequest(
                memory_type=MemoryType.OPERATOR_NOTE,
                title="lookup", summary="lookup test",
            ))
            got = get_memory(db, saved.id)
            assert got is not None
            assert got.title == "lookup"
            assert get_memory(db, 9999) is None
        finally:
            db.close()

    def test_archive_toggle(self, client):
        db = client.test_db_factory()
        try:
            r = save_memory(db, MemoryWriteRequest(
                memory_type=MemoryType.OPERATOR_NOTE,
                title="x", summary="x",
            ))
            assert r.archived is False
            r2 = archive_memory(db, r.id, archived=True)
            assert r2.archived is True
            r3 = archive_memory(db, r.id, archived=False)
            assert r3.archived is False
        finally:
            db.close()


# ====================================================================
# Ingest helpers
# ====================================================================


class TestIngestHelpers:
    def test_memory_from_daily_report(self):
        req = memory_from_daily_report_markdown(
            report_date="2026-05-09",
            markdown="# Daily System Report — 2026-05-09\n\n자동매매 시스템 자료.",
            findings_count=2, warnings_count=1,
        )
        assert req.memory_type == MemoryType.DAILY_REPORT
        assert req.source_kind == SourceKind.DAILY_REPORT
        assert "2026-05-09" in req.title
        assert req.severity == MemorySeverity.WARN

    def test_memory_from_strategy_research(self):
        req = memory_from_strategy_research_report(
            strategy="sma_cross", run_id=42,
            audit_level="WARNING",
            summary="PF 1.10 — 임계 미달.",
            findings_count=3, suggestions_count=2,
        )
        assert req.memory_type == MemoryType.STRATEGY_RESEARCH
        assert req.source_id == 42
        assert req.strategy == "sma_cross"
        assert req.severity == MemorySeverity.HIGH

    def test_memory_from_risk_audit_critical(self):
        req = memory_from_risk_audit_report(
            audit_level="RED", risk_score=85,
            summary="긴급정지 권고.",
            pause_recommended=True, stop_recommended=True,
            events_count=4,
        )
        assert req.memory_type == MemoryType.RISK_INCIDENT
        assert req.severity == MemorySeverity.CRITICAL
        assert req.next_action and "EMERGENCY_STOP" in req.next_action

    def test_ingest_helper_sanitizes_in_save(self, client):
        db = client.test_db_factory()
        try:
            req = memory_from_daily_report_markdown(
                report_date="2026-05-09",
                markdown="report contains email user@example.com",
            )
            with pytest.raises(SecretLeakError):
                save_memory(db, req)
        finally:
            db.close()


# ====================================================================
# MemoryRecord invariants
# ====================================================================


class TestMemoryRecordInvariants:
    def test_record_rejects_is_order_signal_true(self):
        from datetime import datetime
        with pytest.raises(ValueError, match="is_order_signal"):
            MemoryRecord(
                id=1, created_at=datetime.now(), updated_at=datetime.now(),
                memory_type=MemoryType.OPERATOR_NOTE,
                source_kind=None, source_id=None,
                strategy=None, symbol=None, mode=None,
                severity=MemorySeverity.INFO,
                title="x", summary="x", lessons=None, next_action=None,
                tags=(), meta={}, author=None, archived=False,
                is_order_signal=True,    # ← invariant 위반
            )


# ====================================================================
# API endpoints
# ====================================================================


class TestAPI:
    def test_search_returns_empty_for_fresh_db(self, client):
        res = client.get("/api/agents/memory/search")
        assert res.status_code == 200
        body = res.json()
        assert body["items"] == []
        assert "주문 신호" in body["notice"]

    def test_create_via_api_then_search(self, client):
        res = client.post("/api/agents/memory", json={
            "memory_type": "operator_note",
            "title": "운영자 메모", "summary": "장초반 변동성 주의",
            "tags": ["operator", "open"],
        })
        assert res.status_code == 200
        body = res.json()
        assert body["is_order_signal"] is False
        new_id = body["id"]

        res = client.get("/api/agents/memory/search?keyword=변동성")
        assert res.status_code == 200
        items = res.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == new_id

    def test_create_blocks_secret(self, client):
        res = client.post("/api/agents/memory", json={
            "title": "leak",
            "summary": "key sk-ant-abc123def456ghi789jkl0 leaked",
        })
        assert res.status_code == 400
        body = res.json()
        assert body["detail"]["error"] == "secret_leak_blocked"

    def test_get_404_for_unknown(self, client):
        res = client.get("/api/agents/memory/9999")
        assert res.status_code == 404

    def test_archive_endpoint(self, client):
        created = client.post("/api/agents/memory", json={
            "title": "x", "summary": "x",
        }).json()
        res = client.post(
            f"/api/agents/memory/{created['id']}/archive",
            json={"archived": True},
        )
        assert res.status_code == 200
        assert res.json()["archived"] is True

    def test_from_daily_report_endpoint(self, client):
        res = client.post("/api/agents/memory/from-daily-report", json={
            "report_date": "2026-05-09",
            "markdown":     "# Daily System Report — 2026-05-09\n\n시스템 자료.",
            "findings_count": 1, "warnings_count": 0,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["memory_type"] == "daily_report"
        assert "2026-05-09" in body["title"]

    def test_from_strategy_research_endpoint(self, client):
        res = client.post("/api/agents/memory/from-strategy-research", json={
            "strategy": "sma_cross", "run_id": 1,
            "audit_level": "CRITICAL",
            "summary": "PF 0.7 — 손실 우세.",
        })
        assert res.status_code == 200
        body = res.json()
        assert body["memory_type"] == "strategy_research"
        assert body["severity"] == "CRITICAL"

    def test_from_risk_audit_endpoint(self, client):
        res = client.post("/api/agents/memory/from-risk-audit", json={
            "audit_level": "RED", "risk_score": 90,
            "summary": "긴급 권고",
            "pause_recommended": True, "stop_recommended": True,
            "events_count": 4,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["memory_type"] == "risk_incident"
        assert body["severity"] == "CRITICAL"

    def test_from_daily_report_blocks_secret(self, client):
        res = client.post("/api/agents/memory/from-daily-report", json={
            "report_date": "2026-05-09",
            "markdown": "user@example.com leaked",
        })
        assert res.status_code == 400


# ====================================================================
# Static module guards
# ====================================================================


class TestStaticGuards:
    def _agent_src(self) -> str:
        return _AGENT_PATH.read_text(encoding="utf-8")

    def _routes_src(self) -> str:
        return _ROUTES_PATH.read_text(encoding="utf-8")

    def _import_lines(self, src: str) -> list[str]:
        return [ln.strip() for ln in src.splitlines()
                if ln.strip().startswith(("from ", "import "))]

    def test_agent_does_not_import_brokers(self):
        for line in self._import_lines(self._agent_src()):
            for forbidden in (
                "from app.brokers.kis",
                "from app.brokers.mock_broker",
                "from app.brokers.base",
            ):
                assert forbidden not in line

    def test_agent_does_not_import_executor_or_router(self):
        for line in self._import_lines(self._agent_src()):
            for forbidden in (
                "from app.execution.executor",
                "from app.execution.order_executor",
                "from app.execution.order_router",
                "import app.execution",
            ):
                assert forbidden not in line

    def test_agent_does_not_import_permission_or_assist(self):
        for line in self._import_lines(self._agent_src()):
            for forbidden in (
                "from app.permission",
                "from app.ai.assist",
                "import app.permission",
            ):
                assert forbidden not in line

    def test_agent_does_not_call_place_or_route(self):
        """실제 *호출*만 검사 — docstring에서 정책 설명 시 단어 사용은 허용."""
        src = self._agent_src()
        for forbidden in (
            "broker.place_order(", "broker.cancel_order(",
            "self.place_order(", "self.cancel_order(",
            "await broker.place_order", "await broker.cancel_order",
            "= route_order(", "await route_order(",
            "= submit_candidate(", "await submit_candidate(",
        ):
            assert forbidden not in src, (
                f"agent_memory.py must not contain '{forbidden}'"
            )

    def test_agent_does_not_import_external_http_or_ai(self):
        src = self._agent_src()
        for forbidden in (
            "import httpx", "import requests", "import urllib3",
            "from anthropic", "import anthropic",
            "from openai", "import openai",
        ):
            assert forbidden not in src

    def test_agent_does_not_reference_orderrequest(self):
        src = self._agent_src()
        for forbidden in ("OrderRequest(", ": OrderRequest", "-> OrderRequest"):
            assert forbidden not in src

    def test_no_buy_sell_hold_in_enums(self):
        for cls in (MemoryType, SourceKind, MemorySeverity):
            for member in cls:
                v = str(member.value).upper()
                assert "BUY" not in v
                assert "SELL" not in v
                assert v != "HOLD"

    def test_routes_does_not_call_broker(self):
        src = self._routes_src()
        for forbidden in (
            "broker.place_order(", "broker.cancel_order(",
            ".place_order(", ".cancel_order(",
            "route_order(",
        ):
            assert forbidden not in src

    def test_routes_does_not_import_broker_or_executor(self):
        for line in self._import_lines(self._routes_src()):
            for forbidden in (
                "from app.brokers.kis",
                "from app.brokers.mock_broker",
                "from app.brokers.base",
                "from app.execution.executor",
                "from app.execution.order_router",
            ):
                assert forbidden not in line

    def test_sanitize_patterns_present(self):
        """Sanitize 패턴이 핵심 카테고리(키 / 계좌 / 주민번호 / JWT / email / phone)
        를 모두 다루는지 정적 검사."""
        src = self._agent_src()
        for required_label in (
            "api_key_long", "anthropic_key", "openai_key",
            "kr_account", "kr_resident", "jwt", "email", "kr_phone",
        ):
            assert required_label in src, (
                f"agent_memory.py must define sanitize pattern '{required_label}'"
            )
