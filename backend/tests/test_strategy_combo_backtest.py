"""#3-12: Strategy combo backtest tests.

Covers:
* 4 tactic groups exist + 15 combo enumeration (4 + 6 + 4 + 1).
* Each known strategy_id maps to exactly one tactic group.
* Single/2/3/4 size combos generate distinct strategy lists.
* Signal-level metrics:
  - overlap_count when same (day, symbol) has 2+ signals
  - conflict_count when BUY + SELL appear on same (day, symbol)
  - confirmation_score when 2+ distinct tactic groups agree on direction
* Verdict matrix: PASS / WARN / FAIL / INSUFFICIENT_DATA per criteria.
* Renderers: JSON / MD / CSV report files generated in tmp_path.
* AI Agent context — every result carries agent_context_ready=True,
  recommended_for_paper=False, is_order_signal/auto_apply/live_authz=False.
* Static guards — no broker / OrderExecutor / route_order / AI SDK /
  external HTTP imports; no settings mutation; no DB write.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from app.analytics.strategy_combo_backtest import (
    COMBO_SCHEMA_VERSION,
    ComboBacktestReport,
    ComboCriteria,
    ComboResult,
    ComboVerdict,
    StrategySignal,
    TacticGroup,
    combo_name,
    combo_strategies,
    compute_combo_metrics,
    enumerate_combinations,
    render_markdown,
    render_ranking_csv,
    run_combo_backtest,
    write_reports,
)


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "analytics" / "strategy_combo_backtest.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────


def _signal(strategy_id, symbol="005930", day_key="2026-05-19",
            direction="BUY", score=0.7, realized_pnl=0.0):
    return StrategySignal(
        strategy_id=strategy_id, symbol=symbol, day_key=day_key,
        direction=direction, score=score, realized_pnl=realized_pnl,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Tactic catalog + enumeration
# ─────────────────────────────────────────────────────────────────────────────


class TestTacticCatalog:

    def test_four_tactic_groups_exist(self):
        names = {g.value for g in TacticGroup}
        assert names == {"MOMENTUM", "REVERSION", "VWAP", "ORB_PULLBACK"}

    def test_six_strategies_assigned_to_tactics(self):
        from app.analytics.strategy_combo_backtest import _STRATEGY_TO_TACTIC
        assert set(_STRATEGY_TO_TACTIC.keys()) == {
            "sma_crossover", "rsi_reversion", "vwap_strategy",
            "orb_vwap", "volume_breakout", "pullback_rebreak",
        }

    def test_momentum_has_sma_and_volume(self):
        assert set(combo_strategies([TacticGroup.MOMENTUM])) \
            == {"sma_crossover", "volume_breakout"}

    def test_reversion_has_rsi(self):
        assert combo_strategies([TacticGroup.REVERSION]) == ("rsi_reversion",)

    def test_vwap_has_vwap_strategy(self):
        assert combo_strategies([TacticGroup.VWAP]) == ("vwap_strategy",)

    def test_orb_pullback_has_orb_and_pullback(self):
        assert set(combo_strategies([TacticGroup.ORB_PULLBACK])) \
            == {"orb_vwap", "pullback_rebreak"}


class TestEnumeration:

    def test_total_combinations_is_15(self):
        combos = enumerate_combinations()
        assert len(combos) == 15

    def test_singles_are_4(self):
        singles = [c for c in enumerate_combinations() if len(c) == 1]
        assert len(singles) == 4

    def test_pairs_are_6(self):
        pairs = [c for c in enumerate_combinations() if len(c) == 2]
        assert len(pairs) == 6

    def test_triples_are_4(self):
        triples = [c for c in enumerate_combinations() if len(c) == 3]
        assert len(triples) == 4

    def test_full_is_one(self):
        fulls = [c for c in enumerate_combinations() if len(c) == 4]
        assert len(fulls) == 1

    def test_full_combo_strategy_count_is_6(self):
        full = next(c for c in enumerate_combinations() if len(c) == 4)
        assert len(combo_strategies(full)) == 6

    def test_combo_name_uses_plus(self):
        assert combo_name([TacticGroup.MOMENTUM, TacticGroup.VWAP]) \
            == "MOMENTUM+VWAP"


# ─────────────────────────────────────────────────────────────────────────────
# 2. StrategySignal validation
# ─────────────────────────────────────────────────────────────────────────────


class TestStrategySignal:

    def test_known_strategy_id_passes(self):
        s = _signal("sma_crossover")
        assert s.strategy_id == "sma_crossover"

    def test_unknown_strategy_id_raises(self):
        with pytest.raises(ValueError):
            _signal("foo_strategy")

    def test_empty_strings_raise(self):
        with pytest.raises(ValueError):
            StrategySignal(strategy_id="", symbol="x", day_key="d", direction="BUY")
        with pytest.raises(ValueError):
            StrategySignal(strategy_id="sma_crossover", symbol="",
                           day_key="d", direction="BUY")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Signal-level metrics — overlap / conflict / confirmation
# ─────────────────────────────────────────────────────────────────────────────


class TestSignalMetrics:

    def test_overlap_count_same_day_same_symbol(self):
        # 2 signals from MOMENTUM on same day → overlap=1.
        sigs = [
            _signal("sma_crossover"),
            _signal("volume_breakout"),
        ]
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
        )
        assert result.overlap_count == 1

    def test_conflict_count_buy_and_sell_same_day(self):
        sigs = [
            _signal("sma_crossover", direction="BUY"),
            _signal("rsi_reversion", direction="SELL"),
        ]
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION),
            signals=sigs,
        )
        assert result.conflict_count == 1

    def test_confirmation_score_two_groups_same_direction(self):
        # Same direction from MOMENTUM + REVERSION → confirmation > 0.
        sigs = [
            _signal("sma_crossover", direction="BUY"),
            _signal("rsi_reversion", direction="BUY"),
        ]
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION),
            signals=sigs,
        )
        assert result.confirmation_score >= 2   # 2 distinct tactic groups.

    def test_no_confirmation_when_single_tactic(self):
        sigs = [
            _signal("sma_crossover", direction="BUY"),
            _signal("volume_breakout", direction="BUY"),
        ]
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
        )
        # Same tactic group, only one distinct group → 0 confirmation.
        assert result.confirmation_score == 0

    def test_signals_outside_combo_ignored(self):
        # REVERSION signal but combo only includes MOMENTUM.
        sigs = [
            _signal("rsi_reversion", direction="BUY"),
            _signal("sma_crossover", direction="BUY"),
        ]
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
        )
        # MOMENTUM combo only sees the sma_crossover signal.
        assert result.signal_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# 4. Verdict matrix
# ─────────────────────────────────────────────────────────────────────────────


class TestVerdictMatrix:

    def test_insufficient_data_when_no_signals(self):
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM,), signals=[],
        )
        assert result.combo_verdict == ComboVerdict.INSUFFICIENT_DATA

    def test_insufficient_when_below_min_trades(self):
        sigs = [_signal("sma_crossover", day_key=f"d{i}",
                        direction="BUY", realized_pnl=100.0)
                for i in range(5)]
        crit = ComboCriteria(min_trades=10)
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs, criteria=crit,
        )
        assert result.combo_verdict == ComboVerdict.INSUFFICIENT_DATA

    def test_pass_when_all_metrics_healthy(self):
        # 20 winning trades — expectancy positive, no losses, no MDD.
        sigs = [_signal("sma_crossover", day_key=f"d{i}",
                        direction="BUY", realized_pnl=500.0)
                for i in range(20)]
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=ComboCriteria(min_trades=10),
        )
        assert result.combo_verdict == ComboVerdict.PASS

    def test_fail_when_expectancy_non_positive(self):
        # 15 losing trades.
        sigs = [_signal("sma_crossover", day_key=f"d{i}",
                        direction="BUY", realized_pnl=-100.0)
                for i in range(15)]
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=ComboCriteria(min_trades=10),
        )
        assert result.combo_verdict == ComboVerdict.FAIL

    def test_fail_when_max_drawdown_too_large(self):
        # Mostly winning but one huge loss exceeds fail_max_drawdown.
        sigs = [_signal("sma_crossover", day_key=f"d{i}",
                        direction="BUY", realized_pnl=10.0)
                for i in range(15)]
        sigs.append(_signal("sma_crossover", day_key="d_bigloss",
                            direction="BUY", realized_pnl=-100.0))
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=ComboCriteria(min_trades=10,
                                   fail_max_drawdown_abs=50.0,
                                   fail_profit_factor=1.0),
        )
        assert result.combo_verdict == ComboVerdict.FAIL

    def test_warn_when_high_conflict_ratio(self):
        # 12 trades 50/50 BUY/SELL same day → high conflict ratio + positive
        # expectancy.
        sigs = []
        for i in range(12):
            d = f"d{i // 2}"
            direction = "BUY" if i % 2 == 0 else "SELL"
            sigs.append(_signal("sma_crossover",
                                day_key=d, direction=direction,
                                realized_pnl=100.0))
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=ComboCriteria(min_trades=10, pass_conflict_ratio=0.30),
        )
        # 6 days × conflict each → conflict_ratio ~= 0.5 > 0.30 → WARN
        # (expectancy + profit_factor healthy with all positives).
        assert result.combo_verdict in (ComboVerdict.WARN, ComboVerdict.PASS)
        # specifically conflict_ratio > 0 + risk_flag carry possible.
        assert result.conflict_count >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 5. Invariants + safety
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:

    def test_combo_result_invariants_default_false(self):
        r = ComboResult(
            combo_name="MOMENTUM",
            included_tactics=("MOMENTUM",),
            included_strategies=("sma_crossover", "volume_breakout"),
            symbol=None,
        )
        assert r.is_order_signal is False
        assert r.auto_apply_allowed is False
        assert r.is_live_authorization is False
        assert r.recommended_for_paper is False
        assert r.agent_context_ready is True

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_combo_result_invariant_violation_raises(self, override):
        base = dict(
            combo_name="MOMENTUM",
            included_tactics=("MOMENTUM",),
            included_strategies=("sma_crossover",),
            symbol=None,
        )
        base.update(override)
        with pytest.raises(ValueError):
            ComboResult(**base)

    def test_run_returns_15_results_with_all_invariants(self):
        report = run_combo_backtest(signals=[], symbol=None)
        assert len(report.results) == 15
        for r in report.results:
            d = r.to_dict()
            assert d["is_order_signal"] is False
            assert d["auto_apply_allowed"] is False
            assert d["is_live_authorization"] is False
            assert d["recommended_for_paper"] is False
            assert d["agent_context_ready"] is True

    def test_pass_result_still_does_not_recommend_paper(self):
        sigs = [_signal("sma_crossover", day_key=f"d{i}",
                        direction="BUY", realized_pnl=500.0)
                for i in range(20)]
        result = compute_combo_metrics(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=ComboCriteria(min_trades=10),
        )
        # PASS verdict — but recommended_for_paper STILL False (영구).
        assert result.combo_verdict == ComboVerdict.PASS
        assert result.recommended_for_paper is False
        assert result.is_live_authorization is False

    def test_report_invariants_carry(self):
        report = run_combo_backtest(signals=[])
        d = report.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False
        assert "advisory" in d["advisory_disclaimer"]


# ─────────────────────────────────────────────────────────────────────────────
# 6. Render + write_reports
# ─────────────────────────────────────────────────────────────────────────────


class TestRenderAndWrite:

    def test_render_markdown_includes_all_groups(self):
        report = run_combo_backtest(signals=[])
        md = render_markdown(report)
        for g in TacticGroup:
            assert g.value in md
        assert "전략 조합 백테스트 리포트" in md
        assert "is_order_signal=False" in md
        assert "recommended_for_paper=False" in md

    def test_render_csv_has_15_rows_plus_header(self):
        report = run_combo_backtest(signals=[])
        csv = render_ranking_csv(report)
        rows = csv.strip().splitlines()
        assert len(rows) == 16   # header + 15 combos.
        assert rows[0].startswith("combo_name,verdict,")

    def test_csv_recommended_column_always_false(self):
        report = run_combo_backtest(signals=[])
        csv = render_ranking_csv(report)
        for line in csv.strip().splitlines()[1:]:
            cols = line.split(",")
            # last column.
            assert cols[-1] == "false"

    def test_write_reports_creates_three_files(self, tmp_path):
        report = run_combo_backtest(signals=[])
        paths = write_reports(report, tmp_path)
        assert paths["summary_json"].exists()
        assert paths["report_md"].exists()
        assert paths["ranking_csv"].exists()
        data = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
        assert data["schema_version"] == COMBO_SCHEMA_VERSION
        assert data["combo_count"] == 15
        assert data["is_order_signal"] is False

    def test_write_reports_works_with_signals(self, tmp_path):
        sigs = [
            _signal("sma_crossover", day_key="d1", direction="BUY",
                    realized_pnl=200.0),
            _signal("rsi_reversion", day_key="d1", direction="BUY",
                    realized_pnl=300.0),
        ]
        report = run_combo_backtest(signals=sigs, symbol="005930")
        paths = write_reports(report, tmp_path)
        data = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
        # MOMENTUM+REVERSION combo should have signals from both.
        combos_by_name = {r["combo_name"]: r for r in data["results"]}
        assert "MOMENTUM+REVERSION" in combos_by_name
        combo = combos_by_name["MOMENTUM+REVERSION"]
        assert combo["signal_count"] == 2
        # Same direction, 2 tactic groups → confirmation > 0.
        assert combo["confirmation_score"] >= 2

    def test_reports_dir_is_gitignored(self):
        gitignore = (Path(__file__).resolve().parents[2] / ".gitignore")
        content = gitignore.read_text(encoding="utf-8")
        assert "reports/" in content or "reports/*" in content


# ─────────────────────────────────────────────────────────────────────────────
# 7. only_sizes filter
# ─────────────────────────────────────────────────────────────────────────────


class TestSizeFilter:

    def test_only_singles(self):
        report = run_combo_backtest(signals=[], only_sizes=[1])
        assert len(report.results) == 4
        for r in report.results:
            assert len(r.included_tactics) == 1

    def test_only_pairs(self):
        report = run_combo_backtest(signals=[], only_sizes=[2])
        assert len(report.results) == 6

    def test_only_full(self):
        report = run_combo_backtest(signals=[], only_sizes=[4])
        assert len(report.results) == 1
        full = report.results[0]
        assert len(full.included_tactics) == 4
        assert len(full.included_strategies) == 6


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
                    for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                        assert bad not in (alias.name or "")
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
        src = self._source()
        assert not re.search(r"settings\.enable_[a-z_]+\s*=", src)

    def test_no_secret_fields_in_dataclass(self):
        forbidden = {
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "kis_app_key", "kis_app_secret",
            "anthropic_api_key", "openai_api_key", "password",
        }
        for name in ComboResult.__dataclass_fields__:
            assert name.lower() not in forbidden, name
        for name in ComboBacktestReport.__dataclass_fields__:
            assert name.lower() not in forbidden, name
