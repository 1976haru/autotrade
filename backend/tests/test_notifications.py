"""체크리스트 #64: NotificationService / TelegramChannel / templates 테스트.

검증 invariant:
  - NotificationEvent는 Secret 의심 패턴이 message에 있으면 ValueError
  - NotificationService.notify는 *raise하지 않는다*
  - dedupe_window_seconds 안에 같은 dedupe_key는 skipped
  - CRITICAL은 always_send_critical=True이면 dedupe 우회
  - severity < min_severity는 skipped
  - enabled=false면 모든 이벤트 skipped
  - TelegramChannel.send는 token / chat_id가 없으면 not_configured skip
  - TelegramChannel은 raise하지 않고 SendResult 반환
  - status()는 Secret 미포함
"""

from __future__ import annotations

import json

import pytest

from app.notifications.channels import (
    NoOpChannel,
    TelegramChannel,
)
from app.notifications.service import (
    NotificationService,
    build_service_from_settings,
)
from app.notifications.templates import (
    build_approval_pending_event,
    build_broker_error_event,
    build_daily_loss_warning_event,
    build_data_stale_event,
    build_emergency_stop_event,
    build_margin_risk_event,
    build_repeated_rejection_event,
    build_risk_auditor_event,
)
from app.notifications.types import (
    NotificationEvent,
    NotificationKind,
    NotificationSeverity,
    SendResult,
)


# ====================================================================
# NotificationEvent invariants
# ====================================================================


def test_event_rejects_kis_token_pattern_in_message():
    with pytest.raises(ValueError, match="forbidden token pattern"):
        NotificationEvent(
            kind=NotificationKind.TEST,
            severity=NotificationSeverity.INFO,
            title="leak",
            message="found KIS_APP_KEY=ABC123",
        )


def test_event_rejects_anthropic_secret_pattern():
    with pytest.raises(ValueError, match="forbidden"):
        NotificationEvent(
            kind=NotificationKind.TEST,
            severity=NotificationSeverity.INFO,
            title="leak",
            message="ANTHROPIC_API_KEY=sk-xyz",
        )


def test_event_rejects_telegram_token_pattern():
    with pytest.raises(ValueError, match="forbidden"):
        NotificationEvent(
            kind=NotificationKind.TEST,
            severity=NotificationSeverity.INFO,
            title="leak",
            message="my TELEGRAM_BOT_TOKEN was 12345:abc",
        )


def test_event_accepts_clean_message():
    event = NotificationEvent(
        kind=NotificationKind.TEST,
        severity=NotificationSeverity.INFO,
        title="ok",
        message="이것은 평범한 운영자 알림입니다.",
    )
    assert event.title == "ok"


# ====================================================================
# NoOpChannel
# ====================================================================


def test_noop_channel_always_skipped():
    ch = NoOpChannel()
    assert ch.is_configured() is False
    event = _basic_event()
    result = ch.send(event)
    assert result.ok is True
    assert result.skipped_reason == "noop_channel"


# ====================================================================
# TelegramChannel
# ====================================================================


def test_telegram_channel_skipped_when_not_configured():
    ch = TelegramChannel(bot_token="", chat_id="")
    assert ch.is_configured() is False
    result = ch.send(_basic_event())
    assert result.ok is False
    assert result.skipped_reason == "not_configured"


def test_telegram_channel_dry_run():
    ch = TelegramChannel(
        bot_token="123:abc", chat_id="999", dry_run=True,
    )
    assert ch.is_configured() is True
    result = ch.send(_basic_event())
    assert result.ok is True
    assert result.skipped_reason == "dry_run"


def test_telegram_channel_calls_opener_with_post_to_send_message():
    """opener를 주입해 실제 네트워크 호출 없이 검증."""
    calls = []

    class _FakeResponse:
        def __init__(self, status=200):
            self.status = status
        def getcode(self):
            return self.status
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_opener(req, timeout=None):
        calls.append({
            "url":     req.full_url,
            "method":  req.get_method(),
            "headers": dict(req.header_items()),
            "data":    req.data,
            "timeout": timeout,
        })
        return _FakeResponse(200)

    ch = TelegramChannel(
        bot_token="123:abc", chat_id="999",
        http_opener=fake_opener, timeout_seconds=3.0,
    )
    result = ch.send(_basic_event())
    assert result.ok is True
    assert len(calls) == 1
    call = calls[0]
    assert "api.telegram.org" in call["url"]
    assert "/sendMessage" in call["url"]
    assert call["method"] == "POST"
    # body에 chat_id + text 포함
    body = json.loads(call["data"].decode("utf-8"))
    assert body["chat_id"] == "999"
    assert "text" in body and len(body["text"]) > 0


def test_telegram_channel_does_not_raise_on_network_error():
    def raising_opener(req, timeout=None):  # noqa: ARG001
        raise OSError("simulated network error")
    ch = TelegramChannel(
        bot_token="123:abc", chat_id="999",
        http_opener=raising_opener, max_retries=0,
    )
    result = ch.send(_basic_event())
    # raise되지 않음
    assert result.ok is False
    assert result.error is not None
    assert "OSError" in result.error or "simulated" in result.error


def test_telegram_channel_does_not_raise_on_arbitrary_exception():
    def insane_opener(req, timeout=None):  # noqa: ARG001
        raise RuntimeError("anything can happen")
    ch = TelegramChannel(
        bot_token="123:abc", chat_id="999",
        http_opener=insane_opener, max_retries=0,
    )
    result = ch.send(_basic_event())
    assert result.ok is False


def test_telegram_channel_retries_on_failure_then_succeeds():
    counter = {"n": 0}

    class _Resp:
        def __init__(self, status):
            self.status = status
        def getcode(self):
            return self.status
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def flaky_opener(req, timeout=None):  # noqa: ARG001
        counter["n"] += 1
        if counter["n"] == 1:
            raise OSError("first attempt fails")
        return _Resp(200)

    ch = TelegramChannel(
        bot_token="123:abc", chat_id="999",
        http_opener=flaky_opener, max_retries=1,
        timeout_seconds=1.0,
    )
    result = ch.send(_basic_event())
    assert result.ok is True
    assert counter["n"] == 2


# ====================================================================
# NotificationService — gates
# ====================================================================


def test_service_skips_when_disabled():
    ch = _RecordingChannel()
    service = NotificationService(channel=ch, enabled=False)
    result = service.notify(_basic_event())
    assert result.ok is True
    assert result.skipped_reason == "notifications_disabled"
    assert ch.sent == []


def test_service_skips_when_below_min_severity():
    ch = _RecordingChannel()
    service = NotificationService(
        channel=ch, enabled=True,
        min_severity=int(NotificationSeverity.WARN),
    )
    info_event = NotificationEvent(
        kind=NotificationKind.TEST,
        severity=NotificationSeverity.INFO,
        title="info", message="below",
    )
    result = service.notify(info_event)
    assert result.skipped_reason == "below_min_severity"
    assert ch.sent == []


def test_service_dedupes_within_window():
    ch = _RecordingChannel()
    service = NotificationService(
        channel=ch, enabled=True,
        min_severity=int(NotificationSeverity.INFO),
        dedupe_window_seconds=60,
    )
    e = NotificationEvent(
        kind=NotificationKind.TEST,
        severity=NotificationSeverity.WARN,
        title="t", message="m",
        dedupe_key="same-key",
    )
    r1 = service.notify(e)
    r2 = service.notify(e)
    assert r1.ok is True and r1.skipped_reason is None
    assert r2.ok is True and r2.skipped_reason == "deduped"
    assert len(ch.sent) == 1


def test_critical_bypasses_dedupe_when_always_send_critical():
    ch = _RecordingChannel()
    service = NotificationService(
        channel=ch, enabled=True, dedupe_window_seconds=60,
        always_send_critical=True,
    )
    e = NotificationEvent(
        kind=NotificationKind.EMERGENCY_STOP,
        severity=NotificationSeverity.CRITICAL,
        title="STOP", message="critical",
        dedupe_key="emergency",
    )
    r1 = service.notify(e)
    r2 = service.notify(e)
    assert r1.ok and r2.ok
    assert r1.skipped_reason is None
    assert r2.skipped_reason is None
    assert len(ch.sent) == 2


def test_service_never_raises_when_channel_raises():
    class _BadChannel:
        name = "bad"
        def is_configured(self): return True
        def send(self, event):  # noqa: ARG002
            raise RuntimeError("channel broken")
    service = NotificationService(channel=_BadChannel(), enabled=True)
    # raise되지 않음
    result = service.notify(_basic_event())
    assert result.ok is False
    assert "RuntimeError" in (result.error or "")


def test_status_excludes_secrets():
    ch = TelegramChannel(bot_token="123:secret", chat_id="999",
                         dry_run=True)
    service = NotificationService(channel=ch, enabled=True)
    status = service.status()
    # Secret 값이 status dict 어디에도 들어가지 *않는다*.
    serialized = json.dumps(status)
    assert "123:secret" not in serialized
    assert "999" not in serialized
    # 그래도 boolean 정보는 노출
    assert status["channel"] == "telegram"
    assert status["channel_configured"] is True


# ====================================================================
# templates
# ====================================================================


def test_emergency_stop_on_is_critical():
    e = build_emergency_stop_event(enabled=True, level="LEVEL_1",
                                    reason_code="manual_operator",
                                    decided_by="ops1")
    assert e.severity == NotificationSeverity.CRITICAL
    assert "LEVEL_1" in e.message
    assert "ops1" in e.message


def test_emergency_stop_off_is_info():
    e = build_emergency_stop_event(enabled=False, decided_by="ops1")
    assert e.severity == NotificationSeverity.INFO


def test_data_stale_event_carries_symbol():
    e = build_data_stale_event(symbol="005930", age_seconds=120,
                                threshold_seconds=60)
    assert "005930" in e.message
    assert "120" in e.message
    assert e.severity == NotificationSeverity.WARN


def test_approval_pending_event_dedupe_per_approval_id():
    e = build_approval_pending_event(
        approval_id=42, symbol="005930", side="BUY", quantity=10,
        strategy="sma", requested_by_ai=True,
    )
    assert "approval_pending:42" in e.dedupe_key
    assert "AI 제안: 예" in e.message


def test_daily_loss_warning_severity_escalates():
    e1 = build_daily_loss_warning_event(current_loss=-50_000, limit=200_000)
    e2 = build_daily_loss_warning_event(current_loss=-150_000, limit=200_000)
    e3 = build_daily_loss_warning_event(current_loss=-190_000, limit=200_000)
    assert e1.severity == NotificationSeverity.INFO
    assert e2.severity == NotificationSeverity.WARN
    assert e3.severity == NotificationSeverity.CRITICAL


def test_broker_error_is_critical():
    e = build_broker_error_event(broker="kis", operation="get_balance",
                                 message="timeout after 5s")
    assert e.severity == NotificationSeverity.CRITICAL


def test_repeated_rejection_is_warn():
    e = build_repeated_rejection_event(count=5, window_seconds=60, threshold=3)
    assert e.severity == NotificationSeverity.WARN


def test_margin_risk_severity_based_on_distance():
    e_safe = build_margin_risk_event(used_pct=60, liquidation_distance_pct=10)
    e_warn = build_margin_risk_event(used_pct=80, liquidation_distance_pct=8)
    e_crit = build_margin_risk_event(used_pct=92, liquidation_distance_pct=2)
    assert e_safe.severity == NotificationSeverity.WARN
    assert e_warn.severity == NotificationSeverity.WARN
    assert e_crit.severity == NotificationSeverity.CRITICAL


def test_risk_auditor_event_escalates_with_audit_level():
    e_green = build_risk_auditor_event(audit_level="GREEN", risk_score=10,
                                       summary="ok")
    e_yellow = build_risk_auditor_event(audit_level="YELLOW", risk_score=40,
                                        summary="caution",
                                        pause_recommended=True)
    e_red = build_risk_auditor_event(audit_level="RED", risk_score=85,
                                     summary="serious",
                                     emergency_recommended=True)
    assert e_green.severity == NotificationSeverity.INFO
    assert e_yellow.severity == NotificationSeverity.WARN
    assert e_red.severity == NotificationSeverity.CRITICAL


# ====================================================================
# build_service_from_settings + factory
# ====================================================================


def test_build_service_from_settings_with_empty_token_uses_noop():
    settings = _FakeSettings(
        notifications_enabled=True,
        telegram_bot_token="",
        telegram_chat_id="",
    )
    service = build_service_from_settings(settings)
    assert service.enabled is False
    assert service._channel_name() == "noop"


def test_build_service_from_settings_disabled_uses_noop_even_with_token():
    settings = _FakeSettings(
        notifications_enabled=False,
        telegram_bot_token="123:secret",
        telegram_chat_id="999",
    )
    service = build_service_from_settings(settings)
    assert service.enabled is False
    assert service._channel_name() == "noop"


def test_build_service_from_settings_with_token_and_enabled_uses_telegram():
    settings = _FakeSettings(
        notifications_enabled=True,
        telegram_bot_token="123:secret",
        telegram_chat_id="999",
    )
    service = build_service_from_settings(settings)
    assert service.enabled is True
    assert service._channel_name() == "telegram"
    # status도 secret 미포함
    s_json = json.dumps(service.status())
    assert "123:secret" not in s_json
    assert "999" not in s_json


# ====================================================================
# helpers
# ====================================================================


def _basic_event() -> NotificationEvent:
    return NotificationEvent(
        kind=NotificationKind.TEST,
        severity=NotificationSeverity.WARN,
        title="hello", message="world",
    )


class _RecordingChannel:
    name = "recording"
    def __init__(self):
        self.sent = []
    def is_configured(self):
        return True
    def send(self, event):
        self.sent.append(event)
        return SendResult(ok=True, channel=self.name)


class _FakeSettings:
    def __init__(self, **overrides):
        defaults = dict(
            notifications_enabled=False,
            notifications_min_severity="INFO",
            notifications_dedupe_window_seconds=60,
            notifications_always_send_critical=True,
            telegram_bot_token="",
            telegram_chat_id="",
            telegram_timeout_seconds=5.0,
            telegram_max_retries=1,
        )
        defaults.update(overrides)
        for k, v in defaults.items():
            setattr(self, k, v)
