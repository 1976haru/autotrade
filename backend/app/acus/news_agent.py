"""ACUS Stage 05 — NewsAgent (4번째 필터, Claude API 활용).

각 후보 종목의 최근 이슈를 Claude(sonnet)로 분류해 작전주/일회성 위험을 advisory 판정:
  A. 작전/이상급등 의심 (-2점)
  B. 일회성 호재/악재 (-1점)
  C. 안정적 펀더멘털 (+1점)
  D. 펀더멘털 개선 추세 (+2점)

통과 기준:
  NEWS_STABLE  : 종합 점수 ≥ 0
  NEWS_RISKY   : 종합 점수 < 0 (작전주/일회성)
  NEWS_UNKNOWN : 응답 실패 / API 미구성 / 비용 한도 초과 (= *제외 아님*)

안전:
  - Claude API 비용 한도(기본 $5) 초과 시 자동 중단 → 잔여 종목 NEWS_UNKNOWN.
  - API key 는 ``settings`` (= .env) 에서만 읽으며 출력/체크포인트에 *원문 0건*.
  - 본 모듈은 ``anthropic`` 을 top-level import 하지 않는다(실 호출은 lazy
    ``app.ai.client.AiClient`` 위임 — 그 자체가 anthropic 을 lazy import).
  - rate limit / 일시 오류는 SDK 내장 재시도(max_retries) + 본 루프 graceful fallback.

⚠ Claude 응답은 *모델 학습 시점 지식* 기반이며 실시간 뉴스 검색이 아니다(웹 검색 도구
미연결). advisory 자료로만 사용 — 확정 사실 아님.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.acus import types as T
from app.acus.deps import NewsAnalyzeFn

DEFAULT_COST_CAP_USD = 5.0
# sonnet 가격(추정, USD/MTok). 비용 한도 추적용 — 청구 정확값은 콘솔 확인.
INPUT_USD_PER_MTOK = 3.0
OUTPUT_USD_PER_MTOK = 15.0
MAX_TOKENS = 512

_SYSTEM_PROMPT = (
    "당신은 한국 주식 리서치 보조자입니다. 주어진 종목의 최근 이슈를 분류하고 "
    "반드시 JSON 한 개만 출력합니다. 형식: "
    '{"category": "A|B|C|D", "score": -2|-1|1|2, "rationale": "간단 근거"}. '
    "A=작전/이상급등 의심(-2), B=일회성 호재/악재(-1), C=안정적 펀더멘털(+1), "
    "D=펀더멘털 개선 추세(+2). 확실하지 않으면 보수적으로 분류하세요. "
    "투자 추천/매수·매도 의견은 쓰지 마세요."
)

_CATEGORY_SCORE = {"A": -2, "B": -1, "C": 1, "D": 2}


def _build_prompt(symbol: str, name: Optional[str]) -> str:
    label = f"{name}({symbol})" if name else symbol
    return (
        f"한국 주식 {label} 의 최근 60일 주요 이슈를 분류하세요:\n"
        "A. 작전/이상급등 의심 (-2점)\n"
        "B. 일회성 호재/악재 (-1점)\n"
        "C. 안정적 펀더멘털 (+1점)\n"
        "D. 펀더멘털 개선 추세 (+2점)\n"
        "점수와 근거를 JSON 한 개로만 응답하세요."
    )


def parse_news_response(text: str) -> Optional[dict[str, Any]]:
    """Claude 텍스트 응답에서 첫 JSON 객체 파싱 → {category, score, rationale}."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except (ValueError, json.JSONDecodeError):
        return None
    cat = str(obj.get("category", "")).strip().upper()[:1]
    score = obj.get("score")
    if cat in _CATEGORY_SCORE:
        score = _CATEGORY_SCORE[cat]
    try:
        score = int(score)
    except (TypeError, ValueError):
        return None
    rationale = str(obj.get("rationale", ""))[:300]
    return {"category": cat or "?", "score": score, "rationale": rationale}


def build_claude_analyzer(*, model: str | None = None) -> NewsAnalyzeFn:
    """실 Claude 분석기 생성. API key 미구성 시 호출 즉시 RuntimeError(→ 루프가 UNKNOWN 처리).

    usage_out 에 input_tokens/output_tokens/usd/calls 를 누적한다(비용 추적).
    """
    def _analyze(symbol: str, name: Optional[str], usage_out: dict[str, Any]) -> Optional[dict[str, Any]]:
        import asyncio

        from app.ai.client import AiClient  # lazy — anthropic 은 AiClient 내부 lazy import

        client = AiClient(model=model)
        prompt = _build_prompt(symbol, name)
        resp = asyncio.run(client.analyze(system=_SYSTEM_PROMPT, prompt=prompt,
                                          max_tokens=MAX_TOKENS))
        usd = (resp.input_tokens / 1_000_000 * INPUT_USD_PER_MTOK
               + resp.output_tokens / 1_000_000 * OUTPUT_USD_PER_MTOK)
        usage_out["input_tokens"] = usage_out.get("input_tokens", 0) + resp.input_tokens
        usage_out["output_tokens"] = usage_out.get("output_tokens", 0) + resp.output_tokens
        usage_out["usd"] = round(usage_out.get("usd", 0.0) + usd, 6)
        usage_out["calls"] = usage_out.get("calls", 0) + 1
        return parse_news_response(resp.text)

    return _analyze


def _symbol_name(symbol: str) -> Optional[str]:
    try:
        from app.universe.default_universe import FALLBACK_MARKET_CAP_TOP50_NAMES
        return FALLBACK_MARKET_CAP_TOP50_NAMES.get(symbol)
    except Exception:  # noqa: BLE001
        return None


def run_news_agent(
    candidate_meta: list[dict[str, Any]],
    *,
    analyze_news: Optional[NewsAnalyzeFn] = None,
    cost_cap_usd: float = DEFAULT_COST_CAP_USD,
) -> dict[str, Any]:
    """Stage 05 — 후보 종목 뉴스 분류. analyze_news=None 이면 전부 NEWS_UNKNOWN."""
    usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "usd": 0.0, "calls": 0}
    per: list[dict[str, Any]] = []
    cost_capped = False

    for m in candidate_meta:
        symbol = m.get("symbol")
        if not symbol:
            continue
        if analyze_news is None:
            per.append({"symbol": symbol, "news_class": T.NEWS_UNKNOWN,
                        "score": None, "reason": "api_not_configured"})
            continue
        if usage["usd"] >= cost_cap_usd:
            cost_capped = True
            per.append({"symbol": symbol, "news_class": T.NEWS_UNKNOWN,
                        "score": None, "reason": "cost_cap_reached"})
            continue
        try:
            parsed = analyze_news(symbol, _symbol_name(symbol), usage)
        except Exception as exc:  # noqa: BLE001 — 단일 종목 실패가 전체 중단 X
            per.append({"symbol": symbol, "news_class": T.NEWS_UNKNOWN, "score": None,
                        "reason": f"api_error:{type(exc).__name__}"})
            continue
        if not parsed:
            per.append({"symbol": symbol, "news_class": T.NEWS_UNKNOWN, "score": None,
                        "reason": "unparseable_response"})
            continue
        score = int(parsed.get("score", 0))
        klass = T.NEWS_STABLE if score >= 0 else T.NEWS_RISKY
        per.append({"symbol": symbol, "news_class": klass, "score": score,
                    "category": parsed.get("category"),
                    "rationale": parsed.get("rationale", "")})

    return {
        "stage": "stage_05_news_agent",
        "cost_cap_usd": cost_cap_usd,
        "estimated_cost_usd": usage["usd"],
        "api_calls": usage["calls"],
        "cost_capped": cost_capped,
        "api_configured": analyze_news is not None,
        "news_caveat": ("Claude 학습 시점 지식 기반 분류 — 실시간 뉴스 아님; advisory 자료."),
        "news_stable": [p["symbol"] for p in per if p.get("news_class") == T.NEWS_STABLE],
        "news_risky": [p["symbol"] for p in per if p.get("news_class") == T.NEWS_RISKY],
        "news_unknown": [p["symbol"] for p in per if p.get("news_class") == T.NEWS_UNKNOWN],
        "per_symbol": per,
        "is_order_signal": False,
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
        "contains_secret": False,
    }
