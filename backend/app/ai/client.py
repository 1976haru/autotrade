from dataclasses import dataclass

from app.core.config import get_settings


@dataclass
class AiResponse:
    text:          str
    model:         str
    input_tokens:  int
    output_tokens: int


class AiNotConfiguredError(RuntimeError):
    """ANTHROPIC_API_KEY가 비어 있어 AI 호출이 불가능한 상태."""


class AiClient:
    """Anthropic SDK 래퍼.

    SDK는 lazy import한다 — anthropic 패키지가 미설치이거나 키가 없는 환경에서도
    AiClient 객체 자체는 생성할 수 있어야 테스트와 의존성 그래프가 깨지지 않는다.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.anthropic_api_key
        self.model = model or settings.anthropic_model

    async def analyze(self, *, system: str, prompt: str, max_tokens: int = 1024) -> AiResponse:
        if not self.api_key:
            raise AiNotConfiguredError("ANTHROPIC_API_KEY is not set")

        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self.api_key)
        msg = await client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        return AiResponse(
            text=text,
            model=msg.model,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )
