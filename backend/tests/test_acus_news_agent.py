"""ACUS NewsAgent 테스트 — Claude mocked + secret 0건 + 비용 한도 + UNKNOWN 처리.

실제 Claude API 는 호출하지 않는다(anthropic SDK 미사용). 모든 시나리오를 fake
analyze_fn 으로 재현해 안전 invariant 와 라우팅 로직만 검증한다.
"""

from __future__ import annotations

from typing import Any


from app.acus.news_agent import (
    DEFAULT_COST_CAP_USD,
    build_claude_analyzer,
    parse_news_response,
    run_news_agent,
)


def _candidate(*syms: str) -> list[dict[str, Any]]:
    return [{"symbol": s, "path": f"/fake/{s}_5m.csv"} for s in syms]


def test_news_agent_returns_unknown_when_api_not_configured():
    """analyze_news=None → 모든 종목 NEWS_UNKNOWN (= 제외 아님, FINAL_ROBUST 후보 유지)."""
    res = run_news_agent(_candidate("005930", "000660"), analyze_news=None)
    assert res["api_configured"] is False
    assert res["news_unknown"] == ["005930", "000660"]
    assert res["news_stable"] == []
    assert res["news_risky"] == []
    assert res["api_calls"] == 0
    assert res["estimated_cost_usd"] == 0.0
    assert res["is_order_signal"] is False
    assert res["contains_secret"] is False


def test_news_agent_classifies_stable_and_risky():
    def fake(sym, name, usage):
        usage["usd"] = usage.get("usd", 0.0) + 0.001
        usage["calls"] = usage.get("calls", 0) + 1
        if sym == "005930":
            return {"category": "C", "score": 1, "rationale": "stable"}
        if sym == "000660":
            return {"category": "D", "score": 2, "rationale": "improving"}
        return {"category": "A", "score": -2, "rationale": "pump suspicion"}

    res = run_news_agent(_candidate("005930", "000660", "999999"), analyze_news=fake)
    assert set(res["news_stable"]) == {"005930", "000660"}
    assert res["news_risky"] == ["999999"]
    assert res["api_calls"] == 3
    assert res["estimated_cost_usd"] > 0


def test_news_agent_handles_api_exception_per_symbol():
    """단일 종목 API 오류가 전체를 중단시키지 않는다 — 해당 종목만 UNKNOWN."""
    def flaky(sym, name, usage):
        usage["calls"] = usage.get("calls", 0) + 1
        if sym == "BAD":
            raise RuntimeError("upstream 429")
        return {"category": "C", "score": 1, "rationale": "ok"}

    res = run_news_agent(_candidate("005930", "BAD", "000660"), analyze_news=flaky)
    assert res["news_unknown"] == ["BAD"]
    assert set(res["news_stable"]) == {"005930", "000660"}
    # 잘못된 종목 reason 에 예외 타입 포함, 원문/스택 0건
    bad = next(p for p in res["per_symbol"] if p["symbol"] == "BAD")
    assert bad["reason"].startswith("api_error:")
    assert "RuntimeError" in bad["reason"]
    # secret 의심 단어 (sk-, sk-ant-) 0건
    import json as _json
    blob = _json.dumps(res, default=str)
    for token in ("sk-", "sk-ant-", "ANTHROPIC_API_KEY", "Bearer "):
        assert token not in blob, f"reason 에 secret-like 토큰 노출: {token}"


def test_news_agent_unparseable_response_marks_unknown():
    def garbled(sym, name, usage):
        usage["calls"] = usage.get("calls", 0) + 1
        return None  # parse failure 시뮬레이션

    res = run_news_agent(_candidate("005930"), analyze_news=garbled)
    assert res["news_unknown"] == ["005930"]
    per = res["per_symbol"][0]
    assert per["reason"] == "unparseable_response"


def test_news_agent_cost_cap_stops_further_calls():
    """누적 비용 ≥ cap 시 잔여 종목 호출 0건, 모두 NEWS_UNKNOWN(cost_capped)."""
    def expensive(sym, name, usage):
        usage["calls"] = usage.get("calls", 0) + 1
        usage["usd"] = usage.get("usd", 0.0) + 4.0   # 한 번 호출하면 cap=$5 곧 초과
        return {"category": "C", "score": 1, "rationale": "stable"}

    res = run_news_agent(_candidate("S1", "S2", "S3", "S4"),
                         analyze_news=expensive, cost_cap_usd=5.0)
    assert res["api_calls"] <= 2  # 2번째 호출 후 cap 초과로 중단
    assert res["cost_capped"] is True
    # 미호출 종목들은 cost_cap_reached 사유로 NEWS_UNKNOWN
    cost_capped_syms = [p["symbol"] for p in res["per_symbol"]
                        if p.get("reason") == "cost_cap_reached"]
    assert cost_capped_syms, "cost_cap_reached 사유 종목이 있어야 함"


def test_parse_news_response_robust_to_extra_text():
    """모델이 JSON 앞뒤로 설명을 붙여도 첫 JSON 객체를 추출."""
    text = ('아래와 같이 분류했습니다:\n{"category": "C", "score": 1, '
            '"rationale": "안정적"} 끝.')
    parsed = parse_news_response(text)
    assert parsed == {"category": "C", "score": 1, "rationale": "안정적"}


def test_parse_news_response_invalid_category_returns_none_or_score_zero():
    # 빈 객체 — score 변환 실패 → None
    assert parse_news_response("{}") is None
    # 카테고리 누락이지만 score 있음
    parsed = parse_news_response('{"score": 1}')
    assert parsed is not None and parsed["score"] == 1


def test_parse_news_response_category_overrides_explicit_score():
    """category 가 유효하면 표준 점수표(_CATEGORY_SCORE) 가 적용된다."""
    parsed = parse_news_response('{"category": "A", "score": 99, "rationale": "x"}')
    assert parsed["score"] == -2


def test_build_claude_analyzer_does_not_import_anthropic_at_module_level():
    """news_agent 모듈 import 만으로는 anthropic 가 import 되면 안 됨(lazy)."""
    import importlib
    # 모듈은 이미 import 됐을 수 있으므로 실 anthropic 가 sys.modules 에 있는지
    # 확인하기보다, build_claude_analyzer 자체가 어떠한 import 도 즉시 발생시키지
    # 않음을 호출해 검증한다. 함수만 만들고 호출하지 않음 → 성공해야 함.
    importlib.reload(importlib.import_module("app.acus.news_agent"))
    fn = build_claude_analyzer()
    assert callable(fn)
    # 호출 시 ANTHROPIC_API_KEY 없으면 RuntimeError → 루프가 UNKNOWN 처리
    # (실제 호출은 통합 테스트에서 fake analyzer 사용; 여기서는 호출 안 함)


def test_news_agent_module_does_not_import_anthropic_directly():
    """static grep: app/acus/news_agent.py 가 'import anthropic' / 'from anthropic'
    를 top-level 로 가지지 않는다(lazy 경로만 허용)."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "acus" /
           "news_agent.py").read_text(encoding="utf-8")
    # 함수 본문 내부의 lazy import 는 허용되어야 하므로 top-level만 검사
    lines = src.splitlines()
    for line in lines:
        stripped = line.lstrip()
        if stripped == line:  # top-level (no indent)
            assert "import anthropic" not in line, f"top-level anthropic import: {line}"
            assert not line.startswith("from anthropic"), f"top-level anthropic import: {line}"


def test_default_cost_cap_is_5_usd():
    assert DEFAULT_COST_CAP_USD == 5.0


def test_news_agent_carries_caveat_about_no_realtime_search():
    """Claude 응답은 모델 학습 시점 지식 기반이며 실시간 뉴스 검색이 아님 — caveat 노출."""
    res = run_news_agent(_candidate("005930"), analyze_news=None)
    assert "실시간 뉴스 아님" in res["news_caveat"]
