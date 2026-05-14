"""KIS Paper readiness 가드 테스트 (#89).

본 파일은 broker / KIS 실제 API 를 호출하지 *않는다* — pure settings 검사.
"""

from __future__ import annotations

import pytest

from app.kis_paper.readiness import (
    BlockedReason,
    KisPaperReadiness,
    evaluate_readiness,
)


def _settings(**overrides) -> dict:
    """기본 안전 설정. 운영자가 overrides 로 위반 케이스 시뮬."""
    base = {
        "kis_is_paper":                True,
        "enable_live_trading":         False,
        "enable_ai_execution":         False,
        "enable_futures_live_trading": False,
        "default_mode":                "PAPER",
        "kis_app_key":                 "TESTKEY_paper_001",
        "kis_app_secret":              "TESTSECRET_paper_001",
        "kis_account_no":              "12345678-01",
    }
    base.update(overrides)
    return base


# ====================================================================
# 1. 정상 ready
# ====================================================================


def test_default_paper_settings_are_ready():
    rd = evaluate_readiness(_settings())
    assert rd.ready is True
    assert rd.can_run_kis_paper is True
    assert rd.can_run_mock is True
    assert rd.blocked_reasons == ()


def test_safety_flags_carry_in_output():
    rd = evaluate_readiness(_settings())
    assert rd.safety_flags["kis_is_paper"] is True
    assert rd.safety_flags["enable_live_trading"] is False
    assert rd.safety_flags["enable_ai_execution"] is False


def test_response_invariants_locked():
    rd = evaluate_readiness(_settings())
    d = rd.to_dict()
    assert d["is_order_intent"] is False
    assert d["is_order_signal"] is False


# ====================================================================
# 2. 차단 — LIVE flag
# ====================================================================


def test_enable_live_trading_blocks_kis_paper_and_mock():
    rd = evaluate_readiness(_settings(enable_live_trading=True))
    assert rd.ready is False
    assert rd.can_run_kis_paper is False
    assert rd.can_run_mock is False
    assert BlockedReason.ENABLE_LIVE_TRADING_TRUE in rd.blocked_reasons


def test_enable_ai_execution_blocks_both_modes():
    rd = evaluate_readiness(_settings(enable_ai_execution=True))
    assert rd.ready is False
    assert rd.can_run_kis_paper is False
    assert rd.can_run_mock is False
    assert BlockedReason.ENABLE_AI_EXECUTION_TRUE in rd.blocked_reasons


def test_enable_futures_live_blocks_test():
    rd = evaluate_readiness(_settings(enable_futures_live_trading=True))
    assert rd.ready is False
    assert BlockedReason.ENABLE_FUTURES_LIVE_TRUE in rd.blocked_reasons


# ====================================================================
# 3. KIS_IS_PAPER=false 차단
# ====================================================================


def test_kis_is_paper_false_blocks_kis_paper_mode():
    rd = evaluate_readiness(_settings(kis_is_paper=False))
    assert rd.can_run_kis_paper is False
    assert BlockedReason.KIS_IS_PAPER_FALSE in rd.blocked_reasons
    # mock 모드는 KIS_IS_PAPER 와 독립 — 안전 flag 만 봄.
    assert rd.can_run_mock is True


# ====================================================================
# 4. KIS key 누락 — capability 만 영향, 차단 reason 아님
# ====================================================================


def test_missing_kis_key_disables_paper_mode_only():
    rd = evaluate_readiness(_settings(kis_app_key=""))
    assert rd.kis_key_present is False
    assert rd.can_run_kis_paper is False
    assert rd.can_run_mock is True
    # blocker 가 아니라 detail 안내.
    assert any("KIS_APP_KEY 미설정" in m for m in rd.detail_messages)


def test_missing_kis_secret_disables_paper_mode():
    rd = evaluate_readiness(_settings(kis_app_secret=""))
    assert rd.kis_secret_present is False
    assert rd.can_run_kis_paper is False


def test_missing_kis_account_disables_paper_mode():
    rd = evaluate_readiness(_settings(kis_account_no=""))
    assert rd.kis_account_present is False
    assert rd.can_run_kis_paper is False


# ====================================================================
# 5. LIVE_* default_mode 차단 (LIVE_SHADOW 는 예외)
# ====================================================================


def test_default_mode_live_manual_blocks():
    rd = evaluate_readiness(_settings(default_mode="LIVE_MANUAL_APPROVAL"))
    assert BlockedReason.DEFAULT_MODE_LIVE in rd.blocked_reasons


def test_default_mode_live_ai_execution_blocks():
    rd = evaluate_readiness(_settings(default_mode="LIVE_AI_EXECUTION"))
    assert BlockedReason.DEFAULT_MODE_LIVE in rd.blocked_reasons


def test_default_mode_live_shadow_is_allowed():
    """LIVE_SHADOW 는 read-only 모드 — readiness 차단하지 않음."""
    rd = evaluate_readiness(_settings(default_mode="LIVE_SHADOW"))
    assert BlockedReason.DEFAULT_MODE_LIVE not in rd.blocked_reasons


# ====================================================================
# 6. Secret 자체 미출력 — *_present bool 만 carry
# ====================================================================


def test_readiness_does_not_carry_secret_values():
    s = _settings(kis_app_secret="REAL_SECRET_VALUE_LONG_AND_SENSITIVE")
    rd = evaluate_readiness(s)
    blob = str(rd.to_dict())
    # secret 원문이 응답 dict 에 노출되지 않아야 한다.
    assert "REAL_SECRET_VALUE_LONG_AND_SENSITIVE" not in blob


def test_readiness_does_not_carry_kis_account_no_value():
    s = _settings(kis_account_no="87654321-99")
    rd = evaluate_readiness(s)
    blob = str(rd.to_dict())
    assert "87654321" not in blob


def test_readiness_does_not_carry_kis_app_key_value():
    s = _settings(kis_app_key="PSTSecretLookingValue1234567890abcdef")
    rd = evaluate_readiness(s)
    blob = str(rd.to_dict())
    assert "PSTSecretLookingValue1234567890abcdef" not in blob


# ====================================================================
# 7. invariant dataclass guard
# ====================================================================


def test_readiness_rejects_true_is_order_intent():
    with pytest.raises(ValueError):
        KisPaperReadiness(
            ready=True, can_run_kis_paper=True, can_run_mock=True,
            is_order_intent=True,
        )


def test_readiness_rejects_true_is_order_signal():
    with pytest.raises(ValueError):
        KisPaperReadiness(
            ready=True, can_run_kis_paper=True, can_run_mock=True,
            is_order_signal=True,
        )
