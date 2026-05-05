import asyncio

from app.ai.client import AiClient, AiResponse
from app.ai.service import _build_user_prompt, analyze, parse_score


def run(coro):
    return asyncio.run(coro)


class _StubClient(AiClient):
    def __init__(self, response_text: str):
        self.api_key = "test"
        self.model = "stub-model"
        self._text = response_text
        self.last_call: dict | None = None

    async def analyze(self, *, system: str, prompt: str, max_tokens: int = 1024) -> AiResponse:
        self.last_call = {"system": system, "prompt": prompt, "max_tokens": max_tokens}
        return AiResponse(text=self._text, model="stub-model", input_tokens=5, output_tokens=10)


def test_parse_score_valid_json_returns_dict():
    text = '{"tech":80,"trend":60,"news":70,"flow":75,"total":72,"signal":"진입","conf":85,"entry":75000,"target":78000,"stop":73000}\nGood signal.'
    score = parse_score(text)
    assert score is not None
    assert score["total"] == 72
    assert score["signal"] == "진입"


def test_parse_score_returns_none_when_no_json():
    assert parse_score("관망 권장. 정보 부족.") is None


def test_parse_score_returns_none_when_total_missing():
    assert parse_score('{"tech":50,"signal":"관망"}') is None


def test_parse_score_returns_none_when_invalid_json():
    assert parse_score('{"total": 50, broken}') is None


def test_parse_score_picks_first_block_with_total():
    text = '{"a":1,"b":2}\n{"tech":30,"total":40}'
    assert parse_score(text)["total"] == 40


def test_build_user_prompt_includes_all_provided_fields():
    p = _build_user_prompt(
        ticker="005930",
        extra="실적 발표일",
        active_strats=["ORB", "VWAP"],
        risk={"maxDailyLoss": 300_000},
    )
    assert "005930" in p
    assert "실적 발표일" in p
    assert "ORB, VWAP" in p
    assert "300000" in p


def test_build_user_prompt_omits_empty_optional_fields():
    p = _build_user_prompt(ticker="005930", extra=None, active_strats=[], risk={})
    assert "분석 종목: 005930" in p
    assert "추가 컨텍스트" not in p
    assert "활성 전략" not in p
    assert "리스크 파라미터" not in p


def test_analyze_returns_parsed_score_when_present():
    text = '{"tech":80,"trend":60,"news":70,"flow":75,"total":72,"signal":"진입","conf":85,"entry":75000,"target":78000,"stop":73000}\n분석 텍스트.'
    client = _StubClient(text)
    result = run(analyze(
        ticker="005930", extra=None, active_strats=[], risk={}, client=client,
    ))
    assert result.text == text
    assert result.model == "stub-model"
    assert result.score is not None
    assert result.score["total"] == 72


def test_analyze_returns_none_score_when_text_lacks_json():
    client = _StubClient("관망 권장입니다. 정보 부족.")
    result = run(analyze(
        ticker="005930", extra=None, active_strats=[], risk={}, client=client,
    ))
    assert result.score is None
    assert result.text.startswith("관망")


def test_analyze_passes_user_prompt_components():
    client = _StubClient("관망 권장.")
    run(analyze(
        ticker="005930", extra="실적", active_strats=["ORB"],
        risk={"maxDailyLoss": 100_000}, client=client,
    ))
    assert client.last_call is not None
    assert "005930" in client.last_call["prompt"]
    assert "실적" in client.last_call["prompt"]
    assert "ORB" in client.last_call["prompt"]
