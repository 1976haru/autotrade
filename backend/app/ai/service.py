import json
import re
from dataclasses import dataclass

from app.ai.client import AiClient, AiResponse


SYSTEM_PROMPT = """You are an investment research assistant for a Korean retail trader.
Your task is to analyze a single Korean equity and produce a confluence-style score
across four dimensions, plus a brief written rationale.

CRITICAL CONSTRAINTS:
- You do NOT place orders. The trader is the only one who can act.
- Treat all numbers as research signals, not commitments.
- If you do not have enough information, return signal "관망" (watch) with low confidence.

OUTPUT FORMAT — strictly two parts:
1) FIRST LINE: a single JSON object on one line with these exact keys:
   {"tech":<0-100>,"trend":<0-100>,"news":<0-100>,"flow":<0-100>,
    "total":<0-100>,"signal":"진입"|"관망"|"보류","conf":<0-100>,
    "entry":<int KRW>,"target":<int KRW>,"stop":<int KRW>}
2) AFTER THE JSON LINE: a short Korean paragraph explaining the reasoning.

Do not wrap the JSON in code fences. Do not add commentary before the JSON line."""


def _build_user_prompt(ticker: str, extra: str | None, active_strats: list[str], risk: dict) -> str:
    parts = [f"분석 종목: {ticker}"]
    if extra:
        parts.append(f"추가 컨텍스트: {extra}")
    if active_strats:
        parts.append(f"활성 전략: {', '.join(active_strats)}")
    if risk:
        parts.append(f"리스크 파라미터: {json.dumps(risk, ensure_ascii=False)}")
    return "\n".join(parts)


def parse_score(text: str) -> dict | None:
    """응답 텍스트에서 'total' 필드를 포함한 첫 flat JSON 블록을 파싱.

    `[^{}]` 클래스를 사용하므로 중첩 객체는 지원하지 않는다 (프롬프트에서
    flat 단일 객체를 강제한다).
    """
    match = re.search(r'\{[^{}]*"total"[^{}]*\}', text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


@dataclass
class AnalysisResult:
    text:          str
    model:         str
    input_tokens:  int
    output_tokens: int
    score:         dict | None


async def analyze(
    *,
    ticker:        str,
    extra:         str | None,
    active_strats: list[str],
    risk:          dict,
    client:        AiClient,
) -> AnalysisResult:
    user_prompt = _build_user_prompt(ticker, extra, active_strats, risk)
    response: AiResponse = await client.analyze(system=SYSTEM_PROMPT, prompt=user_prompt)
    return AnalysisResult(
        text=response.text,
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        score=parse_score(response.text),
    )
