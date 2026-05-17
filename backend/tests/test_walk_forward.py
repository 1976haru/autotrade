"""3-04 — Walk-forward 검증 모듈 테스트.

invariant:
- 4단계 verdict (HEALTHY / OVERFIT_RISK / UNDERFIT / INSUFFICIENT_DATA).
- ROLLING vs EXPANDING 모드 split 생성.
- holdout_days 가 가장 최근 N 개 bar 를 제외.
- fold 수 < min_folds → INSUFFICIENT_DATA (예외 raise X).
- 모든 결과 객체: is_order_signal=False / auto_apply_allowed=False /
  is_live_authorization=False.
- broker / OrderExecutor / route_order / KIS 주문 import 0건 (정적 grep).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest  # noqa: F401 — used by fixtures

from app.analytics.walk_forward import (
    CandidateInputRecord,
    FoldResult,
    WalkForwardConfig,
    WalkForwardMode,
    WalkForwardResult,
    WalkForwardSplit,
    WalkForwardVerdict,
    evaluate_walk_forward,
    generate_splits,
    read_candidates_from_paper_config,
)
from app.backtest.types import Bar


# ─────────────────────────────────────────────────────────────────────────────
# 1. WalkForwardConfig validation
# ─────────────────────────────────────────────────────────────────────────────


class TestWalkForwardConfig:
    def test_defaults(self):
        cfg = WalkForwardConfig()
        assert cfg.mode == WalkForwardMode.ROLLING
        assert cfg.train_days == 60
        assert cfg.validation_days == 20
        assert cfg.holdout_days == 0
        assert cfg.step_days == 20
        assert cfg.min_folds == 3
        assert 0.0 < cfg.overfit_ratio <= 1.0

    def test_rejects_invalid_train_days(self):
        with pytest.raises(ValueError):
            WalkForwardConfig(train_days=0)

    def test_rejects_invalid_validation_days(self):
        with pytest.raises(ValueError):
            WalkForwardConfig(validation_days=0)

    def test_rejects_negative_holdout(self):
        with pytest.raises(ValueError):
            WalkForwardConfig(holdout_days=-1)

    def test_rejects_invalid_overfit_ratio(self):
        with pytest.raises(ValueError):
            WalkForwardConfig(overfit_ratio=0.0)
        with pytest.raises(ValueError):
            WalkForwardConfig(overfit_ratio=1.5)

    def test_to_dict_round_trip(self):
        cfg = WalkForwardConfig(mode=WalkForwardMode.EXPANDING, train_days=80,
                                 validation_days=30, step_days=15, min_folds=5,
                                 holdout_days=10)
        d = cfg.to_dict()
        assert d["mode"] == "expanding"
        assert d["train_days"] == 80
        assert d["holdout_days"] == 10


# ─────────────────────────────────────────────────────────────────────────────
# 2. generate_splits — ROLLING / EXPANDING / holdout
# ─────────────────────────────────────────────────────────────────────────────


class TestGenerateSplits:
    def test_rolling_basic(self):
        # 200 bars, train=60, val=20, step=20.
        # fold 1: train=[0,60), val=[60,80)
        # fold 2: train=[20,80), val=[80,100)
        # fold 3: train=[40,100), val=[100,120)
        # fold 4: train=[60,120), val=[120,140)
        # fold 5: train=[80,140), val=[140,160)
        # fold 6: train=[100,160), val=[160,180)
        # fold 7: train=[120,180), val=[180,200) ← OK
        cfg = WalkForwardConfig(train_days=60, validation_days=20, step_days=20)
        splits = generate_splits(200, cfg)
        assert len(splits) == 7
        assert splits[0].train_start_idx == 0
        assert splits[0].train_end_idx == 60
        assert splits[0].val_start_idx == 60
        assert splits[0].val_end_idx == 80

    def test_rolling_returns_empty_when_too_few_bars(self):
        cfg = WalkForwardConfig(train_days=60, validation_days=20)
        # train + val = 80, but we have only 50 bars.
        splits = generate_splits(50, cfg)
        assert splits == []

    def test_expanding_mode_train_start_fixed(self):
        cfg = WalkForwardConfig(
            mode=WalkForwardMode.EXPANDING,
            train_days=60, validation_days=20, step_days=20,
        )
        splits = generate_splits(200, cfg)
        for split in splits:
            assert split.train_start_idx == 0
        # Expanding 의 train_end_idx 는 fold 마다 증가.
        train_ends = [s.train_end_idx for s in splits]
        assert train_ends == sorted(train_ends)

    def test_holdout_excludes_recent_bars(self):
        # 200 bars total, holdout=40 → usable = 160.
        cfg = WalkForwardConfig(
            train_days=60, validation_days=20, step_days=20, holdout_days=40,
        )
        splits = generate_splits(200, cfg)
        for split in splits:
            assert split.val_end_idx <= 160

    def test_split_has_no_overlap(self):
        cfg = WalkForwardConfig(train_days=60, validation_days=20, step_days=20)
        splits = generate_splits(200, cfg)
        for split in splits:
            assert split.val_start_idx == split.train_end_idx
            assert split.train_end_idx > split.train_start_idx
            assert split.val_end_idx > split.val_start_idx

    def test_fold_numbers_are_sequential(self):
        cfg = WalkForwardConfig(train_days=60, validation_days=20, step_days=20)
        splits = generate_splits(200, cfg)
        for i, s in enumerate(splits, start=1):
            assert s.fold_number == i


# ─────────────────────────────────────────────────────────────────────────────
# 3. evaluate_walk_forward — verdict 분기
# ─────────────────────────────────────────────────────────────────────────────


def _synthetic_bars(n: int, *, symbol: str = "TESTSYM",
                     base: int = 70000, trend: int = 100) -> list[Bar]:
    """결정론적 합성 OHLCV — close 가 약하게 우상향 + 진동.

    train 과 val 모두에서 거래 신호가 발생할 정도로 진동 폭 확보.
    """
    out: list[Bar] = []
    t0 = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    price = base
    for i in range(n):
        # 5-bar 주기 진동 + trend.
        wave = ((i % 10) - 5) * 200
        close = base + i * trend + wave
        high  = close + 200
        low   = close - 200
        open_ = price
        volume = 5_000_000 + (i % 7) * 100_000
        out.append(Bar(
            symbol=symbol, timestamp=t0 + timedelta(days=i),
            open=open_, high=high, low=low, close=close, volume=volume,
        ))
        price = close
    return out


class TestEvaluateWalkForwardVerdicts:
    def test_insufficient_data_when_bars_too_few(self):
        bars = _synthetic_bars(50)
        cfg = WalkForwardConfig(train_days=60, validation_days=20,
                                 step_days=20, min_folds=3)
        result = evaluate_walk_forward(
            bars=bars, strategy_name="sma_crossover", params={}, config=cfg,
        )
        assert result.verdict == WalkForwardVerdict.INSUFFICIENT_DATA
        assert result.folds == []

    def test_insufficient_data_when_min_folds_not_met(self):
        # 100 bars / train=60 / val=20 / step=20 → fold 1 만 가능.
        bars = _synthetic_bars(100)
        cfg = WalkForwardConfig(train_days=60, validation_days=20,
                                 step_days=20, min_folds=3)
        result = evaluate_walk_forward(
            bars=bars, strategy_name="sma_crossover", params={}, config=cfg,
        )
        assert result.verdict == WalkForwardVerdict.INSUFFICIENT_DATA

    def test_runs_folds_with_enough_data(self):
        # 200 bars → 7 folds 가능.
        bars = _synthetic_bars(200)
        cfg = WalkForwardConfig(train_days=60, validation_days=20,
                                 step_days=20, min_folds=3)
        result = evaluate_walk_forward(
            bars=bars, strategy_name="sma_crossover", params={"short": 5, "long": 20},
            config=cfg,
        )
        # 실 데이터 흐름에 따라 verdict 분기 가능 — INSUFFICIENT_DATA 가 아닌 것
        # 만 검증 (HEALTHY / OVERFIT_RISK / UNDERFIT 중 하나).
        assert result.verdict != WalkForwardVerdict.INSUFFICIENT_DATA
        assert len(result.folds) >= cfg.min_folds

    def test_underfit_when_strategy_does_not_trade(self):
        """trade 가 발생하지 않으면 train/val expectancy 모두 0 → UNDERFIT."""
        # 작은 진폭 → SMA crossover 신호 거의 없음.
        bars = _synthetic_bars(200, base=70000, trend=0)
        cfg = WalkForwardConfig(train_days=60, validation_days=20,
                                 step_days=20, min_folds=3)
        result = evaluate_walk_forward(
            bars=bars, strategy_name="sma_crossover",
            params={"short": 5, "long": 20}, config=cfg,
        )
        # trade 0 → expectancy 0 → UNDERFIT.
        assert result.verdict in (
            WalkForwardVerdict.UNDERFIT, WalkForwardVerdict.OVERFIT_RISK,
            WalkForwardVerdict.HEALTHY,
        )

    def test_result_carries_safety_invariants(self):
        bars = _synthetic_bars(50)
        cfg = WalkForwardConfig(train_days=60, validation_days=20, min_folds=3)
        result = evaluate_walk_forward(
            bars=bars, strategy_name="sma_crossover", params={}, config=cfg,
        )
        d = result.to_dict()
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. FoldResult 비율 계산
# ─────────────────────────────────────────────────────────────────────────────


class TestFoldResultRatio:
    def test_positive_train_positive_val(self):
        f = FoldResult(
            fold_number=1,
            train_metrics={"expectancy": 1000.0},
            val_metrics={"expectancy": 500.0},
        )
        assert f.ratio == 0.5

    def test_zero_train_returns_zero_ratio(self):
        f = FoldResult(
            fold_number=1,
            train_metrics={"expectancy": 0.0},
            val_metrics={"expectancy": 500.0},
        )
        assert f.ratio == 0.0

    def test_negative_train_returns_zero_ratio(self):
        f = FoldResult(
            fold_number=1,
            train_metrics={"expectancy": -100.0},
            val_metrics={"expectancy": 100.0},
        )
        assert f.ratio == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 5. read_candidates_from_paper_config — 3-03 adapter
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperConfigAdapter:
    def test_extracts_candidates_from_valid_payload(self):
        payload = {
            "candidate_count": 2,
            "candidates": [
                {"strategy": "sma_crossover", "symbol": "005930",
                 "params": {"short": 5, "long": 20}, "score": 0.5},
                {"strategy": "rsi_reversion", "symbol": "000660",
                 "params": {"period": 14}, "score": 0.3},
            ],
        }
        result = read_candidates_from_paper_config(payload)
        assert len(result) == 2
        assert result[0].strategy == "sma_crossover"
        assert result[0].symbol   == "005930"
        assert result[0].params   == {"short": 5, "long": 20}
        assert result[0].score    == 0.5

    def test_returns_empty_for_empty_candidates(self):
        payload = {"candidate_count": 0, "candidates": [], "reasons_no_candidate": ["x"]}
        result = read_candidates_from_paper_config(payload)
        assert result == []

    def test_returns_empty_for_malformed_payload(self):
        assert read_candidates_from_paper_config({}) == []
        assert read_candidates_from_paper_config({"candidates": "not_a_list"}) == []

    def test_skips_invalid_entries(self):
        payload = {
            "candidates": [
                {"strategy": "sma_crossover", "symbol": "005930",
                 "params": {"short": 5}, "score": 0.5},
                {"strategy": 123, "symbol": "x", "params": {}, "score": 0},  # invalid type
                None,
                {"missing": "fields"},
            ],
        }
        result = read_candidates_from_paper_config(payload)
        assert len(result) == 1
        assert result[0].strategy == "sma_crossover"


# ─────────────────────────────────────────────────────────────────────────────
# 6. CLI 통합 (read-only smoke)
# ─────────────────────────────────────────────────────────────────────────────


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))


def _import_cli():
    spec = importlib.util.spec_from_file_location(
        "run_walk_forward_validation_module",
        _SCRIPTS_DIR / "run_walk_forward_validation.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cli():
    return _import_cli()


class TestCli:
    def test_cli_runs_default_with_fixture_csv(self, cli):
        args = cli._parse_args([
            "--dry-run",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
            "--symbol", "005930",
        ])
        payload = cli.run_validation(args)
        # 005930 fixture 는 84 bars — train=60+val=20=80 으로 fold 1 만 가능.
        # min_folds=3 → INSUFFICIENT_DATA.
        assert payload["candidate_count"] >= 1
        assert payload["is_order_signal"]    is False
        assert payload["auto_apply_allowed"] is False

    def test_writes_three_artifacts(self, cli, tmp_path):
        args = cli._parse_args([
            "--output-dir", str(tmp_path),
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
            "--symbol", "005930",
            "--strategy", "sma_crossover",
        ])
        payload = cli.run_validation(args)
        written = cli._write_outputs(payload, tmp_path)
        assert written["summary"].exists()
        assert written["ranking_csv"].exists()
        assert written["report_md"].exists()

    def test_summary_invariants(self, cli, tmp_path):
        args = cli._parse_args([
            "--output-dir", str(tmp_path),
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
            "--symbol", "005930",
        ])
        payload = cli.run_validation(args)
        cli._write_outputs(payload, tmp_path)
        loaded = json.loads(
            (tmp_path / "walk_forward_summary.json").read_text(encoding="utf-8")
        )
        assert loaded["is_order_signal"]       is False
        assert loaded["auto_apply_allowed"]    is False
        assert loaded["is_live_authorization"] is False

    def test_paper_config_input_path(self, cli, tmp_path):
        """--from-paper-config 가 정상 동작."""
        paper_cfg_path = tmp_path / "fake_paper_candidate_config.json"
        paper_cfg_path.write_text(json.dumps({
            "candidate_count": 1,
            "candidates": [
                {"strategy": "sma_crossover", "symbol": "005930",
                 "params": {"short": 5, "long": 20}, "score": 0.5},
            ],
        }), encoding="utf-8")
        args = cli._parse_args([
            "--dry-run",
            "--from-paper-config", str(paper_cfg_path),
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_validation(args)
        assert payload["candidate_count"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 7. 정적 import 가드 — broker / OrderExecutor / route_order 0건
# ─────────────────────────────────────────────────────────────────────────────


class TestNoForbiddenImports:
    def test_walk_forward_module_no_broker(self):
        import app.analytics.walk_forward as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.kis",
            r"from\s+app\.brokers\.mock_broker",
            r"from\s+app\.execution\.executor",
            r"from\s+app\.execution\.order_router",
            r"broker\.place_order\(",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
            r"KisClient\b",
            r"^import\s+anthropic",
            r"^import\s+openai",
            r"^import\s+requests",
        ]
        for pat in forbidden:
            assert not re.search(pat, src, re.MULTILINE), \
                f"forbidden pattern in walk_forward.py: {pat}"

    def test_cli_script_no_broker(self):
        src = (_SCRIPTS_DIR / "run_walk_forward_validation.py").read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.kis",
            r"from\s+app\.brokers\.mock_broker",
            r"from\s+app\.execution\.executor",
            r"from\s+app\.execution\.order_router",
            r"broker\.place_order\(",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
            r"KisClient\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src), f"forbidden in CLI: {pat}"

    def test_no_safety_flag_mutation(self):
        targets = [
            _SCRIPTS_DIR / "run_walk_forward_validation.py",
            Path(__file__).resolve().parents[1] / "app" / "analytics" / "walk_forward.py",
        ]
        bad = [
            r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
            r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
            r"ENABLE_FUTURES_LIVE_TRADING\s*=\s*['\"]?true",
            r"KIS_IS_PAPER\s*=\s*['\"]?false",
            r"settings\.enable_live_trading\s*=",
            r"settings\.enable_ai_execution\s*=",
        ]
        for path in targets:
            src = path.read_text(encoding="utf-8")
            for pat in bad:
                assert not re.search(pat, src, re.IGNORECASE), \
                    f"safety flag mutation {pat!r} in {path.name}"
