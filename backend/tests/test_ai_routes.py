import pytest
from sqlalchemy import select

from app.ai.client import AiClient, AiNotConfiguredError, AiResponse
from app.api.deps import get_ai_client
from app.db.models import AiAnalysisLog
from app.main import app


class _ScriptedClient(AiClient):
    """Returns scripted text on every analyze call; can be configured to raise."""

    def __init__(self, text: str | None = None, raises: Exception | None = None):
        self.api_key = "test"
        self.model = "scripted"
        self._text = text
        self._raises = raises
        self.calls: list[dict] = []

    async def analyze(self, *, system, prompt, max_tokens=1024):
        self.calls.append({"system": system, "prompt": prompt})
        if self._raises is not None:
            raise self._raises
        return AiResponse(text=self._text, model="scripted", input_tokens=11, output_tokens=22)


def _override(client_obj):
    app.dependency_overrides[get_ai_client] = lambda: client_obj


def test_analyze_returns_text_score_and_disables_orders(client):
    text = '{"tech":70,"trend":60,"news":80,"flow":75,"total":71,"signal":"진입","conf":80,"entry":75000,"target":78000,"stop":73000}\n진입 권장.'
    _override(_ScriptedClient(text=text))

    res = client.post("/api/ai/analyze", json={"ticker": "005930"})
    assert res.status_code == 200
    body = res.json()
    assert body["can_execute_order"] is False
    assert body["text"] == text
    assert body["model"] == "scripted"
    assert body["score"]["total"] == 71


def test_analyze_returns_none_score_when_text_lacks_json(client):
    _override(_ScriptedClient(text="관망 권장. 정보 부족."))
    res = client.post("/api/ai/analyze", json={"ticker": "005930"})
    assert res.status_code == 200
    assert res.json()["score"] is None


def test_analyze_persists_audit_log(client):
    text = '{"tech":50,"trend":50,"news":50,"flow":50,"total":55,"signal":"관망","conf":40,"entry":0,"target":0,"stop":0}\n관망.'
    _override(_ScriptedClient(text=text))

    client.post("/api/ai/analyze", json={
        "ticker": "005930",
        "extra":  "실적 발표일",
        "activeStrats": ["ORB", "VWAP"],
        "risk":   {"maxDailyLoss": 300_000},
    })

    with client.test_db_factory() as db:
        rows = db.execute(select(AiAnalysisLog)).scalars().all()
        assert len(rows) == 1
        log = rows[0]
        assert log.ticker == "005930"
        assert log.extra == "실적 발표일"
        assert log.active_strats == ["ORB", "VWAP"]
        assert log.risk_params == {"maxDailyLoss": 300_000}
        assert log.text == text
        assert log.model == "scripted"
        assert log.input_tokens == 11
        assert log.output_tokens == 22
        assert log.score["total"] == 55
        assert log.error is None


def test_analyze_returns_disabled_message_when_not_configured(client):
    _override(_ScriptedClient(raises=AiNotConfiguredError("no key")))
    res = client.post("/api/ai/analyze", json={"ticker": "005930"})
    assert res.status_code == 200
    body = res.json()
    assert body["can_execute_order"] is False
    assert "비활성화" in body["text"]

    with client.test_db_factory() as db:
        log = db.execute(select(AiAnalysisLog)).scalar_one()
        assert log.error == "no key"
        assert log.text is None


def test_analyze_provider_error_returns_502_with_audit(client):
    _override(_ScriptedClient(raises=RuntimeError("upstream rate limited")))
    res = client.post("/api/ai/analyze", json={"ticker": "005930"})
    assert res.status_code == 502
    assert "rate limited" in res.json()["detail"]

    with client.test_db_factory() as db:
        log = db.execute(select(AiAnalysisLog)).scalar_one()
        assert log.error == "upstream rate limited"
        assert log.text is None


def test_analyze_rate_limit_error_returns_429_with_audit(client):
    """SDK가 max_retries회 backoff 후에도 풀지 못한 429는 502가 아닌 429로 매핑된다."""
    anthropic = pytest.importorskip("anthropic")

    # RateLimitError는 httpx Response를 요구한다.
    import httpx
    req  = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(429, request=req, json={"error": {"message": "rate limited"}})
    err  = anthropic.RateLimitError("rate limited", response=resp, body=None)

    _override(_ScriptedClient(raises=err))
    res = client.post("/api/ai/analyze", json={"ticker": "005930"})
    assert res.status_code == 429
    assert "rate limited" in res.json()["detail"].lower()

    with client.test_db_factory() as db:
        log = db.execute(select(AiAnalysisLog)).scalar_one()
        assert log.text is None
        assert "rate" in (log.error or "").lower()


def test_analyze_empty_ticker_returns_400_no_log(client):
    _override(_ScriptedClient(text="ignored"))
    res = client.post("/api/ai/analyze", json={"ticker": "   "})
    assert res.status_code == 400

    with client.test_db_factory() as db:
        rows = db.execute(select(AiAnalysisLog)).scalars().all()
        assert rows == []


def test_can_execute_order_always_false_even_when_signal_is_entry(client):
    text = '{"tech":99,"trend":99,"news":99,"flow":99,"total":99,"signal":"진입","conf":99,"entry":75000,"target":80000,"stop":73000}\n강한 진입.'
    _override(_ScriptedClient(text=text))
    body = client.post("/api/ai/analyze", json={"ticker": "005930"}).json()
    assert body["can_execute_order"] is False


def test_analyze_persists_default_mode_on_audit_row(client):
    """123: AiAnalysisLog.mode is populated with settings.default_mode at call time
    so timeline/AI sub-tab can render 092/108 ModeBadge for AI rows."""
    _override(_ScriptedClient(text="ok"))
    client.post("/api/ai/analyze", json={"ticker": "005930"})

    with client.test_db_factory() as db:
        log = db.execute(select(AiAnalysisLog)).scalar_one()
        # default_mode in tests is SIMULATION (env unset)
        assert log.mode == "SIMULATION"


def test_analyze_audit_row_carries_mode_even_on_provider_error(client):
    """Failed calls still write the audit row — the mode column should be set
    so partial-success records still classify correctly by mode."""
    _override(_ScriptedClient(raises=RuntimeError("upstream gone")))
    res = client.post("/api/ai/analyze", json={"ticker": "005930"})
    assert res.status_code == 502

    with client.test_db_factory() as db:
        log = db.execute(select(AiAnalysisLog)).scalar_one()
        assert log.mode == "SIMULATION"
        assert log.error == "upstream gone"
