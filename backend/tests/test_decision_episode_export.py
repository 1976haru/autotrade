"""P-31: decision_episode 학습/분석 데이터 export (CSV/JSONL) 테스트.

CSV/JSONL 생성 + row_count + 필터(날짜/symbol/strategy/action/risk_profile/
outcome_label/review_tag) + secret-safe guard(금지 키/값) + secret 미export +
정적 가드.
"""

from __future__ import annotations

import ast
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.reports.decision_episode_export import (
    CSV_COLUMNS,
    FORBIDDEN_KEYS,
    DecisionEpisodeExportOptions,
    ExportSafetyError,
    SecretLeakError,
    assert_export_safe,
    episode_to_csv_row,
    episode_to_jsonl_record,
    export_decision_episodes,
    export_decision_episodes_csv,
    export_decision_episodes_jsonl,
    filter_episodes,
    reject_forbidden_keys,
)

_MODULE = Path(__file__).resolve().parents[1] / "app" / "reports" / "decision_episode_export.py"
_FIXED = datetime(2026, 5, 23, 14, 30, 0, tzinfo=timezone.utc)


def _ep(i, *, action="BUY", strat="MOMENTUM", label="PROFITABLE", tag="GOOD_DECISION",
        rp="BALANCED", day=10, symbol="005930"):
    return dict(
        episode_id=f"ep-{i:04d}", created_at=f"2026-05-{day:02d}T01:00:00+00:00",
        symbol=symbol, mode="PAPER", final_action=action, confidence=70,
        quality_score=80, reason_code="KIS_PAPER_SUBMITTED",
        selected_strategies=[strat],
        votes=[{"strategy": strat, "signal": action, "score": 70}],
        market_summary={"price": 75000, "market_regime": "TREND_UP", "data_status": "OK"},
        vote_summary={"top_strategy": strat, "buy_vote_count": 1,
                      "sell_vote_count": 0, "hold_vote_count": 0},
        council={"risk_profile": rp, "market_regime": "TREND_UP"},
        order_quality_summary={"order_status": "FILLED", "fill_status": "FILLED",
                               "latency_ms": 300, "slippage_bps": 5},
        broker_order_no=f"PAPER-{i}",
        outcome={"status": "COMPLETE", "label": label, "return_close": 0.9},
        outcome_summary={"status": "COMPLETE", "label": label, "return_5m": 0.3,
                         "return_30m": 1.2, "return_close": 0.9,
                         "max_favorable_excursion": 1.5, "max_adverse_excursion": -0.4},
        sell_reason_summary=({"reason_code": "STOP_LOSS", "category": "RISK_EXIT"}
                             if action == "SELL" else {}),
        review_summary={"grade": "GOOD", "primary_tag": tag},
        review={"tags": [tag]},
    )


def _sample():
    return [_ep(i) for i in range(5)] + [
        _ep(99, action="SELL"),
        _ep(50, label="LOSS", tag="BAD_DECISION", day=12, symbol="000660"),
    ]


# ── CSV / JSONL 생성 ──

def test_csv_export(tmp_path):
    r = export_decision_episodes_csv(_sample(), output_dir=tmp_path, now=_FIXED)
    assert r["format"] == "csv"
    assert r["row_count"] == 7
    p = Path(r["file_path"])
    assert p.exists()
    with p.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 7
    assert list(rows[0].keys()) == list(CSV_COLUMNS)


def test_jsonl_export(tmp_path):
    r = export_decision_episodes_jsonl(_sample(), output_dir=tmp_path, now=_FIXED)
    assert r["format"] == "jsonl"
    assert r["row_count"] == 7
    lines = Path(r["file_path"]).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 7
    rec = json.loads(lines[0])
    assert rec["episode_id"]
    assert rec["is_order_signal"] is False


def test_dispatcher_format(tmp_path):
    assert export_decision_episodes(_sample(), fmt="csv", output_dir=tmp_path,
                                    now=_FIXED)["format"] == "csv"
    assert export_decision_episodes(_sample(), fmt="jsonl", output_dir=tmp_path,
                                    now=_FIXED)["format"] == "jsonl"
    with pytest.raises(ValueError):
        export_decision_episodes(_sample(), fmt="xml", output_dir=tmp_path)


def test_row_count_accuracy(tmp_path):
    r = export_decision_episodes_csv(_sample(),
                                     DecisionEpisodeExportOptions(final_actions=("SELL",)),
                                     output_dir=tmp_path, now=_FIXED)
    assert r["row_count"] == 1


# ── 필터 ──

def test_filter_date_range():
    eps = _sample()
    out = filter_episodes(eps, DecisionEpisodeExportOptions(start_date="2026-05-11"))
    assert all(e["created_at"] >= "2026-05-11" for e in out)
    assert len(out) == 1   # day=12 episode 만.


def test_filter_symbol():
    out = filter_episodes(_sample(), DecisionEpisodeExportOptions(symbols=("000660",)))
    assert all(e["symbol"] == "000660" for e in out)
    assert len(out) == 1


def test_filter_strategy():
    out = filter_episodes(_sample(), DecisionEpisodeExportOptions(strategies=("MOMENTUM",)))
    assert len(out) == 7   # 전부 MOMENTUM.
    assert filter_episodes(_sample(), DecisionEpisodeExportOptions(strategies=("ORB",))) == []


def test_filter_final_action():
    out = filter_episodes(_sample(), DecisionEpisodeExportOptions(final_actions=("SELL",)))
    assert len(out) == 1 and out[0]["final_action"] == "SELL"


def test_filter_risk_profile():
    out = filter_episodes(_sample(), DecisionEpisodeExportOptions(risk_profiles=("BALANCED",)))
    assert len(out) == 7
    assert filter_episodes(_sample(),
                           DecisionEpisodeExportOptions(risk_profiles=("AGGRESSIVE",))) == []


def test_filter_outcome_label():
    out = filter_episodes(_sample(), DecisionEpisodeExportOptions(outcome_labels=("LOSS",)))
    assert len(out) == 1


def test_filter_review_tag():
    out = filter_episodes(_sample(),
                          DecisionEpisodeExportOptions(review_tags=("BAD_DECISION",)))
    assert len(out) == 1


def test_filter_max_rows():
    out = filter_episodes(_sample(), DecisionEpisodeExportOptions(max_rows=3))
    assert len(out) == 3


# ── secret-safe guard ──

def test_reject_forbidden_keys():
    for bad_key in ("app_secret", "api_key", "account_no", "access_token",
                    "KIS_APP_SECRET", "authorization", "token", "private_key"):
        with pytest.raises(ExportSafetyError):
            reject_forbidden_keys({"episode_id": "x", bad_key: "v"})


def test_reject_forbidden_keys_nested():
    with pytest.raises(ExportSafetyError):
        reject_forbidden_keys({"episode_id": "x", "meta": {"api_key": "v"}})


def test_assert_export_safe_value_secret():
    with pytest.raises(SecretLeakError):
        assert_export_safe({"episode_id": "x",
                            "note": "sk-ant-" + "a" * 40})


def test_assert_export_safe_passes_clean():
    safe = assert_export_safe({"episode_id": "ep-1", "symbol": "005930",
                              "broker_order_no": "PAPER-1", "return_close": 0.9})
    assert safe["episode_id"] == "ep-1"


def test_forbidden_keys_constant_covers_required():
    for k in ("app_secret", "api_key", "account_no", "access_token",
              "refresh_token", "password", "token", "private_key"):
        assert k in FORBIDDEN_KEYS


# ── secret 미export 검증 ──

def test_csv_has_no_secret(tmp_path):
    r = export_decision_episodes_csv(_sample(), output_dir=tmp_path, now=_FIXED)
    text = Path(r["file_path"]).read_text(encoding="utf-8-sig").lower()
    for bad in ("app_secret", "account_no", "api_key", "access_token", "password"):
        assert bad not in text


def test_jsonl_has_no_secret(tmp_path):
    r = export_decision_episodes_jsonl(_sample(), output_dir=tmp_path, now=_FIXED)
    text = Path(r["file_path"]).read_text(encoding="utf-8").lower()
    for bad in ("app_secret", "account_no", "api_key", "access_token", "password"):
        assert bad not in text


def test_csv_row_allowlist_only():
    row = episode_to_csv_row(_ep(1))
    assert set(row.keys()) == set(CSV_COLUMNS)
    for k in row:
        assert k.lower() not in FORBIDDEN_KEYS


def test_jsonl_record_invariants():
    rec = episode_to_jsonl_record(_ep(1))
    assert rec["is_order_signal"] is False
    assert rec["is_live_authorization"] is False


def test_injected_forbidden_key_is_dropped_not_exported():
    # episode 에 금지 키가 섞여도 allowlist 매핑이라 export 레코드에 *포함되지 않음*.
    bad = _ep(1)
    bad["account_no"] = "1234567890"
    rec = episode_to_jsonl_record(bad)
    assert "account_no" not in rec
    assert "1234567890" not in json.dumps(rec, ensure_ascii=False)
    row = episode_to_csv_row(bad)
    assert "account_no" not in row


def test_assert_export_safe_raises_on_forbidden_record():
    # 직접 금지 키가 담긴 record 를 assert_export_safe 에 넣으면 차단.
    with pytest.raises(ExportSafetyError):
        assert_export_safe({"episode_id": "x", "account_no": "1234567890"})


# ── 결과 메타 invariant ──

def test_result_meta_invariants(tmp_path):
    r = export_decision_episodes_csv(_sample(), output_dir=tmp_path, now=_FIXED)
    assert r["contains_secret"] is False
    assert r["is_order_signal"] is False
    assert r["is_live_authorization"] is False
    assert "file_path" in r and "row_count" in r


# ── API ──

def test_api_export():
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    r = c.post("/api/agents/decision-episodes/export", json={"format": "csv"})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["format"] == "csv"
    assert res["contains_secret"] is False
    assert res["is_live_authorization"] is False
    assert Path(res["file_path"]).exists()


def test_api_jsonl_export():
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    r = c.post("/api/agents/decision-episodes/export", json={"format": "jsonl"})
    assert r.status_code == 200
    assert r.json()["result"]["format"] == "jsonl"


# ── 정적 가드 ──

def test_module_no_forbidden_imports():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = ("app.brokers", "app.execution", "broker", "httpx", "requests",
                 "anthropic", "openai", "app.ai.assist", "app.ai.client")
    for imp in imported:
        for bad in forbidden:
            assert not imp.startswith(bad), f"forbidden import: {imp}"


def test_module_no_order_or_account_symbols():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    for bad in ("place_order", "cancel_order", "route_order", "OrderExecutor",
                "get_balance", "account_balance"):
        assert bad not in names, f"forbidden code symbol: {bad}"
