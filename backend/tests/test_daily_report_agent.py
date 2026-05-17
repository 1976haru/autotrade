"""#57: Daily Report Agent tests.

본 Agent는 read-only 분석 + markdown 생성만 수행한다. 투자 조언 / 종목 추천
/ 매수 매도 신호를 *절대* 만들지 않으며, broker / OrderExecutor / route_order
호출 0건.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace as _NS

import pytest

from app.agents.base import AgentContext, AgentDecision, AgentRole
from app.agents.daily_report_agent import (
    DailyReportAgent,
    DailyReportInput,
    DailyReportOutput,
    DailyReportStats,
    FindingSeverity,
    LossCauseCategory,
    aggregate_stats,
    analyze_daily,
    classify_findings,
    load_agent_decisions_for_date,
    load_audit_rows_for_date,
    load_emergency_events_for_date,
    load_futures_audit_for_date,
    load_pending_approvals_for_date,
    load_virtual_orders_for_date,
)
from app.db.models import (
    OrderAuditLog,
)


_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "agents" / "daily_report_agent.py"
)
_CLI_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "generate_daily_report.py"
)
_TODAY = date(2026, 5, 9)


# ====================================================================
# Helpers — fake row 객체 (DB 의존 없는 단위 테스트용)
# ====================================================================


def _fake_audit(decision="REJECTED", reasons=None, **kw):
    base = dict(
        id=kw.get("id", 1),
        created_at=kw.get("created_at", datetime(2026, 5, 9, 10, 30)),
        decision=decision,
        reasons=reasons or [],
        symbol=kw.get("symbol", "005930"),
        side=kw.get("side", "BUY"),
        quantity=kw.get("quantity", 10),
        requested_by_ai=kw.get("requested_by_ai", False),
        signal_confidence=kw.get("signal_confidence", None),
        trade_reason=kw.get("trade_reason", None),
        strategy=kw.get("strategy", "sma_cross"),
        ai_decision_meta=kw.get("ai_decision_meta", None),
        archived=kw.get("archived", False),
        executed=kw.get("executed", False),
    )
    return _NS(**base)


def _fake_virtual(status="FILLED", **kw):
    return _NS(
        id=kw.get("id", 1),
        created_at=kw.get("created_at", datetime(2026, 5, 9, 10, 30)),
        updated_at=kw.get("updated_at", datetime(2026, 5, 9, 10, 30)),
        symbol=kw.get("symbol", "005930"),
        side=kw.get("side", "BUY"),
        quantity=kw.get("quantity", 1),
        status=status,
        filled_quantity=kw.get("filled_quantity", 1),
        avg_fill_price=kw.get("avg_fill_price", 70_000),
        filled_at=kw.get("filled_at", datetime(2026, 5, 9, 10, 30)),
        strategy=kw.get("strategy", None),
        mode=kw.get("mode", "SIMULATION"),
    )


def _fake_futures(forced=False, **kw):
    return _NS(
        id=kw.get("id", 1),
        created_at=kw.get("created_at", datetime(2026, 5, 9, 10, 30)),
        contract=kw.get("contract", "K200"),
        side=kw.get("side", "BUY"),
        quantity=kw.get("quantity", 1),
        decision=kw.get("decision", "REJECTED"),
        reasons=kw.get("reasons", []),
        forced_liquidation=forced,
    )


def _fake_agent_decision(decision="OBSERVE", confidence=None, **kw):
    return _NS(
        id=kw.get("id", 1),
        created_at=kw.get("created_at", datetime(2026, 5, 9, 10, 30)),
        agent_name=kw.get("agent_name", "observer"),
        decision=decision,
        confidence=confidence,
        reasons=kw.get("reasons", []),
        chain_id=kw.get("chain_id", None),
    )


def _fake_emergency(reason="MANUAL", **kw):
    return _NS(
        id=kw.get("id", 1),
        created_at=kw.get("created_at", datetime(2026, 5, 9, 10, 30)),
        enabled=kw.get("enabled", True),
        reason_code=reason,
        level=kw.get("level", "LEVEL_1"),
    )


def _fake_pending(status="PENDING", attempts=None, **kw):
    return _NS(
        id=kw.get("id", 1),
        created_at=kw.get("created_at", datetime(2026, 5, 9, 10, 30)),
        status=status,
        attempts=attempts or [],
    )


# ====================================================================
# Output invariants
# ====================================================================


class TestOutputInvariants:
    def test_output_rejects_auto_apply_allowed_true(self):
        with pytest.raises(ValueError, match="auto_apply_allowed"):
            DailyReportOutput(
                report_date=_TODAY,
                stats=DailyReportStats(operation_mode="SIMULATION"),
                findings=(), tomorrow_warnings=(), action_items=(),
                improvement_candidates=(), markdown_report="", summary_lines=(),
                auto_apply_allowed=True,    # ← invariant 위반
            )

    def test_output_rejects_is_order_signal_true(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            DailyReportOutput(
                report_date=_TODAY,
                stats=DailyReportStats(operation_mode="SIMULATION"),
                findings=(), tomorrow_warnings=(), action_items=(),
                improvement_candidates=(), markdown_report="", summary_lines=(),
                is_order_signal=True,        # ← invariant 위반
            )

    def test_output_to_dict_json_serializable(self):
        out = analyze_daily(DailyReportInput(report_date=_TODAY,
                                              operation_mode="SIMULATION"))
        # JSON 직렬화 검증
        d = out.to_dict()
        s = json.dumps(d)
        d2 = json.loads(s)
        assert d2["report_date"] == _TODAY.isoformat()
        assert d2["auto_apply_allowed"] is False
        assert d2["is_order_signal"] is False


# ====================================================================
# Stats aggregation
# ====================================================================


class TestStats:
    def test_empty_input_produces_baseline_stats(self):
        stats = aggregate_stats(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION",
        ))
        assert stats.total_orders == 0
        assert stats.approved_count == 0
        assert stats.rejected_count == 0
        assert stats.virtual_order_count == 0
        assert stats.futures_order_count == 0

    def test_count_orders_by_decision(self):
        rows = (
            _fake_audit(decision="APPROVED"),
            _fake_audit(decision="APPROVED"),
            _fake_audit(decision="REJECTED"),
            _fake_audit(decision="NEEDS_APPROVAL"),
        )
        stats = aggregate_stats(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION",
            audit_rows=rows,
        ))
        assert stats.total_orders == 4
        assert stats.approved_count == 2
        assert stats.rejected_count == 1
        assert stats.needs_approval_count == 1

    def test_virtual_order_count(self):
        rows = (
            _fake_virtual(status="FILLED"),
            _fake_virtual(status="FILLED"),
            _fake_virtual(status="REJECTED"),
        )
        stats = aggregate_stats(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION",
            virtual_orders=rows,
        ))
        assert stats.virtual_order_count == 3
        assert stats.virtual_filled_count == 2

    def test_futures_audit_forced_liquidation(self):
        rows = (
            _fake_futures(forced=True),
            _fake_futures(forced=False),
            _fake_futures(forced=True),
        )
        stats = aggregate_stats(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION",
            futures_audit_rows=rows,
        ))
        assert stats.futures_order_count == 3
        assert stats.futures_forced_liquidation_count == 2

    def test_strategy_breakdown(self):
        rows = (
            _fake_audit(decision="APPROVED", strategy="sma"),
            _fake_audit(decision="REJECTED", strategy="sma"),
            _fake_audit(decision="APPROVED", strategy="rsi"),
        )
        stats = aggregate_stats(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION",
            audit_rows=rows,
        ))
        assert stats.strategy_breakdown["sma"]["order_count"] == 2
        assert stats.strategy_breakdown["sma"]["approved"] == 1
        assert stats.strategy_breakdown["sma"]["rejected"] == 1
        assert stats.strategy_breakdown["rsi"]["order_count"] == 1

    def test_agent_breakdown_avg_confidence(self):
        rows = (
            _fake_agent_decision(agent_name="observer", confidence=80, decision="OBSERVE"),
            _fake_agent_decision(agent_name="observer", confidence=60, decision="WARN"),
            _fake_agent_decision(agent_name="risk", confidence=90, decision="REJECT"),
        )
        stats = aggregate_stats(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION",
            agent_decisions=rows,
        ))
        assert stats.agent_breakdown["observer"]["decision_count"] == 2
        assert stats.agent_breakdown["observer"]["warn"] == 1
        assert stats.agent_breakdown["observer"]["avg_confidence"] == 70
        assert stats.agent_breakdown["risk"]["reject"] == 1
        assert stats.agent_breakdown["risk"]["avg_confidence"] == 90

    def test_risk_event_breakdown_from_reasons(self):
        rows = (
            _fake_audit(decision="REJECTED",
                        reasons=["stale price (60s+)"]),
            _fake_audit(decision="REJECTED",
                        reasons=["duplicate fingerprint detected"]),
            _fake_audit(decision="REJECTED",
                        reasons=["cooldown active"]),
            _fake_audit(decision="REJECTED",
                        reasons=["daily loss limit reached"]),
        )
        stats = aggregate_stats(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION",
            audit_rows=rows,
        ))
        assert stats.risk_event_breakdown["stale_data"] == 1
        assert stats.risk_event_breakdown["duplicate_order"] == 1
        assert stats.risk_event_breakdown["cooldown"] == 1
        assert stats.risk_event_breakdown["loss_limit"] == 1

    def test_emergency_stop_count_and_reasons(self):
        rows = (
            _fake_emergency(reason="DAILY_LOSS_BREACH"),
            _fake_emergency(reason="OPERATOR_MANUAL"),
        )
        stats = aggregate_stats(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION",
            emergency_events=rows,
        ))
        assert stats.emergency_stop_toggle_count == 2
        assert "DAILY_LOSS_BREACH" in stats.emergency_stop_reasons
        assert stats.risk_event_breakdown["emergency_stop"] == 2

    def test_approval_lifecycle_counts(self):
        rows = (
            _fake_pending(status="PENDING"),
            _fake_pending(status="APPROVED"),
            _fake_pending(status="REJECTED"),
            _fake_pending(status="EXPIRED"),
            _fake_pending(status="PENDING",
                          attempts=[{"reason": "stale"}, {"reason": "stale"}]),
        )
        stats = aggregate_stats(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION",
            pending_approvals=rows,
        ))
        assert stats.approval_pending == 2
        assert stats.approval_approved == 1
        assert stats.approval_rejected == 1
        assert stats.approval_expired == 1
        assert stats.approval_revalidation_failures == 2

    def test_avg_signal_confidence(self):
        rows = (
            _fake_audit(decision="APPROVED", signal_confidence=80),
            _fake_audit(decision="REJECTED", signal_confidence=60),
            _fake_audit(decision="APPROVED", signal_confidence=70),
        )
        stats = aggregate_stats(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION",
            audit_rows=rows,
        ))
        assert stats.avg_signal_confidence == pytest.approx(70.0)


# ====================================================================
# Loss-cause classifier
# ====================================================================


class TestFindings:
    def test_data_stale_finding(self):
        rows = tuple(
            _fake_audit(decision="REJECTED", reasons=["stale price (60s+)"])
            for _ in range(4)
        )
        inp = DailyReportInput(report_date=_TODAY, operation_mode="SIMULATION",
                                audit_rows=rows)
        stats = aggregate_stats(inp)
        findings = classify_findings(inp, stats)
        cats = {f.category for f in findings}
        assert LossCauseCategory.DATA_STALE in cats

    def test_duplicate_burst_finding(self):
        rows = tuple(
            _fake_audit(decision="REJECTED", reasons=["duplicate"])
            for _ in range(3)
        )
        inp = DailyReportInput(report_date=_TODAY, operation_mode="SIMULATION",
                                audit_rows=rows)
        stats = aggregate_stats(inp)
        findings = classify_findings(inp, stats)
        assert any(f.category == LossCauseCategory.DUPLICATE_BURST for f in findings)

    def test_loss_limit_breach_finding_critical(self):
        rows = (
            _fake_audit(decision="REJECTED", reasons=["daily loss limit"]),
        )
        inp = DailyReportInput(report_date=_TODAY, operation_mode="SIMULATION",
                                audit_rows=rows)
        stats = aggregate_stats(inp)
        findings = classify_findings(inp, stats)
        loss = [f for f in findings if f.category == LossCauseCategory.LOSS_LIMIT_BREACH]
        assert loss
        assert loss[0].severity == FindingSeverity.CRITICAL

    def test_emergency_stop_finding(self):
        events = (_fake_emergency(reason="MANUAL"),)
        inp = DailyReportInput(report_date=_TODAY, operation_mode="SIMULATION",
                                emergency_events=events)
        stats = aggregate_stats(inp)
        findings = classify_findings(inp, stats)
        assert any(f.category == LossCauseCategory.EMERGENCY_STOP for f in findings)

    def test_ai_overconfidence_finding_when_3_or_more(self):
        rows = tuple(
            _fake_audit(decision="REJECTED", requested_by_ai=True,
                        signal_confidence=85)
            for _ in range(3)
        )
        inp = DailyReportInput(report_date=_TODAY, operation_mode="SIMULATION",
                                audit_rows=rows)
        stats = aggregate_stats(inp)
        findings = classify_findings(inp, stats)
        assert any(f.category == LossCauseCategory.AI_OVERCONFIDENCE for f in findings)

    def test_no_ai_overconfidence_when_below_threshold(self):
        # confidence 70 (< 80) → AI overconf 분류 X
        rows = tuple(
            _fake_audit(decision="REJECTED", requested_by_ai=True,
                        signal_confidence=70)
            for _ in range(5)
        )
        inp = DailyReportInput(report_date=_TODAY, operation_mode="SIMULATION",
                                audit_rows=rows)
        stats = aggregate_stats(inp)
        findings = classify_findings(inp, stats)
        assert not any(f.category == LossCauseCategory.AI_OVERCONFIDENCE for f in findings)

    def test_liquidation_risk_from_forced_futures(self):
        rows = tuple(_fake_futures(forced=True) for _ in range(2))
        inp = DailyReportInput(report_date=_TODAY, operation_mode="SIMULATION",
                                futures_audit_rows=rows)
        stats = aggregate_stats(inp)
        findings = classify_findings(inp, stats)
        liq = [f for f in findings if f.category == LossCauseCategory.LIQUIDATION_RISK]
        assert liq
        assert liq[0].severity == FindingSeverity.CRITICAL

    def test_unknown_fallback_when_many_unclassified(self):
        # 15 REJECTED, 그 중 2개만 stale (분류됨), 나머지 13개는 분류 없음.
        rows = (
            tuple(_fake_audit(decision="REJECTED", reasons=["stale"]) for _ in range(2)) +
            tuple(_fake_audit(decision="REJECTED", reasons=["random reason"])
                  for _ in range(13))
        )
        inp = DailyReportInput(report_date=_TODAY, operation_mode="SIMULATION",
                                audit_rows=rows)
        stats = aggregate_stats(inp)
        findings = classify_findings(inp, stats)
        assert any(f.category == LossCauseCategory.ORDER_REJECTED for f in findings)


# ====================================================================
# Markdown report
# ====================================================================


class TestMarkdownReport:
    def test_markdown_contains_disclaimer(self):
        out = analyze_daily(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION"))
        md = out.markdown_report
        assert "투자 조언이 아니" in md
        assert "시스템 운영" in md
        assert "별도 검증" in md

    def test_markdown_no_buy_sell_signals(self):
        # 본 리포트는 매수/매도 결정 신호를 포함하지 *않는다*.
        rows = (
            _fake_audit(decision="REJECTED", reasons=["stale"], signal_confidence=85,
                        requested_by_ai=True),
        )
        out = analyze_daily(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION", audit_rows=rows))
        md = out.markdown_report
        # 종목 추천 / 결정 신호 단어들 금지
        for forbidden in ("매수 추천", "매도 추천", "BUY signal",
                          "SELL signal", "지금 매수", "지금 매도",
                          "추천 종목"):
            assert forbidden not in md

    def test_markdown_contains_required_sections(self):
        out = analyze_daily(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION"))
        md = out.markdown_report
        assert "# Daily System Report" in md
        assert "## 1. 오늘 요약" in md
        assert "## 2. 손익 요약" in md
        assert "## 3. 시간대별 성과" in md
        assert "## 4. 전략별 성과" in md
        assert "## 5. Agent 판단 요약" in md
        assert "## 6. 리스크 이벤트" in md
        assert "## 7. 승인 큐 요약" in md
        assert "## 8. 손실 원인 분석" in md
        assert "## 9. 내일 주의점" in md
        assert "## 10. 개선 후보" in md
        assert "## 11. Action Items" in md
        assert "## 12. 부록" in md

    def test_markdown_action_items_use_checkboxes(self):
        out = analyze_daily(DailyReportInput(
            report_date=_TODAY, operation_mode="SIMULATION",
            emergency_events=(_fake_emergency(),),
        ))
        md = out.markdown_report
        assert "[ ]" in md  # Action Items checkbox

    def test_markdown_includes_today_summary_table(self):
        rows = (
            _fake_audit(decision="APPROVED"),
            _fake_audit(decision="REJECTED"),
        )
        out = analyze_daily(DailyReportInput(
            report_date=_TODAY, operation_mode="LIVE_AI_ASSIST",
            audit_rows=rows,
        ))
        md = out.markdown_report
        assert "LIVE_AI_ASSIST" in md
        assert "총 주문 수" in md


# ====================================================================
# DB read-only helpers
# ====================================================================


class TestDBHelpers:
    def test_helpers_return_empty_for_fresh_session(self, client):
        db = client.test_db_factory()
        try:
            assert load_audit_rows_for_date(db, _TODAY) == []
            assert load_virtual_orders_for_date(db, _TODAY) == []
            assert load_futures_audit_for_date(db, _TODAY) == []
            assert load_agent_decisions_for_date(db, _TODAY) == []
            assert load_emergency_events_for_date(db, _TODAY) == []
            assert load_pending_approvals_for_date(db, _TODAY) == []
        finally:
            db.close()


# ====================================================================
# Agent class
# ====================================================================


class TestAgentClass:
    def test_metadata(self):
        agent = DailyReportAgent()
        meta = agent.metadata
        assert meta.role == AgentRole.REPORT_WRITER
        assert meta.can_execute_order is False

    def test_run_no_input_returns_no_op(self):
        out = DailyReportAgent().run(AgentContext())
        assert out.decision == AgentDecision.NO_OP
        assert out.is_order_intent is False
        assert out.can_execute_order is False

    def test_run_with_input_returns_report(self):
        ctx = AgentContext(extra={
            "daily_report_input": DailyReportInput(
                report_date=_TODAY, operation_mode="SIMULATION",
            ),
        })
        out = DailyReportAgent().run(ctx)
        assert out.decision == AgentDecision.REPORT
        assert out.metadata["auto_apply_allowed"] is False
        assert out.metadata["is_order_signal"] is False


# ====================================================================
# CLI
# ====================================================================


class TestCLI:
    def test_cli_dry_run_outputs_markdown(self, tmp_path):
        env = dict(__import__("os").environ)
        env.setdefault("DEFAULT_MODE", "SIMULATION")
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(_CLI_PATH),
             "--date", "2026-05-09", "--dry-run"],
            cwd=str(_CLI_PATH.parent.parent),  # backend/
            env=env, capture_output=True, text=True, timeout=60,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        assert "Daily System Report" in result.stdout
        assert "투자 조언이 아니" in result.stdout

    def test_cli_writes_file(self, tmp_path):
        env = dict(__import__("os").environ)
        env.setdefault("DEFAULT_MODE", "SIMULATION")
        env["PYTHONIOENCODING"] = "utf-8"
        out_dir = tmp_path / "reports"
        result = subprocess.run(
            [sys.executable, str(_CLI_PATH),
             "--date", "2026-05-09",
             "--output-dir", str(out_dir)],
            cwd=str(_CLI_PATH.parent.parent),
            env=env, capture_output=True, text=True, timeout=60,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        out_path = out_dir / "daily_2026-05-09.md"
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "Daily System Report" in content
        assert "투자 조언이 아니" in content


# ====================================================================
# API
# ====================================================================


class TestAPI:
    def test_preview_endpoint_returns_markdown(self, client):
        res = client.get("/api/agents/daily-report/preview?date=2026-05-09")
        assert res.status_code == 200
        body = res.json()
        assert body["report_date"] == "2026-05-09"
        assert body["auto_apply_allowed"] is False
        assert body["is_order_signal"] is False
        assert "Daily System Report" in body["markdown_report"]
        assert "투자 조언" in body["notice"] or "advisory" in body["notice"].lower()

    def test_preview_endpoint_400_on_bad_date(self, client):
        res = client.get("/api/agents/daily-report/preview?date=bad-date")
        assert res.status_code == 400

    def test_generate_endpoint_writes_file(self, client, tmp_path):
        out_dir = tmp_path / "reports"
        res = client.post("/api/agents/daily-report/generate", json={
            "date": "2026-05-09",
            "output_dir": str(out_dir),
        })
        assert res.status_code == 200
        body = res.json()
        assert body["report_date"] == "2026-05-09"
        assert body["bytes_written"] > 0
        out_path = Path(body["output_path"])
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "Daily System Report" in content

    def test_preview_does_not_create_audit_rows(self, client):
        from sqlalchemy import select

        db = client.test_db_factory()
        try:
            before = len(db.execute(select(OrderAuditLog)).all())
            client.get("/api/agents/daily-report/preview?date=2026-05-09")
            after = len(db.execute(select(OrderAuditLog)).all())
            assert before == after
        finally:
            db.close()


# ====================================================================
# Static module guards (절대 원칙 강제)
# ====================================================================


class TestStaticGuards:
    def _source(self) -> str:
        return _MODULE_PATH.read_text(encoding="utf-8")

    def _import_lines(self) -> list[str]:
        lines: list[str] = []
        for raw in self._source().splitlines():
            stripped = raw.strip()
            if stripped.startswith("from ") or stripped.startswith("import "):
                lines.append(stripped)
        return lines

    def test_module_does_not_import_brokers(self):
        for line in self._import_lines():
            for forbidden in (
                "from app.brokers.kis",
                "from app.brokers.mock_broker",
                "from app.brokers.base",
                "from app.brokers.futures_base",
            ):
                assert forbidden not in line, (
                    f"daily_report_agent imports forbidden: {line}"
                )

    def test_module_does_not_import_executor_or_router(self):
        for line in self._import_lines():
            for forbidden in (
                "from app.execution.executor",
                "from app.execution.order_executor",
                "from app.execution.order_router",
                "import app.execution",
            ):
                assert forbidden not in line, (
                    f"daily_report_agent imports forbidden: {line}"
                )

    def test_module_does_not_import_permission(self):
        for line in self._import_lines():
            for forbidden in (
                "from app.permission.gate",
                "from app.ai.assist",
                "import app.permission",
            ):
                assert forbidden not in line

    def test_module_does_not_call_place_or_cancel(self):
        src = self._source()
        for forbidden in (
            "broker.place_order(", "broker.cancel_order(",
            "await broker.place_order", "await broker.cancel_order",
            ".place_order(", ".cancel_order(",
        ):
            assert forbidden not in src, (
                f"daily_report_agent must not call: {forbidden}"
            )

    def test_module_does_not_call_route_order(self):
        src = self._source()
        for forbidden in (
            "= route_order(", "await route_order(",
        ):
            assert forbidden not in src

    def test_module_does_not_emit_db_writes(self):
        src = self._source()
        for forbidden in (
            "db.add(", "db.commit(", "db.delete(", "db.merge(",
            "session.add(", "session.commit(", "session.delete(",
            ".insert(", ".update(", ".delete(",
        ):
            assert forbidden not in src, (
                f"daily_report_agent must not write DB: {forbidden}"
            )

    def test_module_does_not_import_external_http_or_ai(self):
        src = self._source()
        for forbidden in (
            "import httpx", "import requests", "import urllib3",
            "from anthropic", "import anthropic",
            "from openai", "import openai",
        ):
            assert forbidden not in src

    def test_module_does_not_reference_orderrequest(self):
        for line in self._import_lines():
            assert "OrderRequest" not in line
        src = self._source()
        for forbidden in ("OrderRequest(", ": OrderRequest", "-> OrderRequest"):
            assert forbidden not in src

    def test_no_buy_sell_hold_in_categories(self):
        for member in LossCauseCategory:
            v = str(member.value).upper()
            assert v != "BUY"
            assert v != "SELL"
            assert v != "HOLD"

    def test_invariant_guards_present_in_source(self):
        src = self._source()
        assert "auto_apply_allowed must be False" in src
        assert "is_order_signal must be False" in src
        # 투자 조언 아님 disclaimer가 markdown에 포함되도록 _DISCLAIMER 상수 존재.
        assert "투자 조언이 아니라" in src
        assert "시스템 운영" in src

    def test_no_recommend_buy_sell_phrases_in_module(self):
        """본 모듈 자체가 매수/매도 추천 문구를 *데이터*로 가지고 있지 않다."""
        src = self._source()
        for forbidden in ("매수 추천", "매도 추천", "지금 매수", "지금 매도",
                          "추천 종목", "추천 매수가"):
            assert forbidden not in src
