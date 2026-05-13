"""Paper Gate evaluator + collector + API + CLI 테스트 (#72)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.models import OrderAuditLog
from app.governance.paper_gate import (
    PaperGateInput,
    PaperGateResult,
    PaperGateThresholds,
    PaperGateVerdict,
    evaluate_paper_gate,
    render_markdown_report,
)
from app.governance.paper_gate_collector import (
    build_paper_gate_input,
    list_paper_strategies,
)


# ---------- helpers ----------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_pass_input(strategy: str = "sma_cross") -> PaperGateInput:
    end = _utcnow()
    start = end - timedelta(days=30)
    return PaperGateInput(
        strategy_name=strategy,
        period_start=start,
        period_end=end,
        trade_count=120,
        active_days=22,
        winning_pnl_sum=200_000,
        losing_pnl_sum=150_000,
        expectancy=350.0,
        max_drawdown_value=800_000,    # 8% of 10M
        initial_cash=10_000_000,
        loss_limit_violations=0,
        audit_missing_count=0,
        stale_or_duplicate_violations=0,
        rejection_rate=0.05,
        fill_polling_consistent=True,
        client_order_id_idempotent=True,
    )


# ---------- DTO invariants ----------


def test_paper_gate_result_rejects_live_authorization_true():
    with pytest.raises(ValueError, match="is_live_authorization"):
        PaperGateResult(
            strategy_name="x",
            period_start=_utcnow(), period_end=_utcnow(),
            verdict=PaperGateVerdict.PASS,
            is_live_authorization=True,
        )


def test_paper_gate_result_rejects_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        PaperGateResult(
            strategy_name="x",
            period_start=_utcnow(), period_end=_utcnow(),
            verdict=PaperGateVerdict.PASS,
            is_order_signal=True,
        )


def test_paper_gate_result_to_dict_has_invariant_flags():
    inp = _make_pass_input()
    r = evaluate_paper_gate(inp)
    d = r.to_dict()
    assert d["is_live_authorization"] is False
    assert d["is_order_signal"] is False
    assert d["live_flag_changed"] is False
    assert d["mode_changed"] is False


# ---------- evaluator — happy path ----------


def test_pass_when_all_criteria_met():
    inp = _make_pass_input()
    r = evaluate_paper_gate(inp)
    assert r.verdict is PaperGateVerdict.PASS
    assert not r.failed_criteria
    assert "Live Manual Approval" in r.next_step
    # 실거래 허가 invariant.
    assert r.is_live_authorization is False


def test_pf_calculation_uses_abs_values():
    inp = _make_pass_input()
    pf = inp.profit_factor
    assert pf is not None and pf == pytest.approx(200_000 / 150_000)


def test_pf_infinite_when_no_losses():
    end = _utcnow()
    inp = PaperGateInput(
        strategy_name="x",
        period_start=end - timedelta(days=30),
        period_end=end,
        trade_count=120, active_days=22,
        winning_pnl_sum=100_000, losing_pnl_sum=0,
        expectancy=100.0,
        max_drawdown_value=100_000,
    )
    assert inp.profit_factor == float("inf")


def test_pf_none_when_zero_volume():
    end = _utcnow()
    inp = PaperGateInput(
        strategy_name="x",
        period_start=end - timedelta(days=30),
        period_end=end,
        trade_count=0, active_days=0,
        winning_pnl_sum=0, losing_pnl_sum=0,
        expectancy=0,
    )
    assert inp.profit_factor is None


# ---------- evaluator — FAIL paths ----------


def test_fail_when_period_under_28_days():
    inp = _make_pass_input()
    short = PaperGateInput(
        **{**inp.__dict__,
           "period_start": inp.period_end - timedelta(days=10)}
    )
    r = evaluate_paper_gate(short)
    assert r.verdict is PaperGateVerdict.FAIL
    assert any("운영 기간" in c for c in r.failed_criteria)


def test_fail_when_trade_count_under_100():
    inp = _make_pass_input()
    weak = PaperGateInput(**{**inp.__dict__, "trade_count": 50})
    r = evaluate_paper_gate(weak)
    assert r.verdict is PaperGateVerdict.FAIL
    assert any("매매 신호" in c for c in r.failed_criteria)


def test_fail_when_expectancy_zero_or_negative():
    inp = _make_pass_input()
    for exp in (0.0, -10.0):
        bad = PaperGateInput(**{**inp.__dict__, "expectancy": exp})
        r = evaluate_paper_gate(bad)
        assert r.verdict is PaperGateVerdict.FAIL
        assert any("기대값" in c for c in r.failed_criteria)


def test_fail_when_pf_below_1_2():
    inp = _make_pass_input()
    bad = PaperGateInput(**{
        **inp.__dict__,
        "winning_pnl_sum": 100_000,
        "losing_pnl_sum":  100_000,  # PF = 1.0
    })
    r = evaluate_paper_gate(bad)
    assert r.verdict is PaperGateVerdict.FAIL
    assert any("PF" in c for c in r.failed_criteria)


def test_fail_when_mdd_exceeds_15pct():
    inp = _make_pass_input()
    bad = PaperGateInput(**{
        **inp.__dict__,
        "max_drawdown_value": 2_000_000,  # 20% of 10M
    })
    r = evaluate_paper_gate(bad)
    assert r.verdict is PaperGateVerdict.FAIL
    assert any("MDD" in c for c in r.failed_criteria)


def test_fail_when_loss_limit_violations_present():
    inp = _make_pass_input()
    bad = PaperGateInput(**{**inp.__dict__, "loss_limit_violations": 2})
    r = evaluate_paper_gate(bad)
    assert r.verdict is PaperGateVerdict.FAIL
    assert any("손실한도 위반" in c for c in r.failed_criteria)


def test_fail_when_audit_missing():
    inp = _make_pass_input()
    bad = PaperGateInput(**{**inp.__dict__, "audit_missing_count": 1})
    r = evaluate_paper_gate(bad)
    assert r.verdict is PaperGateVerdict.FAIL
    assert any("OrderAuditLog 누락" in c for c in r.failed_criteria)


def test_fail_when_stale_duplicate_violations_present():
    inp = _make_pass_input()
    bad = PaperGateInput(**{
        **inp.__dict__, "stale_or_duplicate_violations": 3,
    })
    r = evaluate_paper_gate(bad)
    assert r.verdict is PaperGateVerdict.FAIL


def test_fail_when_fill_polling_inconsistent():
    inp = _make_pass_input()
    bad = PaperGateInput(**{**inp.__dict__, "fill_polling_consistent": False})
    r = evaluate_paper_gate(bad)
    assert r.verdict is PaperGateVerdict.FAIL


def test_fail_when_client_order_id_not_idempotent():
    inp = _make_pass_input()
    bad = PaperGateInput(**{**inp.__dict__, "client_order_id_idempotent": False})
    r = evaluate_paper_gate(bad)
    assert r.verdict is PaperGateVerdict.FAIL


# ---------- evaluator — CAUTION ----------


def test_caution_when_best_day_share_high():
    inp = _make_pass_input()
    cautious = PaperGateInput(**{**inp.__dict__, "best_day_pnl_share": 0.7})
    r = evaluate_paper_gate(cautious)
    assert r.verdict is PaperGateVerdict.CAUTION
    assert any("하루 의존도" in c for c in r.cautions)


def test_caution_when_rejection_rate_high():
    inp = _make_pass_input()
    cautious = PaperGateInput(**{**inp.__dict__, "rejection_rate": 0.45})
    r = evaluate_paper_gate(cautious)
    assert r.verdict is PaperGateVerdict.CAUTION


def test_caution_when_hourly_loss_concentrated():
    inp = _make_pass_input()
    cautious = PaperGateInput(**{**inp.__dict__, "hourly_loss_top_share": 0.7})
    r = evaluate_paper_gate(cautious)
    assert r.verdict is PaperGateVerdict.CAUTION


def test_caution_when_paper_vs_backtest_drift_large():
    inp = _make_pass_input()
    cautious = PaperGateInput(**{
        **inp.__dict__, "paper_vs_backtest_pf_drift": 0.8,
    })
    r = evaluate_paper_gate(cautious)
    assert r.verdict is PaperGateVerdict.CAUTION


# ---------- thresholds override ----------


def test_thresholds_override_changes_verdict():
    inp = _make_pass_input()
    # 1.5 minimum PF — 1.33 fails.
    strict = PaperGateThresholds(min_profit_factor=1.5)
    r = evaluate_paper_gate(inp, strict)
    assert r.verdict is PaperGateVerdict.FAIL


# ---------- markdown report ----------


def test_markdown_report_contains_disclaimer_and_no_buy_sell():
    inp = _make_pass_input()
    r = evaluate_paper_gate(inp)
    text = render_markdown_report(r)
    assert "실거래 허가" in text  # disclaimer
    assert "Live Manual Approval" in text
    # 주문 신호 표현이 들어가서는 안 된다 — Paper Gate는 advisory.
    for banned in ["매수 실행", "매도 실행", "BUY signal", "SELL signal"]:
        assert banned not in text


def test_markdown_report_lists_fail_reasons():
    inp = _make_pass_input()
    bad = PaperGateInput(**{**inp.__dict__, "expectancy": -100.0})
    r = evaluate_paper_gate(bad)
    text = render_markdown_report(r)
    assert "FAIL" in text
    assert "기대값" in text


# ---------- collector ----------


def test_collector_filters_paper_mode_only(client):
    db = client.test_db_factory()
    try:
        # paper trade row.
        db.add(OrderAuditLog(
            created_at=_utcnow() - timedelta(days=2),
            mode="PAPER", requested_by_ai=False,
            symbol="005930", side="BUY", quantity=10,
            order_type="MARKET", latest_price=70_000,
            decision="APPROVED", reasons=[], executed=True,
            filled_quantity=10, message="ok",
            strategy="sma_cross",
        ))
        # simulation row — 제외 대상.
        db.add(OrderAuditLog(
            created_at=_utcnow() - timedelta(days=2),
            mode="SIMULATION", requested_by_ai=False,
            symbol="005930", side="BUY", quantity=10,
            order_type="MARKET", latest_price=70_000,
            decision="APPROVED", reasons=[], executed=True,
            filled_quantity=10, message="ok",
            strategy="sma_cross",
        ))
        db.commit()
        end = _utcnow()
        start = end - timedelta(days=30)
        inp = build_paper_gate_input(
            db, strategy="sma_cross",
            period_start=start, period_end=end,
            expectancy=100.0, winning_pnl_sum=100_000,
            losing_pnl_sum=50_000, max_drawdown_value=500_000,
        )
        assert inp.trade_count == 1
        assert inp.active_days == 1
        assert inp.strategy_name == "sma_cross"
    finally:
        db.close()


def test_collector_rejection_rate(client):
    db = client.test_db_factory()
    try:
        for _ in range(4):
            db.add(OrderAuditLog(
                created_at=_utcnow() - timedelta(hours=2),
                mode="PAPER", requested_by_ai=False,
                symbol="005930", side="BUY", quantity=10,
                order_type="MARKET", latest_price=70_000,
                decision="REJECTED", reasons=["x"], executed=False,
                message="reject", strategy="weak",
            ))
        for _ in range(6):
            db.add(OrderAuditLog(
                created_at=_utcnow() - timedelta(hours=2),
                mode="PAPER", requested_by_ai=False,
                symbol="005930", side="BUY", quantity=10,
                order_type="MARKET", latest_price=70_000,
                decision="APPROVED", reasons=[], executed=True,
                filled_quantity=10, message="ok",
                strategy="weak",
            ))
        db.commit()
        inp = build_paper_gate_input(
            db, strategy="weak",
            period_start=_utcnow() - timedelta(days=30),
            period_end=_utcnow(),
        )
        # 4 REJECTED / 10 total = 40%.
        assert inp.rejection_rate == pytest.approx(0.4)
        assert inp.trade_count == 6
    finally:
        db.close()


def test_list_paper_strategies(client):
    db = client.test_db_factory()
    try:
        for strat in ("alpha", "beta", "alpha"):
            db.add(OrderAuditLog(
                created_at=_utcnow(),
                mode="PAPER", requested_by_ai=False,
                symbol="X", side="BUY", quantity=1,
                order_type="MARKET", latest_price=1,
                decision="APPROVED", reasons=[], executed=True,
                filled_quantity=1, message="ok",
                strategy=strat,
            ))
        db.commit()
        out = list_paper_strategies(
            db,
            period_start=_utcnow() - timedelta(days=30),
            period_end=_utcnow() + timedelta(days=1),
        )
        assert out == ["alpha", "beta"]
    finally:
        db.close()


# ---------- API route ----------


def test_route_paper_gate_evaluate_returns_pass(client):
    body = {
        "strategy_name": "sma_cross",
        "trade_count":   120,
        "active_days":   22,
        "winning_pnl_sum": 200_000,
        "losing_pnl_sum":  150_000,
        "expectancy":     350.0,
        "max_drawdown_value": 800_000,
    }
    res = client.post("/api/governance/paper-gate/evaluate", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["verdict"] == "PASS"
    assert data["is_live_authorization"] is False
    assert data["is_order_signal"] is False
    assert data["live_flag_changed"] is False


def test_route_paper_gate_evaluate_returns_fail(client):
    body = {
        "strategy_name": "weak",
        "trade_count":   30,
        "active_days":   14,
        "winning_pnl_sum": 10_000,
        "losing_pnl_sum":  30_000,
        "expectancy":    -50.0,
        "max_drawdown_value": 3_000_000,
    }
    res = client.post("/api/governance/paper-gate/evaluate", json=body)
    assert res.status_code == 200
    assert res.json()["verdict"] == "FAIL"


def test_route_paper_gate_evaluate_does_not_leak_secrets(client):
    body = {"strategy_name": "x"}
    res = client.post("/api/governance/paper-gate/evaluate", json=body)
    text = res.text.lower()
    for needle in [
        "kis_app_key", "kis_app_secret", "anthropic_api_key",
        "telegram_bot_token", "sk-", "bearer ",
    ]:
        assert needle not in text


# ---------- invariants — static grep guards ----------


_MODULE_PATHS = [
    Path("backend/app/governance/paper_gate.py"),
    Path("backend/app/governance/paper_gate_collector.py"),
    Path("backend/app/api/routes_governance.py"),
    Path("scripts/evaluate_paper_gate.py"),
]


def _resolve(path: Path) -> Path:
    if path.exists():
        return path
    return Path(__file__).resolve().parents[2] / path


def test_paper_gate_does_not_import_broker_or_executor():
    forbidden = [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.order_router",
        "from app.execution.order_executor",
        "from app.execution.executor",
        "from app.execution.paper_trader",
        "import app.brokers",
    ]
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, (
                f"{rel} imports forbidden: {needle!r}"
            )


def test_paper_gate_does_not_call_order_routing_or_broker():
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        ".execute_order(",
        "OrderExecutor(",
        "submit_candidate(",
    ]
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, (
                f"{rel} contains forbidden call: {needle!r}"
            )


def test_paper_gate_does_not_change_safety_flags():
    """안전 플래그 *변경* (Python 대입 / setattr) 검사.

    문서 / 메시지 문자열에 안전 플래그 이름이 *언급*되는 것은 정상.
    실제 Python 어휘 — `settings.enable_*_trading = True` / `setattr(...)` /
    `os.environ["ENABLE_*"] = ` 형태만 차단.
    """
    forbidden = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "setattr(settings, \"enable_",
        "os.environ[\"ENABLE_LIVE_TRADING\"]",
        "os.environ[\"ENABLE_AI_EXECUTION\"]",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"]",
        ".emergency_stop = True",
        ".emergency_stop = False",
    ]
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, (
                f"{rel} mutates safety flag: {needle!r}"
            )


def test_paper_gate_does_not_write_to_db():
    """본 모듈은 SELECT 만 — INSERT/UPDATE/DELETE/.commit/.add 0건.

    단, evaluate_paper_gate_endpoint가 Pydantic model에서 add 메서드 같은
    충돌 어휘를 쓰는지 검사하기엔 ".add" 부분 일치가 false positive — 본
    테스트는 SQL keyword + Session 메서드 호출만 본다.
    """
    write_patterns = [
        "db.add(", "db.add_all(", "db.commit(", "db.flush(",
        "db.delete(", "db.merge(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        src = path.read_text(encoding="utf-8")
        for needle in write_patterns:
            assert needle not in src, (
                f"{rel} writes to DB: {needle!r}"
            )


# ---------- CLI smoke ----------


def test_cli_dry_run_pass_exits_zero():
    import subprocess
    import sys
    project_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [
            sys.executable, "scripts/evaluate_paper_gate.py",
            "--dry-run", "--strategy", "x",
            "--trade-count", "120", "--active-days", "22",
            "--expectancy", "350",
            "--pf-numerator", "200000", "--pf-denominator", "150000",
            "--max-drawdown-value", "800000",
            "--format", "json",
        ],
        cwd=str(project_root),
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert "PASS" in proc.stdout


def test_cli_dry_run_fail_exits_one():
    import subprocess
    import sys
    project_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [
            sys.executable, "scripts/evaluate_paper_gate.py",
            "--dry-run", "--strategy", "x",
            "--trade-count", "30", "--active-days", "10",
            "--expectancy", "-50",
            "--pf-numerator", "10000", "--pf-denominator", "30000",
            "--max-drawdown-value", "2000000",
            "--format", "json",
        ],
        cwd=str(project_root),
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=30,
    )
    assert proc.returncode == 1
    assert "FAIL" in proc.stdout
