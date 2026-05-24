"""#44 / 5-04 — Paper Gate 성과 기준 문서 안전성 테스트.

검증: 필수 정책 문구 존재 + 금지 문구/secret/account 부재.
금지 문자열은 동적 조립해 테스트 소스에 리터럴로 남기지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

_DOC = Path(__file__).resolve().parents[2] / "docs" / "paper_gate_performance_criteria.md"


def _text() -> str:
    return _DOC.read_text(encoding="utf-8")


def test_doc_exists():
    assert _DOC.exists()
    assert len(_text()) > 1500


def test_sample_criteria_present():
    text = _text()
    assert "28" in text and "거래일" in text
    assert "100" in text and "건" in text


def test_performance_criteria_phrases():
    text = _text()
    for kw in ("승률", "win_rate", "payoff_ratio", "profit_factor",
               "max_drawdown", "연속 손실", "order_failure_rate",
               "portfolio_drift", "event_integrity"):
        assert kw in text, f"'{kw}' 기준 문구가 없습니다"


def test_block_phrases():
    text = _text()
    assert "live promotion" in text and "차단" in text


def test_no_auto_promotion_phrase_present():
    # 자동 전환 금지 정책이 명시되어야 한다 (negation).
    text = _text()
    assert "auto live promotion 금지" in text or "전환되지 않습니다" in text
    assert "auto_live_promotion=false" in text or "auto_live_promotion" in text


def test_next_gates_listed():
    text = _text()
    assert "Live Capital Review" in text
    assert "Manual Approval" in text
    assert "Canary" in text


def test_no_profit_or_affirmative_auto_live_claims():
    text = _text()
    for phrase in ["수익 " + "보장", "월 수익 " + "보장",
                   "자동 " + "실전 전환", "자동으로 " + "실전 전환",
                   "이제 " + "실전 가능"]:
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
