"""#4-RiskProfile: AI 운용 성향 프리셋 테스트.

Covers:
* 3 deterministic presets (CONSERVATIVE / BALANCED / AGGRESSIVE).
* Default = BALANCED.
* Per-profile threshold ordering:
  - CONSERVATIVE more strict than BALANCED more strict than AGGRESSIVE
  - confidence threshold descending: CONS > BAL > AGG
  - max_risk_flags ascending: CONS < BAL < AGG
  - position_pct / position_krw ascending: CONS < BAL < AGG
  - risk_veto_max_flags ascending: CONS < BAL < AGG
* Invariants on RiskProfilePolicy:
  - is_order_signal / auto_apply_allowed / is_live_authorization = False
  - True 설정 시 ValueError
* `sizing_policy_for()` reuses 4-08 PositionSizingPolicy with profile values.
* `policy_for(None)` / 알 수 없는 값 → BALANCED fallback.
* `is_live_profile(...)` 항상 False — AGGRESSIVE 도 실거래 허가 아님.
* Static guards — no broker / OrderExecutor / route_order / AI SDK /
  external HTTP imports; no settings mutation; no DB write.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.agents.risk_profile import (
    DEFAULT_RISK_PROFILE,
    RISK_PROFILE_SCHEMA_VERSION,
    RiskProfile,
    RiskProfilePolicy,
    is_live_profile,
    list_profiles,
    policy_for,
    risk_veto_policy_for,
    sizing_policy_for,
)
from app.auto_paper.position_sizer import PositionSizingPolicy


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "agents" / "risk_profile.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Enum + default
# ─────────────────────────────────────────────────────────────────────────────


class TestEnumAndDefault:

    def test_three_profiles_exist(self):
        names = {p.value for p in RiskProfile}
        assert names == {"CONSERVATIVE", "BALANCED", "AGGRESSIVE"}

    def test_default_is_balanced(self):
        assert DEFAULT_RISK_PROFILE == RiskProfile.BALANCED

    def test_schema_version_present(self):
        assert RISK_PROFILE_SCHEMA_VERSION == "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Preset thresholds — 성향별 차이
# ─────────────────────────────────────────────────────────────────────────────


class TestPresetThresholds:

    @pytest.fixture
    def policies(self):
        return {
            RiskProfile.CONSERVATIVE: policy_for(RiskProfile.CONSERVATIVE),
            RiskProfile.BALANCED:     policy_for(RiskProfile.BALANCED),
            RiskProfile.AGGRESSIVE:   policy_for(RiskProfile.AGGRESSIVE),
        }

    def test_confidence_threshold_descending(self, policies):
        c = policies[RiskProfile.CONSERVATIVE].min_confidence_threshold
        b = policies[RiskProfile.BALANCED].min_confidence_threshold
        a = policies[RiskProfile.AGGRESSIVE].min_confidence_threshold
        # 보수적 가 가장 높고, 공격적이 가장 낮다.
        assert c > b > a

    def test_max_risk_flags_ascending(self, policies):
        c = policies[RiskProfile.CONSERVATIVE].max_risk_flags
        b = policies[RiskProfile.BALANCED].max_risk_flags
        a = policies[RiskProfile.AGGRESSIVE].max_risk_flags
        # 보수적은 가장 적게 허용, 공격적은 가장 많이 허용.
        assert c < b < a

    def test_position_pct_ascending(self, policies):
        c = policies[RiskProfile.CONSERVATIVE].max_position_pct
        b = policies[RiskProfile.BALANCED].max_position_pct
        a = policies[RiskProfile.AGGRESSIVE].max_position_pct
        assert c < b < a

    def test_position_krw_ascending(self, policies):
        c = policies[RiskProfile.CONSERVATIVE].max_position_krw
        b = policies[RiskProfile.BALANCED].max_position_krw
        a = policies[RiskProfile.AGGRESSIVE].max_position_krw
        assert c < b < a

    def test_risk_per_trade_pct_ascending(self, policies):
        c = policies[RiskProfile.CONSERVATIVE].max_risk_per_trade_pct
        b = policies[RiskProfile.BALANCED].max_risk_per_trade_pct
        a = policies[RiskProfile.AGGRESSIVE].max_risk_per_trade_pct
        assert c < b < a

    def test_risk_veto_max_flags_ascending(self, policies):
        c = policies[RiskProfile.CONSERVATIVE].risk_veto_max_flags
        b = policies[RiskProfile.BALANCED].risk_veto_max_flags
        a = policies[RiskProfile.AGGRESSIVE].risk_veto_max_flags
        # 보수적은 0(어떤 risk_flag 도 신규 진입 차단), 공격적은 가장 관대.
        assert c <= b <= a
        assert c < a

    def test_max_concurrent_candidates_ascending(self, policies):
        c = policies[RiskProfile.CONSERVATIVE].max_concurrent_candidates
        b = policies[RiskProfile.BALANCED].max_concurrent_candidates
        a = policies[RiskProfile.AGGRESSIVE].max_concurrent_candidates
        assert c < b < a

    def test_balanced_matches_position_sizer_defaults(self, policies):
        # BALANCED 가 4-08 module 의 default 와 동일해야 운영 흐름이 그대로
        # 동작 (backwards compat).
        default_sizing = PositionSizingPolicy()
        b = policies[RiskProfile.BALANCED]
        assert b.max_risk_per_trade_pct == default_sizing.max_risk_per_trade_pct
        assert b.default_stop_loss_pct == default_sizing.default_stop_loss_pct
        assert b.max_position_pct == default_sizing.max_position_pct
        assert b.max_position_krw == default_sizing.max_position_krw
        assert b.min_confidence_threshold == default_sizing.min_confidence_threshold
        assert b.max_risk_flags == default_sizing.max_risk_flags


# ─────────────────────────────────────────────────────────────────────────────
# 3. Invariants — dataclass guards
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:

    def test_default_invariants_false(self):
        p = policy_for(RiskProfile.BALANCED)
        assert p.is_order_signal is False
        assert p.auto_apply_allowed is False
        assert p.is_live_authorization is False

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_invariant_violation_raises(self, override):
        base = dict(
            profile=RiskProfile.BALANCED,
            max_risk_per_trade_pct=0.01,
            default_stop_loss_pct=0.03,
            max_position_pct=0.20,
            max_position_krw=5_000_000,
            min_confidence_threshold=0.40,
            max_risk_flags=3,
        )
        base.update(override)
        with pytest.raises(ValueError):
            RiskProfilePolicy(**base)

    def test_to_dict_carries_invariants(self):
        d = policy_for(RiskProfile.AGGRESSIVE).to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False

    @pytest.mark.parametrize("bad_kwargs", [
        {"max_risk_per_trade_pct": 0.0},
        {"max_risk_per_trade_pct": -0.1},
        {"max_risk_per_trade_pct": 1.1},
        {"default_stop_loss_pct": 0.0},
        {"max_position_pct": 0.0},
        {"max_position_krw": 0},
        {"min_confidence_threshold": -0.01},
        {"min_confidence_threshold": 1.01},
        {"max_risk_flags": -1},
        {"min_unit_quantity": 0},
        {"risk_veto_max_flags": -1},
        {"max_concurrent_candidates": -1},
    ])
    def test_invalid_threshold_raises(self, bad_kwargs):
        base = dict(
            profile=RiskProfile.BALANCED,
            max_risk_per_trade_pct=0.01,
            default_stop_loss_pct=0.03,
            max_position_pct=0.20,
            max_position_krw=5_000_000,
            min_confidence_threshold=0.40,
            max_risk_flags=3,
        )
        base.update(bad_kwargs)
        with pytest.raises(ValueError):
            RiskProfilePolicy(**base)


# ─────────────────────────────────────────────────────────────────────────────
# 4. policy_for fallback
# ─────────────────────────────────────────────────────────────────────────────


class TestPolicyForFallback:

    def test_none_returns_default(self):
        p = policy_for(None)
        assert p.profile == DEFAULT_RISK_PROFILE

    def test_empty_string_returns_default(self):
        p = policy_for("")
        assert p.profile == DEFAULT_RISK_PROFILE

    def test_unknown_string_returns_default(self):
        p = policy_for("EXTREME")
        assert p.profile == DEFAULT_RISK_PROFILE

    def test_case_insensitive_string_lookup(self):
        p = policy_for("aggressive")
        assert p.profile == RiskProfile.AGGRESSIVE

    def test_whitespace_in_string_handled(self):
        p = policy_for("  CONSERVATIVE  ")
        assert p.profile == RiskProfile.CONSERVATIVE

    def test_enum_value_returned_unchanged(self):
        p = policy_for(RiskProfile.AGGRESSIVE)
        assert p.profile == RiskProfile.AGGRESSIVE


# ─────────────────────────────────────────────────────────────────────────────
# 5. sizing_policy_for / risk_veto_policy_for adapters
# ─────────────────────────────────────────────────────────────────────────────


class TestAdapters:

    @pytest.mark.parametrize("profile", list(RiskProfile))
    def test_sizing_policy_for_returns_PositionSizingPolicy(self, profile):
        s = sizing_policy_for(profile)
        assert isinstance(s, PositionSizingPolicy)
        # 매핑이 1:1.
        ref = policy_for(profile)
        assert s.max_risk_per_trade_pct == ref.max_risk_per_trade_pct
        assert s.max_position_pct == ref.max_position_pct
        assert s.max_position_krw == ref.max_position_krw
        assert s.min_confidence_threshold == ref.min_confidence_threshold
        assert s.max_risk_flags == ref.max_risk_flags

    def test_sizing_policy_for_none_uses_balanced(self):
        s = sizing_policy_for(None)
        bal = policy_for(RiskProfile.BALANCED)
        assert s.max_risk_per_trade_pct == bal.max_risk_per_trade_pct

    @pytest.mark.parametrize("profile", list(RiskProfile))
    def test_risk_veto_policy_for_returns_dict(self, profile):
        d = risk_veto_policy_for(profile)
        assert isinstance(d, dict)
        assert "risk_veto_max_flags" in d
        assert "max_concurrent_candidates" in d


# ─────────────────────────────────────────────────────────────────────────────
# 6. list_profiles + label / summary
# ─────────────────────────────────────────────────────────────────────────────


class TestListProfilesAndLabels:

    def test_list_profiles_three_entries(self):
        rows = list_profiles()
        assert len(rows) == 3
        profiles = [r["profile"] for r in rows]
        assert profiles == ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]

    def test_label_ko_carries_korean(self):
        for profile in RiskProfile:
            p = policy_for(profile)
            assert p.label_ko
        assert "보수적" in policy_for(RiskProfile.CONSERVATIVE).label_ko
        assert "안정적" in policy_for(RiskProfile.BALANCED).label_ko
        assert "공격적" in policy_for(RiskProfile.AGGRESSIVE).label_ko

    def test_balanced_label_marks_default(self):
        # 기본값 표시.
        assert "기본값" in policy_for(RiskProfile.BALANCED).label_ko

    def test_aggressive_summary_warns_no_live(self):
        # 공격적도 실거래 안전장치 우회 못함을 명시.
        s = policy_for(RiskProfile.AGGRESSIVE).summary_ko
        assert "실거래" in s
        assert "우회" in s or "LIVE" in s.upper()

    def test_conservative_summary_mentions_손실(self):
        s = policy_for(RiskProfile.CONSERVATIVE).summary_ko
        assert "손실" in s

    def test_list_profiles_invariants_carry(self):
        for row in list_profiles():
            assert row["is_order_signal"] is False
            assert row["auto_apply_allowed"] is False
            assert row["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 7. is_live_profile — 항상 False (공격적 포함)
# ─────────────────────────────────────────────────────────────────────────────


class TestLiveProfileInvariant:

    @pytest.mark.parametrize("profile", [
        RiskProfile.CONSERVATIVE,
        RiskProfile.BALANCED,
        RiskProfile.AGGRESSIVE,
        None,
        "",
        "AGGRESSIVE",
    ])
    def test_is_live_profile_always_false(self, profile):
        assert is_live_profile(profile) is False


# ─────────────────────────────────────────────────────────────────────────────
# 8. Static guards — no broker / OrderExecutor / route_order / AI SDK / HTTP /
#    settings mutation / DB write
# ─────────────────────────────────────────────────────────────────────────────


_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "app.brokers.kis",
    "app.brokers.mock_broker",
    "app.execution.order_router",
    "app.execution.executor",
    "app.execution.order_executor",
    "app.permission.gate",
    "app.ai.assist",
    "app.ai.client",
    "anthropic",
    "openai",
    "httpx",
    "requests",
)


_FORBIDDEN_CALL_SUBSTRINGS = (
    "broker.place_order",
    "broker.cancel_order",
    "route_order(",
    "OrderExecutor",
    "OrderRequest",
)


class TestStaticGuards:

    def _source(self) -> str:
        return _MODULE_PATH.read_text(encoding="utf-8")

    def test_no_forbidden_imports(self):
        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                        assert bad not in (alias.name or "")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                    assert bad not in module

    def test_no_forbidden_calls(self):
        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = ast.unparse(node.func)
                for bad in _FORBIDDEN_CALL_SUBSTRINGS:
                    assert bad not in callee

    def test_no_settings_mutation(self):
        src = self._source()
        assert not re.search(r"settings\.enable_[a-z_]+\s*=", src)

    def test_no_db_write(self):
        src = self._source()
        for bad in ("session.commit", "session.add", "session.delete",
                    "db.commit(", "db.add(", "db.delete("):
            assert bad not in src

    def test_no_secret_fields_in_dataclass(self):
        forbidden = {
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "kis_app_key", "kis_app_secret",
            "anthropic_api_key", "openai_api_key", "password",
        }
        for name in RiskProfilePolicy.__dataclass_fields__:
            assert name.lower() not in forbidden, name
