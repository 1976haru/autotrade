"""#3-13: Regime × Combo backtest tests.

Covers:
* 7 regime × 15 combo = 105 row output.
* TREND_UP: MOMENTUM / ORB_PULLBACK 조합 우대 (PASS 가능).
* SIDEWAYS: REVERSION / VWAP 조합 우대 — momentum 만으로 구성된 조합은
  BLOCKED_REGIME (volume_breakout 차단 정책 + sma_crossover watchlist).
* HIGH_VOLATILITY: 어떤 조합이 metric 우위라도 WATCH (risk_flag 부여).
* LOW_LIQUIDITY: 모든 15 조합 BLOCKED_REGIME.
* UNKNOWN: 모든 조합 BLOCKED_REGIME + recommended 0건 (영구 invariant).
* CHOPPY: WATCH 권고.
* Invariants — is_order_signal / auto_apply / is_live_authorization /
  recommended_for_paper all False (PASS 라벨에도).
* Report file generation (JSON/MD/CSV) in tmp_path.
* Static guards.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from app.agents.market_regime_agent import MarketRegime
from app.analytics.regime_combo_backtest import (
    REGIME_COMBO_SCHEMA_VERSION,
    RegimeComboBacktestReport,
    RegimeComboResult,
    RegimeComboVerdict,
    RegimeStrategySignal,
    render_markdown,
    render_ranking_csv,
    run_regime_combo_backtest,
    write_reports,
)
from app.analytics.strategy_combo_backtest import ComboCriteria


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "analytics" / "regime_combo_backtest.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _sig(strategy_id, *, regime=MarketRegime.TREND_UP, symbol="005930",
         day_key="d1", direction="BUY", pnl=200.0):
    return RegimeStrategySignal(
        strategy_id=strategy_id, symbol=symbol, day_key=day_key,
        direction=direction, regime=regime, realized_pnl=pnl,
    )


def _winning_signals(strategy_id, regime, count=20, day_offset=0, pnl=200.0):
    return [
        RegimeStrategySignal(
            strategy_id=strategy_id, symbol="005930",
            day_key=f"d{day_offset + i}", direction="BUY",
            regime=regime, realized_pnl=pnl,
        )
        for i in range(count)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Output shape — 7 × 15 = 105 rows
# ─────────────────────────────────────────────────────────────────────────────


class TestOutputShape:

    def test_empty_signals_produces_105_rows(self):
        report = run_regime_combo_backtest(signals=[])
        assert len(report.results) == 105   # 7 regime × 15 combo
        # 7 regime 모두 등장.
        regimes_seen = {r.regime for r in report.results}
        assert regimes_seen == {r.value for r in MarketRegime}

    def test_schema_version_present(self):
        report = run_regime_combo_backtest(signals=[])
        assert report.schema_version == REGIME_COMBO_SCHEMA_VERSION

    def test_recommended_by_regime_keyed_by_all_regimes(self):
        report = run_regime_combo_backtest(signals=[])
        assert set(report.recommended_by_regime.keys()) == {
            r.value for r in MarketRegime
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. UNKNOWN — 어떤 조합도 추천 0건 (영구)
# ─────────────────────────────────────────────────────────────────────────────


class TestUnknownRegime:

    def test_unknown_blocks_every_combo(self):
        sigs = _winning_signals("sma_crossover", MarketRegime.UNKNOWN, count=20)
        report = run_regime_combo_backtest(signals=sigs)
        unknown_rows = [r for r in report.results if r.regime == "UNKNOWN"]
        assert len(unknown_rows) == 15
        for r in unknown_rows:
            assert r.verdict == RegimeComboVerdict.BLOCKED_REGIME

    def test_unknown_recommendations_empty(self):
        report = run_regime_combo_backtest(signals=_winning_signals(
            "sma_crossover", MarketRegime.UNKNOWN, count=30,
        ))
        assert report.recommended_by_regime["UNKNOWN"] == []

    def test_report_construction_rejects_unknown_recommendation(self):
        with pytest.raises(ValueError):
            RegimeComboBacktestReport(
                generated_at="t", schema_version="1.0", symbol=None,
                results=[], criteria=ComboCriteria(),
                recommended_by_regime={"UNKNOWN": ["MOMENTUM"]},
            )


# ─────────────────────────────────────────────────────────────────────────────
# 3. LOW_LIQUIDITY — 모든 조합 BLOCKED_REGIME
# ─────────────────────────────────────────────────────────────────────────────


class TestLowLiquidity:

    def test_low_liquidity_blocks_every_combo(self):
        sigs = _winning_signals("rsi_reversion", MarketRegime.LOW_LIQUIDITY, count=20)
        report = run_regime_combo_backtest(signals=sigs)
        rows = [r for r in report.results if r.regime == "LOW_LIQUIDITY"]
        assert len(rows) == 15
        for r in rows:
            assert r.verdict == RegimeComboVerdict.BLOCKED_REGIME
            assert "regime_low_liquidity" in r.risk_flags

    def test_low_liquidity_recommendations_empty(self):
        sigs = _winning_signals("rsi_reversion", MarketRegime.LOW_LIQUIDITY, count=30)
        report = run_regime_combo_backtest(signals=sigs)
        assert report.recommended_by_regime["LOW_LIQUIDITY"] == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. TREND_UP — MOMENTUM / ORB_PULLBACK 우대
# ─────────────────────────────────────────────────────────────────────────────


class TestTrendUpFavored:

    def test_momentum_single_passes_in_trend_up(self):
        # MOMENTUM 만 — TREND_UP policy 의 preferred 안에 모두 있음.
        sigs = _winning_signals("sma_crossover", MarketRegime.TREND_UP, count=20)
        report = run_regime_combo_backtest(signals=sigs)
        momentum = next(r for r in report.results
                        if r.regime == "TREND_UP" and r.combo_name == "MOMENTUM")
        assert momentum.verdict == RegimeComboVerdict.PASS
        assert "MOMENTUM" in report.recommended_by_regime["TREND_UP"]

    def test_orb_pullback_single_passes_in_trend_up(self):
        sigs = _winning_signals("orb_vwap", MarketRegime.TREND_UP, count=20)
        report = run_regime_combo_backtest(signals=sigs)
        row = next(r for r in report.results
                   if r.regime == "TREND_UP" and r.combo_name == "ORB_PULLBACK")
        assert row.verdict == RegimeComboVerdict.PASS
        assert "ORB_PULLBACK" in report.recommended_by_regime["TREND_UP"]


# ─────────────────────────────────────────────────────────────────────────────
# 5. TREND_DOWN — momentum 차단 (volume_breakout / orb_vwap blocked)
# ─────────────────────────────────────────────────────────────────────────────


class TestTrendDownBlocks:

    def test_trend_down_blocks_momentum_full(self):
        # MOMENTUM 만 — volume_breakout 가 TREND_DOWN.blocked 에 있음.
        sigs = _winning_signals("sma_crossover", MarketRegime.TREND_DOWN, count=20)
        report = run_regime_combo_backtest(signals=sigs)
        row = next(r for r in report.results
                   if r.regime == "TREND_DOWN" and r.combo_name == "MOMENTUM")
        assert row.verdict == RegimeComboVerdict.BLOCKED_REGIME
        assert "volume_breakout" in row.blocked_strategies

    def test_trend_down_reversion_passes(self):
        # REVERSION — TREND_DOWN.watchlist 에 rsi_reversion (blocked 아님).
        sigs = _winning_signals("rsi_reversion", MarketRegime.TREND_DOWN, count=20)
        report = run_regime_combo_backtest(signals=sigs)
        row = next(r for r in report.results
                   if r.regime == "TREND_DOWN" and r.combo_name == "REVERSION")
        # blocked 아니므로 metric 기반 → PASS / WATCH 중 하나.
        assert row.verdict in (RegimeComboVerdict.PASS, RegimeComboVerdict.WATCH)


# ─────────────────────────────────────────────────────────────────────────────
# 6. SIDEWAYS — REVERSION / VWAP 우대 (MOMENTUM 차단)
# ─────────────────────────────────────────────────────────────────────────────


class TestSidewaysFavored:

    def test_sideways_blocks_momentum(self):
        # MOMENTUM 만 — volume_breakout 가 SIDEWAYS.blocked 에 있음.
        sigs = _winning_signals("sma_crossover", MarketRegime.SIDEWAYS, count=20)
        report = run_regime_combo_backtest(signals=sigs)
        row = next(r for r in report.results
                   if r.regime == "SIDEWAYS" and r.combo_name == "MOMENTUM")
        assert row.verdict == RegimeComboVerdict.BLOCKED_REGIME

    def test_sideways_reversion_vwap_combo_passes(self):
        # REVERSION+VWAP — 둘 다 SIDEWAYS.preferred. blocked 0건.
        sigs = (
            _winning_signals("rsi_reversion", MarketRegime.SIDEWAYS,
                             count=12, day_offset=0)
            + _winning_signals("vwap_strategy", MarketRegime.SIDEWAYS,
                               count=12, day_offset=100)
        )
        report = run_regime_combo_backtest(signals=sigs)
        row = next(r for r in report.results
                   if r.regime == "SIDEWAYS"
                   and r.combo_name == "REVERSION+VWAP")
        assert row.verdict == RegimeComboVerdict.PASS
        assert "REVERSION+VWAP" in report.recommended_by_regime["SIDEWAYS"]


# ─────────────────────────────────────────────────────────────────────────────
# 7. HIGH_VOLATILITY — 위험 경고 (WATCH)
# ─────────────────────────────────────────────────────────────────────────────


class TestHighVolatilityWarn:

    def test_high_volatility_reversion_watch_or_blocked(self):
        # REVERSION — HIGH_VOLATILITY.watchlist 에 rsi_reversion (blocked 아님).
        sigs = _winning_signals("rsi_reversion", MarketRegime.HIGH_VOLATILITY,
                                count=20)
        report = run_regime_combo_backtest(signals=sigs)
        row = next(r for r in report.results
                   if r.regime == "HIGH_VOLATILITY"
                   and r.combo_name == "REVERSION")
        # HIGH_VOLATILITY 는 항상 WATCH 라벨 + size 축소 권고.
        assert row.verdict == RegimeComboVerdict.WATCH
        assert "regime_high_volatility" in row.risk_flags

    def test_high_volatility_momentum_blocked(self):
        # MOMENTUM 만 — volume_breakout 가 HIGH_VOLATILITY.blocked.
        sigs = _winning_signals("sma_crossover", MarketRegime.HIGH_VOLATILITY,
                                count=20)
        report = run_regime_combo_backtest(signals=sigs)
        row = next(r for r in report.results
                   if r.regime == "HIGH_VOLATILITY"
                   and r.combo_name == "MOMENTUM")
        assert row.verdict == RegimeComboVerdict.BLOCKED_REGIME


# ─────────────────────────────────────────────────────────────────────────────
# 8. CHOPPY — REVERSION watchlist + WATCH 라벨
# ─────────────────────────────────────────────────────────────────────────────


class TestChoppyWatch:

    def test_choppy_reversion_watch(self):
        sigs = _winning_signals("rsi_reversion", MarketRegime.CHOPPY, count=20)
        report = run_regime_combo_backtest(signals=sigs)
        row = next(r for r in report.results
                   if r.regime == "CHOPPY" and r.combo_name == "REVERSION")
        assert row.verdict == RegimeComboVerdict.WATCH
        assert "regime_choppy" in row.risk_flags


# ─────────────────────────────────────────────────────────────────────────────
# 9. Invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:

    def test_default_invariants_false(self):
        r = RegimeComboResult(
            regime="TREND_UP",
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
    def test_invariant_violation_raises(self, override):
        base = dict(
            regime="TREND_UP",
            combo_name="MOMENTUM",
            included_tactics=("MOMENTUM",),
            included_strategies=("sma_crossover",),
            symbol=None,
        )
        base.update(override)
        with pytest.raises(ValueError):
            RegimeComboResult(**base)

    def test_pass_still_does_not_recommend_paper(self):
        sigs = _winning_signals("sma_crossover", MarketRegime.TREND_UP, count=20)
        report = run_regime_combo_backtest(signals=sigs)
        pass_rows = [r for r in report.results
                     if r.verdict == RegimeComboVerdict.PASS]
        assert len(pass_rows) >= 1
        for r in pass_rows:
            assert r.recommended_for_paper is False
            assert r.is_live_authorization is False

    def test_to_dict_invariants(self):
        report = run_regime_combo_backtest(signals=[])
        d = report.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False
        assert "advisory" in d["advisory_disclaimer"]


# ─────────────────────────────────────────────────────────────────────────────
# 10. Report file generation
# ─────────────────────────────────────────────────────────────────────────────


class TestReportFiles:

    def test_three_files_generated(self, tmp_path):
        report = run_regime_combo_backtest(signals=[])
        paths = write_reports(report, tmp_path)
        for k in ("summary_json", "report_md", "ranking_csv"):
            assert paths[k].exists()

    def test_json_carries_invariants(self, tmp_path):
        report = run_regime_combo_backtest(signals=[])
        paths = write_reports(report, tmp_path)
        d = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
        assert d["schema_version"] == REGIME_COMBO_SCHEMA_VERSION
        assert d["row_count"] == 105
        assert d["is_order_signal"] is False
        assert d["recommended_by_regime"]["UNKNOWN"] == []

    def test_markdown_contains_regimes_and_safety_text(self, tmp_path):
        report = run_regime_combo_backtest(signals=[])
        paths = write_reports(report, tmp_path)
        md = paths["report_md"].read_text(encoding="utf-8")
        for r in MarketRegime:
            assert r.value in md
        assert "UNKNOWN" in md
        assert "LOW_LIQUIDITY" in md
        assert "advisory" in md.lower()
        assert "recommended_for_paper=False" in md

    def test_csv_has_header_and_105_rows(self, tmp_path):
        report = run_regime_combo_backtest(signals=[])
        paths = write_reports(report, tmp_path)
        csv = paths["ranking_csv"].read_text(encoding="utf-8").strip()
        rows = csv.splitlines()
        assert len(rows) == 106   # header + 105 rows.
        # 마지막 column 항상 'false'.
        for line in rows[1:]:
            assert line.split(",")[-1] == "false"

    def test_reports_dir_is_gitignored(self):
        gitignore = (Path(__file__).resolve().parents[2] / ".gitignore")
        content = gitignore.read_text(encoding="utf-8")
        assert "reports/" in content or "reports/*" in content


# ─────────────────────────────────────────────────────────────────────────────
# 11. RegimeStrategySignal validation
# ─────────────────────────────────────────────────────────────────────────────


class TestSignalValidation:

    def test_valid_signal_passes(self):
        s = _sig("sma_crossover", regime=MarketRegime.TREND_UP)
        assert s.regime == MarketRegime.TREND_UP

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError):
            RegimeStrategySignal(
                strategy_id="foo", symbol="x", day_key="d", direction="BUY",
                regime=MarketRegime.TREND_UP,
            )

    def test_string_regime_raises(self):
        with pytest.raises(ValueError):
            RegimeStrategySignal(
                strategy_id="sma_crossover", symbol="x", day_key="d",
                direction="BUY", regime="TREND_UP",   # str, not enum.
            )


# ─────────────────────────────────────────────────────────────────────────────
# 12. Static guards
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

    def test_no_db_write(self):
        src = self._source()
        for bad in ("session.commit", "session.add", "session.delete",
                    "db.commit(", "db.add(", "db.delete("):
            assert bad not in src

    def test_no_settings_mutation(self):
        src = self._source()
        assert not re.search(r"settings\.enable_[a-z_]+\s*=", src)

    def test_no_secret_fields(self):
        forbidden = {
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number",
        }
        for name in RegimeComboResult.__dataclass_fields__:
            assert name.lower() not in forbidden, name


# ─────────────────────────────────────────────────────────────────────────────
# 13. Rendering helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestRenderingHelpers:

    def test_render_markdown_returns_string(self):
        report = run_regime_combo_backtest(signals=[])
        md = render_markdown(report)
        assert "장세별 전략 조합 백테스트 리포트" in md

    def test_render_ranking_csv_returns_106_lines(self):
        report = run_regime_combo_backtest(signals=[])
        csv = render_ranking_csv(report)
        assert len(csv.strip().splitlines()) == 106
