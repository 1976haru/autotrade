"""#4-09: Risk veto evaluator tests.

Covers:
* Priority matrix — EMERGENCY_STOP > PRE_MARKET_BLOCK > RiskOfficer REJECT >
  stale_data > duplicate_signal > high_correlation > overfit_risk >
  low_liquidity.
* Severity — BLOCK (1~2) blocks EXIT too; BLOCK_NEW_ENTRY (3~8) allows EXIT
  with holding position.
* Per-entry risk_flags handling (case-insensitive, alias normalization).
* RiskOfficer reject lookup by (strategy, symbol).
* Global veto vs per-entry veto.
* Result invariants (is_order_signal / auto_apply / live_authz all False).
* Static import guard — no broker / OrderExecutor / route_order / AI SDK /
  external HTTP / DB write.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.agents.paper_start_explanation import (
    ExplanationVerdict,
    PaperStartExplanation,
    StrategyExplanation,
)
from app.auto_paper.risk_veto import (
    RISK_VETO_SCHEMA_VERSION,
    RiskVetoDecision,
    RiskVetoReason,
    RiskVetoReport,
    RiskVetoSeverity,
    evaluate_risk_veto,
)


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "risk_veto.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — build PaperStartExplanation fixtures directly (bypass 4-05 logic).
# ─────────────────────────────────────────────────────────────────────────────


def _exp(strategy: str, symbol: str, *,
         bucket: str = "recommended",
         risk_flags: list[str] | None = None,
         overfit_verdict: str | None = None) -> StrategyExplanation:
    return StrategyExplanation(
        strategy=strategy, symbol=symbol,
        bucket=bucket,
        paper_candidate_status="READY_FOR_PAPER",
        rationale_lines=["test rationale"],
        risk_flags=list(risk_flags or []),
        overfit_verdict=overfit_verdict,
    )


def _explanation(*, verdict=ExplanationVerdict.READY_TO_REVIEW,
                 recommended=None, watchlist=None, excluded=None,
                 market_regime="TREND_UP"):
    return PaperStartExplanation(
        generated_at="2026-05-18T00:00:00+00:00",
        schema_version="1.0",
        verdict=verdict,
        recommended_explanations=list(recommended or []),
        watchlist_explanations=list(watchlist or []),
        excluded_explanations=list(excluded or []),
        market_regime=market_regime,
        regime_confidence=0.8,
        regime_reasons=[],
        regime_risk_flags=[],
        regime_allowed_tactics=[],
        regime_blocked_tactics=[],
        overfit_count=0,
        overfit_strategies=[],
        headline="test",
        risk_summary=[],
        operator_note="",
        next_actions=[],
        can_start_paper=True,
        blocking_reasons=[],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Schema invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestSchemaInvariants:

    def test_decision_invariants_default_false(self):
        d = RiskVetoDecision(
            strategy="s", symbol="x", vetoed=False,
        )
        assert d.is_order_signal is False
        assert d.auto_apply_allowed is False
        assert d.is_live_authorization is False
        assert d.severity == RiskVetoSeverity.NONE

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_decision_invariant_violation_raises(self, override):
        base = dict(strategy="s", symbol="x", vetoed=False)
        base.update(override)
        with pytest.raises(ValueError):
            RiskVetoDecision(**base)

    def test_decision_vetoed_true_requires_severity(self):
        # vetoed=True with NONE severity is contradictory.
        with pytest.raises(ValueError):
            RiskVetoDecision(
                strategy="s", symbol="x", vetoed=True,
                reasons=[RiskVetoReason.STALE_DATA],
                severity=RiskVetoSeverity.NONE,
            )

    def test_decision_not_vetoed_with_reasons_raises(self):
        with pytest.raises(ValueError):
            RiskVetoDecision(
                strategy="s", symbol="x", vetoed=False,
                reasons=[RiskVetoReason.STALE_DATA],
                severity=RiskVetoSeverity.BLOCK_NEW_ENTRY,
            )

    def test_report_invariants_default_false(self):
        r = RiskVetoReport(
            generated_at="t", schema_version=RISK_VETO_SCHEMA_VERSION,
            loop_state="RUNNING", explanation_verdict="READY_TO_REVIEW",
        )
        assert r.is_order_signal is False
        assert r.auto_apply_allowed is False
        assert r.is_live_authorization is False

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_report_invariant_violation_raises(self, override):
        base = dict(
            generated_at="t", schema_version="1.0",
            loop_state="RUNNING", explanation_verdict="READY_TO_REVIEW",
        )
        base.update(override)
        with pytest.raises(ValueError):
            RiskVetoReport(**base)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Global veto — EMERGENCY_STOP, PRE_MARKET_BLOCK
# ─────────────────────────────────────────────────────────────────────────────


class TestGlobalVeto:

    def test_emergency_stop_global_veto(self):
        exp = _explanation(recommended=[_exp("sma_crossover", "005930")])
        r = evaluate_risk_veto(
            explanation=exp, loop_state="EMERGENCY_STOP",
        )
        assert r.has_global_veto is True
        assert RiskVetoReason.EMERGENCY_STOP in r.global_veto_reasons
        assert r.global_severity == RiskVetoSeverity.BLOCK
        # 모든 entry decision 도 vetoed.
        for d in r.decisions:
            assert d.vetoed is True
            assert RiskVetoReason.EMERGENCY_STOP in d.reasons
            assert d.severity == RiskVetoSeverity.BLOCK

    def test_pre_market_block_global_veto(self):
        exp = _explanation(
            verdict=ExplanationVerdict.DO_NOT_START,
            recommended=[_exp("sma_crossover", "005930")],
        )
        r = evaluate_risk_veto(explanation=exp, loop_state="RUNNING")
        assert r.has_global_veto is True
        assert RiskVetoReason.PRE_MARKET_BLOCK in r.global_veto_reasons
        assert r.global_severity == RiskVetoSeverity.BLOCK

    def test_emergency_stop_combined_with_pre_market(self):
        exp = _explanation(
            verdict=ExplanationVerdict.DO_NOT_START,
            recommended=[_exp("sma_crossover", "005930")],
        )
        r = evaluate_risk_veto(
            explanation=exp, loop_state="EMERGENCY_STOP",
        )
        assert RiskVetoReason.EMERGENCY_STOP == r.global_veto_reasons[0]
        assert RiskVetoReason.PRE_MARKET_BLOCK in r.global_veto_reasons
        assert r.global_severity == RiskVetoSeverity.BLOCK

    def test_no_global_veto_when_running_and_ready(self):
        exp = _explanation(recommended=[_exp("sma_crossover", "005930")])
        r = evaluate_risk_veto(explanation=exp, loop_state="RUNNING")
        assert r.has_global_veto is False
        assert r.global_severity == RiskVetoSeverity.NONE


# ─────────────────────────────────────────────────────────────────────────────
# 3. RiskOfficer REJECT
# ─────────────────────────────────────────────────────────────────────────────


class TestRiskOfficerReject:

    def test_reject_creates_per_entry_veto(self):
        exp = _explanation(recommended=[_exp("sma_crossover", "005930")])
        r = evaluate_risk_veto(
            explanation=exp, loop_state="RUNNING",
            risk_officer_rejects={
                ("sma_crossover", "005930"): "낮은 신뢰도 + 변동성 급증",
            },
        )
        d = r.decisions[0]
        assert d.vetoed is True
        assert RiskVetoReason.RISK_OFFICER_REJECT in d.reasons
        # BLOCK_NEW_ENTRY — EXIT 은 보유 시 허용.
        assert d.severity == RiskVetoSeverity.BLOCK_NEW_ENTRY
        assert d.allow_exit_if_holding is True

    def test_reject_only_applies_to_specified_key(self):
        exp = _explanation(recommended=[
            _exp("sma_crossover", "005930"),
            _exp("rsi_reversion", "035720"),
        ])
        r = evaluate_risk_veto(
            explanation=exp, loop_state="RUNNING",
            risk_officer_rejects={
                ("sma_crossover", "005930"): "rejected",
            },
        )
        by_key = {(d.strategy, d.symbol): d for d in r.decisions}
        assert by_key[("sma_crossover", "005930")].vetoed is True
        assert by_key[("rsi_reversion", "035720")].vetoed is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. risk_flags from StrategyExplanation
# ─────────────────────────────────────────────────────────────────────────────


class TestRiskFlags:

    @pytest.mark.parametrize("flag, expected", [
        ("stale_data",       RiskVetoReason.STALE_DATA),
        ("STALE_DATA",       RiskVetoReason.STALE_DATA),
        ("stale",            RiskVetoReason.STALE_DATA),
        ("duplicate_signal", RiskVetoReason.DUPLICATE_SIGNAL),
        ("high_correlation", RiskVetoReason.HIGH_CORRELATION),
        ("overfit_risk",     RiskVetoReason.OVERFIT_RISK),
        ("low_liquidity",    RiskVetoReason.LOW_LIQUIDITY),
        ("low-liquidity",    RiskVetoReason.LOW_LIQUIDITY),
    ])
    def test_flag_alias_normalized(self, flag, expected):
        exp = _explanation(recommended=[
            _exp("s", "x", risk_flags=[flag]),
        ])
        r = evaluate_risk_veto(explanation=exp, loop_state="RUNNING")
        assert expected in r.decisions[0].reasons
        assert r.decisions[0].severity == RiskVetoSeverity.BLOCK_NEW_ENTRY

    def test_unknown_flag_ignored(self):
        exp = _explanation(recommended=[
            _exp("s", "x", risk_flags=["unrelated_marketing_signal"]),
        ])
        r = evaluate_risk_veto(explanation=exp, loop_state="RUNNING")
        assert r.decisions[0].vetoed is False

    def test_overfit_verdict_carries_to_reason(self):
        exp = _explanation(recommended=[
            _exp("s", "x", overfit_verdict="OVERFIT_RISK"),
        ])
        r = evaluate_risk_veto(explanation=exp, loop_state="RUNNING")
        d = r.decisions[0]
        assert d.vetoed is True
        assert RiskVetoReason.OVERFIT_RISK in d.reasons

    def test_extra_risk_flags_param(self):
        # caller can carry extra flags (e.g. KIS stale-data detection result).
        exp = _explanation(recommended=[_exp("s", "x")])
        r = evaluate_risk_veto(
            explanation=exp, loop_state="RUNNING",
            extra_risk_flags={("s", "x"): ["stale_data", "high_correlation"]},
        )
        d = r.decisions[0]
        assert d.vetoed is True
        assert RiskVetoReason.STALE_DATA in d.reasons
        assert RiskVetoReason.HIGH_CORRELATION in d.reasons


# ─────────────────────────────────────────────────────────────────────────────
# 5. Priority order
# ─────────────────────────────────────────────────────────────────────────────


class TestPriority:

    def test_emergency_first_in_reasons(self):
        exp = _explanation(
            verdict=ExplanationVerdict.DO_NOT_START,
            recommended=[_exp("s", "x", risk_flags=["stale_data"])],
        )
        r = evaluate_risk_veto(
            explanation=exp, loop_state="EMERGENCY_STOP",
            risk_officer_rejects={("s", "x"): "rejected"},
        )
        d = r.decisions[0]
        # Priority order should put EMERGENCY_STOP first.
        assert d.reasons[0] == RiskVetoReason.EMERGENCY_STOP
        # PRE_MARKET_BLOCK second.
        assert d.reasons[1] == RiskVetoReason.PRE_MARKET_BLOCK

    def test_severity_emergency_overrides(self):
        # stale_data alone → BLOCK_NEW_ENTRY (EXIT allowed when holding).
        # adding EMERGENCY_STOP → BLOCK (no EXIT).
        exp = _explanation(recommended=[
            _exp("s", "x", risk_flags=["stale_data"]),
        ])
        r = evaluate_risk_veto(
            explanation=exp, loop_state="EMERGENCY_STOP",
        )
        d = r.decisions[0]
        assert d.severity == RiskVetoSeverity.BLOCK
        assert d.allow_exit_if_holding is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. Summary + headline
# ─────────────────────────────────────────────────────────────────────────────


class TestSummary:

    def test_summary_counts_by_reason(self):
        exp = _explanation(recommended=[
            _exp("s1", "x1", risk_flags=["stale_data"]),
            _exp("s2", "x2", risk_flags=["stale_data", "high_correlation"]),
            _exp("s3", "x3"),
        ])
        r = evaluate_risk_veto(explanation=exp, loop_state="RUNNING")
        assert r.summary.get("STALE_DATA") == 2
        assert r.summary.get("HIGH_CORRELATION") == 1
        # s3 is not vetoed.
        not_vetoed = [d for d in r.decisions if not d.vetoed]
        assert len(not_vetoed) == 1

    def test_headline_when_global_veto(self):
        exp = _explanation(recommended=[_exp("s", "x")])
        r = evaluate_risk_veto(explanation=exp, loop_state="EMERGENCY_STOP")
        assert "Risk veto 우선" in r.headline
        assert "긴급정지" in r.headline

    def test_headline_when_per_entry_veto(self):
        exp = _explanation(recommended=[
            _exp("s1", "x1", risk_flags=["stale_data"]),
        ])
        r = evaluate_risk_veto(explanation=exp, loop_state="RUNNING")
        assert "Risk veto 우선" in r.headline

    def test_headline_when_no_veto(self):
        exp = _explanation(recommended=[_exp("s", "x")])
        r = evaluate_risk_veto(explanation=exp, loop_state="RUNNING")
        assert "Risk veto 없음" in r.headline


# ─────────────────────────────────────────────────────────────────────────────
# 7. to_dict serialization
# ─────────────────────────────────────────────────────────────────────────────


class TestSerialization:

    def test_decision_to_dict_invariants(self):
        exp = _explanation(recommended=[
            _exp("s", "x", risk_flags=["stale_data"]),
        ])
        r = evaluate_risk_veto(explanation=exp, loop_state="RUNNING")
        d = r.decisions[0].to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False
        assert d["vetoed"] is True
        assert "STALE_DATA" in d["reasons"]
        assert len(d["reasons_label_ko"]) >= 1

    def test_report_to_dict_invariants(self):
        exp = _explanation(recommended=[_exp("s", "x")])
        r = evaluate_risk_veto(
            explanation=exp, loop_state="EMERGENCY_STOP",
        )
        d = r.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False
        assert d["has_global_veto"] is True
        assert "EMERGENCY_STOP" in d["global_veto_reasons"]
        assert d["global_severity"] == "BLOCK"

    def test_no_secret_fields_in_schema(self):
        forbidden = {
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_number", "kis_app_key", "kis_app_secret",
            "anthropic_api_key", "openai_api_key",
        }
        for name in RiskVetoDecision.__dataclass_fields__:
            assert name.lower() not in forbidden
        for name in RiskVetoReport.__dataclass_fields__:
            assert name.lower() not in forbidden


# ─────────────────────────────────────────────────────────────────────────────
# 8. Static guards
# ─────────────────────────────────────────────────────────────────────────────


_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "app.brokers.kis",
    "app.brokers.mock_broker",
    "app.execution.order_router",
    "app.execution.executor",
    "app.execution.order_executor",
    "app.permission.gate",
    "app.ai.assist",
    "app.ai.client",
    "anthropic",
    "openai",
    "httpx",
    "requests",
)


_FORBIDDEN_CALL_SUBSTRINGS = (
    "broker.place_order",
    "broker.cancel_order",
    "route_order(",
    "OrderExecutor",
    "OrderRequest",
)


class TestStaticGuards:

    def _source(self) -> str:
        return _MODULE_PATH.read_text(encoding="utf-8")

    def test_no_forbidden_imports(self):
        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name or ""
                    for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                        assert bad not in name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                    assert bad not in module

    def test_no_forbidden_calls(self):
        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = ast.unparse(node.func)
                for bad in _FORBIDDEN_CALL_SUBSTRINGS:
                    assert bad not in callee

    def test_no_db_write_surface(self):
        src = self._source()
        for bad in ("session.commit", "session.add", "session.delete",
                    "db.commit(", "db.add(", "db.delete("):
            assert bad not in src

    def test_no_settings_mutation(self):
        import re
        src = self._source()
        assert not re.search(r"settings\.enable_[a-z_]+\s*=", src)
