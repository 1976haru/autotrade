"""#4-RiskProfileCompare: 성향별 Paper 결과 비교 리포트 테스트.

Covers:
* 3 프리셋(CONSERVATIVE / BALANCED / AGGRESSIVE) 모두 비교.
* BALANCED 가 기본 추천 (recommended_profile).
* AGGRESSIVE 도 `is_live_authorization=False` 영구 — safety guard 우회 불가.
* INSUFFICIENT_DATA — explanation 이 empty 일 때.
* 리포트 파일 3종 (JSON / MD / CSV) 생성 + 내용 validity.
* 실거래 호출 0건 — broker spy.
* secret 0건 — schema 에 api_key / account 필드 없음.
* 정적 가드 — broker / route_order / OrderExecutor / AI SDK import 없음.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.agents.paper_start_explanation import (
    ExplanationVerdict,
    PaperStartExplanation,
    StrategyExplanation,
)
from app.agents.risk_profile import RiskProfile
from app.analytics.risk_profile_comparison import (
    COMPARISON_SCHEMA_VERSION,
    ComparisonReport,
    ProfileResult,
    compare_profiles,
    render_markdown,
    render_ranking_csv,
    write_reports,
)
from app.auto_paper.ledger import reset_ledger_for_tests
from app.brokers.kis import KisBrokerAdapter


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "analytics" / "risk_profile_comparison.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_ledger():
    reset_ledger_for_tests()
    yield
    reset_ledger_for_tests()


@pytest.fixture
def kis_spy(monkeypatch):
    spy = MagicMock(side_effect=AssertionError(
        "place_order must not be called by comparison"
    ))
    monkeypatch.setattr(KisBrokerAdapter, "place_order", spy)
    cspy = MagicMock(side_effect=AssertionError(
        "cancel_order must not be called by comparison"
    ))
    monkeypatch.setattr(KisBrokerAdapter, "cancel_order", cspy)
    return spy, cspy


def _se(strategy, symbol, *, bucket="recommended", risk_flags=None,
        overfit_verdict=None):
    return StrategyExplanation(
        strategy=strategy, symbol=symbol,
        bucket=bucket,
        paper_candidate_status="READY_FOR_PAPER",
        rationale_lines=["test"],
        risk_flags=list(risk_flags or []),
        overfit_verdict=overfit_verdict,
    )


def _exp(*, recommended=None, watchlist=None, excluded=None,
         verdict=ExplanationVerdict.READY_TO_REVIEW,
         market_regime="TREND_UP"):
    return PaperStartExplanation(
        generated_at="2026-05-19T01:00:00+00:00",
        schema_version="1.0",
        verdict=verdict,
        recommended_explanations=list(recommended or []),
        watchlist_explanations=list(watchlist or []),
        excluded_explanations=list(excluded or []),
        market_regime=market_regime,
        regime_confidence=0.85,
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


def _clean_input():
    """1 clean recommended entry + price/equity/confidence/pnl."""
    return dict(
        explanation=_exp(recommended=[_se("sma_crossover", "005930")]),
        loop_state="RUNNING",
        positions=[],
        price_lookup={("sma_crossover", "005930"): 70_000.0},
        account_equity=10_000_000.0,
        confidence_lookup={("sma_crossover", "005930"): 0.90},
        pnl_lookup={("sma_crossover", "005930"): 250.0},  # +250 KRW / share
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. 3 프리셋 모두 비교됨
# ─────────────────────────────────────────────────────────────────────────────


class TestThreeProfilesCompared:

    def test_all_three_profiles_in_results(self, kis_spy):
        report = compare_profiles(**_clean_input())
        profiles = [r.profile for r in report.results]
        assert profiles == ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]

    def test_status_ok_when_entries_present(self, kis_spy):
        report = compare_profiles(**_clean_input())
        assert report.status == "OK"

    def test_schema_version_present(self, kis_spy):
        report = compare_profiles(**_clean_input())
        assert report.schema_version == COMPARISON_SCHEMA_VERSION

    def test_signal_count_consistent(self, kis_spy):
        report = compare_profiles(**_clean_input())
        # 1 recommended entry → signal_count=1 for every profile.
        for r in report.results:
            assert r.signal_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. BALANCED 가 기본 추천
# ─────────────────────────────────────────────────────────────────────────────


class TestBalancedDefault:

    def test_recommended_profile_is_balanced(self, kis_spy):
        report = compare_profiles(**_clean_input())
        assert report.recommended_profile == "BALANCED"

    def test_recommendation_reason_mentions_default(self, kis_spy):
        report = compare_profiles(**_clean_input())
        assert "기본 추천" in report.recommendation_reason
        assert "BALANCED" in report.recommendation_reason

    def test_balanced_default_even_when_aggressive_has_better_pnl(self, kis_spy):
        # 동일 입력 — AGGRESSIVE 가 자연히 더 큰 pnl 을 만들겠지만 본 모듈은
        # 그래도 BALANCED 를 추천.
        report = compare_profiles(**_clean_input())
        agg = next(r for r in report.results if r.profile == "AGGRESSIVE")
        bal = next(r for r in report.results if r.profile == "BALANCED")
        # AGGRESSIVE 가 더 큰 size → 더 큰 pnl_estimate (sanity check).
        assert agg.paper_pnl_estimate >= bal.paper_pnl_estimate
        # 그래도 추천은 BALANCED.
        assert report.recommended_profile == "BALANCED"


# ─────────────────────────────────────────────────────────────────────────────
# 3. AGGRESSIVE safety guard 우회 불가
# ─────────────────────────────────────────────────────────────────────────────


class TestAggressiveSafetyGuard:

    def test_aggressive_profile_result_invariants(self, kis_spy):
        report = compare_profiles(**_clean_input())
        agg = next(r for r in report.results if r.profile == "AGGRESSIVE")
        assert agg.is_order_signal is False
        assert agg.auto_apply_allowed is False
        assert agg.is_live_authorization is False

    def test_aggressive_to_dict_carries_invariants(self, kis_spy):
        report = compare_profiles(**_clean_input())
        agg = next(r for r in report.results if r.profile == "AGGRESSIVE")
        d = agg.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False

    def test_report_invariants_locked_for_all_profiles(self, kis_spy):
        report = compare_profiles(**_clean_input())
        d = report.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False
        # advisory disclaimer mentions AGGRESSIVE not bypassing safety.
        assert "AGGRESSIVE" in report.advisory_disclaimer
        assert "우회하지 않으며" in report.advisory_disclaimer

    def test_no_broker_calls_under_any_profile(self, kis_spy):
        compare_profiles(**_clean_input())
        spy, cspy = kis_spy
        assert spy.call_count == 0
        assert cspy.call_count == 0

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_profile_result_invariant_violation_raises(self, override):
        base = dict(profile="AGGRESSIVE")
        base.update(override)
        with pytest.raises(ValueError):
            ProfileResult(**base)

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_comparison_report_invariant_violation_raises(self, override):
        base = dict(
            generated_at="t", schema_version="1.0",
            status="OK", period_label="x",
            results=[ProfileResult(profile="BALANCED")],
            recommended_profile="BALANCED",
            recommendation_reason="r",
        )
        base.update(override)
        with pytest.raises(ValueError):
            ComparisonReport(**base)

    def test_recommended_profile_must_be_known(self):
        with pytest.raises(ValueError):
            ComparisonReport(
                generated_at="t", schema_version="1.0",
                status="OK", period_label="x",
                results=[ProfileResult(profile="BALANCED")],
                recommended_profile="EXTREME",
                recommendation_reason="r",
            )


# ─────────────────────────────────────────────────────────────────────────────
# 4. INSUFFICIENT_DATA
# ─────────────────────────────────────────────────────────────────────────────


class TestInsufficientData:

    def test_empty_explanation_returns_insufficient_data(self, kis_spy):
        report = compare_profiles(
            explanation=_exp(),   # all buckets empty.
            loop_state="RUNNING",
        )
        assert report.status == "INSUFFICIENT_DATA"
        assert report.recommended_profile == "BALANCED"
        assert any("INSUFFICIENT_DATA" in n for n in report.notes)
        # 3 profile rows with zeroed metrics.
        assert len(report.results) == 3
        for r in report.results:
            assert r.signal_count == 0
            assert r.paper_decision_count == 0
            assert r.expectancy is None


# ─────────────────────────────────────────────────────────────────────────────
# 5. Profile differentiation — same input produces different results
# ─────────────────────────────────────────────────────────────────────────────


class TestProfileDifferentiation:

    def test_position_size_ordering_cons_lt_bal_lt_agg(self, kis_spy):
        # equity 1억 + high confidence + clean flag → BUY 통과 → size 차이.
        report = compare_profiles(
            explanation=_exp(recommended=[_se("sma_crossover", "005930")]),
            loop_state="RUNNING",
            price_lookup={("sma_crossover", "005930"): 70_000.0},
            account_equity=100_000_000.0,
            confidence_lookup={("sma_crossover", "005930"): 0.95},
            pnl_lookup={("sma_crossover", "005930"): 100.0},
        )
        sizes = {r.profile: r.position_size_avg for r in report.results}
        assert sizes["CONSERVATIVE"] is not None
        assert sizes["BALANCED"]     is not None
        assert sizes["AGGRESSIVE"]   is not None
        assert sizes["CONSERVATIVE"] < sizes["BALANCED"] < sizes["AGGRESSIVE"]

    def test_risk_flag_blocks_only_conservative_when_one_flag(self, kis_spy):
        report = compare_profiles(
            explanation=_exp(recommended=[
                _se("sma_crossover", "005930", risk_flags=["stale_data"]),
            ]),
            loop_state="RUNNING",
            price_lookup={("sma_crossover", "005930"): 70_000.0},
            account_equity=10_000_000.0,
            confidence_lookup={("sma_crossover", "005930"): 0.9},
            pnl_lookup={("sma_crossover", "005930"): 50.0},
        )
        cons = next(r for r in report.results if r.profile == "CONSERVATIVE")
        bal  = next(r for r in report.results if r.profile == "BALANCED")
        agg  = next(r for r in report.results if r.profile == "AGGRESSIVE")
        # CONSERVATIVE (max_flags=0) blocks → BUY 0 + 1 veto.
        assert cons.buy_count == 0
        assert cons.risk_veto_count >= 1
        # BALANCED / AGGRESSIVE relax → BUY 1.
        assert bal.buy_count == 1
        assert agg.buy_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# 6. 리포트 파일 3종 생성
# ─────────────────────────────────────────────────────────────────────────────


class TestReportFileGeneration:

    def test_write_reports_creates_three_files(self, tmp_path, kis_spy):
        report = compare_profiles(**_clean_input())
        paths = write_reports(report, tmp_path)
        assert paths["summary_json"].exists()
        assert paths["report_md"].exists()
        assert paths["ranking_csv"].exists()
        # JSON 은 valid.
        data = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
        assert data["schema_version"] == COMPARISON_SCHEMA_VERSION
        assert data["status"] == "OK"
        assert data["recommended_profile"] == "BALANCED"
        # invariants in JSON.
        assert data["is_order_signal"] is False
        assert data["auto_apply_allowed"] is False
        assert data["is_live_authorization"] is False

    def test_markdown_carries_invariants_and_recommendation(self, tmp_path, kis_spy):
        report = compare_profiles(**_clean_input())
        paths = write_reports(report, tmp_path)
        md = paths["report_md"].read_text(encoding="utf-8")
        assert "Paper 운용 성향 비교 리포트" in md
        assert "is_order_signal=False" in md
        assert "auto_apply_allowed=False" in md
        assert "is_live_authorization=False" in md
        assert "추천 프리셋" in md
        assert "BALANCED" in md
        assert "AGGRESSIVE" in md
        assert "안전장치를 우회" in md or "우회하지 않" in md

    def test_csv_has_header_and_three_rows(self, tmp_path, kis_spy):
        report = compare_profiles(**_clean_input())
        paths = write_reports(report, tmp_path)
        csv = paths["ranking_csv"].read_text(encoding="utf-8").strip()
        rows = csv.splitlines()
        assert len(rows) == 4  # header + 3 profiles.
        header = rows[0].split(",")
        for needed in ("profile", "expectancy", "win_rate",
                       "profit_factor", "max_drawdown"):
            assert needed in header

    def test_insufficient_data_report_writes_three_files(self, tmp_path, kis_spy):
        report = compare_profiles(
            explanation=_exp(), loop_state="RUNNING",
        )
        paths = write_reports(report, tmp_path)
        assert paths["summary_json"].exists()
        data = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
        assert data["status"] == "INSUFFICIENT_DATA"

    def test_reports_dir_is_gitignored(self):
        """`reports/` 가 gitignore 에 등록되어 있어야 — 본 PR 의 결과물 어떤 것도
        커밋되지 않도록."""
        gitignore = (Path(__file__).resolve().parents[2] / ".gitignore")
        content = gitignore.read_text(encoding="utf-8")
        assert "reports/" in content or "reports/*" in content


# ─────────────────────────────────────────────────────────────────────────────
# 7. Schema sanity — no secret fields
# ─────────────────────────────────────────────────────────────────────────────


class TestSchemaSanity:

    def test_profile_result_has_no_secret_fields(self):
        forbidden = {
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "kis_app_key", "kis_app_secret",
            "anthropic_api_key", "openai_api_key", "password",
        }
        for name in ProfileResult.__dataclass_fields__:
            assert name.lower() not in forbidden, name

    def test_comparison_report_has_no_secret_fields(self):
        forbidden = {
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number",
        }
        for name in ComparisonReport.__dataclass_fields__:
            assert name.lower() not in forbidden, name


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

    def test_no_db_write(self):
        src = self._source()
        for bad in ("session.commit", "session.add", "session.delete",
                    "db.commit(", "db.add(", "db.delete(",
                    "DELETE FROM", "UPDATE "):
            # docstring 에 등장할 수 있으므로 token-strip 으로 docstring 제외.
            # 본 모듈은 짧으므로 직접 비교 가능.
            assert bad not in src or _is_in_docstring(src, bad)

    def test_no_settings_mutation(self):
        src = self._source()
        assert not re.search(r"settings\.enable_[a-z_]+\s*=", src)


def _is_in_docstring(src: str, token: str) -> bool:
    """헬퍼 — token 이 module/function docstring 안에만 등장하는지."""
    # 매우 단순한 추정 — 본 모듈은 docstring 에 DB/route_order 단어가 없으므로
    # 본 helper 가 실행될 일이 없어야 한다. 안전 fallback.
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 9. Rendering helpers — markdown / csv direct
# ─────────────────────────────────────────────────────────────────────────────


class TestRenderingHelpers:

    def test_render_markdown_returns_korean_string(self, kis_spy):
        report = compare_profiles(**_clean_input())
        md = render_markdown(report)
        assert isinstance(md, str)
        assert "Paper 운용 성향 비교 리포트" in md

    def test_render_ranking_csv_sorts_by_expectancy(self, kis_spy):
        # 다양한 expectancy 시나리오.
        report = compare_profiles(**_clean_input())
        csv = render_ranking_csv(report)
        rows = csv.strip().splitlines()
        assert rows[0].startswith("profile,")
        # expectancy column index = 1.
        expecs = []
        for line in rows[1:]:
            parts = line.split(",")
            try:
                expecs.append(float(parts[1]))
            except ValueError:
                expecs.append(None)
        # None 아닌 값들은 내림차순.
        non_none = [e for e in expecs if e is not None]
        assert non_none == sorted(non_none, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Risk profile enum used
# ─────────────────────────────────────────────────────────────────────────────


class TestProfileEnumUsage:

    def test_all_profile_enum_values_covered(self, kis_spy):
        report = compare_profiles(**_clean_input())
        expected = {p.value for p in RiskProfile}
        actual = {r.profile for r in report.results}
        assert actual == expected
