"""체크리스트 #64: 알림 채널 구현.

본 모듈은 broker / 주문 / Secret 어떤 것도 import하지 않는다.
TelegramChannel은 stdlib `urllib.request`만 사용 — 외부 의존성 X.

`TelegramChannel.send`는 절대 raise하지 않는다. timeout / retry 제한:
- 단일 요청 timeout 5초 (default)
- 최대 1회 retry (default)
- Telegram API는 sendMessage 호출만 사용. 다른 API 미사용.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from app.notifications.types import (
    NotificationChannel,
    NotificationEvent,
    SendResult,
)


# ---------- NoOp ----------


class NoOpChannel(NotificationChannel):
    """설정이 없거나 NOTIFICATIONS_ENABLED=false일 때 사용. 항상 skipped 반환."""

    name = "noop"

    def is_configured(self) -> bool:
        return False

    def send(self, event: NotificationEvent) -> SendResult:  # noqa: ARG002
        return SendResult(
            ok=True, channel=self.name, skipped_reason="noop_channel",
        )


# ---------- Telegram ----------


_TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramChannel(NotificationChannel):
    """Telegram Bot sendMessage 호출 — token / chat_id는 *생성자 인자*로만 받음.

    - token / chat_id를 인스턴스 외부에 노출하지 않는다 (logger 미사용,
      __repr__ 미오버라이드는 기본 dataclass 형태가 아니라 일반 class).
    - 단일 요청 timeout, 최대 1회 retry. 실패해도 raise 하지 않음.
    - `disable_notification`은 INFO 이하 severity일 때 True (silent push).
    - 호출자가 dry_run=True를 주면 실제 HTTP 호출을 건너뛰고 'dry_run' skip.
    """

    name = "telegram"

    def __init__(
        self,
        *,
        bot_token:        str,
        chat_id:          str,
        timeout_seconds:  float = 5.0,
        max_retries:      int   = 1,
        api_base:         str   = _TELEGRAM_API_BASE,
        # urlopen 함수 주입 — 테스트에서 모킹 가능.
        http_opener:      Any   = None,
        dry_run:          bool  = False,
    ) -> None:
        self._token        = (bot_token or "").strip()
        self._chat_id      = (chat_id or "").strip()
        self._timeout      = max(0.5, float(timeout_seconds))
        self._max_retries  = max(0, int(max_retries))
        self._api_base     = api_base.rstrip("/")
        self._opener       = http_opener  # callable(req, timeout=...) -> response
        self._dry_run      = bool(dry_run)

    def is_configured(self) -> bool:
        return bool(self._token) and bool(self._chat_id)

    def send(self, event: NotificationEvent) -> SendResult:
        if not self.is_configured():
            return SendResult(
                ok=False, channel=self.name,
                skipped_reason="not_configured",
            )
        if self._dry_run:
            return SendResult(
                ok=True, channel=self.name,
                skipped_reason="dry_run",
            )

        text = self._format_text(event)
        payload = {
            "chat_id":              self._chat_id,
            "text":                 text,
            "disable_notification": int(event.severity) < 20,  # < WARN
            "parse_mode":           "HTML",
            "disable_web_page_preview": True,
        }
        url = f"{self._api_base}/bot{self._token}/sendMessage"
        body = json.dumps(payload).encode("utf-8")
        req = urlrequest.Request(  # noqa: S310 — fixed scheme = telegram api
            url, data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent":   "auto-trader/1.0 telegram-channel",
            },
            method="POST",
        )

        last_err: str | None = None
        attempts = 0
        max_attempts = self._max_retries + 1
        opener = self._opener or urlrequest.urlopen
        while attempts < max_attempts:
            attempts += 1
            try:
                with opener(req, timeout=self._timeout) as resp:  # type: ignore[arg-type]
                    status = getattr(resp, "status", None) or resp.getcode()
                    if 200 <= int(status) < 300:
                        return SendResult(ok=True, channel=self.name)
                    last_err = f"http_{status}"
            except urlerror.URLError as e:
                last_err = f"url_error:{type(e).__name__}"
            except TimeoutError as e:
                last_err = f"timeout:{e}"
            except Exception as e:  # noqa: BLE001 — channel must never raise
                last_err = f"{type(e).__name__}:{e}"
            # backoff between retries — 짧게 (총 timeout이 backend 응답을 지연
            # 시키지 않게).
            if attempts < max_attempts:
                time.sleep(min(0.5, self._timeout * 0.1))

        return SendResult(
            ok=False, channel=self.name,
            error=(last_err or "unknown_error"),
        )

    def _format_text(self, event: NotificationEvent) -> str:
        """Telegram HTML — 짧고 안전한 문자열. <b>title</b>\\n body."""
        # title / message는 NotificationEvent.__post_init__에서 secret 검사 통과
        # 한 상태. 추가로 < / > / & 만 escape.
        title = _html_escape(event.title)
        message = _html_escape(event.message)
        return f"<b>{title}</b>\n{message}"


def _html_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ---------- factory ----------


def build_channel_from_settings(settings: Any) -> NotificationChannel:
    """`Settings` 객체로부터 활성 채널 생성. NOTIFICATIONS_ENABLED=false 또는
    Telegram token/chat_id 부재 시 NoOpChannel.
    """
    enabled = bool(getattr(settings, "notifications_enabled", False))
    if not enabled:
        return NoOpChannel()
    token = (getattr(settings, "telegram_bot_token", "") or "").strip()
    chat  = (getattr(settings, "telegram_chat_id", "") or "").strip()
    if not (token and chat):
        return NoOpChannel()
    return TelegramChannel(
        bot_token=token,
        chat_id=chat,
        timeout_seconds=float(getattr(settings, "telegram_timeout_seconds", 5.0)),
        max_retries=int(getattr(settings, "telegram_max_retries", 1)),
    )
