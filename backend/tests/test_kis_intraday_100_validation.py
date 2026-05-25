"""KIS-INTRADAY-100-VALIDATION-01 — 실제 KIS 분봉 100종목 전략검증 테스트.

read-only 분봉 수집 + 품질/전략검증 파이프라인의 단위 + 안전 invariant 를 검증한다.
실제 KIS 네트워크 호출 0건 (httpx.MockTransport / 순수 함수만).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.brokers.kis_client import KisClient
from app.market_data import kis_intraday_fetch as fetch
from app.market_data import kis_intraday_universe as uni
from app.system import kis_intraday_100_final_result as final
from app.system import real_intraday_final_result as rifr

_SRC = Path(__file__).resolve().parents[1] / "app"


def run(coro):
    return asyncio.run(coro)


# ─────────────────────────── universe ───────────────────────────

def test_universe_builds_100_symbols():
    r = uni.build_kis_intraday_validation_universe(100)
    assert len(r.symbols) == 100
    assert all(uni.validate_symbol(s) for s in r.symbols)
    # 중복 0건.
    assert len(set(r.symbols)) == len(r.symbols)


def test_universe_excludes_preferred_stock():
    r = uni.build_kis_intraday_validation_universe(100)
    assert "005935" not in r.symbols  # 삼성전자우 (우선주)
    assert any(e["reason"] == uni.EXCLUDE_PREFERRED for e in r.excluded)


def test_universe_invariants_safe():
    r = uni.build_kis_intraday_validation_universe(100)
    assert r.is_order_signal is False
    assert r.is_investment_advice is False
    assert r.contains_secret is False


def test_validate_symbol_rejects_non_6digit():
    assert uni.validate_symbol("005930")
    assert not uni.validate_symbol("5930")
    assert not uni.validate_symbol("00593A")
    assert not uni.validate_symbol("")
    assert not uni.validate_symbol(None)


def test_classify_excluded_reason():
    assert uni.classify_excluded_symbol_reason("005930") is None
    assert uni.classify_excluded_symbol_reason("005935") == uni.EXCLUDE_PREFERRED
    assert uni.classify_excluded_symbol_reason("ABC") == uni.EXCLUDE_INVALID_FORMAT


def test_dedupe_symbols_preserves_order():
    uniq, dups = uni.dedupe_symbols(["005930", "000660", "005930", "035420"])
    assert uniq == ["005930", "000660", "035420"]
    assert dups == ["005930"]


# ─────────────────────────── fetch parsing ───────────────────────────

def _row(date="20260522", hour="100000", o="100", h="110", lo="95", c="105", vol="1000"):
    return {"stck_bsop_date": date, "stck_cntg_hour": hour, "stck_oprc": o,
            "stck_hgpr": h, "stck_lwpr": lo, "stck_prpr": c, "cntg_vol": vol}


def test_parse_minute_rows_maps_fields():
    pr = fetch.parse_minute_rows([_row()], "005930")
    assert len(pr.records) == 1
    rec = pr.records[0]
    assert rec["symbol"] == "005930"
    assert rec["open"] == 100.0 and rec["high"] == 110.0
    assert rec["low"] == 95.0 and rec["close"] == 105.0 and rec["volume"] == 1000.0
    assert rec["timestamp"] == "2026-05-22T10:00:00+09:00"
    assert pr.min_hour == "100000"


def test_parse_minute_rows_drops_zero_and_invalid():
    rows = [_row(o="0", h="0", lo="0", c="0"), _row(hour="bad"), _row(o="", c="")]
    pr = fetch.parse_minute_rows(rows, "005930")
    assert len(pr.records) == 0
    assert pr.dropped_rows == 3


def test_parse_minute_rows_tracks_earliest_hour():
    rows = [_row(hour="150000"), _row(hour="090000"), _row(hour="120000")]
    pr = fetch.parse_minute_rows(rows, "005930")
    assert pr.min_hour == "090000"


def test_prev_minute_hour():
    assert fetch.prev_minute_hour("100000") == "095900"
    assert fetch.prev_minute_hour("090000") == "085900"


def test_resample_1m_to_5m_aggregates():
    recs = [
        {"timestamp": "2026-05-22T09:00:00+09:00", "symbol": "X", "open": 100,
         "high": 105, "low": 99, "close": 102, "volume": 10},
        {"timestamp": "2026-05-22T09:01:00+09:00", "symbol": "X", "open": 102,
         "high": 108, "low": 101, "close": 107, "volume": 20},
        {"timestamp": "2026-05-22T09:04:00+09:00", "symbol": "X", "open": 107,
         "high": 110, "low": 95, "close": 96, "volume": 5},
        {"timestamp": "2026-05-22T09:05:00+09:00", "symbol": "X", "open": 96,
         "high": 97, "low": 94, "close": 95, "volume": 7},
    ]
    out = fetch.resample_1m_to_5m(recs)
    assert len(out) == 2
    b0 = out[0]
    assert b0["timestamp"] == "2026-05-22T09:00:00+09:00"
    assert b0["open"] == 100        # 버킷 첫 bar open
    assert b0["high"] == 110        # max
    assert b0["low"] == 95          # min
    assert b0["close"] == 96        # 마지막 bar close
    assert b0["volume"] == 35       # 합
    assert out[1]["timestamp"] == "2026-05-22T09:05:00+09:00"


def test_dedupe_by_timestamp_sorts():
    recs = [
        {"timestamp": "2026-05-22T09:05:00+09:00", "symbol": "X"},
        {"timestamp": "2026-05-22T09:00:00+09:00", "symbol": "X"},
        {"timestamp": "2026-05-22T09:05:00+09:00", "symbol": "X"},  # dup
    ]
    out = fetch.dedupe_by_timestamp(recs)
    assert len(out) == 2
    assert out[0]["timestamp"] < out[1]["timestamp"]


# ─────────────────────────── KisClient read-only quote method ───────────────────────────

def test_inquire_time_dailychartprice_is_read_only_quote():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"method": request.method, "path": request.url.path,
                     "params": dict(request.url.params), "tr_id": request.headers.get("tr_id")})
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        if request.url.path.endswith("/quotations/inquire-time-dailychartprice"):
            return httpx.Response(200, json={"rt_cd": "0", "output2": [_row()]})
        return httpx.Response(404, json={"detail": request.url.path})

    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(handler))
    data = run(c.inquire_time_dailychartprice("005930", date="20260522", hour="153000"))
    assert data["rt_cd"] == "0"
    call = [s for s in seen if "inquire-time-dailychartprice" in s["path"]][0]
    assert call["method"] == "GET"                       # read-only
    assert call["tr_id"] == "FHKST03010230"
    assert call["params"]["FID_INPUT_ISCD"] == "005930"
    assert call["params"]["FID_INPUT_DATE_1"] == "20260522"
    # 어떤 호출도 주문 endpoint(order-cash) 로 가지 않음.
    assert not any("order-cash" in s["path"] for s in seen)


# ─────────────────────────── final result orchestrator ───────────────────────────

def _intraday_dict(*, pass_n, trades, pf, exp, wf, agent="AGENT_ADDS_VALUE"):
    per = [{"symbol": f"{i:06d}", "included": True, "quality_status": "PASS",
            "trades": trades // max(1, pass_n), "profit_factor": pf, "expectancy": exp,
            "walk_forward_score": wf, "agent_value_verdict": agent, "verdict": "CAUTIOUS_CANDIDATE",
            "real_data_used": True, "sample_fixture_only": False}
           for i in range(pass_n)]
    return {
        "symbols_count": pass_n, "total_bars": pass_n * 1000, "total_days": 14,
        "total_trades": trades, "pass_symbols": [p["symbol"] for p in per],
        "warn_symbols": [], "blocked_symbols": [], "per_symbol": per,
        "overall_verdict": "RESEARCH_ONLY", "win_rate": 0.55, "profit_factor": pf,
        "expectancy": exp, "max_drawdown": 5.0, "walk_forward_score": wf,
        "agent_value_summary": agent, "agent_helped_symbols": [p["symbol"] for p in per],
        "agent_hurt_symbols": [], "agent_no_trade_symbols": [], "bar_size_minutes": 5.0,
    }


def _build(d, **kw):
    # build_final_result 를 직접 호출하지 않고 orchestrator 내부 캡만 검증하기 위해
    # evaluate 대신 build_final_result + caps 를 흉내내는 대신, 캡 함수를 직접 테스트.
    return final._apply_kis_caps(rifr.build_final_result(
        d, actual_data_used=True, data_source=final.DATA_SOURCE).user_final_judgement, **kw)


def test_caps_data_not_reliable_when_few_pass():
    reasons = []
    j = final._apply_kis_caps("WORTH_MORE_RESEARCH", pass_count=10, total_trades=2000,
                              stress_fail=0, reasons=reasons)
    assert j == final.DATA_NOT_RELIABLE


def test_caps_promising_downgraded_when_pass_below_70():
    reasons = []
    j = final._apply_kis_caps("PROMISING_FOR_PAPER_TEST", pass_count=50, total_trades=2000,
                              stress_fail=0, reasons=reasons)
    assert j == "WORTH_MORE_RESEARCH"


def test_caps_promising_downgraded_when_trades_below_500():
    reasons = []
    j = final._apply_kis_caps("PROMISING_FOR_PAPER_TEST", pass_count=80, total_trades=200,
                              stress_fail=0, reasons=reasons)
    assert j == "WORTH_MORE_RESEARCH"


def test_caps_promising_downgraded_when_stress_fail():
    reasons = []
    j = final._apply_kis_caps("PROMISING_FOR_PAPER_TEST", pass_count=80, total_trades=2000,
                              stress_fail=2, reasons=reasons)
    assert j == "WORTH_MORE_RESEARCH"


def test_caps_promising_kept_when_all_conditions_met():
    reasons = []
    j = final._apply_kis_caps("PROMISING_FOR_PAPER_TEST", pass_count=80, total_trades=2000,
                              stress_fail=0, reasons=reasons)
    assert j == "PROMISING_FOR_PAPER_TEST"


def test_evaluate_kis_intraday_100_invariants(tmp_path):
    # 빈 디렉토리 → PASS 0 → DATA_NOT_RELIABLE, 안전 invariant 유지.
    r = final.evaluate_kis_intraday_100(tmp_path, run_stress=False)
    assert r.user_final_judgement == final.DATA_NOT_RELIABLE
    assert r.is_live_authorization is False
    assert r.broker_order_sent is False
    assert r.order_created is False
    assert r.kis_order_api_called is False
    assert r.contains_secret is False
    assert r.do_not_auto_apply is True
    assert r.no_profit_guarantee is True


def test_final_result_dataclass_rejects_unsafe():
    base = dict(
        generated_at="t", data_source="KIS", requested_symbols=1, collected_symbols=1,
        collection_failed=0, trading_day_count=1, bar_size_minutes=5.0, pass_count=1,
        warn_count=0, blocked_count=0, pass_symbols=(), blocked_symbols=(), total_bars=1,
        total_trades=1, median_win_rate=None, median_profit_factor=None, median_expectancy=None,
        median_mdd=None, median_walk_forward_score=None, agent_value_summary="X",
        agent_helped_symbols=(), agent_hurt_symbols=(), agent_no_trade_symbols=(),
        stress_fail_count=0, stress_overall="PASS", developer_verdict="RESEARCH_ONLY",
        user_final_judgement="WORTH_MORE_RESEARCH", one_line_conclusion="x",
        paper_rehearsal_recommended=False, top_10_promising_symbols=(), excluded_symbols=())
    with pytest.raises(ValueError):
        final.KisIntraday100Result(**{**base, "is_live_authorization": True})
    with pytest.raises(ValueError):
        final.KisIntraday100Result(**{**base, "kis_order_api_called": True})


def test_to_dict_and_markdown_safe(tmp_path):
    r = final.evaluate_kis_intraday_100(tmp_path, run_stress=False)
    d = final.to_dict(r)
    assert d["is_live_authorization"] is False
    assert d["kis_order_api_called"] is False
    md = final.render_markdown(r)
    assert "실전 승인" in md
    assert "수익 보장" in md
    # 수익 보장 / 자동 실전 전환 단언형 문구 0건.
    for banned in ("수익을 보장합니다", "수익 보장합니다", "지금 매수", "지금 매도", "Place Order"):
        assert banned not in md


# ─────────────────────────── 정적 안전 가드 ───────────────────────────

import re


@pytest.mark.parametrize("modfile", [
    "market_data/kis_intraday_fetch.py",
    "market_data/kis_intraday_universe.py",
    "system/kis_intraday_100_final_result.py",
])
def test_modules_have_no_order_imports_or_calls(modfile):
    src = (_SRC / modfile).read_text(encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router",
                        "anthropic", "openai", "httpx", "requests", "app.ai.client"):
                assert mod not in ln, f"{modfile} forbidden import: {ln.strip()}"
    for call in ("route_order(", ".place_order(", "OrderExecutor(", ".cancel_order(",
                 "submit_candidate("):
        assert call not in src, f"{modfile} forbidden call: {call}"


def test_collector_script_is_read_only():
    script = Path(__file__).resolve().parents[2] / "scripts" / "collect_kis_intraday_ohlcv.py"
    src = script.read_text(encoding="utf-8")
    # 실제 호출 패턴 (prose 가 아닌 코드).
    for call in ("route_order(", ".place_order(", "/trading/order-cash", "OrderExecutor("):
        assert call not in src, f"collector forbidden call: {call}"
    # read-only 시세 메서드만 사용.
    assert "inquire_time_dailychartprice" in src


# ─────────────────────────── endpoint ───────────────────────────

def test_kis_intraday_100_endpoint(client, safe_default_flags):
    r = client.get("/api/system/kis-intraday-100-validation/latest")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "user_final_judgement" in d
    assert d["is_live_authorization"] is False
    assert d["broker_order_sent"] is False
    assert d["kis_order_api_called"] is False
    assert d["do_not_auto_apply"] is True
    assert d["no_profit_guarantee"] is True
    # secret / 계좌번호 패턴 0건.
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_kis_intraday_100_endpoint_get_only(client, safe_default_flags):
    assert client.post(
        "/api/system/kis-intraday-100-validation/latest", json={}).status_code in (404, 405)
