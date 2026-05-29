"""ACUS 파이프라인 테스트 — mocked deps + checkpoint resume + 안전 invariant.

본 테스트는 외부 호출 0건(KIS / Anthropic / network 0건). 실제 council 백테스트는
fake_council 로 대체해 빠르게 검증한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.acus import types as T
from app.acus.deps import PipelineDeps
from app.acus.pipeline import run_pipeline


_KST = timezone(timedelta(hours=9))


@dataclass
class FakeBar:
    symbol: str
    timestamp: datetime
    open: float = 100.0
    high: float = 100.05
    low: float = 99.95
    close: float = 100.0
    volume: float = 1000.0
    vwap: float | None = None
    market_regime: str | None = None
    time_phase: str | None = None
    gap_pct: float | None = None


def _make_bars(symbol: str, days: int, bars_per_day: int = 6) -> list[FakeBar]:
    """tight spread bars(~0.1% range) — LiquidityAgent spread ≤0.5% 통과용."""
    base = datetime(2026, 1, 5, 9, 0, tzinfo=_KST)
    out: list[FakeBar] = []
    for d in range(days):
        for b in range(bars_per_day):
            ts = base + timedelta(days=d, minutes=5 * b)
            px = 100.0 + d * 0.1
            out.append(FakeBar(symbol=symbol, timestamp=ts,
                               open=px, high=px + 0.05, low=px - 0.05, close=px,
                               volume=10_000_000.0))  # 거래대금 ~10억/bar → 60억/day → LIQUID
    return out


def _fake_load(symbol: str, days: int = 80):
    bars = _make_bars(symbol, days=days)

    def _loader(path: str):
        return bars, {"symbols": [symbol], "real_data_used": False,
                      "sample_fixture_only": True}
    return _loader


# 분류별 fake council backtest — 종목별로 다른 PF 결과 주입
def _make_council(profile: str):
    """profile 별 council 백테스트 결과 fixture.
       robust   : train PF 1.5, validate PF 1.2  -> ROBUST
       consistent: train PF 1.05, validate PF 1.0 -> CONSISTENT
       decayed  : train PF 1.5, validate PF 0.6  -> DECAYED
       rejected : train PF 0.8, validate PF 0.7  -> REJECTED
       error    : raises
    """
    def _fn(bars):
        if profile == "error":
            raise RuntimeError("synthetic backtest failure")
        # train/validate 구분: bars 가 train(처음 40 거래일) 인지 validate 인지
        # 시점으로 추정 (단위테스트에서는 정확 구분 불필요 — 첫번째 호출=train).
        if profile == "robust":
            return {"profit_factor": 1.5, "expectancy": 0.01, "average_return": 0.004,
                    "win_rate": 0.55, "max_drawdown": 5, "trades": 50,
                    "insufficient_data": False}
        if profile == "consistent":
            return {"profit_factor": 1.05, "expectancy": 0.002, "average_return": 0.003,
                    "win_rate": 0.52, "max_drawdown": 4, "trades": 40,
                    "insufficient_data": False}
        if profile == "decayed":
            # NOTE: 단순화 — train/validate 같은 값 반환(테스트는 separate fake per symbol)
            return {"profit_factor": 1.5, "expectancy": 0.01, "average_return": 0.003,
                    "win_rate": 0.5, "max_drawdown": 6, "trades": 30,
                    "insufficient_data": False}
        return {"profit_factor": 0.8, "expectancy": -0.001, "average_return": -0.001,
                "win_rate": 0.4, "max_drawdown": 10, "trades": 20,
                "insufficient_data": False}
    return _fn


@pytest.fixture()
def fake_input_dir(tmp_path: Path) -> Path:
    d = tmp_path / "intraday"
    d.mkdir()
    # 디렉토리에 placeholder CSV 만 만들면 load_bars 가 fake 로 가로챔
    for sym in ("005930", "000660", "035420"):
        (d / f"{sym}_5m.csv").write_text("timestamp,open,high,low,close,volume,symbol\n",
                                         encoding="utf-8")
    return d


def _make_deps(per_symbol_profile: dict[str, str]) -> PipelineDeps:
    def _loader(path: str):
        # path 의 stem 에서 종목코드 추출
        sym = Path(path).stem.removesuffix("_5m")
        return _fake_load(sym)(path)

    counts = {sym: 0 for sym in per_symbol_profile}

    def _council(bars):
        if not bars:
            raise ValueError("empty")
        sym = getattr(bars[0], "symbol", "?")
        profile = per_symbol_profile.get(sym, "rejected")
        counts[sym] = counts.get(sym, 0) + 1
        # robust/consistent: train(첫호출)=좋고 validate(두번째)=좋음.
        # decayed: train 좋고 validate 나쁨 → 두번째 호출 PF 0.6 으로 override
        if profile == "decayed" and counts[sym] >= 2:
            return {"profit_factor": 0.6, "expectancy": -0.005, "average_return": -0.002,
                    "win_rate": 0.4, "max_drawdown": 8, "trades": 25,
                    "insufficient_data": False}
        # regime sub-backtests: profile 동일 시 PF 가 robust 임계(1.1)를 넘으면 REGIME_ROBUST
        return _make_council(profile)(bars)

    return PipelineDeps(load_bars=_loader, council_backtest=_council, analyze_news=None)


# ─────────────────────────────────────────────────────────────────────────────
# 안전 invariant 테스트
# ─────────────────────────────────────────────────────────────────────────────

def test_symbol_evaluation_safety_invariants_enforced():
    from app.acus.types import SymbolEvaluation
    with pytest.raises(ValueError):
        SymbolEvaluation(symbol="X", is_order_signal=True)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        SymbolEvaluation(symbol="X", auto_apply_allowed=True)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        SymbolEvaluation(symbol="X", applied_to_runtime=True)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        SymbolEvaluation(symbol="X", is_live_authorization=True)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        SymbolEvaluation(symbol="X", contains_secret=True)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        SymbolEvaluation(symbol="X", no_profit_guarantee=False)  # type: ignore[call-arg]


def test_final_report_dataclass_rejects_unsafe_values():
    from app.acus.final_report import ACUSFinalReport
    with pytest.raises(ValueError):
        ACUSFinalReport(generated_at="x", universe_size=0, final_robust_count=0,
                        verdict=T.VERDICT_STRONG, pool_classification=T.POOL_STRONG,
                        one_line_conclusion="x", funnel={}, final_robust_symbols=(),
                        is_order_signal=True)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ACUSFinalReport(generated_at="x", universe_size=0, final_robust_count=0,
                        verdict="OK", pool_classification=T.POOL_STRONG,
                        one_line_conclusion="x", funnel={}, final_robust_symbols=())


# ─────────────────────────────────────────────────────────────────────────────
# 파이프라인 무인 동작 + 5중 교집합
# ─────────────────────────────────────────────────────────────────────────────

def test_pipeline_runs_unattended_and_produces_final_report(fake_input_dir, tmp_path):
    deps = _make_deps({"005930": "robust", "000660": "consistent", "035420": "rejected"})
    root = tmp_path / "out"
    result = run_pipeline(input_dir=fake_input_dir, deps=deps, root=root,
                          resume=False, min_bars=10, min_days=2)
    fr = result["final_report"]
    assert fr["is_order_signal"] is False
    assert fr["is_live_authorization"] is False
    assert fr["auto_apply_allowed"] is False
    assert fr["applied_to_runtime"] is False
    assert fr["contains_secret"] is False
    assert fr["no_profit_guarantee"] is True

    assert (root / "started_at.txt").exists()
    assert (root / "completed_at.txt").exists()
    assert (root / "progress.md").exists()
    assert (root / "acus_final_report.md").exists()
    assert (root / "checkpoints" / "stage_01_collection_ready.json").exists()
    assert (root / "checkpoints" / "stage_08_final_report.json").exists()

    # 005930 + 000660 → backtest 통과; news=UNKNOWN(허용); liquid/risk 충족 → FINAL_ROBUST 후보
    assert fr["final_robust_count"] >= 1
    # 035420 (rejected) 는 절대 final_robust 에 들어가면 안 됨
    assert "035420" not in fr["final_robust_symbols"]


def test_pipeline_per_symbol_error_does_not_halt_entire_run(fake_input_dir, tmp_path):
    """단일 종목 오류가 전체 파이프라인을 중단시키지 않는다."""
    # 005930 만 error, 나머지는 정상 — 파이프라인은 끝까지 진행해야 함
    deps = _make_deps({"005930": "error", "000660": "consistent", "035420": "robust"})
    root = tmp_path / "out"
    result = run_pipeline(input_dir=fake_input_dir, deps=deps, root=root,
                          resume=False, min_bars=10, min_days=2)
    fr = result["final_report"]
    assert (root / "completed_at.txt").exists(), "completed_at 미생성 — 파이프라인 중단"
    assert fr["universe_size"] == 3
    # 005930 은 backtest 단계에서 INSUFFICIENT/REJECTED 로 분류, FINAL_ROBUST 제외
    assert "005930" not in fr["final_robust_symbols"]


def test_pipeline_resumes_from_checkpoint(fake_input_dir, tmp_path):
    deps = _make_deps({"005930": "robust", "000660": "consistent", "035420": "rejected"})
    root = tmp_path / "out"
    # 1차 실행
    run_pipeline(input_dir=fake_input_dir, deps=deps, root=root,
                 resume=False, min_bars=10, min_days=2)
    s02_path = root / "checkpoints" / "stage_02_backtest_agent.json"
    mtime_before = s02_path.stat().st_mtime

    # 2차 실행 (resume=True) — backtest 체크포인트 그대로 재사용
    result2 = run_pipeline(input_dir=fake_input_dir, deps=deps, root=root,
                           resume=True, min_bars=10, min_days=2)
    mtime_after = s02_path.stat().st_mtime
    assert mtime_after == mtime_before, "resume 모드에서 stage_02 체크포인트가 재실행됨"
    assert result2["final_report"]["verdict"] in (
        T.VERDICT_STRONG, T.VERDICT_WEAK, T.VERDICT_INSUFFICIENT)


# ─────────────────────────────────────────────────────────────────────────────
# 보안 + 정책 invariant
# ─────────────────────────────────────────────────────────────────────────────

def _strip_docstrings_and_comments(src: str) -> str:
    """ast 로 파싱해 docstring(모듈/함수/클래스) + `#` 주석을 제거한 *실행 코드* 만 반환."""
    import ast as _ast
    import io
    import tokenize as _tk

    # 1) docstring 제거 (모듈/Function/AsyncFunction/Class 의 첫 Expr-Str)
    try:
        tree = _ast.parse(src)
    except SyntaxError:
        tree = None
    docstring_spans: list[tuple[int, int]] = []
    if tree is not None:
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Module, _ast.FunctionDef,
                                  _ast.AsyncFunctionDef, _ast.ClassDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], _ast.Expr) and isinstance(
                        body[0].value, _ast.Constant) and isinstance(body[0].value.value, str):
                    n = body[0]
                    docstring_spans.append((n.lineno, n.end_lineno or n.lineno))
    # 2) `#` 주석 제거 (tokenize)
    lines = src.splitlines(keepends=True)
    try:
        tokens = list(_tk.generate_tokens(io.StringIO(src).readline))
    except _tk.TokenizeError:
        tokens = []
    out_lines = list(lines)
    for tok in tokens:
        if tok.type == _tk.COMMENT:
            (sl, sc) = tok.start
            (el, ec) = tok.end
            if sl == el:
                line = out_lines[sl - 1]
                out_lines[sl - 1] = line[:sc] + line[ec:]
    # 3) docstring 라인 자체 제거
    keep = []
    in_doc = {ln for s, e in docstring_spans for ln in range(s, e + 1)}
    for i, line in enumerate(out_lines, start=1):
        if i in in_doc:
            continue
        keep.append(line)
    return "".join(keep)


def test_acus_modules_do_not_import_broker_or_order_executor():
    """acus 패키지가 broker / OrderExecutor / route_order / KIS 주문 API 를 import 하지 않음.

    docstring/comment 의 정책 언급은 *허용* — 실행 코드에서만 검출.
    """
    import pathlib
    pkg = pathlib.Path(__file__).resolve().parents[1] / "app" / "acus"
    forbidden = (
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.executor",
        "from app.execution.order_executor",
        "from app.execution.order_router",
        "import route_order",
        "OrderExecutor",
        ".place_order(",
        ".cancel_order(",
        "ENABLE_LIVE_TRADING",
        "ENABLE_AI_EXECUTION",
        "KIS_IS_PAPER",
        "import anthropic",   # acus 자체는 anthropic top-level import 금지 (AiClient 가 lazy)
        "from anthropic",
    )
    files = list(pkg.glob("*.py"))
    assert files, "acus 모듈 파일이 없음"
    for f in files:
        raw = f.read_text(encoding="utf-8")
        code = _strip_docstrings_and_comments(raw)
        for token in forbidden:
            assert token not in code, f"금지 토큰 '{token}' 발견 (코드 라인): {f.name}"


def test_forbidden_features_constant_is_preserved():
    """PIT 모듈의 FORBIDDEN_FEATURES 가 ACUS RegimeAgent 와 함께 유지된다."""
    from app.backtest.point_in_time_regime import FORBIDDEN_FEATURES
    assert "same_day_close" in FORBIDDEN_FEATURES
    assert "post_entry_high" in FORBIDDEN_FEATURES
    assert "post_entry_volume" in FORBIDDEN_FEATURES
    # regime_agent 가 금지 feature 를 참조하지 않음(소스 정적 검증)
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "acus" /
           "regime_agent.py").read_text(encoding="utf-8")
    for forb in FORBIDDEN_FEATURES:
        assert forb not in src, f"regime_agent 가 금지 feature 사용: {forb}"


def test_final_report_markdown_contains_safety_notices():
    from app.acus.final_report import ACUSFinalReport, render_markdown
    r = ACUSFinalReport(
        generated_at="2026-05-28T00:00:00+00:00",
        universe_size=10, final_robust_count=2,
        verdict=T.VERDICT_INSUFFICIENT, pool_classification=T.POOL_INSUFFICIENT,
        one_line_conclusion="x", funnel={"a": 1}, final_robust_symbols=("005930",),
        final_robust_detail=({"symbol": "005930", "score": 50.0,
                              "backtest_class": T.BT_ROBUST, "regime_class": T.REGIME_ROBUST,
                              "liquidity_class": T.LIQUID, "news_class": T.NEWS_STABLE,
                              "risk_class": T.RISK_OK},),
    )
    md = render_markdown(r)
    assert "실거래 승인 아님" in md
    assert "수익 보장 아님" in md
    assert "Paper 자동 진입 0" in md
    assert "주문 신호 아님" not in md.lower() or "is_order_signal: false" in md


def test_passes_all_requires_strict_intersection():
    """5중 교집합 — 하나라도 빠지면 통과 X (NEWS_UNKNOWN 만 예외적으로 허용)."""
    from app.acus.types import SymbolEvaluation
    # 모든 통과 + news UNKNOWN → 허용
    ev_unknown = SymbolEvaluation(
        symbol="A", backtest_class=T.BT_ROBUST, regime_class=T.REGIME_ROBUST,
        liquidity_class=T.LIQUID, news_class=T.NEWS_UNKNOWN, risk_class=T.RISK_OK)
    assert ev_unknown.passes_all() is True
    # news RISKY → 차단
    ev_risky = SymbolEvaluation(
        symbol="A", backtest_class=T.BT_ROBUST, regime_class=T.REGIME_ROBUST,
        liquidity_class=T.LIQUID, news_class=T.NEWS_RISKY, risk_class=T.RISK_OK)
    assert ev_risky.passes_all() is False
    # backtest DECAYED → 차단
    ev_decayed = SymbolEvaluation(
        symbol="A", backtest_class=T.BT_DECAYED, regime_class=T.REGIME_ROBUST,
        liquidity_class=T.LIQUID, news_class=T.NEWS_STABLE, risk_class=T.RISK_OK)
    assert ev_decayed.passes_all() is False
    # liquidity ILLIQUID → 차단
    ev_illiquid = SymbolEvaluation(
        symbol="A", backtest_class=T.BT_ROBUST, regime_class=T.REGIME_ROBUST,
        liquidity_class=T.ILLIQUID, news_class=T.NEWS_STABLE, risk_class=T.RISK_OK)
    assert ev_illiquid.passes_all() is False
    # risk HIGH → 차단
    ev_risk_high = SymbolEvaluation(
        symbol="A", backtest_class=T.BT_ROBUST, regime_class=T.REGIME_ROBUST,
        liquidity_class=T.LIQUID, news_class=T.NEWS_STABLE, risk_class=T.RISK_HIGH)
    assert ev_risk_high.passes_all() is False


def test_verdict_thresholds():
    from app.acus.final_report import _verdict_and_pool
    assert _verdict_and_pool(0) == (T.VERDICT_INSUFFICIENT, T.POOL_INSUFFICIENT)
    assert _verdict_and_pool(9) == (T.VERDICT_INSUFFICIENT, T.POOL_INSUFFICIENT)
    assert _verdict_and_pool(10) == (T.VERDICT_WEAK, T.POOL_DATE)
    assert _verdict_and_pool(29) == (T.VERDICT_WEAK, T.POOL_DATE)
    assert _verdict_and_pool(30) == (T.VERDICT_STRONG, T.POOL_STRONG)
    assert _verdict_and_pool(100) == (T.VERDICT_STRONG, T.POOL_STRONG)


def test_round_trip_cost_fraction_matches_spec():
    """spec: 수수료 0.015%×2 + 세금 0.18% + 슬리피지 0.05% = 0.26% = 0.0026."""
    assert abs(T.ROUND_TRIP_COST_FRACTION - 0.0026) < 1e-9


def test_backtest_classify_thresholds():
    from app.acus.backtest_agent import _classify, MIN_TRADES
    n = MIN_TRADES + 1
    assert _classify(1.5, 1.2, n, n) == T.BT_ROBUST
    assert _classify(1.2, 1.0, n, n) == T.BT_ROBUST
    assert _classify(1.3, 0.7, n, n) == T.BT_DECAYED
    assert _classify(1.05, 0.95, n, n) == T.BT_CONSISTENT
    assert _classify(0.9, 0.95, n, n) == T.BT_REJECTED
    assert _classify(None, 1.0, n, n) == T.BT_INSUFFICIENT
    assert _classify(1.5, 1.2, 1, n) == T.BT_INSUFFICIENT  # train trades 부족
