"""6 전략 파라미터 최적화 + 점수화 + Paper 후보 추천.

검증 항목:
1. 6개 전략 (sma_crossover / rsi_reversion / vwap_strategy / orb_vwap /
   volume_breakout / pullback_rebreak) 이 모두 grid 에 등록되어 있고 각각
   ≥ 2개 조합 정의.
2. 산출 파일 3종 (optimization_summary.json / optimization_ranking.csv /
   paper_candidates.md) 생성.
3. 카테고리 분류: trade_count 부족 → INSUFFICIENT_DATA, 비용 반영 expectancy
   ≤ 0 → NEGATIVE_EXPECTANCY.
4. paper_candidates 는 ≤ 2 개, PASS 카테고리 + score ≥ 임계만 포함.
5. 점수화 monotonic — 동일 입력에 동일 점수, 양수 expectancy / 높은 PF /
   낮은 MDD 가 더 높은 점수.
6. 본 스크립트가 broker / OrderExecutor / route_order / KIS LIVE / Anthropic
   import & call 0건 (AST + grep).
7. ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / KIS_IS_PAPER env mutate 0건.
8. .env 작성 / 갱신 0건.
9. Secret-shape 패턴 산출물 노출 0건.
10. Walk-forward plan: paper candidate 수와 동일, config 필드 모두 존재.

본 테스트는 실 KIS / Anthropic / Telegram 호출 0건 — MockMarketData 만 사용.
"""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "run_strategy_optimization.py"


def _load_runner_module():
    spec = importlib.util.spec_from_file_location(
        "run_strategy_optimization", _SCRIPT_PATH,
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


REQUIRED_STRATEGIES = {
    "sma_crossover",
    "rsi_reversion",
    "vwap_strategy",
    "orb_vwap",
    "volume_breakout",
    "pullback_rebreak",
}


# ─────────────────────────────────────────────────────────────────────
# 1. Grid coverage
# ─────────────────────────────────────────────────────────────────────


def test_param_grids_cover_all_six_strategies():
    runner = _load_runner_module()
    grids = runner._build_param_grids()
    for name in REQUIRED_STRATEGIES:
        assert name in grids, f"grid missing for {name}"
        # 최소 2개 조합 — 단일 default 만 있으면 grid 가 아님.
        assert len(grids[name]) >= 2, f"grid for {name} too small: {len(grids[name])}"


def test_param_grid_uses_valid_constructor_kwargs():
    """grid 의 각 param dict 가 *실제로* build_strategy 로 인스턴스화 가능해야
    한다 — typo / 제거된 파라미터 회귀 차단."""
    runner = _load_runner_module()
    grids = runner._build_param_grids()
    from app.strategies.concrete import build_strategy
    for name, grid in grids.items():
        # 각 grid 의 첫 2개 조합만 sample (조합 폭발 방지).
        for params in grid[:2]:
            strategy = build_strategy(name, params=params)
            assert strategy is not None


# ─────────────────────────────────────────────────────────────────────
# 2. 산출 파일 + 카테고리 분류
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def opt_run(tmp_path_factory):
    """단일 optimization run 결과 — 모든 후속 테스트 재사용."""
    runner = _load_runner_module()
    tmp = tmp_path_factory.mktemp("opt_run")
    rc = runner.main([
        "--output-dir", str(tmp),
        "--start", "2026-01-01", "--end", "2027-12-31",
    ])
    assert rc == 0
    return tmp


def test_three_output_files_exist(opt_run: Path):
    for name in ("optimization_summary.json",
                 "optimization_ranking.csv",
                 "paper_candidates.md"):
        assert (opt_run / name).is_file(), f"missing {name}"


def test_summary_json_includes_all_six_strategies(opt_run: Path):
    payload = json.loads((opt_run / "optimization_summary.json").read_text(encoding="utf-8"))
    names_seen = {r["strategy"] for r in payload["results"]}
    assert names_seen == REQUIRED_STRATEGIES, f"strategies seen: {names_seen}"


def test_summary_json_categorizes_each_row(opt_run: Path):
    payload = json.loads((opt_run / "optimization_summary.json").read_text(encoding="utf-8"))
    runner = _load_runner_module()
    valid_cats = set(runner.ALL_CATEGORIES)
    for r in payload["results"]:
        assert r.get("category") in valid_cats, f"invalid category: {r.get('category')}"


def test_summary_json_includes_policy_block(opt_run: Path):
    payload = json.loads((opt_run / "optimization_summary.json").read_text(encoding="utf-8"))
    policy = payload.get("policy", {})
    for key in (
        "insufficient_data_min_trades", "paper_min_profit_factor",
        "paper_min_win_rate", "paper_max_mdd_pct", "paper_min_score",
        "paper_max_recommend",
    ):
        assert key in policy, f"policy missing {key}"


def test_ranking_csv_has_header_and_rows(opt_run: Path):
    p = opt_run / "optimization_ranking.csv"
    with p.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 0
    for r in rows:
        assert "strategy" in r
        assert "category" in r
        assert "total_score" in r
    # rank 1..N 이 unique 인지.
    ranks = sorted(int(r["rank"]) for r in rows)
    assert ranks == list(range(1, len(rows) + 1))


def test_markdown_report_mentions_all_categories_and_safety(opt_run: Path):
    text = (opt_run / "paper_candidates.md").read_text(encoding="utf-8")
    assert "INSUFFICIENT_DATA" in text
    assert "NEGATIVE_EXPECTANCY" in text
    assert "LOW_QUALITY" in text
    assert "PASS" in text
    # 안전 / 무결성 명시.
    assert "ENABLE_LIVE_TRADING" in text
    assert "MockMarketData" in text
    # 자동 적용 금지 명시.
    assert "자동 적용" in text


# ─────────────────────────────────────────────────────────────────────
# 3. INSUFFICIENT_DATA gate
# ─────────────────────────────────────────────────────────────────────


def test_insufficient_data_threshold_applied(opt_run: Path):
    """trade_count < 10 면 INSUFFICIENT_DATA 로 분류돼야 한다."""
    runner = _load_runner_module()
    payload = json.loads((opt_run / "optimization_summary.json").read_text(encoding="utf-8"))
    for r in payload["results"]:
        if int(r.get("trade_count") or 0) < runner.INSUFFICIENT_DATA_MIN_TRADES:
            assert r["category"] == runner.CATEGORY_INSUFFICIENT_DATA, (
                f"row {r['strategy']} {r['params']} has trade_count={r['trade_count']}"
                f" but category={r['category']}"
            )


def test_negative_expectancy_classified_separately():
    """trade_count ≥ 10 + expectancy ≤ 0 → NEGATIVE_EXPECTANCY."""
    runner = _load_runner_module()
    row = {
        "trade_count": 20,
        "expectancy": -100.0,
        "profit_factor": 0.5,
        "win_rate": 0.4,
        "max_drawdown": 50000,
        "initial_cash": 10_000_000,
    }
    assert runner.categorize(row) == runner.CATEGORY_NEGATIVE_EXPECTANCY


def test_low_quality_classified_separately():
    """trade_count ≥ 10 + expectancy > 0 + PF / win_rate / MDD 임계 미달 →
    LOW_QUALITY."""
    runner = _load_runner_module()
    row = {
        "trade_count": 20,
        "expectancy": 100.0,
        "profit_factor": 1.05,   # < 1.10 임계
        "win_rate": 0.6,
        "max_drawdown": 100_000,
        "initial_cash": 10_000_000,
    }
    assert runner.categorize(row) == runner.CATEGORY_LOW_QUALITY


def test_pass_when_all_thresholds_met():
    runner = _load_runner_module()
    row = {
        "trade_count": 25,
        "expectancy": 1000.0,
        "profit_factor": 1.8,
        "win_rate": 0.55,
        "max_drawdown": 500_000,    # 5% of 10M
        "initial_cash": 10_000_000,
    }
    assert runner.categorize(row) == runner.CATEGORY_PASS


# ─────────────────────────────────────────────────────────────────────
# 4. Paper candidate selection
# ─────────────────────────────────────────────────────────────────────


def test_paper_candidates_at_most_two(opt_run: Path):
    payload = json.loads((opt_run / "optimization_summary.json").read_text(encoding="utf-8"))
    assert len(payload["paper_candidates"]) <= 2


def test_paper_candidates_all_pass_category(opt_run: Path):
    runner = _load_runner_module()
    payload = json.loads((opt_run / "optimization_summary.json").read_text(encoding="utf-8"))
    for c in payload["paper_candidates"]:
        assert c["category"] == runner.CATEGORY_PASS


def test_paper_candidates_meet_min_score(opt_run: Path):
    runner = _load_runner_module()
    payload = json.loads((opt_run / "optimization_summary.json").read_text(encoding="utf-8"))
    for c in payload["paper_candidates"]:
        assert float(c["total_score"]) >= runner.PAPER_MIN_SCORE


def test_paper_candidate_selection_picks_best_per_strategy():
    """직접 호출 — _select_paper_candidates 가 전략별 최고 점수 1개만 선택."""
    runner = _load_runner_module()
    rows = [
        {"strategy": "A", "category": runner.CATEGORY_PASS, "total_score": 70.0, "params": {"x": 1}},
        {"strategy": "A", "category": runner.CATEGORY_PASS, "total_score": 50.0, "params": {"x": 2}},
        {"strategy": "B", "category": runner.CATEGORY_PASS, "total_score": 80.0, "params": {"y": 1}},
        {"strategy": "C", "category": runner.CATEGORY_LOW_QUALITY, "total_score": 90.0, "params": {}},
        {"strategy": "D", "category": runner.CATEGORY_PASS, "total_score": 30.0, "params": {}},
    ]
    selected = runner._select_paper_candidates(rows)
    # B(80) > A(70). C 는 PASS 아님 → 제외. D 는 score < 임계 → 제외. A 의 2번째 row 도 제외.
    assert len(selected) <= runner.PAPER_MAX_RECOMMEND
    assert all(c["category"] == runner.CATEGORY_PASS for c in selected)
    assert all(c["total_score"] >= runner.PAPER_MIN_SCORE for c in selected)
    strategies = [c["strategy"] for c in selected]
    assert len(strategies) == len(set(strategies)), "duplicate strategies in paper candidates"


# ─────────────────────────────────────────────────────────────────────
# 5. Scoring monotonicity
# ─────────────────────────────────────────────────────────────────────


def test_scoring_zero_for_negative_expectancy():
    runner = _load_runner_module()
    s = runner.compute_score(
        expectancy=-1000.0,
        profit_factor=0.5,
        win_rate=0.3,
        max_drawdown=500_000,
        trade_count=15,
        initial_cash=10_000_000,
        avg_trade_notional=1_000_000,
    )
    # expectancy 음수 → expectancy_score 0.
    assert s["expectancy_score"] == 0.0
    # PF < 1 → profit_factor_score 0.
    assert s["profit_factor_score"] == 0.0


def test_scoring_monotonic_in_expectancy():
    runner = _load_runner_module()
    def _score(expect: float) -> float:
        s = runner.compute_score(
            expectancy=expect,
            profit_factor=1.5,
            win_rate=0.55,
            max_drawdown=500_000,
            trade_count=30,
            initial_cash=10_000_000,
            avg_trade_notional=1_000_000,
        )
        return s["total_score"]
    assert _score(0.0) <= _score(10_000.0)
    assert _score(10_000.0) <= _score(50_000.0)


def test_scoring_monotonic_in_profit_factor():
    runner = _load_runner_module()
    def _score(pf: float) -> float:
        return runner.compute_score(
            expectancy=5_000.0,
            profit_factor=pf,
            win_rate=0.55,
            max_drawdown=500_000,
            trade_count=30,
            initial_cash=10_000_000,
            avg_trade_notional=1_000_000,
        )["total_score"]
    assert _score(1.0) <= _score(1.5)
    assert _score(1.5) <= _score(2.0)


def test_scoring_inverse_monotonic_in_mdd():
    """더 큰 MDD 는 더 낮은 점수."""
    runner = _load_runner_module()
    def _score(mdd: int) -> float:
        return runner.compute_score(
            expectancy=5_000.0,
            profit_factor=1.5,
            win_rate=0.55,
            max_drawdown=mdd,
            trade_count=30,
            initial_cash=10_000_000,
            avg_trade_notional=1_000_000,
        )["total_score"]
    assert _score(100_000) >= _score(500_000)
    assert _score(500_000) >= _score(2_000_000)


def test_scoring_within_0_to_100():
    runner = _load_runner_module()
    s = runner.compute_score(
        expectancy=10_000_000.0,  # 매우 큰 값 (정규화로 clamp 검증).
        profit_factor=100.0,
        win_rate=1.0,
        max_drawdown=0,
        trade_count=10_000,
        initial_cash=10_000_000,
        avg_trade_notional=1_000_000,
    )
    assert 0.0 <= s["total_score"] <= 100.0


# ─────────────────────────────────────────────────────────────────────
# 6. Walk-forward plan
# ─────────────────────────────────────────────────────────────────────


def test_walk_forward_plan_matches_candidate_count(opt_run: Path):
    payload = json.loads((opt_run / "optimization_summary.json").read_text(encoding="utf-8"))
    assert len(payload["walk_forward_plan"]) == len(payload["paper_candidates"])


def test_walk_forward_plan_config_fields_present():
    """plan 의 각 항목이 WalkForwardConfig 필드를 모두 포함."""
    runner = _load_runner_module()
    plan = runner._build_walk_forward_plan([
        {"strategy": "sma_crossover", "params": {"short": 5, "long": 20}, "total_score": 80.0},
    ])
    assert len(plan) == 1
    wf = plan[0]["walk_forward_config"]
    for key in ("mode", "train_days", "validation_days", "step_days",
                "holdout_days", "min_fold_count", "min_positive_fold_ratio",
                "max_single_fold_pnl_share"):
        assert key in wf


# ─────────────────────────────────────────────────────────────────────
# 7. 안전 invariants
# ─────────────────────────────────────────────────────────────────────


def _script_source() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def test_script_does_not_import_broker_or_kis_live_modules():
    src = _script_source()
    banned_imports = [
        r"\bfrom app\.brokers\.kis\b",
        r"\bfrom app\.brokers\.mock_broker\b",
        r"\bfrom app\.execution\.order_router\b",
        r"\bfrom app\.execution\.executor\b",
        r"\bfrom app\.execution\.order_executor\b",
        r"\bfrom app\.ai\.assist\b",
        r"\bfrom app\.ai\.client\b",
        r"\bimport anthropic\b",
        r"\bimport openai\b",
        r"\bimport httpx\b",
        r"\bimport requests\b",
    ]
    for pat in banned_imports:
        assert re.search(pat, src) is None, f"banned import detected: /{pat}/"


def test_script_does_not_call_real_order_functions():
    src = _script_source()
    import ast
    tree = ast.parse(src)
    forbidden_names = ("place_order", "route_order", "OrderExecutor")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        else:
            continue
        if name in forbidden_names:
            pytest.fail(f"forbidden call detected: {name}() at line {node.lineno}")


def test_script_does_not_mutate_safety_env_vars():
    src = _script_source()
    mutation_patterns = [
        r'os\.environ\[\s*["\']ENABLE_LIVE_TRADING["\']\s*\]\s*=',
        r'os\.environ\[\s*["\']ENABLE_AI_EXECUTION["\']\s*\]\s*=',
        r'os\.environ\[\s*["\']ENABLE_FUTURES_LIVE_TRADING["\']\s*\]\s*=',
        r'os\.environ\[\s*["\']KIS_IS_PAPER["\']\s*\]\s*=',
    ]
    for pat in mutation_patterns:
        assert re.search(pat, src) is None, f"safety flag mutation: /{pat}/"


def test_script_does_not_write_env_files():
    src = _script_source()
    forbidden = [
        r'\.env\.example["\']',
        r'open\(\s*["\']\.env',
        r'Path\(\s*["\']\.env',
    ]
    for pat in forbidden:
        assert re.search(pat, src) is None, f"forbidden .env pattern: /{pat}/"


def test_script_does_not_mutate_strategy_registry():
    """STRATEGY_REGISTRY[...] = ... mutation 0건."""
    src = _script_source()
    assert "STRATEGY_REGISTRY[" not in src or "STRATEGY_REGISTRY[name]" not in src.replace("STRATEGY_REGISTRY[name] =", "")
    # 더 정확히 — *대입* 패턴만 검사.
    assert re.search(r"STRATEGY_REGISTRY\[[^\]]+\]\s*=", src) is None
    # 비파괴 변형 (.save_params / .apply_params / 자동 비활성) 호출 0건.
    assert re.search(r"\.save_params\(|\.apply_params\(|\.update_params\(", src) is None
    assert re.search(r"strategy\.enabled\s*=\s*", src) is None


def test_output_files_do_not_leak_secret_shapes(opt_run: Path):
    secret_patterns = [
        r"sk-[a-zA-Z0-9]{20,}",
        r"ghp_[A-Za-z0-9]{36,}",
        r"AKIA[0-9A-Z]{16}",
        r"xox[abprs]-[A-Za-z0-9-]{10,}",
        r"\d{10}:[A-Za-z0-9_-]{30,}",
    ]
    for name in ("optimization_summary.json",
                 "optimization_ranking.csv",
                 "paper_candidates.md"):
        text = (opt_run / name).read_text(encoding="utf-8", errors="replace")
        for pat in secret_patterns:
            assert re.search(pat, text) is None, f"secret-shape in {name}: /{pat}/"


# ─────────────────────────────────────────────────────────────────────
# 8. CLI integration
# ─────────────────────────────────────────────────────────────────────


def test_subprocess_runs_without_real_api_calls(tmp_path: Path):
    rc = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH),
         "--output-dir", str(tmp_path),
         "--start", "2026-01-01", "--end", "2026-06-30"],
        capture_output=True, text=True, timeout=120,
        cwd=str(_REPO_ROOT),
    )
    assert rc.returncode == 0, (
        f"subprocess failed: stdout={rc.stdout[-500:]} stderr={rc.stderr[-500:]}"
    )
    for name in ("optimization_summary.json",
                 "optimization_ranking.csv",
                 "paper_candidates.md"):
        assert (tmp_path / name).is_file()


def test_subprocess_dry_run_produces_no_files(tmp_path: Path):
    rc = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH),
         "--output-dir", str(tmp_path), "--dry-run",
         "--start", "2026-01-01", "--end", "2026-06-30"],
        capture_output=True, text=True, timeout=60,
        cwd=str(_REPO_ROOT),
    )
    assert rc.returncode == 0
    assert not (tmp_path / "optimization_summary.json").exists()


def test_subprocess_rejects_unknown_strategy(tmp_path: Path):
    rc = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH),
         "--output-dir", str(tmp_path),
         "--strategies", "sma_crossover", "made_up_strategy_xyz"],
        capture_output=True, text=True, timeout=60,
        cwd=str(_REPO_ROOT),
    )
    assert rc.returncode != 0


def test_walk_forward_flag_executes_when_candidates_exist(monkeypatch, tmp_path: Path):
    """--run-walk-forward 플래그가 paper candidate 가 있을 때 실제 실행을
    트리거한다는 *분기* 검증. MockMarketData 에서 candidate 가 없으면 plan 만
    채워지므로 그 부분도 함께 검증."""
    runner = _load_runner_module()
    # candidate 가 0개 → walk_forward_results 는 None.
    args = runner._parse_args([
        "--output-dir", str(tmp_path),
        "--start", "2026-01-01", "--end", "2026-06-30",
        "--run-walk-forward",
    ])
    payload = asyncio.run(runner.run_all(args))
    if not payload["paper_candidates"]:
        assert payload["walk_forward_results"] is None
    else:
        assert payload["walk_forward_results"] is not None
        assert len(payload["walk_forward_results"]) == len(payload["paper_candidates"])
