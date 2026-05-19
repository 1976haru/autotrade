"""#3-14: combo correlation / overlap / concentration risk tests.

Covers:
* overlap / same_direction / conflict 카운트 + ratio.
* correlation_score / concentration_score / max_strategy_weight /
  max_symbol_weight.
* Verdict 매트릭스 PASS / WATCH / HIGH_RISK / BLOCK / INSUFFICIENT_DATA.
* 특정 전략 / 특정 종목 쏠림 감지 (concentration trigger).
* BLOCK verdict 발생 시 reason 등재.
* invariants (is_order_signal=False / auto_apply=False / live_authz=False /
  recommended_for_paper=False) — BLOCK 라벨에도 영구.
* Report 파일 (JSON/MD/CSV) 생성 (tmp_path) + 결과 invariant carry.
* Static guards.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from app.analytics.combo_correlation_risk import (
    COMBO_RISK_SCHEMA_VERSION,
    ComboRiskReport,
    ComboRiskResult,
    ComboRiskVerdict,
    RiskCriteria,
    compute_combo_risk,
    render_markdown,
    render_ranking_csv,
    run_combo_risk_analysis,
    write_reports,
)
from app.analytics.strategy_combo_backtest import (
    StrategySignal,
    TacticGroup,
)


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "analytics" / "combo_correlation_risk.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _sig(strategy_id, *, symbol="005930", day_key="d1",
         direction="BUY", pnl=100.0):
    return StrategySignal(
        strategy_id=strategy_id, symbol=symbol, day_key=day_key,
        direction=direction, realized_pnl=pnl,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Overlap 계산
# ─────────────────────────────────────────────────────────────────────────────


class TestOverlap:

    def test_no_overlap_single_signal(self):
        sigs = [_sig("sma_crossover")]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=RiskCriteria(min_signals=1),
        )
        assert result.overlap_count == 0
        assert result.overlap_ratio == 0.0

    def test_overlap_two_signals_same_day(self):
        # 같은 (day, symbol) 에 2 signal → overlap=1.
        sigs = [_sig("sma_crossover"), _sig("volume_breakout")]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=RiskCriteria(min_signals=1),
        )
        assert result.overlap_count == 1
        assert result.overlap_ratio == pytest.approx(1 / 2)

    def test_no_overlap_different_days(self):
        sigs = [_sig("sma_crossover", day_key="d1"),
                _sig("volume_breakout", day_key="d2")]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=RiskCriteria(min_signals=1),
        )
        assert result.overlap_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Same-direction 계산
# ─────────────────────────────────────────────────────────────────────────────


class TestSameDirection:

    def test_same_direction_two_tactic_groups(self):
        # MOMENTUM + REVERSION 같은 날 BUY → same_direction=1.
        sigs = [
            _sig("sma_crossover", direction="BUY"),
            _sig("rsi_reversion", direction="BUY"),
        ]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION),
            signals=sigs, criteria=RiskCriteria(min_signals=1),
        )
        assert result.same_direction_count == 1
        # unique_day_symbol=1 → ratio=1.
        assert result.same_direction_ratio == pytest.approx(1.0)

    def test_no_same_direction_when_single_tactic(self):
        # 같은 tactic group 의 2 signal → same_direction=0 (서로 다른 group 필요).
        sigs = [
            _sig("sma_crossover", direction="BUY"),
            _sig("volume_breakout", direction="BUY"),
        ]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=RiskCriteria(min_signals=1),
        )
        assert result.same_direction_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Conflict 계산
# ─────────────────────────────────────────────────────────────────────────────


class TestConflict:

    def test_conflict_buy_sell_same_day(self):
        sigs = [
            _sig("sma_crossover", direction="BUY"),
            _sig("rsi_reversion", direction="SELL"),
        ]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION),
            signals=sigs, criteria=RiskCriteria(min_signals=1),
        )
        assert result.conflict_count == 1
        assert result.conflict_ratio > 0

    def test_no_conflict_same_direction(self):
        sigs = [
            _sig("sma_crossover", direction="BUY"),
            _sig("rsi_reversion", direction="BUY"),
        ]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION),
            signals=sigs, criteria=RiskCriteria(min_signals=1),
        )
        assert result.conflict_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. Correlation score
# ─────────────────────────────────────────────────────────────────────────────


class TestCorrelationScore:

    def test_correlation_higher_when_same_direction_high(self):
        # 5 days, same direction BUY from 2 tactics.
        sigs = []
        for i in range(5):
            sigs.append(_sig("sma_crossover", day_key=f"d{i}", direction="BUY"))
            sigs.append(_sig("rsi_reversion", day_key=f"d{i}", direction="BUY"))
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION),
            signals=sigs, criteria=RiskCriteria(min_signals=1),
        )
        assert result.correlation_score > 0.5

    def test_correlation_low_when_distributed(self):
        # 5 days, all SELL — diversity 낮음, 같은 방향 일치.
        # tactic_count=4 (full) → diversity_penalty 강함 → 점수 낮춤.
        sigs = []
        all_strats = ["sma_crossover", "rsi_reversion",
                      "vwap_strategy", "orb_vwap"]
        for i in range(5):
            sigs.append(_sig(all_strats[i % 4], day_key=f"d{i}", direction="BUY"))
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION,
                     TacticGroup.VWAP, TacticGroup.ORB_PULLBACK),
            signals=sigs, criteria=RiskCriteria(min_signals=1),
        )
        # diversity_penalty = 1 - 0.45 = 0.55 → correlation_score 가 0.5 이하.
        assert result.correlation_score <= 0.5


# ─────────────────────────────────────────────────────────────────────────────
# 5. Concentration score — strategy / symbol 쏠림 감지
# ─────────────────────────────────────────────────────────────────────────────


class TestConcentration:

    def test_strategy_concentration_max(self):
        # 모든 signal sma_crossover → max_single_strategy_weight=1.0.
        sigs = [_sig("sma_crossover", day_key=f"d{i}") for i in range(10)]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=RiskCriteria(min_signals=1),
        )
        assert result.max_single_strategy_weight == 1.0
        assert result.concentration_score == 1.0

    def test_symbol_concentration_max(self):
        # 모든 signal 005930 (single symbol).
        sigs = [
            _sig("sma_crossover", symbol="005930", day_key=f"d{i}")
            for i in range(5)
        ] + [
            _sig("volume_breakout", symbol="005930", day_key=f"d{i+5}")
            for i in range(5)
        ]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=RiskCriteria(min_signals=1),
        )
        assert result.max_single_symbol_weight == 1.0
        assert result.concentration_score == 1.0

    def test_concentration_low_when_balanced(self):
        # 2 strategies × 2 symbols 균등 분포.
        sigs = []
        for i in range(4):
            sigs.append(_sig("sma_crossover",
                             symbol="005930", day_key=f"d{i}"))
            sigs.append(_sig("rsi_reversion",
                             symbol="035720", day_key=f"d{i+10}"))
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION),
            signals=sigs, criteria=RiskCriteria(min_signals=1),
        )
        # 각 strategy / symbol 비중 = 0.5.
        assert result.max_single_strategy_weight == pytest.approx(0.5)
        assert result.max_single_symbol_weight == pytest.approx(0.5)
        assert result.concentration_score == pytest.approx(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Verdict matrix
# ─────────────────────────────────────────────────────────────────────────────


class TestVerdictMatrix:

    def test_insufficient_data_when_signals_below_min(self):
        sigs = [_sig("sma_crossover") for _ in range(3)]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=RiskCriteria(min_signals=5),
        )
        assert result.risk_verdict == ComboRiskVerdict.INSUFFICIENT_DATA

    def test_block_when_extreme_concentration(self):
        # 단일 strategy + 단일 symbol → concentration=1.0 ≥ block(0.85).
        sigs = [_sig("sma_crossover", day_key=f"d{i}") for i in range(10)]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=RiskCriteria(min_signals=5),
        )
        assert result.risk_verdict == ComboRiskVerdict.BLOCK
        assert "extreme_concentration" in result.risk_flags

    def test_block_when_extreme_same_direction(self):
        # 10 days, MOMENTUM + REVERSION 둘 다 BUY → same_dir_ratio = 1.0.
        # 균형 분포로 concentration block 회피.
        sigs = []
        symbols = ["005930", "035720"]
        for i in range(10):
            sym = symbols[i % 2]
            sigs.append(_sig("sma_crossover", symbol=sym,
                             day_key=f"d{i}", direction="BUY"))
            sigs.append(_sig("rsi_reversion", symbol=sym,
                             day_key=f"d{i}", direction="BUY"))
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION),
            signals=sigs, criteria=RiskCriteria(
                min_signals=5, block_concentration=0.99,   # 회피
            ),
        )
        assert result.risk_verdict == ComboRiskVerdict.BLOCK
        assert "extreme_same_direction" in result.risk_flags

    def test_block_when_extreme_conflict(self):
        # 10 days BUY+SELL 동시 → conflict_ratio 0.5.
        # concentration 회피 위해 2 symbol 사용.
        sigs = []
        symbols = ["005930", "035720"]
        for i in range(10):
            sym = symbols[i % 2]
            sigs.append(_sig("sma_crossover", symbol=sym,
                             day_key=f"d{i}", direction="BUY"))
            sigs.append(_sig("rsi_reversion", symbol=sym,
                             day_key=f"d{i}", direction="SELL"))
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION),
            signals=sigs, criteria=RiskCriteria(
                min_signals=5,
                block_concentration=0.99,
                block_same_dir_ratio=0.99,
                block_conflict_ratio=0.30,    # 0.5 > 0.30 → BLOCK
            ),
        )
        assert result.risk_verdict == ComboRiskVerdict.BLOCK
        assert "extreme_conflict" in result.risk_flags

    def test_pass_when_balanced(self):
        # 12 signals, 4 strategies, 3 symbols, 다양한 day → concentration 낮음.
        sigs = []
        symbols = ["005930", "035720", "000660"]
        strats = ["sma_crossover", "rsi_reversion",
                  "vwap_strategy", "orb_vwap"]
        for i in range(12):
            sigs.append(_sig(
                strats[i % 4],
                symbol=symbols[i % 3],
                day_key=f"d{i}",
                direction="BUY" if i % 2 == 0 else "SELL",
            ))
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION,
                     TacticGroup.VWAP, TacticGroup.ORB_PULLBACK),
            signals=sigs, criteria=RiskCriteria(min_signals=5),
        )
        # 균등 분포 + 다양한 day_symbol → boundary 안.
        assert result.risk_verdict in (ComboRiskVerdict.PASS,
                                       ComboRiskVerdict.WATCH)

    def test_watch_when_boundary_overlap(self):
        # 8 signals, same day_symbol → overlap_ratio 높음 but concentration
        # 조절.
        sigs = []
        strats = ["sma_crossover", "rsi_reversion",
                  "vwap_strategy", "orb_vwap"]
        symbols = ["005930", "035720"]
        for i in range(8):
            sigs.append(_sig(
                strats[i % 4],
                symbol=symbols[i % 2],
                day_key="d0" if i < 4 else "d1",
                direction="BUY",
            ))
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM, TacticGroup.REVERSION,
                     TacticGroup.VWAP, TacticGroup.ORB_PULLBACK),
            signals=sigs,
            criteria=RiskCriteria(min_signals=5, watch_overlap_ratio=0.10),
        )
        # overlap_ratio 큼 → WATCH 또는 HIGH_RISK.
        assert result.risk_verdict in (
            ComboRiskVerdict.WATCH, ComboRiskVerdict.HIGH_RISK,
            ComboRiskVerdict.BLOCK,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7. Invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:

    def test_default_invariants_false(self):
        r = ComboRiskResult(
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
            combo_name="MOMENTUM",
            included_tactics=("MOMENTUM",),
            included_strategies=("sma_crossover",),
            symbol=None,
        )
        base.update(override)
        with pytest.raises(ValueError):
            ComboRiskResult(**base)

    def test_block_verdict_still_does_not_recommend_paper(self):
        sigs = [_sig("sma_crossover", day_key=f"d{i}") for i in range(10)]
        result = compute_combo_risk(
            tactics=(TacticGroup.MOMENTUM,), signals=sigs,
            criteria=RiskCriteria(min_signals=5),
        )
        # BLOCK verdict 발생 — recommended_for_paper 영구 False.
        assert result.risk_verdict == ComboRiskVerdict.BLOCK
        assert result.recommended_for_paper is False
        assert result.is_live_authorization is False

    def test_ratio_out_of_range_raises(self):
        with pytest.raises(ValueError):
            ComboRiskResult(
                combo_name="X",
                included_tactics=(),
                included_strategies=(),
                symbol=None,
                overlap_ratio=1.5,   # > 1.0
            )


# ─────────────────────────────────────────────────────────────────────────────
# 8. Report + run_combo_risk_analysis
# ─────────────────────────────────────────────────────────────────────────────


class TestReport:

    def test_run_returns_15_results(self):
        report = run_combo_risk_analysis(signals=[])
        assert len(report.results) == 15

    def test_report_to_dict_invariants(self):
        report = run_combo_risk_analysis(signals=[])
        d = report.to_dict()
        assert d["schema_version"] == COMBO_RISK_SCHEMA_VERSION
        assert d["is_order_signal"] is False
        assert "advisory" in d["advisory_disclaimer"]

    def test_only_sizes_filter(self):
        report = run_combo_risk_analysis(signals=[], only_sizes=[1])
        assert len(report.results) == 4

    def test_invalid_criteria_raises(self):
        with pytest.raises(ValueError):
            RiskCriteria(min_signals=0)
        with pytest.raises(ValueError):
            RiskCriteria(pass_overlap_ratio=1.5)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Render + write_reports
# ─────────────────────────────────────────────────────────────────────────────


class TestRenderAndWrite:

    def test_three_files_generated(self, tmp_path):
        report = run_combo_risk_analysis(signals=[])
        paths = write_reports(report, tmp_path)
        for k in ("summary_json", "report_md", "ranking_csv"):
            assert paths[k].exists()

    def test_json_carries_invariants(self, tmp_path):
        report = run_combo_risk_analysis(signals=[])
        paths = write_reports(report, tmp_path)
        d = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
        assert d["schema_version"] == COMBO_RISK_SCHEMA_VERSION
        assert d["combo_count"] == 15
        assert d["is_order_signal"] is False

    def test_markdown_carries_safety_text(self, tmp_path):
        report = run_combo_risk_analysis(signals=[])
        paths = write_reports(report, tmp_path)
        md = paths["report_md"].read_text(encoding="utf-8")
        assert "is_order_signal=False" in md
        assert "recommended_for_paper=False" in md
        assert "중복" in md or "쏠림" in md

    def test_csv_last_column_always_false(self, tmp_path):
        report = run_combo_risk_analysis(signals=[])
        paths = write_reports(report, tmp_path)
        csv = paths["ranking_csv"].read_text(encoding="utf-8")
        for line in csv.strip().splitlines()[1:]:
            assert line.split(",")[-1] == "false"

    def test_reports_dir_is_gitignored(self):
        gitignore = (Path(__file__).resolve().parents[2] / ".gitignore")
        content = gitignore.read_text(encoding="utf-8")
        assert "reports/" in content or "reports/*" in content


# ─────────────────────────────────────────────────────────────────────────────
# 10. Render helpers direct
# ─────────────────────────────────────────────────────────────────────────────


class TestRenderHelpers:

    def test_render_markdown_returns_korean(self):
        report = run_combo_risk_analysis(signals=[])
        md = render_markdown(report)
        assert "전략 조합 중복" in md

    def test_render_csv_has_15_rows_plus_header(self):
        report = run_combo_risk_analysis(signals=[])
        csv = render_ranking_csv(report)
        assert len(csv.strip().splitlines()) == 16


# ─────────────────────────────────────────────────────────────────────────────
# 11. Static guards
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
        for name in ComboRiskResult.__dataclass_fields__:
            assert name.lower() not in forbidden, name
        for name in ComboRiskReport.__dataclass_fields__:
            assert name.lower() not in forbidden, name
