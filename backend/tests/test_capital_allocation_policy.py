"""P-13: 물타기 / 추가매수 / 피라미딩 정책 — 문서 + 단위 + API + 정적 가드.

검증 항목 (사용자 요청서 §6 1-20):
 1. capital_allocation_policy.md 존재
 2. 문서에 "물타기" 포함
 3. 문서에 "추가매수" 포함
 4. 문서에 "기본 금지" 포함
 5. allow_additional_buy 기본 False
 6. allow_averaging_down 기본 False
 7. allow_pyramiding 기본 False
 8-10. 보수/안정/공격 모두 추가매수 기본 False
 11. 공격형 자동 물타기 허용 아님
 12. 옵트인 True 도 RiskManager / PermissionGate 우회 안 함
 13-15. policy response 에 message 3종 포함
 16-18. reason_code 3종 존재 (ADDITIONAL_BUY_DISABLED / AVERAGING_DOWN_DISABLED / PYRAMIDING_DISABLED)
 19. 정적 grep — 실거래 미변경
 20. 문서에 Live AI Execution 금지 명시
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auto_paper.additional_buy_policy import (
    ADDITIONAL_BUY_DISABLED,
    ADDITIONAL_BUY_DISABLED_MESSAGE_KO,
    AVERAGING_DOWN_DISABLED,
    AVERAGING_DOWN_DISABLED_MESSAGE_KO,
    CapitalAllocationPolicyResult,
    PYRAMIDING_DISABLED,
    PYRAMIDING_DISABLED_MESSAGE_KO,
    policy_message_for,
    reason_code_for,
    resolve_capital_allocation_policy,
)
from app.auto_paper.capital_config import (
    DEFAULT_ALLOW_ADDITIONAL_BUY,
    DEFAULT_ALLOW_AVERAGING_DOWN,
    DEFAULT_ALLOW_PYRAMIDING,
    reset_paper_capital_for_tests,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app


_DOC_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "capital_allocation_policy.md"
)
_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "additional_buy_policy.py"
)
_CAPITAL_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "capital_config.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_store():
    reset_paper_capital_for_tests()
    yield
    reset_paper_capital_for_tests()


@pytest.fixture
def api_client():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False,
    )

    def _override_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 정책 문서 존재 + 필수 문구 (검증 1-4, 20)
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicyDoc:
    def test_doc_exists(self):
        # 검증 1.
        assert _DOC_PATH.exists(), f"{_DOC_PATH} 누락"

    def test_doc_contains_averaging_down_term(self):
        # 검증 2.
        text = _DOC_PATH.read_text(encoding="utf-8")
        assert "물타기" in text

    def test_doc_contains_additional_buy_term(self):
        # 검증 3.
        text = _DOC_PATH.read_text(encoding="utf-8")
        assert "추가매수" in text

    def test_doc_contains_default_forbidden(self):
        # 검증 4.
        text = _DOC_PATH.read_text(encoding="utf-8")
        assert "기본 금지" in text

    def test_doc_mentions_pyramiding(self):
        text = _DOC_PATH.read_text(encoding="utf-8")
        assert "피라미딩" in text

    def test_doc_mentions_live_ai_execution_forbidden(self):
        # 검증 20: Live AI Execution 은 금지 명시.
        text = _DOC_PATH.read_text(encoding="utf-8")
        # 두 표현 중 하나라도 있으면 통과.
        assert (
            "Live AI Execution" in text
            or "LIVE_AI_EXECUTION" in text
        )
        # *금지* 또는 *영구 미허가* 표현 명시.
        assert "금지" in text

    def test_doc_p13_section_present(self):
        # P-13 섹션 존재.
        text = _DOC_PATH.read_text(encoding="utf-8")
        assert "P-13" in text
        assert (
            "물타기" in text and "추가매수" in text and "피라미딩" in text
        )

    def test_doc_mentions_riskmanager_permissiongate_still_apply(self):
        # 옵트인 시에도 RiskManager / PermissionGate 가 적용된다는 정책 명시.
        text = _DOC_PATH.read_text(encoding="utf-8")
        assert "RiskManager" in text
        assert "PermissionGate" in text

    def test_doc_includes_reason_code_table(self):
        # reason_code 표 포함.
        text = _DOC_PATH.read_text(encoding="utf-8")
        for code in (
            ADDITIONAL_BUY_DISABLED,
            AVERAGING_DOWN_DISABLED,
            PYRAMIDING_DISABLED,
        ):
            assert code in text, f"reason_code {code} 문서 누락"


# ─────────────────────────────────────────────────────────────────────────────
# 2. 시스템 default 영구 False (검증 5-7)
# ─────────────────────────────────────────────────────────────────────────────


class TestSystemDefaultsFalse:
    def test_allow_additional_buy_default_false(self):
        # 검증 5.
        assert DEFAULT_ALLOW_ADDITIONAL_BUY is False

    def test_allow_averaging_down_default_false(self):
        # 검증 6.
        assert DEFAULT_ALLOW_AVERAGING_DOWN is False

    def test_allow_pyramiding_default_false(self):
        # 검증 7.
        assert DEFAULT_ALLOW_PYRAMIDING is False

    def test_resolve_default_all_false(self):
        r = resolve_capital_allocation_policy()
        assert r.allow_additional_buy is False
        assert r.allow_averaging_down is False
        assert r.allow_pyramiding is False
        assert r.source == "system_default"


# ─────────────────────────────────────────────────────────────────────────────
# 3. 모든 risk profile 에서 default False (검증 8-11)
# ─────────────────────────────────────────────────────────────────────────────


class TestRiskProfileAllFalse:
    @pytest.mark.parametrize(
        "profile", ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"],
    )
    def test_all_profiles_default_false(self, profile):
        # 검증 8/9/10.
        r = resolve_capital_allocation_policy(risk_profile=profile)
        assert r.allow_additional_buy is False, profile
        assert r.allow_averaging_down is False, profile
        assert r.allow_pyramiding is False, profile
        assert r.risk_profile == profile

    def test_aggressive_does_not_auto_allow_averaging_down(self):
        # 검증 11.
        r = resolve_capital_allocation_policy(risk_profile="AGGRESSIVE")
        assert r.allow_averaging_down is False
        assert r.allow_additional_buy is False
        assert r.allow_pyramiding is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. policy response 에 message + reason_code 포함 (검증 13-18)
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicyResponseFields:
    def test_response_includes_additional_buy_message(self):
        # 검증 13.
        r = resolve_capital_allocation_policy()
        assert r.additional_buy_policy_message
        assert "기본 금지" in r.additional_buy_policy_message

    def test_response_includes_averaging_down_message(self):
        # 검증 14.
        r = resolve_capital_allocation_policy()
        assert r.averaging_down_policy_message
        assert "손실 확대" in r.averaging_down_policy_message

    def test_response_includes_pyramiding_message(self):
        # 검증 15.
        r = resolve_capital_allocation_policy()
        assert r.pyramiding_policy_message
        assert "피라미딩" in r.pyramiding_policy_message

    def test_reason_code_additional_buy_disabled_constant(self):
        # 검증 16.
        assert ADDITIONAL_BUY_DISABLED == "ADDITIONAL_BUY_DISABLED"

    def test_reason_code_averaging_down_disabled_constant(self):
        # 검증 17.
        assert AVERAGING_DOWN_DISABLED == "AVERAGING_DOWN_DISABLED"

    def test_reason_code_pyramiding_disabled_constant(self):
        # 검증 18.
        assert PYRAMIDING_DISABLED == "PYRAMIDING_DISABLED"

    def test_response_carries_reason_codes_when_disabled(self):
        r = resolve_capital_allocation_policy()
        # 모두 disabled → reason_code 채워짐.
        assert r.additional_buy_reason_code == ADDITIONAL_BUY_DISABLED
        assert r.averaging_down_reason_code == AVERAGING_DOWN_DISABLED
        assert r.pyramiding_reason_code == PYRAMIDING_DISABLED

    def test_response_no_reason_code_when_opted_in(self):
        r = resolve_capital_allocation_policy(
            manual_allow_additional_buy=True,
            manual_allow_averaging_down=True,
            manual_allow_pyramiding=True,
        )
        assert r.additional_buy_reason_code is None
        assert r.averaging_down_reason_code is None
        assert r.pyramiding_reason_code is None

    def test_to_dict_full_payload(self):
        r = resolve_capital_allocation_policy()
        d = r.to_dict()
        for key in (
            "allow_additional_buy", "allow_averaging_down", "allow_pyramiding",
            "additional_buy_policy_message",
            "averaging_down_policy_message",
            "pyramiding_policy_message",
            "additional_buy_reason_code",
            "averaging_down_reason_code",
            "pyramiding_reason_code",
            "source", "is_paper_only", "is_order_signal",
            "is_live_authorization",
        ):
            assert key in d


# ─────────────────────────────────────────────────────────────────────────────
# 5. opt-in 시에도 RiskManager / PermissionGate 우회 안 함 (검증 12)
# ─────────────────────────────────────────────────────────────────────────────


class TestOptInDoesNotBypassOtherLayers:
    def test_optin_addnl_buy_does_not_disable_cash_check(self):
        from app.auto_paper.capital_state import (
            CashCheckVerdict,
            check_buy_cash_sufficient,
        )

        # 옵트인 적용.
        r = resolve_capital_allocation_policy(
            manual_allow_additional_buy=True,
        )
        assert r.allow_additional_buy is True
        # 그래도 P-07 cash check 는 *별도* 적용.
        cash = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=10,
            available_cash_krw=100_000,
        )
        assert cash.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH

    def test_optin_does_not_disable_daily_limit(self):
        from app.risk.loss_limits import (
            DAILY_BUY_LIMIT_EXCEEDED,
            check_daily_buy_limit,
        )

        r = resolve_capital_allocation_policy(
            manual_allow_additional_buy=True,
            manual_allow_averaging_down=True,
        )
        assert r.allow_additional_buy is True
        # P-10 daily 는 *별도* 적용.
        daily = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=7,
            today_buy_used_amount=2_500_000,
            max_daily_buy_amount=3_000_000,
        )
        assert daily.reason_code == DAILY_BUY_LIMIT_EXCEEDED

    def test_optin_does_not_disable_symbol_weight(self):
        from app.risk.position_limits import (
            SYMBOL_WEIGHT_LIMIT_EXCEEDED,
            check_symbol_weight_limit,
        )

        r = resolve_capital_allocation_policy(
            manual_allow_pyramiding=True,
        )
        assert r.allow_pyramiding is True
        # P-11 weight 는 *별도* 적용.
        weight = check_symbol_weight_limit(
            side="BUY", symbol="005930",
            price=100_000, quantity=7,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=1_600_000,
            max_symbol_weight_pct=0.20,
        )
        assert weight.reason_code == SYMBOL_WEIGHT_LIMIT_EXCEEDED


# ─────────────────────────────────────────────────────────────────────────────
# 6. policy_message_for / reason_code_for helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_policy_message_disabled(self):
        assert policy_message_for(
            key="additional_buy", allowed=False,
        ) == ADDITIONAL_BUY_DISABLED_MESSAGE_KO
        assert policy_message_for(
            key="averaging_down", allowed=False,
        ) == AVERAGING_DOWN_DISABLED_MESSAGE_KO
        assert policy_message_for(
            key="pyramiding", allowed=False,
        ) == PYRAMIDING_DISABLED_MESSAGE_KO

    def test_policy_message_enabled_mentions_optin(self):
        msg = policy_message_for(key="additional_buy", allowed=True)
        assert "옵트인" in msg
        assert "RiskManager" in msg or "PermissionGate" in msg or "현금" in msg

    def test_policy_message_unknown_key_raises(self):
        with pytest.raises(ValueError, match="unknown policy key"):
            policy_message_for(key="margin_call", allowed=False)

    def test_reason_code_disabled(self):
        assert reason_code_for(
            key="additional_buy", allowed=False,
        ) == ADDITIONAL_BUY_DISABLED
        assert reason_code_for(
            key="averaging_down", allowed=False,
        ) == AVERAGING_DOWN_DISABLED
        assert reason_code_for(
            key="pyramiding", allowed=False,
        ) == PYRAMIDING_DISABLED

    def test_reason_code_enabled_is_none(self):
        assert reason_code_for(key="additional_buy", allowed=True) is None
        assert reason_code_for(key="averaging_down", allowed=True) is None
        assert reason_code_for(key="pyramiding", allowed=True) is None

    def test_reason_code_unknown_key_raises(self):
        with pytest.raises(ValueError, match="unknown policy key"):
            reason_code_for(key="margin_call", allowed=False)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestDataclassInvariants:
    def test_is_paper_only_must_be_true(self):
        with pytest.raises(ValueError, match="is_paper_only"):
            CapitalAllocationPolicyResult(
                allow_additional_buy=False,
                allow_averaging_down=False,
                allow_pyramiding=False,
                additional_buy_policy_message="x",
                averaging_down_policy_message="x",
                pyramiding_policy_message="x",
                additional_buy_reason_code=ADDITIONAL_BUY_DISABLED,
                averaging_down_reason_code=AVERAGING_DOWN_DISABLED,
                pyramiding_reason_code=PYRAMIDING_DISABLED,
                is_paper_only=False,  # type: ignore[arg-type]
            )

    def test_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            CapitalAllocationPolicyResult(
                allow_additional_buy=False,
                allow_averaging_down=False,
                allow_pyramiding=False,
                additional_buy_policy_message="x",
                averaging_down_policy_message="x",
                pyramiding_policy_message="x",
                additional_buy_reason_code=ADDITIONAL_BUY_DISABLED,
                averaging_down_reason_code=AVERAGING_DOWN_DISABLED,
                pyramiding_reason_code=PYRAMIDING_DISABLED,
                is_order_signal=True,  # type: ignore[arg-type]
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            CapitalAllocationPolicyResult(
                allow_additional_buy=False,
                allow_averaging_down=False,
                allow_pyramiding=False,
                additional_buy_policy_message="x",
                averaging_down_policy_message="x",
                pyramiding_policy_message="x",
                additional_buy_reason_code=ADDITIONAL_BUY_DISABLED,
                averaging_down_reason_code=AVERAGING_DOWN_DISABLED,
                pyramiding_reason_code=PYRAMIDING_DISABLED,
                is_live_authorization=True,  # type: ignore[arg-type]
            )


# ─────────────────────────────────────────────────────────────────────────────
# 8. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestApiEndpoints:
    def test_get_default_policy(self, api_client):
        r = api_client.get("/api/auto-paper/capital-allocation-policy")
        assert r.status_code == 200
        body = r.json()
        assert body["allow_additional_buy"] is False
        assert body["allow_averaging_down"] is False
        assert body["allow_pyramiding"] is False
        assert body["additional_buy_reason_code"] == "ADDITIONAL_BUY_DISABLED"
        assert body["averaging_down_reason_code"] == "AVERAGING_DOWN_DISABLED"
        assert body["pyramiding_reason_code"] == "PYRAMIDING_DISABLED"
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False
        # defaults block.
        assert body["defaults"]["allow_additional_buy"] is False
        assert body["defaults"]["allow_averaging_down"] is False
        assert body["defaults"]["allow_pyramiding"] is False

    @pytest.mark.parametrize(
        "profile", ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"],
    )
    def test_post_with_each_profile_returns_false(self, api_client, profile):
        r = api_client.post(
            "/api/auto-paper/capital-allocation-policy",
            json={"risk_profile": profile},
        )
        body = r.json()
        assert body["risk_profile"] == profile
        assert body["allow_additional_buy"] is False
        assert body["allow_averaging_down"] is False
        assert body["allow_pyramiding"] is False

    def test_post_with_manual_optin(self, api_client):
        r = api_client.post(
            "/api/auto-paper/capital-allocation-policy",
            json={"manual_allow_additional_buy": True},
        )
        body = r.json()
        assert body["allow_additional_buy"] is True
        assert body["allow_averaging_down"] is False
        assert body["allow_pyramiding"] is False
        # source.
        assert body["source"] == "manual"
        # reason_code 사라짐.
        assert body["additional_buy_reason_code"] is None
        # 다른 두 정책은 여전히 disabled.
        assert body["averaging_down_reason_code"] == "AVERAGING_DOWN_DISABLED"
        assert body["pyramiding_reason_code"] == "PYRAMIDING_DISABLED"

    def test_post_with_all_manual_optin(self, api_client):
        r = api_client.post(
            "/api/auto-paper/capital-allocation-policy",
            json={
                "manual_allow_additional_buy": True,
                "manual_allow_averaging_down": True,
                "manual_allow_pyramiding": True,
            },
        )
        body = r.json()
        assert body["allow_additional_buy"] is True
        assert body["allow_averaging_down"] is True
        assert body["allow_pyramiding"] is True

    def test_response_includes_notice(self, api_client):
        # GET — 짧은 advisory 알림 (Paper 전용 + 실거래 권한 부여 아님).
        r_get = api_client.get("/api/auto-paper/capital-allocation-policy")
        body_get = r_get.json()
        assert "notice" in body_get
        assert "advisory" in body_get["notice"]
        assert "실거래" in body_get["notice"]

        # POST — 핵심 안전 메시지 (공격형 자동 허용 안 함 / 별도 적용 포함).
        r_post = api_client.post(
            "/api/auto-paper/capital-allocation-policy", json={},
        )
        body_post = r_post.json()
        assert "공격형" in body_post["notice"]
        assert "별도" in body_post["notice"]


# ─────────────────────────────────────────────────────────────────────────────
# 9. 정적 import / safety flag 가드 (검증 19)
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_module_file_exists(self):
        assert _MODULE_PATH.exists()

    def test_no_broker_imports_in_p13_module(self):
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                for banned in (
                    "app.brokers", "app.execution",
                    "app.execution.executor", "app.execution.order_router",
                    "app.kis_paper.engine",
                    "anthropic", "openai", "httpx", "requests", "urllib3",
                    "app.core.config",
                ):
                    assert not m.startswith(banned), (
                        f"additional_buy_policy.py 가 금지 모듈 '{m}' import"
                    )

    def test_no_broker_call_patterns(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in (
            r"\bbroker\.place_order\s*\(",
            r"\bbroker\.cancel_order\s*\(",
            r"\broute_order\s*\(",
            r"OrderExecutor\s*\(",
            r"OrderRequest\s*\(",
            r"\.enable_live_trading\s*=",
            r"\.enable_ai_execution\s*=",
        ):
            assert not re.search(pat, text), (
                f"additional_buy_policy.py 에 금지 패턴: /{pat}/"
            )

    def test_no_safety_flag_mutations(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in [
            r"settings\.enable_live_trading\s*=",
            r"settings\.enable_ai_execution\s*=",
            r"ENABLE_LIVE_TRADING\s*=\s*True",
            r"ENABLE_AI_EXECUTION\s*=\s*True",
            r"KIS_IS_PAPER\s*=\s*False",
        ]:
            assert not re.search(pat, text), (
                f"P-13 모듈 안전 flag mutation 의심: /{pat}/"
            )

    def test_capital_config_defaults_remain_false(self):
        # capital_config.py 의 DEFAULT_ALLOW_* 라인이 *True* 가 아닌지 정적 검증.
        text = _CAPITAL_CONFIG_PATH.read_text(encoding="utf-8")
        # AST 로 모듈 상수 추출.
        tree = ast.parse(text)
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(
                node.target, ast.Name,
            ):
                if node.target.id in (
                    "DEFAULT_ALLOW_ADDITIONAL_BUY",
                    "DEFAULT_ALLOW_AVERAGING_DOWN",
                    "DEFAULT_ALLOW_PYRAMIDING",
                ):
                    if isinstance(node.value, ast.Constant):
                        found[node.target.id] = node.value.value
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id in (
                        "DEFAULT_ALLOW_ADDITIONAL_BUY",
                        "DEFAULT_ALLOW_AVERAGING_DOWN",
                        "DEFAULT_ALLOW_PYRAMIDING",
                    ) and isinstance(node.value, ast.Constant):
                        found[tgt.id] = node.value.value
        # 3개 모두 False.
        assert found.get("DEFAULT_ALLOW_ADDITIONAL_BUY") is False
        assert found.get("DEFAULT_ALLOW_AVERAGING_DOWN") is False
        assert found.get("DEFAULT_ALLOW_PYRAMIDING") is False

    def test_no_default_true_in_any_capital_module(self):
        # 검증 19: allow_*_=True default 어디에도 없음.
        for path in (_MODULE_PATH, _CAPITAL_CONFIG_PATH):
            text = path.read_text(encoding="utf-8")
            for pat in [
                r"DEFAULT_ALLOW_ADDITIONAL_BUY\s*[:=].*True",
                r"DEFAULT_ALLOW_AVERAGING_DOWN\s*[:=].*True",
                r"DEFAULT_ALLOW_PYRAMIDING\s*[:=].*True",
            ]:
                assert not re.search(pat, text), (
                    f"{path.name} 안 default True: /{pat}/"
                )

    def test_p12_module_preserved(self):
        # 회귀: 기존 P-12 check_duplicate_position_buy 그대로 import 가능.
        from app.auto_paper.capital_state import check_duplicate_position_buy
        assert callable(check_duplicate_position_buy)
