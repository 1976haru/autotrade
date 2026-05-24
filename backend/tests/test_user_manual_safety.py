"""#68 / 8-06 — 운영자 매뉴얼(docs/user_manual.md) 안전성 테스트.

검증:
- 문서 존재
- 실전 방향 flag 활성화 문구 0건 (LIVE/AI/FUTURES = true, KIS_IS_PAPER = false)
- 실제 secret-like / account-like 값 0건
- "수익 보장" / "자동 실전 전환" 류 과장/오인 문구 0건
- "실전 전환은 별도 승인" 필요 문구 존재
- MARKET_CLOSED / NO_MARKET_DATA / CREDENTIALS_MISSING 설명 존재
- 문제 보고 양식 존재

주의: 금지 문자열은 *동적으로 조립*해 테스트 소스에 리터럴로 남기지 않는다
(정적 스캐너 / repo hygiene 가 테스트 파일을 오탐하지 않도록).
"""

from __future__ import annotations

import re
from pathlib import Path

_MANUAL = Path(__file__).resolve().parents[2] / "docs" / "user_manual.md"


def _text() -> str:
    return _MANUAL.read_text(encoding="utf-8")


def test_manual_exists():
    assert _MANUAL.exists(), "docs/user_manual.md 가 없습니다"
    assert len(_text()) > 2000, "매뉴얼 내용이 너무 짧습니다"


def test_no_live_flag_activation_phrases():
    text = _text()
    # '=true' 를 직접 리터럴로 쓰지 않고 조립 — 테스트 소스에 위험 리터럴 0건.
    eq_true = "=" + "true"
    eq_false = "=" + "false"
    for flag in ("ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
                 "ENABLE_FUTURES_LIVE_TRADING"):
        assert (flag + eq_true) not in text, f"{flag} 활성화 문구가 있으면 안 됨"
    # KIS_IS_PAPER 는 false 권유가 있으면 안 됨.
    assert ("KIS_IS_PAPER" + eq_false) not in text


def test_no_profit_or_auto_live_claims():
    text = _text()
    banned = [
        "수익 " + "보장",
        "월 수익 " + "보장",
        "무조건 " + "성공",
        "자동 " + "실전 전환",
        "자동으로 " + "실전 전환",
    ]
    for phrase in banned:
        assert phrase not in text, f"금지 문구: {phrase!r}"


def test_no_secret_like_values():
    text = _text()
    patterns = [
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        re.compile(r"xox[bpaoist]-[A-Za-z0-9-]{10,}"),
        re.compile(r"Bearer\s+[A-Za-z0-9\.\-_]{20,}"),
        re.compile(r"eyJ[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}"),
        re.compile(r"(?:access_token|refresh_token)\s*=\s*[A-Za-z0-9]{8,}"),
    ]
    for pat in patterns:
        assert not pat.search(text), f"secret-like 값 탐지: {pat.pattern}"


def test_no_account_like_numbers():
    text = _text()
    # 한국 계좌번호 형식(8-2) 같은 실제 숫자열 0건. placeholder(<YOUR_...>)는 허용.
    assert not re.search(r"\b\d{8}-\d{2}\b", text), "계좌번호 형식 숫자열이 있으면 안 됨"
    assert not re.search(r"\b\d{10,14}\b", text), "긴 숫자열(계좌 의심)이 있으면 안 됨"


def test_requires_separate_live_approval_phrase():
    assert "실전 전환은 별도 승인" in _text()


def test_explains_does_not_guarantee_profit():
    # 오인 방지 문구가 명시되어야 한다.
    assert "수익을 보장하지 않" in _text()


def test_error_codes_explained():
    text = _text()
    for code in ("MARKET_CLOSED", "NO_MARKET_DATA", "CREDENTIALS_MISSING"):
        assert code in text, f"{code} 설명이 없습니다"


def test_problem_report_form_present():
    assert "[문제 보고 양식]" in _text()


def test_uses_placeholder_for_credentials():
    text = _text()
    # 자격은 placeholder 로만 안내.
    assert "<YOUR_KIS_APP_KEY>" in text
    assert "<YOUR_KIS_APP_SECRET>" in text
    assert "<YOUR_KIS_ACCOUNT_NO>" in text


def test_safe_flag_values_present():
    text = _text()
    eq_false = "=" + "false"
    # 안전값 안내가 있어야 한다.
    assert ("ENABLE_LIVE_TRADING" + eq_false) in text
    assert ("KIS_IS_PAPER" + "=" + "true") in text
