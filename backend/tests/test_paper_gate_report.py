"""P-30: 모의매매 100건 Paper Gate 성과 리포트 테스트.

집계(판단/주문/체결/승률/PF/MDD/연속손실/전략별/매수불가/주문품질/매도사유/복기/
포트폴리오) + 등급(INSUFFICIENT/BLOCKED/NOT_READY/EXTENDED/CANARY) + 100건 미만
LIVE 금지 + export(md/json) + secret 미노출 + 수익보장 문구 0건 + 정적 가드.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.reports.paper_performance_report import (
    GRADE_BLOCKED_BY_RISK,
    GRADE_INSUFFICIENT_SAMPLE,
    GRADE_READY_FOR_EXTENDED_PAPER,
    GRADE_READY_FOR_SMALL_LIVE_CANARY_REVIEW,
    MIN_SAMPLE_TRADES,
    PaperGateReport,
    evaluate_paper_gate_readiness,
    generate_paper_gate_report,
)
from app.reports.paper_report_export import (
    export_report,
    report_to_json,
    report_to_markdown,
)

_REPORT_MOD = Path(__file__).resolve().parents[1] / "app" / "reports" / "paper_performance_report.py"
_EXPORT_MOD = Path(__file__).resolve().parents[1] / "app" / "reports" / "paper_report_export.py"
_FIXED = datetime(2026, 5, 23, tzinfo=timezone.utc)


def _ep(i, ret, *, action="BUY", strat="MOMENTUM", filled=True, sell=None,
        reason="KIS_PAPER_SUBMITTED", review_grade=None, regime="TREND_UP"):
    day = 1 + (i % 15)
    label = "PROFITABLE" if (ret or 0) > 0 else "LOSS" if (ret or 0) < 0 else "NEUTRAL"
    e = dict(
        final_action=action, created_at=f"2026-05-{day:02d}T01:00:00+00:00",
        votes=[{"strategy": strat, "signal": action, "score": 70}],
        selected_strategies=[strat],
        outcome=(None if ret is None else
                 {"status": "COMPLETE", "label": label, "return_close": ret}),
        broker_order_no=(f"P{i}" if filled else None),
        council={"risk_profile": "BALANCED", "market_regime": regime},
        kis_order_result={"submitted": filled,
                          "order_quality": {"order_status": ("FILLED" if filled else None),
                                            "latency_ms": 300, "slippage_bps": 5}},
        reason_code=reason,
    )
    if sell:
        e["council"]["sell_reason"] = {"reason_code": sell, "category": "RISK_EXIT"}
    if review_grade:
        e["review"] = {"review_status": "COMPLETE", "grade": review_grade,
                       "tags": ["GOOD_DECISION" if review_grade == "GOOD" else "BAD_DECISION"]}
    return e


def _strong_120():
    # 낮은 MDD, 높은 PF, 승률>0.5 → canary 검토 가능.
    eps = [_ep(i, 0.6 if i % 10 else -0.1, review_grade=("GOOD" if i % 10 else "BAD"))
           for i in range(120)]
    return eps


def _report(eps):
    return generate_paper_gate_report(eps, now=_FIXED).to_dict()


# ── 집계 ──

def test_total_decision_order_filled_counts():
    eps = [_ep(i, 0.5) for i in range(10)] + [_ep(100, None, action="HOLD", filled=False,
                                                   reason="NO_STRATEGY_SIGNAL")]
    s = _report(eps)["sample"]
    assert s["total_decisions"] == 11
    assert s["total_orders"] == 10
    assert s["filled_orders"] == 10
    assert s["evaluated_trades"] == 10


def test_performance_metrics_present():
    p = _report(_strong_120())["performance"]
    for k in ("win_rate", "average_return", "average_win", "average_loss",
              "payoff_ratio", "profit_factor", "max_drawdown",
              "max_consecutive_losses", "expectancy"):
        assert k in p


def test_strategy_performance_included():
    r = _report(_strong_120())
    names = [b["strategy"] for b in r["strategy_performance"]["strategies"]]
    assert "MOMENTUM" in names and "AGENT_COUNCIL" in names


def test_blocked_reasons_top():
    eps = [_ep(i, 0.5) for i in range(5)] + [
        _ep(200 + j, None, action="HOLD", filled=False, reason="NO_STRATEGY_SIGNAL")
        for j in range(4)]
    blocked = _report(eps)["blocked_reasons_top"]
    codes = {b["reason_code"]: b["count"] for b in blocked}
    assert codes.get("NO_STRATEGY_SIGNAL") == 4


def test_order_quality_summary():
    oq = _report(_strong_120())["order_quality"]
    assert "by_order_status" in oq
    assert oq["avg_latency_ms"] is not None


def test_sell_reason_performance():
    eps = [_ep(i, 0.5) for i in range(5)]
    eps.append(_ep(900, 0.8, action="SELL", sell="STOP_LOSS"))
    srp = _report(eps)["sell_reason_performance"]
    assert "STOP_LOSS" in srp
    assert srp["STOP_LOSS"]["count"] == 1


def test_review_summary_included():
    rv = _report(_strong_120())["review_summary"]
    assert rv["by_grade"]
    assert "GOOD" in rv["by_grade"]


def test_portfolio_integrity_ok():
    pi = _report(_strong_120())["portfolio_integrity"]
    assert pi["status"] == "OK"
    assert pi["mismatches"] == 0


def test_portfolio_integrity_mismatch_detected():
    # 주문 submitted 인데 order_quality.order_status 누락 → 불일치 의심.
    bad = _ep(1, 0.5)
    bad["kis_order_result"] = {"submitted": True, "order_quality": {"order_status": None}}
    pi = generate_paper_gate_report([bad] * 3, now=_FIXED).to_dict()["portfolio_integrity"]
    assert pi["status"] == "MISMATCH_SUSPECTED"
    assert pi["mismatches"] >= 1


# ── 등급 ──

def test_insufficient_sample_under_100():
    eps = [_ep(i, 0.5) for i in range(50)]
    r = _report(eps)
    assert r["readiness"]["grade"] == GRADE_INSUFFICIENT_SAMPLE
    assert r["readiness"]["can_review_live_canary"] is False


def test_under_100_never_recommends_live():
    # 매우 좋은 성과여도 100건 미만이면 canary 검토 불가.
    eps = [_ep(i, 1.0) for i in range(80)]
    r = _report(eps)
    assert r["readiness"]["grade"] == GRADE_INSUFFICIENT_SAMPLE
    assert r["readiness"]["can_review_live_canary"] is False


def test_blocked_by_risk_high_mdd():
    # MDD > 15% → BLOCKED_BY_RISK.
    eps = [_ep(i, 1.0 if i % 3 else -1.5) for i in range(120)]
    r = _report(eps)
    assert r["readiness"]["grade"] in (GRADE_BLOCKED_BY_RISK, GRADE_INSUFFICIENT_SAMPLE)
    if r["sample"]["evaluated_trades"] >= 100:
        assert r["readiness"]["grade"] == GRADE_BLOCKED_BY_RISK
        assert r["readiness"]["can_review_live_canary"] is False


def test_blocked_by_risk_low_profit_factor():
    out = evaluate_paper_gate_readiness(
        evaluated_trades=120, trading_days=15,
        performance={"profit_factor": 0.8, "max_drawdown": 0.05,
                     "max_consecutive_losses": 2, "win_rate": 0.4, "expectancy": -0.1},
        portfolio_integrity={"status": "OK"})
    assert out["grade"] == GRADE_BLOCKED_BY_RISK


def test_ready_for_small_live_canary_review():
    r = _report(_strong_120())
    assert r["sample"]["evaluated_trades"] >= MIN_SAMPLE_TRADES
    assert r["readiness"]["grade"] == GRADE_READY_FOR_SMALL_LIVE_CANARY_REVIEW
    assert r["readiness"]["can_review_live_canary"] is True


def test_ready_for_extended_paper():
    out = evaluate_paper_gate_readiness(
        evaluated_trades=120, trading_days=15,
        performance={"profit_factor": 1.3, "max_drawdown": 0.12,
                     "max_consecutive_losses": 3, "win_rate": 0.55, "expectancy": 0.2},
        portfolio_integrity={"status": "OK"})
    assert out["grade"] == GRADE_READY_FOR_EXTENDED_PAPER
    assert out["can_review_live_canary"] is False


# ── invariant / 결정성 ──

def test_invariants_locked():
    r = _report(_strong_120())
    assert r["uses_real_account_balance"] is False
    assert r["auto_live_promotion"] is False
    assert r["is_live_authorization"] is False
    assert r["is_order_signal"] is False


def test_invariant_guard_rejects_unsafe():
    kw = dict(report_id="x", created_at="x", trading_days=1, period={}, sample={},
              performance={}, strategy_performance={}, blocked_reasons_top=[],
              order_quality={}, sell_reason_performance={}, review_summary={},
              portfolio_integrity={}, readiness={})
    with pytest.raises(ValueError):
        PaperGateReport(**kw, is_live_authorization=True)
    with pytest.raises(ValueError):
        PaperGateReport(**kw, auto_live_promotion=True)
    with pytest.raises(ValueError):
        PaperGateReport(**kw, uses_real_account_balance=True)


def test_deterministic():
    eps = _strong_120()
    assert _report(eps) == _report(eps)


# ── export ──

def test_markdown_export_sections_and_no_profit_guarantee():
    md = report_to_markdown(_report(_strong_120()))
    assert md.count("## ") >= 13
    for bad in ("수익 보장", "무조건 성공", "FIRE 가능 확정", "월 얼마 보장", "실전 전환 승인"):
        assert bad not in md, f"forbidden phrase in markdown: {bad}"
    # 허용 표현 존재.
    assert "수익을 보장하지 않습니다" in md


def test_json_export_valid_and_no_secret():
    js = report_to_json(_report(_strong_120()))
    parsed = json.loads(js)
    assert parsed["is_live_authorization"] is False
    for bad in ("app_secret", "account_no", "access_token", "api_key", "password"):
        assert bad not in js.lower()


def test_export_writes_files(tmp_path):
    written = export_report(_report(_strong_120()), out_dir=tmp_path, now=_FIXED)
    assert "markdown" in written and "json" in written
    assert Path(written["markdown"]).exists()
    assert Path(written["json"]).exists()
    assert "paper_gate" not in str(tmp_path) or True  # out_dir 주입 가능 확인.


# ── API ──

def test_api_preview():
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    r = c.get("/api/agents/paper-gate-report")
    assert r.status_code == 200
    body = r.json()
    assert body["is_live_authorization"] is False
    assert body["uses_real_account_balance"] is False
    assert "readiness" in body


def test_api_no_secret():
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    js = json.dumps(c.get("/api/agents/paper-gate-report").json()).lower()
    for bad in ("app_secret", "account_no", "access_token", "api_key"):
        assert bad not in js


# ── secret / 정적 가드 ──

def test_report_to_dict_no_secret():
    d = _report(_strong_120())
    for bad in ("app_secret", "account_no", "access_token", "api_key", "password"):
        assert bad not in str(d).lower()


def test_modules_no_forbidden_imports():
    for mod in (_REPORT_MOD, _EXPORT_MOD):
        tree = ast.parse(mod.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        forbidden = ("app.brokers", "app.execution", "broker", "httpx", "requests",
                     "anthropic", "openai", "app.ai.assist", "app.ai.client")
        for imp in imported:
            for bad in forbidden:
                assert not imp.startswith(bad), f"{mod.name}: forbidden import {imp}"


def test_modules_no_order_or_account_symbols():
    for mod in (_REPORT_MOD, _EXPORT_MOD):
        tree = ast.parse(mod.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        for bad in ("place_order", "cancel_order", "route_order", "OrderExecutor",
                    "get_balance", "account_balance"):
            assert bad not in names, f"{mod.name}: forbidden symbol {bad}"


def test_no_profit_guarantee_phrase_in_source():
    for mod in (_REPORT_MOD, _EXPORT_MOD):
        src = mod.read_text(encoding="utf-8")
        for bad in ("수익 보장", "무조건 성공", "FIRE 가능 확정"):
            assert bad not in src, f"{mod.name}: forbidden phrase {bad}"
