"""#42 / 5-02 — Live Manual Approval Gate 문서 안전성 테스트.

검증:
- docs/live_manual_approval_gate.md 존재
- ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION 만으로 주문 불가 문구 존재
- Live Capital Review / Manual Approval / Operator Approval / Symbol Whitelist /
  Max notional / Daily live limit 필요 문구 존재
- 기본 차단 + reason_code 설명 존재
- 실제 secret-like / account-like 값 0건
- "수익 보장" / "자동 실전 전환" 류 문구 0건
- LIVE flag 활성화 설정 예시(=true) 0건, KIS_IS_PAPER=false 0건

주의: 금지 문자열은 동적 조립해 테스트 소스에 리터럴로 남기지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

_DOC = Path(__file__).resolve().parents[2] / "docs" / "live_manual_approval_gate.md"


def _text() -> str:
    return _DOC.read_text(encoding="utf-8")


def test_doc_exists():
    assert _DOC.exists()
    assert len(_text()) > 1500


def test_enable_flags_alone_insufficient_phrase():
    text = _text()
    assert "ENABLE_LIVE_TRADING" in text
    assert "ENABLE_AI_EXECUTION" in text
    assert "자동 허용되지 않" in text or "주문되지 않" in text


def test_required_conditions_present():
    text = _text()
    for kw in ("Live Capital Review", "Manual Approval", "Operator Approval",
               "Symbol Whitelist", "Max order notional", "Daily live limit"):
        assert kw in text, f"'{kw}' 설명이 없습니다"


def test_default_blocked_phrase():
    assert "기본적으로 차단" in _text() or "기본 차단" in _text() or "차단 상태" in _text()


def test_reason_codes_documented():
    text = _text()
    for code in ("LIVE_MANUAL_APPROVAL_REQUIRED", "LIVE_CAPITAL_REVIEW_REQUIRED",
                 "SYMBOL_WHITELIST_REQUIRED", "MAX_ORDER_NOTIONAL_REQUIRED",
                 "DAILY_LIVE_LIMIT_REQUIRED", "PAPER_APPROVAL_NOT_LIVE_APPROVAL"):
        assert code in text, f"reason_code {code} 설명이 없습니다"


def test_paper_live_separation_phrase():
    assert "Paper" in _text() and "실전 승인" in _text()


def test_no_profit_or_auto_live_claims():
    text = _text()
    for phrase in ["수익 " + "보장", "자동 " + "실전 전환", "실전 전환 " + "승인"]:
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
