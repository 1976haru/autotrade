"""#43 / 5-03 — 실전 Canary Gate 문서 안전성 테스트.

검증: 필수 정책 문구 존재 + 금지 문구/secret/account 부재.
금지 문자열은 동적 조립해 테스트 소스에 리터럴로 남기지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

_DOC = Path(__file__).resolve().parents[2] / "docs" / "live_canary_gate.md"


def _text() -> str:
    return _DOC.read_text(encoding="utf-8")


def test_doc_exists():
    assert _DOC.exists()
    assert len(_text()) > 1500


def test_required_prereq_phrases():
    text = _text()
    for kw in ("Paper Gate", "Live Capital Review", "Manual Approval",
               "Operator Approval", "Symbol Whitelist", "1일 1건",
               "최소 주문금액", "최대 주문금액", "daily live notional",
               "LIVE_AI_EXECUTION"):
        assert kw in text, f"'{kw}' 문구가 없습니다"


def test_ai_execution_blocked_before_gate_phrase():
    text = _text()
    assert "LIVE_AI_EXECUTION" in text
    assert "canary gate 통과 전 불가" in text or "canary 전 불가" in text


def test_gate_pass_not_order_phrase():
    text = _text()
    assert "실제 실전 주문은 생성되지 않" in text or "실제 주문은 생성되지 않" in text
    assert "자동 주문이 아닙니다" in text or "자동 실행 아님" in text


def test_reason_codes_documented():
    text = _text()
    for code in ("CANARY_PAPER_GATE_REQUIRED", "CANARY_AI_EXECUTION_BLOCKED",
                 "CANARY_DAILY_ORDER_LIMIT_REQUIRED",
                 "CANARY_MIN_ORDER_NOTIONAL_REQUIRED",
                 "CANARY_MAX_ORDER_NOTIONAL_TOO_HIGH",
                 "CANARY_RISK_PROFILE_REQUIRED", "CANARY_REVIEW_READY"):
        assert code in text, f"reason_code {code} 미문서"


def test_default_blocked_phrase():
    assert "기본 차단" in _text() or "기본 상태" in _text() or "항상 차단" in _text()


def test_no_profit_or_auto_live_or_now_live_claims():
    text = _text()
    for phrase in ["수익 " + "보장", "자동 " + "실전 전환", "실전 전환 " + "승인",
                   "이제 " + "실전"]:
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
