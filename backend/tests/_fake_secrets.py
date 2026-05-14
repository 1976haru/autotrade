"""Fake secret placeholders for tests (#93).

본 모듈은 *테스트 fixture* 로 사용되는 fake secret 들을 한 곳에 모은다. 신규
테스트가 secret 입력을 시뮬레이션할 때 본 모듈의 상수를 사용하면:

1. 일관된 *명백히-fake* prefix (`FAKE-`)로 보안 스캐너가 진짜 secret 과 구분 가능.
2. 패턴을 바꿔야 할 때 한 곳만 수정.
3. 실수로 진짜 secret 을 commit 하는 위험 감소.

본 파일 자체는 `backend/tests/**` allowlist 에 포함되어 scripts/security_scan.py
가 검출하지 않는다. 다만 본 상수들의 *형식*은 다음 규칙을 따른다:

- *명백한 placeholder*: `FAKE-` 또는 `PLACEHOLDER-` prefix.
- 진짜 형식과 *구분되는 sentinel value*: 예) `0000` repeated, `ZZZZ` ending.
- 본 모듈은 *주문 시그널 / broker 인증 / 실제 API 호출* 어디에도 사용되지 *않는다*.

CLAUDE.md 절대 원칙:
- 본 파일은 broker / OrderExecutor / route_order import 0건.
- 본 상수들은 실제 secret 으로 작동하지 않는 *길이 / 형식 부적합* 값들이다.
"""

from __future__ import annotations

# ============================================================
# 1. KIS (한국투자증권 OpenAPI)
# ============================================================
# KIS App Key/Secret 은 영문 대소문자 + 숫자 36~40자 정도. 본 placeholder 들은
# 명백히 "FAKE" 표식이 들어가 있어 KIS 서버 인증을 통과하지 못한다.

FAKE_KIS_APP_KEY      = "FAKE-KIS-APP-KEY-PLACEHOLDER-0000000000"
FAKE_KIS_APP_SECRET   = "FAKE-KIS-APP-SECRET-PLACEHOLDER-00000000000000000000ZZZZ"
FAKE_KIS_ACCOUNT_NO   = "00000000-00"  # 실제 한국 계좌번호 형식이지만 모두 0.

# ============================================================
# 2. AI providers (Anthropic / OpenAI)
# ============================================================
# 본 placeholder 들은 *형식*은 sk- prefix 를 가지지만 길이가 짧아 실제
# API 호출 시 인증 실패한다.

FAKE_ANTHROPIC_API_KEY = "sk-ant-FAKE-PLACEHOLDER-00000000000000000000000000"
FAKE_OPENAI_API_KEY    = "sk-FAKE-PLACEHOLDER-00000000000000000000000000ZZ"

# ============================================================
# 3. Notification (Telegram)
# ============================================================
# Telegram bot token 형식: `<digits>:<base64>`. 본 placeholder 는 영문 digit
# 형식만 흉내내고 실제 인증을 통과하지 못한다.

FAKE_TELEGRAM_BOT_TOKEN = "0000000000:AA-FAKE-PLACEHOLDER-00000000000000000000000000"
FAKE_TELEGRAM_CHAT_ID   = "0"

# ============================================================
# 4. GitHub
# ============================================================
FAKE_GITHUB_PAT = "ghp_FAKEPLACEHOLDER00000000000000000000ZZZZ"

# ============================================================
# 5. JWT / Bearer
# ============================================================
FAKE_JWT_TOKEN = (
    "eyJ-FAKE-HEADER-PLACEHOLDER-0000000000"
    ".eyJ-FAKE-PAYLOAD-PLACEHOLDER-0000000000"
    ".FAKE-SIGNATURE-PLACEHOLDER-00000000000000000000"
)

# ============================================================
# 6. *secret-shaped* sentinel for sanitizer 테스트
# ============================================================
# sanitize_text() 같은 함수가 secret-shaped 입력을 *잡는지* 검증할 때 사용.
# 본 sentinel 들은 *실제로 secret 처럼 보여야* 하므로 길이가 충분히 길다.

SECRET_SHAPED_FOR_SANITIZER_OPENAI    = "sk-" + "x" * 40
SECRET_SHAPED_FOR_SANITIZER_ANTHROPIC = "sk-ant-" + "x" * 40
SECRET_SHAPED_FOR_SANITIZER_KR_ACCOUNT = "12345678-90"


# ============================================================
# 7. self-check — 본 모듈이 진짜 secret 이 아님을 알리는 자기 검증
# ============================================================


def assert_all_placeholders_contain_fake_marker() -> None:
    """본 함수는 본 모듈의 placeholder 들이 *명백한 FAKE 표시* 를 가지는지
    self-check 한다. test_repository_hygiene.py 에서 호출."""
    placeholders = (
        FAKE_KIS_APP_KEY,
        FAKE_KIS_APP_SECRET,
        FAKE_ANTHROPIC_API_KEY,
        FAKE_OPENAI_API_KEY,
        FAKE_TELEGRAM_BOT_TOKEN,
        FAKE_GITHUB_PAT,
        FAKE_JWT_TOKEN,
    )
    for p in placeholders:
        markers = ("FAKE", "PLACEHOLDER", "0000")
        if not any(m in p for m in markers):
            raise AssertionError(
                f"placeholder {p[:10]}... 가 'FAKE' / 'PLACEHOLDER' / '0000' "
                "마커를 포함하지 않음"
            )
