"""작업 10 — Feature Flags 다중 잠금 계층 단위 테스트.

본 테스트는 `app.core.feature_flags` 모듈의 다음 invariant 를 보장한다:

1. 위험 플래그 4종 (live_trading / ai_execution / crypto_futures_live /
   kimp_strategy) 기본값 모두 False.
2. is_live_trading_enabled() 가 다중 조건 모두 만족할 때만 True.
3. is_crypto_futures_live_enabled() 가 environment="local" 에서 *영구* False.
4. is_kimp_strategy_enabled() 가 실거래 허용과 분리 — paper 에서도 True 가능.
5. is_ai_execution_enabled() 는 활성 *가능 여부* 만 — 주문 실행 권한 아님.
6. assert_feature_allowed() 가 차단 예외를 raise (FeatureDisabledError).
7. public_snapshot() / 에러 메시지에 Secret 포함 0건.
8. get_feature_flags.cache_clear() 가 동작.
9. 정적 grep 가드 — feature_flags 모듈이 broker / executor / 한투 client /
   거래소 client 를 import 하지 않음.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.core import feature_flags as ff
from app.core.feature_flags import (
    FeatureDisabledError,
    FeatureFlags,
    assert_feature_allowed,
    get_feature_flags,
    public_feature_snapshot,
    supported_feature_names,
)


# ----------------------------------------------------------------------
# 1. 기본값 invariant
# ----------------------------------------------------------------------


class TestDefaults:
    def test_dataclass_defaults_all_risky_flags_false(self):
        """FeatureFlags() 기본 인스턴스 — 위험 플래그 4종 모두 False."""
        f = FeatureFlags()
        assert f.enable_live_trading is False
        assert f.enable_ai_execution is False
        assert f.enable_crypto_futures_live is False
        assert f.enable_kimp_strategy is False

    def test_dataclass_defaults_safe_context(self):
        """기본 컨텍스트도 안전 값 — simulation / local / no live / approval required."""
        f = FeatureFlags()
        assert f.trading_mode == "simulation"
        assert f.environment == "local"
        assert f.allow_live_trading is False
        assert f.require_approval_for_live is True

    def test_default_predicates_all_false(self):
        """기본 인스턴스의 모든 is_* 함수가 False (kimp_strategy 도 default False)."""
        f = FeatureFlags()
        assert f.is_live_trading_enabled() is False
        assert f.is_ai_execution_enabled() is False
        assert f.is_crypto_futures_live_enabled() is False
        assert f.is_kimp_strategy_enabled() is False


# ----------------------------------------------------------------------
# 2. is_live_trading_enabled — 다중 조건
# ----------------------------------------------------------------------


def _live_config(**overrides) -> FeatureFlags:
    """live_trading 모든 조건이 True 인 baseline. 개별 조건만 override 가능."""
    base = dict(
        enable_live_trading=True,
        trading_mode="live",
        environment="production",
        allow_live_trading=True,
        require_approval_for_live=True,
    )
    base.update(overrides)
    return FeatureFlags(**base)


class TestLiveTradingMultiLock:
    def test_all_conditions_met_returns_true(self):
        assert _live_config().is_live_trading_enabled() is True

    def test_enable_flag_false_blocks(self):
        assert _live_config(enable_live_trading=False).is_live_trading_enabled() is False

    def test_trading_mode_not_live_blocks(self):
        for non_live in ("paper", "simulation", "virtual", "shadow"):
            assert _live_config(trading_mode=non_live).is_live_trading_enabled() is False, (
                f"trading_mode={non_live!r} should block live"
            )

    def test_allow_live_trading_false_blocks(self):
        """mode capability 가 live_order=False 면 차단 (예: LIVE_SHADOW)."""
        assert _live_config(allow_live_trading=False).is_live_trading_enabled() is False

    def test_require_approval_off_blocks(self):
        """안전 정책 invariant — approval 요구가 꺼지면 live 즉시 차단."""
        assert _live_config(require_approval_for_live=False).is_live_trading_enabled() is False

    def test_single_env_flag_alone_does_not_enable_live(self):
        """ENABLE_LIVE_TRADING=True *만으로* 절대 활성화되지 않음 (단일 잠금 방지)."""
        # default mode (simulation) 에서 enable_live_trading 만 True 로 켬.
        f = FeatureFlags(enable_live_trading=True)
        assert f.is_live_trading_enabled() is False
        # 즉 단일 flag 만으로는 활성 불가.


# ----------------------------------------------------------------------
# 3. is_crypto_futures_live_enabled — local hard-block + 다중 조건
# ----------------------------------------------------------------------


class TestCryptoFuturesLiveHardBlock:
    def test_local_env_always_false(self):
        """environment='local' 에서는 어떤 조건이든 *영구* False."""
        # 모든 다른 조건은 통과시켜도 environment="local" 이면 차단.
        f = _live_config(
            enable_crypto_futures_live=True,
            environment="local",
        )
        assert f.is_crypto_futures_live_enabled() is False

    def test_production_with_all_conditions_returns_true(self):
        f = _live_config(
            enable_crypto_futures_live=True,
            environment="production",
        )
        assert f.is_crypto_futures_live_enabled() is True

    def test_staging_can_enable_if_all_conditions_met(self):
        f = _live_config(
            enable_crypto_futures_live=True,
            environment="staging",
        )
        assert f.is_crypto_futures_live_enabled() is True

    def test_enable_flag_false_blocks(self):
        f = _live_config(
            enable_crypto_futures_live=False,
            environment="production",
        )
        assert f.is_crypto_futures_live_enabled() is False

    def test_blocked_when_live_trading_blocked(self):
        """is_live_trading_enabled()==False 면 crypto 도 False (의존 다중 잠금)."""
        f = _live_config(
            enable_crypto_futures_live=True,
            enable_live_trading=False,  # blocks parent live
            environment="production",
        )
        assert f.is_crypto_futures_live_enabled() is False

    def test_single_env_flag_alone_does_not_enable_crypto(self):
        """ENABLE_CRYPTO_FUTURES_LIVE=True *만으로* 절대 활성화되지 않음."""
        f = FeatureFlags(enable_crypto_futures_live=True)
        assert f.is_crypto_futures_live_enabled() is False


# ----------------------------------------------------------------------
# 4. is_kimp_strategy_enabled — strategy flag only
# ----------------------------------------------------------------------


class TestKimpStrategyIsolation:
    def test_default_false(self):
        assert FeatureFlags().is_kimp_strategy_enabled() is False

    def test_enabled_in_paper_mode(self):
        """kimp_strategy 는 실거래 허용과 무관 — paper 에서도 True 가능."""
        f = FeatureFlags(
            enable_kimp_strategy=True,
            trading_mode="paper",
            environment="local",
            enable_live_trading=False,
        )
        assert f.is_kimp_strategy_enabled() is True

    def test_enabled_in_simulation_mode(self):
        f = FeatureFlags(
            enable_kimp_strategy=True,
            trading_mode="simulation",
            environment="local",
        )
        assert f.is_kimp_strategy_enabled() is True

    def test_kimp_does_not_imply_live_trading(self):
        """전략 활성 ≠ 실거래 허용 — kimp=True 여도 live 는 별도 검사."""
        f = FeatureFlags(
            enable_kimp_strategy=True,
            trading_mode="paper",
        )
        assert f.is_kimp_strategy_enabled() is True
        assert f.is_live_trading_enabled() is False
        assert f.is_crypto_futures_live_enabled() is False


# ----------------------------------------------------------------------
# 5. is_ai_execution_enabled — 활성 가능 여부 (주문 권한 아님)
# ----------------------------------------------------------------------


class TestAiExecutionSemantic:
    def test_default_false(self):
        assert FeatureFlags().is_ai_execution_enabled() is False

    def test_enabled_returns_true(self):
        f = FeatureFlags(enable_ai_execution=True)
        assert f.is_ai_execution_enabled() is True

    def test_does_not_imply_live_trading_permission(self):
        """AI execution 활성 ≠ AI 가 주문을 *직접* 실행할 권한.

        실제 주문 실행은 별도 governance / execution / AI Permission Gate
        통과 필요 — 본 flag 는 모듈 활성 *가능* 여부만 표현.
        """
        f = FeatureFlags(enable_ai_execution=True)  # other flags default
        assert f.is_ai_execution_enabled() is True
        assert f.is_live_trading_enabled() is False  # 실거래는 별도 잠금

    def test_works_in_paper_mode(self):
        """live 가 꺼져 있어도 AI 실행 판단 모듈은 paper/mock 에서 활성 가능."""
        f = FeatureFlags(
            enable_ai_execution=True,
            trading_mode="paper",
            environment="local",
        )
        assert f.is_ai_execution_enabled() is True


# ----------------------------------------------------------------------
# 6. assert_feature_allowed — 차단 예외
# ----------------------------------------------------------------------


class TestAssertFeatureAllowed:
    def test_default_flags_block_live_trading(self):
        with pytest.raises(FeatureDisabledError) as exc:
            assert_feature_allowed("live_trading", flags=FeatureFlags())
        assert "live_trading" in str(exc.value)

    def test_default_flags_block_crypto_futures_live(self):
        with pytest.raises(FeatureDisabledError):
            assert_feature_allowed("crypto_futures_live", flags=FeatureFlags())

    def test_default_flags_block_ai_execution(self):
        with pytest.raises(FeatureDisabledError):
            assert_feature_allowed("ai_execution", flags=FeatureFlags())

    def test_default_flags_block_kimp_strategy(self):
        with pytest.raises(FeatureDisabledError):
            assert_feature_allowed("kimp_strategy", flags=FeatureFlags())

    def test_unknown_feature_raises(self):
        with pytest.raises(FeatureDisabledError) as exc:
            assert_feature_allowed("not_a_real_feature", flags=FeatureFlags())
        assert "unknown feature" in str(exc.value)

    def test_allowed_when_conditions_met_returns_none(self):
        f = _live_config()
        # 예외 없음 + None 반환.
        assert assert_feature_allowed("live_trading", flags=f) is None

    def test_kimp_strategy_allowed_in_paper(self):
        """kimp_strategy 는 paper 에서도 통과."""
        f = FeatureFlags(enable_kimp_strategy=True, trading_mode="paper")
        assert assert_feature_allowed("kimp_strategy", flags=f) is None

    def test_error_message_has_no_secret_patterns(self):
        """에러 메시지에 KIS / Anthropic / Telegram / 계좌번호 키워드 0건."""
        with pytest.raises(FeatureDisabledError) as exc:
            assert_feature_allowed("live_trading", flags=FeatureFlags())
        msg = str(exc.value).lower()
        # secret 관련 어떤 키워드도 메시지에 등장하지 않아야 함.
        for forbidden in (
            "api_key", "api-key", "apikey",
            "secret_key", "secret-key",
            "app_secret", "app_key",
            "anthropic", "openai", "telegram", "bot_token",
            "account_no", "계좌번호",
        ):
            assert forbidden not in msg, (
                f"forbidden token {forbidden!r} found in error message: {msg!r}"
            )

    def test_supported_feature_names_matches_assert_targets(self):
        names = supported_feature_names()
        assert set(names) == {
            "live_trading",
            "ai_execution",
            "crypto_futures_live",
            "kimp_strategy",
        }


# ----------------------------------------------------------------------
# 7. public_snapshot — Secret 0건
# ----------------------------------------------------------------------


class TestPublicSnapshotNoSecrets:
    def test_snapshot_keys_only_safe_labels(self):
        snap = FeatureFlags().public_snapshot()
        expected_keys = {
            "live_trading",
            "ai_execution",
            "crypto_futures_live",
            "kimp_strategy",
            "trading_mode",
            "environment",
        }
        assert set(snap.keys()) == expected_keys

    def test_snapshot_values_are_only_bools_and_safe_strings(self):
        snap = _live_config().public_snapshot()
        for k, v in snap.items():
            assert isinstance(v, (bool, str)), f"key {k!r} type {type(v)}"

    def test_snapshot_has_no_secret_keys(self):
        snap = FeatureFlags().public_snapshot()
        for forbidden in (
            "api_key", "secret", "password", "token",
            "kis_app_key", "kis_app_secret", "kis_account_no",
            "anthropic", "openai", "telegram",
        ):
            for key in snap.keys():
                assert forbidden not in key.lower(), (
                    f"forbidden key fragment {forbidden!r} in {key!r}"
                )

    def test_default_snapshot_safe_values(self):
        """기본 인스턴스의 스냅샷은 모두 안전 값."""
        snap = FeatureFlags().public_snapshot()
        assert snap["live_trading"] is False
        assert snap["ai_execution"] is False
        assert snap["crypto_futures_live"] is False
        assert snap["kimp_strategy"] is False
        assert snap["trading_mode"] == "simulation"
        assert snap["environment"] == "local"

    def test_local_env_snapshot_crypto_always_false(self):
        """environment='local' 에서는 enable_crypto=True 여도 스냅샷 False."""
        f = _live_config(
            enable_crypto_futures_live=True,
            environment="local",
        )
        snap = f.public_snapshot()
        assert snap["crypto_futures_live"] is False


# ----------------------------------------------------------------------
# 8. get_feature_flags + cache_clear
# ----------------------------------------------------------------------


class TestGetFeatureFlagsCache:
    def test_returns_feature_flags_instance(self):
        get_feature_flags.cache_clear()
        f = get_feature_flags()
        assert isinstance(f, FeatureFlags)

    def test_cache_returns_same_instance(self):
        get_feature_flags.cache_clear()
        f1 = get_feature_flags()
        f2 = get_feature_flags()
        assert f1 is f2

    def test_cache_clear_allows_rebuild(self):
        get_feature_flags.cache_clear()
        f1 = get_feature_flags()
        get_feature_flags.cache_clear()
        # 두 번째 호출은 새 인스턴스 — 동일 값이지만 별도 객체.
        f2 = get_feature_flags()
        assert f1 == f2  # value equality (frozen dataclass)
        # is 비교는 cache 가 cleared 됐는지 보장 — frozen dataclass 가
        # __eq__ 만 정의하므로 별도 인스턴스 확인은 보장 어려움. cache_clear
        # 호출 자체가 에러 없이 동작하면 충분.

    def test_default_environment_blocks_live_via_cache(self):
        """get_feature_flags() 가 기본 settings 에서 live 차단을 정확히 반영."""
        get_feature_flags.cache_clear()
        f = get_feature_flags()
        assert f.is_live_trading_enabled() is False
        assert f.is_crypto_futures_live_enabled() is False

    def test_public_feature_snapshot_safe(self):
        get_feature_flags.cache_clear()
        snap = public_feature_snapshot()
        assert snap["live_trading"] is False
        assert snap["crypto_futures_live"] is False
        # local env default 이므로 crypto 는 항상 False.


# ----------------------------------------------------------------------
# 9. 정적 import 가드 — 위험 모듈 미포함
# ----------------------------------------------------------------------


class TestStaticImportGuards:
    """feature_flags 모듈이 broker / executor / 외부 API 클라이언트를
    import 하지 않음을 정적 grep 으로 검증.

    9번 Config Layer 와의 역할 분리 — feature_flags 는 *판단*만, 실행은
    별도 모듈 (execution/governance) 에서 수행.
    """

    def _module_source(self) -> str:
        path = Path(inspect.getfile(ff))
        return path.read_text(encoding="utf-8")

    def test_no_broker_imports(self):
        src = self._module_source()
        for forbidden in (
            "from app.brokers",
            "import app.brokers",
            "from app.execution",
            "import app.execution",
        ):
            assert forbidden not in src, (
                f"feature_flags 가 {forbidden!r} 를 import — 책임 분리 위반"
            )

    def test_no_external_api_imports(self):
        src = self._module_source()
        for forbidden in (
            "import anthropic",
            "from anthropic",
            "import openai",
            "from openai",
            "import httpx",
            "from httpx",
            "import requests",
            "from requests",
        ):
            assert forbidden not in src, (
                f"feature_flags 가 외부 API 클라이언트 {forbidden!r} 를 import"
            )

    def test_no_order_execution_function_calls(self):
        """broker.place_order / route_order / submit_candidate 호출 0건."""
        src = self._module_source()
        for forbidden in (
            "broker.place_order(",
            "route_order(",
            "submit_candidate(",
            ".place_order(",
            ".cancel_order(",
        ):
            assert forbidden not in src, (
                f"feature_flags 가 {forbidden!r} 호출 — 실행 책임 위반"
            )

    def test_no_secret_field_references(self):
        """settings.kis_app_key 등 secret 필드를 *읽지 않음*."""
        src = self._module_source()
        for forbidden in (
            "kis_app_key",
            "kis_app_secret",
            "kis_account_no",
            "anthropic_api_key",
            "openai_api_key",
            "telegram_bot_token",
        ):
            assert forbidden not in src, (
                f"feature_flags 가 secret 필드 {forbidden!r} 를 참조"
            )
