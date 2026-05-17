"""6 전략 baseline backtest runner (feature/backtest-six-strategies).

테스트 검증 항목 (사용자 요청 매트릭스):
1. 6개 전략 (`sma_crossover` / `rsi_reversion` / `vwap_strategy` / `orb_vwap` /
   `volume_breakout` / `pullback_rebreak`) 이 모두 백테스트 대상에 포함된다.
2. 산출 파일 3종 (`strategy_backtest_summary.json` / `_ranking.csv` /
   `_report.md`) 이 생성된다.
3. 각 전략 결과에 필수 12개 지표가 모두 키로 존재한다.
4. 수수료 / 슬리피지 반영 결과가 별도 필드 (`fee_adjusted_return` /
   `slippage_adjusted_return`) 로 carry 된다.
5. 본 스크립트가 broker / OrderExecutor / route_order / KIS LIVE 함수를
   *호출 / import 하지 않는다*.
6. 본 스크립트가 `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
   `ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER` 환경변수를 *수정하지
   않는다*.

본 테스트는 *순수* — 실 KIS / Anthropic / Telegram 호출 0건. `MockMarketData`
의 결정론적 합성 OHLCV 만 사용.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


# scripts/ 가 backend/ 의 부모 디렉토리에 있어 보통의 import 가 안 됨 — 직접 spec
# 으로 로드.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "run_backtest_all_strategies.py"


def _load_runner_module():
    spec = importlib.util.spec_from_file_location(
        "run_backtest_all_strategies", _SCRIPT_PATH,
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────────────────────────────────────────────────
# 1. 6개 전략 커버리지
# ─────────────────────────────────────────────────────────────────────

REQUIRED_STRATEGIES = {
    "sma_crossover",
    "rsi_reversion",
    "vwap_strategy",
    "orb_vwap",
    "volume_breakout",
    "pullback_rebreak",
}


def test_registry_includes_all_six_strategies():
    """본 PR 의 baseline 대상은 *명시적으로* 6개. registry 가 6개 미만이면
    회귀로 간주 — 다음 단계 (파라미터 최적화) 가 누락된 전략을 놓침."""
    from app.strategies.concrete import STRATEGY_REGISTRY
    missing = REQUIRED_STRATEGIES - set(STRATEGY_REGISTRY.keys())
    assert not missing, f"registry missing strategies: {missing}"


def test_runner_executes_all_six_strategies(tmp_path: Path):
    """runner.run_all() 은 6개 전체 결과를 반환한다."""
    runner = _load_runner_module()
    args = runner._parse_args([
        "--output-dir", str(tmp_path),
        "--start", "2026-01-01", "--end", "2027-12-31",
    ])
    import asyncio
    result = asyncio.run(runner.run_all(args))
    rows = result["rows"]
    names = {r.get("strategy") for r in rows}
    assert names == REQUIRED_STRATEGIES, f"runner returned: {names}"


# ─────────────────────────────────────────────────────────────────────
# 2. 산출 파일 3종 생성
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def baseline_run(tmp_path_factory):
    """단일 run 결과를 module-scope 로 공유 — 본 fixture 가 무거운 계산 1회만
    수행. 모든 후속 검증이 본 결과를 재사용."""
    runner = _load_runner_module()
    tmp = tmp_path_factory.mktemp("baseline_run")
    args = runner._parse_args([
        "--output-dir", str(tmp),
        "--start", "2026-01-01", "--end", "2027-12-31",
    ])
    rc = runner.main([
        "--output-dir", str(tmp),
        "--start", "2026-01-01", "--end", "2027-12-31",
    ])
    assert rc == 0
    return tmp


def test_summary_json_exists_and_lists_six_strategies(baseline_run: Path):
    p = baseline_run / "strategy_backtest_summary.json"
    assert p.is_file()
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert "strategies" in payload
    names = {r.get("strategy") for r in payload["strategies"]}
    assert names == REQUIRED_STRATEGIES


def test_ranking_csv_exists_and_has_six_rows(baseline_run: Path):
    p = baseline_run / "strategy_backtest_ranking.csv"
    assert p.is_file()
    with p.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    names = {r.get("strategy") for r in rows}
    assert names == REQUIRED_STRATEGIES
    # rank 컬럼이 1..6 으로 unique 인지.
    ranks = sorted(int(r["rank"]) for r in rows)
    assert ranks == [1, 2, 3, 4, 5, 6]


def test_markdown_report_exists_and_references_all_strategies(baseline_run: Path):
    p = baseline_run / "strategy_backtest_report.md"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    for name in REQUIRED_STRATEGIES:
        assert name in text, f"strategy {name} missing from report.md"
    # disclaimer + 안전 항목 명시.
    assert "MockMarketData" in text
    assert "ENABLE_LIVE_TRADING" in text


# ─────────────────────────────────────────────────────────────────────
# 3. 필수 지표 12종
# ─────────────────────────────────────────────────────────────────────


REQUIRED_METRICS = {
    "total_return",
    "annualized_return",
    "win_rate",
    "trade_count",
    "profit_factor",
    "expectancy",
    "max_drawdown",
    "avg_trade_pnl",
    "loss_streak",
    "sharpe_like_score",
    "fee_adjusted_return",
    "slippage_adjusted_return",
}


def test_all_required_metrics_present_in_summary_json(baseline_run: Path):
    p = baseline_run / "strategy_backtest_summary.json"
    payload = json.loads(p.read_text(encoding="utf-8"))
    for row in payload["strategies"]:
        assert "error" not in row, f"strategy {row['strategy']} errored: {row.get('error')}"
        missing = REQUIRED_METRICS - row.keys()
        assert not missing, f"strategy {row['strategy']} missing metrics: {missing}"


def test_risk_adjusted_score_also_present(baseline_run: Path):
    """사용자 요청서: 'sharpe_like_score 또는 risk_adjusted_score' — 본 PR 은
    *둘 다* 노출 (sharpe_like_score 가 None 일 때 risk_adjusted_score 가 fallback)."""
    p = baseline_run / "strategy_backtest_summary.json"
    payload = json.loads(p.read_text(encoding="utf-8"))
    for row in payload["strategies"]:
        assert "risk_adjusted_score" in row


# ─────────────────────────────────────────────────────────────────────
# 4. 수수료 / 슬리피지 반영
# ─────────────────────────────────────────────────────────────────────


def test_fee_and_slippage_adjusted_returns_carry(baseline_run: Path):
    """fee_adjusted_return 과 slippage_adjusted_return 이 *별개 값* 으로 carry —
    슬리피지가 0 이상이면 slippage_adjusted ≤ fee_adjusted (≤ raw)."""
    p = baseline_run / "strategy_backtest_summary.json"
    payload = json.loads(p.read_text(encoding="utf-8"))
    saw_at_least_one_with_trades = False
    for row in payload["strategies"]:
        # 거래가 있는 전략에서만 비교 (no-trade 는 모두 0).
        if row.get("trade_count", 0) == 0:
            continue
        saw_at_least_one_with_trades = True
        raw_pnl = row["raw_pnl"]
        init    = row["initial_cash"]
        raw_ret = raw_pnl / init
        fee_adj = row["fee_adjusted_return"]
        slip_adj = row["slippage_adjusted_return"]
        # 비용은 양수 → fee_adjusted ≤ raw, slippage_adjusted ≤ fee_adjusted.
        assert fee_adj <= raw_ret + 1e-9
        assert slip_adj <= fee_adj + 1e-9
    assert saw_at_least_one_with_trades, "all 6 strategies produced 0 trades — 데이터/구성 회귀 의심"


def test_runner_uses_nonzero_fee_and_slippage_default(baseline_run: Path):
    """기본 commission_bps / tax_bps / slippage_bps 가 *> 0* — 비용 미반영
    결과를 실수로 그대로 사용하지 못하게 한다."""
    p = baseline_run / "strategy_backtest_summary.json"
    payload = json.loads(p.read_text(encoding="utf-8"))
    meta = payload["run_meta"]
    assert meta["commission_bps"] > 0
    assert meta["tax_bps"] > 0
    assert meta["slippage_bps"] > 0


def test_summary_includes_run_meta_with_costs(baseline_run: Path):
    """run_meta 가 cost 설정을 명시적으로 carry — markdown 에도 노출되도록."""
    p = baseline_run / "strategy_backtest_summary.json"
    payload = json.loads(p.read_text(encoding="utf-8"))
    meta = payload["run_meta"]
    for key in ("commission_bps", "tax_bps", "slippage_bps", "execution_model"):
        assert key in meta, f"run_meta missing {key}"


# ─────────────────────────────────────────────────────────────────────
# 5. 안전 invariants — runner 가 실거래 함수를 호출하지 않는다
# ─────────────────────────────────────────────────────────────────────


def _script_source() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def test_script_does_not_import_broker_modules():
    """broker / OrderExecutor / route_order import 0건."""
    src = _script_source()
    # 정적 grep — 외부 함수 호출 / import 가 본 파일에 등장하지 않는다.
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
    """broker.place_order / route_order / OrderExecutor 호출 0건.

    docstring / comment 안에서 *negative assertion* 으로 언급하는 것은 OK —
    *실제 호출 패턴* (백틱 / `-` bullet / `#` comment 제거 후) 만 검사.
    """
    src = _script_source()
    # AST 로 검사 — docstring / comment 는 자동으로 배제됨.
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
    """`os.environ[...] = ...` 으로 ENABLE_* / KIS_IS_PAPER 변경 0건."""
    src = _script_source()
    mutation_patterns = [
        r'os\.environ\[\s*["\']ENABLE_LIVE_TRADING["\']\s*\]\s*=',
        r'os\.environ\[\s*["\']ENABLE_AI_EXECUTION["\']\s*\]\s*=',
        r'os\.environ\[\s*["\']ENABLE_FUTURES_LIVE_TRADING["\']\s*\]\s*=',
        r'os\.environ\[\s*["\']KIS_IS_PAPER["\']\s*\]\s*=',
    ]
    for pat in mutation_patterns:
        assert re.search(pat, src) is None, f"safety flag mutation detected: /{pat}/"


def test_script_does_not_write_env_files():
    """`.env` 작성 / 수정 0건 — `.env.example` 변경도 본 runner 의 책임이
    아님."""
    src = _script_source()
    forbidden = [
        r'\.env\.example["\']',
        r'open\(\s*["\']\.env',
        r'Path\(\s*["\']\.env',
    ]
    for pat in forbidden:
        assert re.search(pat, src) is None, f"forbidden .env write pattern: /{pat}/"


def test_script_runs_as_subprocess_without_real_api_calls(tmp_path: Path):
    """script 를 *subprocess* 로 실행 — 실제 KIS API / Anthropic 호출이
    이루어지면 ConnectionError / Auth 실패로 비-zero exit. 본 테스트는 그 자체
    의 통합 smoke."""
    # 본 테스트는 sys.executable 로 script 를 직접 실행한다 — backend/.venv 와
    # 같은 인터프리터를 사용. KIS Auth / Anthropic Key 가 없어도 본 스크립트는
    # 외부 호출이 0건 이므로 *성공해야* 한다.
    rc = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH),
         "--output-dir", str(tmp_path),
         "--start", "2026-01-01", "--end", "2026-06-30"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(_REPO_ROOT),
    )
    assert rc.returncode == 0, (
        f"subprocess failed: stdout={rc.stdout[-500:]} stderr={rc.stderr[-500:]}"
    )
    # 산출 파일 3종이 모두 생성되었는지.
    for name in ("strategy_backtest_summary.json",
                 "strategy_backtest_ranking.csv",
                 "strategy_backtest_report.md"):
        assert (tmp_path / name).is_file(), f"missing {name} after subprocess run"


# ─────────────────────────────────────────────────────────────────────
# 6. Secret 노출 0건
# ─────────────────────────────────────────────────────────────────────


def test_script_does_not_print_secrets(baseline_run: Path):
    """산출 파일 어디에도 KIS / Anthropic / OpenAI / Telegram secret 패턴이
    포함되지 않는다 — 본 runner 는 secret 을 *읽지도 출력하지도* 않는다."""
    # secret-shaped 패턴 — 진짜 secret 이 아닌 *형태* 만 검사.
    secret_patterns = [
        r"sk-[a-zA-Z0-9]{20,}",       # OpenAI-shape (sk-ant- 도 매칭)
        r"AKIA[0-9A-Z]{16}",          # AWS access key id
        r"ghp_[A-Za-z0-9]{36,}",      # GitHub PAT
        r"xox[abprs]-[A-Za-z0-9-]{10,}",  # Slack
        r"\d{10}:[A-Za-z0-9_-]{30,}", # Telegram bot
    ]
    for name in ("strategy_backtest_summary.json",
                 "strategy_backtest_ranking.csv",
                 "strategy_backtest_report.md"):
        text = (baseline_run / name).read_text(encoding="utf-8", errors="replace")
        for pat in secret_patterns:
            assert re.search(pat, text) is None, (
                f"secret-shaped string in {name}: /{pat}/"
            )


# ─────────────────────────────────────────────────────────────────────
# 7. CLI 옵션 — 부분 실행 / 알 수 없는 전략 거부
# ─────────────────────────────────────────────────────────────────────


def test_runner_rejects_unknown_strategy(tmp_path: Path):
    """`--strategies` 에 등록 안 된 이름을 주면 즉시 에러 — silent skip 금지."""
    runner = _load_runner_module()
    args = runner._parse_args([
        "--output-dir", str(tmp_path),
        "--strategies", "sma_crossover", "definitely_not_a_strategy",
    ])
    import asyncio
    with pytest.raises(SystemExit):
        asyncio.run(runner.run_all(args))


def test_runner_can_run_subset(tmp_path: Path):
    """`--strategies sma_crossover rsi_reversion` 으로 2개만 실행 가능."""
    runner = _load_runner_module()
    args = runner._parse_args([
        "--output-dir", str(tmp_path),
        "--strategies", "sma_crossover", "rsi_reversion",
        "--start", "2026-01-01", "--end", "2026-06-30",
    ])
    import asyncio
    result = asyncio.run(runner.run_all(args))
    names = {r["strategy"] for r in result["rows"]}
    assert names == {"sma_crossover", "rsi_reversion"}
