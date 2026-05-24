"""#69 / 8-07 — 장애 대응 Runbook(docs/runbook.md) 안전성 테스트.

검증:
- 문서 존재
- 필수 오류 코드 + 대응 존재
- 문제 보고 양식 + secret 전달 금지 문구 존재
- 실제 secret-like / account-like 값 0건
- "수익 보장" / "자동 실전 전환" 류 문구 0건
- 실거래 OFF / KIS_IS_PAPER true 안전 문구 존재
- MARKET_CLOSED 정상 가능 문구 존재
- LIVE 활성화 / KIS_IS_PAPER=false 권유 문구 0건

주의: 금지 문자열은 동적 조립해 테스트 소스에 리터럴로 남기지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

_RUNBOOK = Path(__file__).resolve().parents[2] / "docs" / "runbook.md"


def _text() -> str:
    return _RUNBOOK.read_text(encoding="utf-8")


def test_runbook_exists():
    assert _RUNBOOK.exists(), "docs/runbook.md 가 없습니다"
    assert len(_text()) > 2000


def test_required_error_codes_present():
    text = _text()
    for code in (
        "NO_MARKET_DATA", "CREDENTIALS_MISSING", "ORDER_REJECTED",
        "MARKET_CLOSED", "BACKEND_FAIL", "SIDECAR_STOPPED", "DB_FAIL",
        "PRICE_STALE", "BLOCKED_BY_KIS_READINESS", "FILL_POLLING_FAIL",
        "PORTFOLIO_DRIFT", "SECRET_EXPOSURE_SUSPECTED", "UPDATE_FAILED",
        "VERSION_MISMATCH", "RISK_FLAGS_EXCEEDED", "EXIT_PLAN_INVALID",
    ):
        assert code in text, f"{code} 대응이 없습니다"


def test_first_five_steps_present():
    text = _text()
    assert "가장 먼저 해야 할 5단계" in text
    assert "EXE 연결 상태" in text
    assert "Preflight" in text


def test_immediate_stop_criteria_present():
    assert "즉시 중단" in _text()


def test_problem_report_form_present():
    assert "[장애 보고 양식]" in _text()


def test_secret_forbidden_to_send_phrase_present():
    text = _text()
    assert "전달하면 안 되는 정보" in text or "전달 금지" in text


def test_no_live_flag_activation_phrases():
    text = _text()
    eq_true = "=" + "true"
    eq_false = "=" + "false"
    for flag in ("ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
                 "ENABLE_FUTURES_LIVE_TRADING"):
        assert (flag + eq_true) not in text
    assert ("KIS_IS_PAPER" + eq_false) not in text


def test_no_profit_or_auto_live_claims():
    text = _text()
    for phrase in ["수익 " + "보장", "월 수익 " + "보장",
                   "무조건 " + "성공", "자동 " + "실전 전환",
                   "자동으로 " + "실전 전환"]:
        assert phrase not in text, f"금지 문구: {phrase!r}"


def test_no_secret_like_values():
    text = _text()
    for pat in [
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        re.compile(r"Bearer\s+[A-Za-z0-9\.\-_]{20,}"),
        re.compile(r"eyJ[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}"),
        re.compile(r"(?:access_token|refresh_token)\s*=\s*[A-Za-z0-9]{8,}"),
    ]:
        assert not pat.search(text), f"secret-like 값 탐지: {pat.pattern}"


def test_no_account_like_numbers():
    text = _text()
    assert not re.search(r"\b\d{8}-\d{2}\b", text)
    assert not re.search(r"\b\d{10,14}\b", text)


def test_market_closed_normal_phrase_present():
    text = _text()
    assert "MARKET_CLOSED" in text
    assert "정상" in text  # 장 닫힌 날 정상 가능 안내


def test_safe_flags_off_phrase_present():
    text = _text()
    # 실거래 OFF / KIS_IS_PAPER true 안전값 안내가 있어야 한다.
    assert "실거래 OFF" in text
    assert ("KIS_IS_PAPER" + " true") in text or ("KIS_IS_PAPER" + "=" + "true") in text
