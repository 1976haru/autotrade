"""P-32: 이벤트 로그 품질 점검(정합성 진단) 테스트.

연결성/주문/포트폴리오/데이터품질/보안 이슈 감지 + integrity_score +
safe_for_analysis/paper_gate + secret 미노출 + read-only + 정적 가드.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.diagnostics.event_integrity import (
    CAT_PORTFOLIO,
    CAT_SECURITY,
    SEV_CRITICAL,
    SEV_HIGH,
    EventIntegrityReport,
    diagnose_episodes,
)

_MODULE = Path(__file__).resolve().parents[1] / "app" / "diagnostics" / "event_integrity.py"


def _good(i):
    return dict(
        episode_id=f"ep-{i:04d}", final_action="BUY",
        decision_log_id=i, audit_id=i, broker_order_no=f"P{i}",
        reason_code="KIS_PAPER_SUBMITTED",
        market_snapshot={"price": 75000, "market_regime": "TREND_UP"},
        votes=[{"strategy": s, "signal": "HOLD", "score": 30}
               for s in ("ORB", "MOMENTUM", "GAP", "VWAP")],
        council={"risk_profile": "BALANCED"},
        kis_order_result={"submitted": True, "order_quality": {
            "order_status": "FILLED", "avg_fill_price": 75100,
            "latency_ms": 300, "slippage_bps": 5}},
        outcome={"status": "COMPLETE", "label": "PROFITABLE", "return_close": 0.9},
        review={"review_status": "COMPLETE", "grade": "GOOD", "tags": ["GOOD_DECISION"]},
        portfolio_delta={"cash_after": 900000, "position_qty": 10},
    )


def _codes(report):
    return {i["code"] for i in report.to_dict()["issues"]}


# ── 정상 ──

def test_clean_episodes_no_issues():
    r = diagnose_episodes([_good(1), _good(2)])
    assert r.issue_counts[SEV_CRITICAL] == 0
    assert r.issue_counts[SEV_HIGH] == 0
    assert r.integrity_score == 100.0
    assert r.safe_for_analysis is True
    assert r.safe_for_paper_gate is True


# ── 연결성 ──

def test_episode_id_missing():
    bad = _good(1)
    del bad["episode_id"]
    assert "episode_id_missing" in _codes(diagnose_episodes([bad]))


def test_decision_log_unlinked():
    bad = _good(1)
    bad["decision_log_id"] = None
    assert "decision_log_unlinked" in _codes(diagnose_episodes([bad]))


def test_audit_unlinked():
    bad = _good(1)
    bad["audit_id"] = None
    assert "audit_unlinked" in _codes(diagnose_episodes([bad]))


def test_duplicate_episode_id():
    assert "duplicate_episode_id" in _codes(diagnose_episodes([_good(7), _good(7)]))


# ── 주문 ──

def test_broker_order_no_missing():
    bad = _good(1)
    bad["broker_order_no"] = None
    assert "broker_order_no_missing" in _codes(diagnose_episodes([bad]))


def test_dry_run_no_broker_order_is_ok():
    dry = _good(1)
    dry["broker_order_no"] = None
    dry["reason_code"] = "KIS_PAPER_DRY_RUN_OK"
    dry["kis_order_result"]["dry_run"] = True
    dry["audit_id"] = None
    codes = _codes(diagnose_episodes([dry]))
    assert "broker_order_no_missing" not in codes
    assert "audit_unlinked" not in codes


def test_order_quality_missing():
    bad = _good(1)
    del bad["kis_order_result"]["order_quality"]
    assert "order_quality_missing" in _codes(diagnose_episodes([bad]))


def test_fill_price_missing():
    bad = _good(1)
    bad["kis_order_result"]["order_quality"]["avg_fill_price"] = None
    assert "fill_price_missing" in _codes(diagnose_episodes([bad]))


def test_rejection_reason_missing():
    bad = _good(1)
    bad["kis_order_result"]["order_quality"]["order_status"] = "REJECTED"
    assert "rejection_reason_missing" in _codes(diagnose_episodes([bad]))


def test_latency_type_error():
    bad = _good(1)
    bad["kis_order_result"]["order_quality"]["latency_ms"] = "fast"
    assert "latency_ms_type_error" in _codes(diagnose_episodes([bad]))


# ── 포트폴리오 ──

def test_filled_not_in_portfolio():
    bad = _good(1)
    bad["portfolio_delta"] = None
    assert "filled_not_in_portfolio" in _codes(diagnose_episodes([bad]))


def test_rejected_in_portfolio():
    bad = _good(1)
    bad["kis_order_result"]["order_quality"]["order_status"] = "REJECTED"
    bad["reason_code"] = "BLOCKED_BY_RISK_MANAGER"
    bad["portfolio_delta"] = {"quantity": 5}
    issues = diagnose_episodes([bad]).to_dict()["issues"]
    assert any(i["code"] == "rejected_in_portfolio" and i["category"] == CAT_PORTFOLIO
               for i in issues)


def test_negative_cash_critical():
    bad = _good(1)
    bad["portfolio_delta"] = {"cash_after": -100, "position_qty": 1}
    issues = diagnose_episodes([bad]).to_dict()["issues"]
    assert any(i["code"] == "negative_cash" and i["severity"] == SEV_CRITICAL
               for i in issues)


def test_negative_position_critical():
    bad = _good(1)
    bad["portfolio_delta"] = {"cash_after": 100, "position_qty": -5}
    issues = diagnose_episodes([bad]).to_dict()["issues"]
    assert any(i["code"] == "negative_position" and i["severity"] == SEV_CRITICAL
               for i in issues)


# ── 데이터 품질 ──

def test_market_snapshot_missing():
    bad = _good(1)
    bad["market_snapshot"] = {}
    bad["market_summary"] = {"data_status": "NO_MARKET_DATA"}
    assert "market_snapshot_missing" in _codes(diagnose_episodes([bad]))


def test_strategy_votes_incomplete():
    bad = _good(1)
    bad["votes"] = [{"strategy": "ORB", "signal": "BUY"}]
    assert "strategy_votes_incomplete" in _codes(diagnose_episodes([bad]))


def test_final_action_missing():
    bad = _good(1)
    bad["final_action"] = ""
    assert "final_action_missing" in _codes(diagnose_episodes([bad]))


def test_sell_reason_missing():
    s = _good(1)
    s["final_action"] = "SELL"
    s["council"] = {}
    assert "sell_reason_missing" in _codes(diagnose_episodes([s]))


def test_review_missing_when_complete():
    bad = _good(1)
    del bad["review"]
    assert "review_missing" in _codes(diagnose_episodes([bad]))


def test_outcome_pending_is_info_not_critical():
    ep = _good(1)
    ep["outcome"] = {}
    issues = diagnose_episodes([ep]).to_dict()["issues"]
    pend = [i for i in issues if i["code"] == "outcome_pending"]
    assert pend and pend[0]["severity"] == "INFO"


# ── 보안 invariant ──

def test_secret_key_detected_without_value_exposure():
    sec = _good(1)
    sec["market_snapshot"] = {"app_secret": "supersecretvalue"}
    issues = diagnose_episodes([sec]).to_dict()["issues"]
    sec_issues = [i for i in issues if i["category"] == CAT_SECURITY]
    assert sec_issues
    # 값 노출 0건.
    assert "supersecretvalue" not in str(issues)


def test_live_authorization_true_critical():
    bad = _good(1)
    bad["is_live_authorization"] = True
    issues = diagnose_episodes([bad]).to_dict()["issues"]
    assert any(i["code"] == "live_authorization_true" and i["severity"] == SEV_CRITICAL
               for i in issues)


def test_report_no_secret_keys():
    d = diagnose_episodes([_good(i) for i in range(3)]).to_dict()
    for bad in ("app_secret", "account_no", "api_key", "access_token", "password"):
        assert bad not in str(d).lower()


# ── 점수 / 등급 ──

def test_integrity_score_decreases_with_issues():
    clean = diagnose_episodes([_good(1)]).integrity_score
    bad = _good(2)
    del bad["episode_id"]
    dirty = diagnose_episodes([bad]).integrity_score
    assert dirty < clean


def test_safe_for_analysis_false_with_critical():
    bad = _good(1)
    bad["portfolio_delta"] = {"cash_after": -1}
    r = diagnose_episodes([bad])
    assert r.safe_for_analysis is False


def test_safe_for_paper_gate_false_with_high():
    bad = _good(1)
    bad["decision_log_id"] = None  # HIGH
    r = diagnose_episodes([bad])
    assert r.safe_for_paper_gate is False


def test_invariants_locked():
    r = diagnose_episodes([_good(1)])
    assert r.is_live_authorization is False
    assert r.is_order_signal is False
    assert r.uses_account_balance is False


def test_invariant_guard_rejects_unsafe():
    kw = dict(lookback_days=7, total_episodes=0, checked_episodes=0,
              integrity_score=100.0, safe_for_analysis=True, safe_for_paper_gate=True,
              issue_counts={}, by_category={})
    with pytest.raises(ValueError):
        EventIntegrityReport(**kw, is_live_authorization=True)
    with pytest.raises(ValueError):
        EventIntegrityReport(**kw, uses_account_balance=True)


def test_deterministic():
    eps = [_good(i) for i in range(5)]
    assert diagnose_episodes(eps).to_dict() == diagnose_episodes(eps).to_dict()


# ── API ──

def test_api():
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    r = c.get("/api/diagnostics/event-integrity?lookback_days=7")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["contains_secret"] is False
    assert body["summary"]["is_live_authorization"] is False
    assert "integrity_score" in body["summary"]
    assert "report" in body


def test_api_no_secret():
    import json as _json

    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    js = _json.dumps(c.get("/api/diagnostics/event-integrity").json()).lower()
    for bad in ("app_secret", "account_no", "api_key", "access_token"):
        assert bad not in js


# ── 정적 가드 (read-only / no broker) ──

def test_module_no_forbidden_imports():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
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
            assert not imp.startswith(bad), f"forbidden import: {imp}"


def test_module_no_order_or_db_write_symbols():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    for bad in ("place_order", "cancel_order", "route_order", "OrderExecutor",
                "get_balance", "account_balance", "commit", "add", "flush", "delete"):
        assert bad not in names, f"forbidden code symbol (write/order): {bad}"
