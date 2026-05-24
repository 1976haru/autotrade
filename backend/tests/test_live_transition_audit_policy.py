"""#45 / 5-05 — Live 전환 감사 로그 문서 안전성 테스트.

검증: 필수 정책 문구 존재 + 금지 문구/secret/account 부재.
금지 문자열은 동적 조립해 테스트 소스에 리터럴로 남기지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

_DOC = Path(__file__).resolve().parents[2] / "docs" / "live_transition_audit_log.md"


def _text() -> str:
    return _DOC.read_text(encoding="utf-8")


def test_doc_exists():
    assert _DOC.exists()
    assert len(_text()) > 1500


def test_append_only_phrase():
    text = _text()
    assert "append-only" in text
    assert "수정/삭제" in text


def test_required_field_phrases():
    text = _text()
    for kw in ("operator", "reason", "timestamp", "risk_profile",
               "symbol_whitelist", "max_order_notional", "daily_live_limit",
               "capital_review", "paper_gate", "canary_gate", "manual_approval"):
        assert kw in text, f"'{kw}' 문구가 없습니다"


def test_not_order_signal_phrase():
    text = _text()
    assert "주문 신호가 아" in text or "주문 신호/신호가 아" in text or "주문/실전 승인이 아" in text


def test_not_live_approval_phrase():
    text = _text()
    assert "실전 주문은 생성되지 않" in text or "실전 주문은 만들어지지 않" in text


def test_secret_storage_forbidden_phrase():
    text = _text()
    assert "저장되지 않" in text or "기록을 거부" in text or "저장 금지" in text


def test_correction_via_new_event_phrase():
    text = _text()
    assert "새 이벤트" in text


def test_no_profit_or_auto_live_claims():
    text = _text()
    for phrase in ["수익 " + "보장", "월 수익 " + "보장",
                   "자동 " + "실전 전환", "실전 전환 " + "승인"]:
        assert phrase not in text, f"금지 문구: {phrase!r}"


def test_no_live_flag_activation_examples():
    text = _text()
    eq_true = "=" + "true"
    eq_false = "=" + "false"
    for flag in ("ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
                 "ENABLE_FUTURES_LIVE_TRADING"):
        assert (flag + eq_true) not in text
    assert ("KIS_IS_PAPER" + eq_false) not in text


def test_no_secret_or_account_like_values():
    text = _text()
    for pat in [
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        re.compile(r"Bearer\s+[A-Za-z0-9\.\-_]{20,}"),
        re.compile(r"(?:access_token|refresh_token)\s*=\s*[A-Za-z0-9]{8,}"),
        re.compile(r"\b\d{8}-\d{2}\b"),
        re.compile(r"\b\d{10,14}\b"),
    ]:
        assert not pat.search(text), f"secret/account-like 탐지: {pat.pattern}"
