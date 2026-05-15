"""Feature Flags — 위험 기능 다중 잠금 계층 (작업 10).

본 모듈은 `app.core.config.Settings` 의 위험 플래그를 *다중 조건*으로 다시
평가하는 read-only view 다. 단일 env 플래그 (`ENABLE_LIVE_TRADING=true`) 만
으로 실거래가 가능해지는 것을 막기 위해, mode capability + environment +
정책 invariant 까지 동시 만족해야 True 를 반환한다.

설계 원칙 (CLAUDE.md / 작업 10 사양):
- 위험 플래그 4종 (live_trading / ai_execution / crypto_futures_live /
  kimp_strategy) 기본값 모두 False.
- 본 모듈은 *판단*만 — 실제 주문 실행 / 한투 API / 코인거래소 API 호출 0건.
- broker / OrderExecutor / route_order / 한투 client / 거래소 client import
  0건 (정적 grep 가드).
- public_snapshot() / 에러 메시지에 Secret 정보 (KIS app key/secret/계좌번호,
  Anthropic key, Telegram token 등) 0건. settings 의 secret 필드 *참조도
  하지 않음*.
- @lru_cache 적용 — 테스트에서 cache_clear() 호출 가능.

9번 Config Layer 와의 역할 분리:
- config.py    : 설정값 로딩 / 검증 (Settings)
- feature_flags: 기능 활성 *가능 여부* 판단 (본 모듈)
- governance   : 승인 / 감사 / 운영자 확인 (별도)
- execution    : 주문 직전 최종 차단 (별도, assert_feature_allowed 호출자)

향후 execution 모듈이 broker.place_order 호출 *직전* assert_feature_allowed()
를 호출하도록 설계 — feature_flags 가 차단하면 주문 시도 0건.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from app.core.config import Settings, get_settings
from app.core.modes import MODE_CAPABILITIES, OperationMode


class FeatureDisabledError(RuntimeError):
    """가드된 기능이 호출됐지만 활성화되어 있지 않을 때 raise.

    에러 메시지에는 Secret 정보 (API key / 계좌번호 등) 를 포함하지 않는다.
    feature 이름과 차단 사유만 carry.
    """


# OperationMode → trading_mode 라벨 매핑. is_live_trading_enabled() 가
# `trading_mode == "live"` 를 확인할 때 본 라벨이 기준이 된다.
_LIVE_MODES = {
    OperationMode.LIVE_SHADOW,
    OperationMode.LIVE_MANUAL_APPROVAL,
    OperationMode.LIVE_AI_ASSIST,
    OperationMode.LIVE_AI_EXECUTION,
}


def _mode_to_label(mode: OperationMode) -> str:
    """OperationMode 를 trading_mode 라벨로 변환.

    - LIVE_*               -> "live"
    - PAPER                -> "paper"
    - VIRTUAL_AI_EXECUTION -> "virtual"
    - SIMULATION           -> "simulation"
    """
    if mode in _LIVE_MODES:
        return "live"
    if mode == OperationMode.PAPER:
        return "paper"
    if mode == OperationMode.VIRTUAL_AI_EXECUTION:
        return "virtual"
    return "simulation"


@dataclass(frozen=True)
class FeatureFlags:
    """위험 기능 활성 가능 여부를 한 곳에 집계한 read-only view.

    Settings 의 값을 carry — Settings 자체는 변경하지 않는다. 테스트에서는
    본 dataclass 를 직접 생성해 다양한 조건 조합을 검증 가능.

    필드:
    - enable_*: env 옵트인 플래그 (기본 False)
    - trading_mode: OperationMode 라벨 ("live"/"paper"/"virtual"/"simulation")
    - environment: app_env ("local"/"staging"/"production" 등)
    - allow_live_trading: mode capability "live_order" 파생
    - require_approval_for_live: 안전 정책 — *반드시 True 유지* (defensive
      invariant, 명시 옵트아웃 시 is_live_trading_enabled() 즉시 False)
    """

    enable_live_trading: bool = False
    enable_ai_execution: bool = False
    enable_crypto_futures_live: bool = False
    enable_kimp_strategy: bool = False

    trading_mode: str = "simulation"
    environment: str = "local"

    allow_live_trading: bool = False
    require_approval_for_live: bool = True

    # ------------------------------------------------------------------
    # 판단 함수
    # ------------------------------------------------------------------

    def is_live_trading_enabled(self) -> bool:
        """실거래 가능 여부 — 다중 조건 모두 만족해야 True.

        조건 (모두 AND):
        1. enable_live_trading == True
        2. trading_mode == "live"
        3. allow_live_trading == True (mode capability live_order)
        4. require_approval_for_live == True (안전 정책 invariant)

        본 함수는 *feature flag 관점*에서만 평가 — 실제 주문 직전에는
        approval queue / RiskManager / OrderExecutor 의 추가 검증 필요.
        """
        return (
            self.enable_live_trading
            and self.trading_mode == "live"
            and self.allow_live_trading
            and self.require_approval_for_live
        )

    def is_ai_execution_enabled(self) -> bool:
        """AI 실행 *판단 모듈* 활성 가능 여부.

        본 함수는 'AI 실행 모듈을 *켤 수 있는가*' 만 판단 — 'AI 가 *주문을
        직접 실행할 수 있는가*' 의미가 *아님*. 실제 주문 실행은 별도 governance
        + execution + RiskManager + AI Permission Gate (#39) 단계 통과 필요.

        live trading 이 꺼져 있어도 paper/mock 범위에서 AI 실행 판단 모듈은
        활성 가능 — 본 함수는 그 활성 *가능 여부*만 반환.
        """
        return self.enable_ai_execution

    def is_crypto_futures_live_enabled(self) -> bool:
        """코인 선물 실거래 가능 여부 — 강한 다중 잠금.

        조건 (모두 AND):
        1. environment != "local" (로컬 개발환경 hard-block — 우선 조건)
        2. enable_crypto_futures_live == True
        3. is_live_trading_enabled() == True
        4. trading_mode == "live"
        5. allow_live_trading == True

        주식 선물 (KOSPI200 등) 의 `enable_futures_live_trading` 과는 *별개*
        플래그. 코인 거래소 API 호출은 본 PR 범위 외 — 본 함수는 활성 가능
        여부만 판단.
        """
        if self.environment == "local":
            return False
        return (
            self.enable_crypto_futures_live
            and self.is_live_trading_enabled()
            and self.trading_mode == "live"
            and self.allow_live_trading
        )

    def is_kimp_strategy_enabled(self) -> bool:
        """김프(Korean Premium / 김치 프리미엄) 전략 모듈 활성 여부.

        strategy flag only, not execution permission — paper/mock 모드에서도
        `enable_kimp_strategy=True` 면 전략 테스트 가능. 실거래 수행은 별도
        is_crypto_futures_live_enabled() / is_live_trading_enabled() 가
        필요 — 본 flag 는 "전략 모듈을 *코드 단에서 활성화*" 만 의미.
        """
        return self.enable_kimp_strategy

    # ------------------------------------------------------------------
    # 외부 노출
    # ------------------------------------------------------------------

    def public_snapshot(self) -> dict:
        """UI / 외부 API 용 안전 스냅샷 — Secret 포함 0건.

        본 함수는 KIS app key / secret / 계좌번호 / Anthropic key / Telegram
        bot token 등 *어떤 secret 도 참조하지 않는다*. UI / `/api/health` 등
        외부 노출 경로에서는 본 함수의 결과만 사용해야 한다.
        """
        return {
            "live_trading":        self.is_live_trading_enabled(),
            "ai_execution":        self.is_ai_execution_enabled(),
            "crypto_futures_live": self.is_crypto_futures_live_enabled(),
            "kimp_strategy":       self.is_kimp_strategy_enabled(),
            "trading_mode":        self.trading_mode,
            "environment":         self.environment,
        }


# ----------------------------------------------------------------------
# Settings -> FeatureFlags
# ----------------------------------------------------------------------

def _build_from_settings(settings: Settings) -> FeatureFlags:
    """Settings 인스턴스에서 FeatureFlags 를 생성 (no cache).

    Settings 의 risky flag + default_mode + app_env 를 carry. mode capability
    "live_order" 를 allow_live_trading 으로 매핑.
    """
    mode = settings.default_mode
    caps = MODE_CAPABILITIES[mode]
    return FeatureFlags(
        enable_live_trading=settings.enable_live_trading,
        enable_ai_execution=settings.enable_ai_execution,
        enable_crypto_futures_live=settings.enable_crypto_futures_live,
        enable_kimp_strategy=settings.enable_kimp_strategy,
        trading_mode=_mode_to_label(mode),
        environment=settings.app_env,
        allow_live_trading=caps["live_order"],
        # require_approval_for_live 는 안전 정책 invariant — 기본 True.
        # Settings 에 해당 필드 없음 → 항상 True (defensive default).
        require_approval_for_live=True,
    )


@lru_cache
def get_feature_flags() -> FeatureFlags:
    """현재 Settings 기반 FeatureFlags 인스턴스 (cached).

    @lru_cache 적용 — 테스트에서는 ``get_feature_flags.cache_clear()`` 호출
    후 환경 / settings 변경 후 재호출.
    """
    return _build_from_settings(get_settings())


# ----------------------------------------------------------------------
# 가드 헬퍼
# ----------------------------------------------------------------------

# 지원되는 feature 이름 매트릭스 — assert_feature_allowed() / 추후
# governance 모듈에서 공유.
_FEATURE_CHECKERS = {
    "live_trading":        FeatureFlags.is_live_trading_enabled,
    "ai_execution":        FeatureFlags.is_ai_execution_enabled,
    "crypto_futures_live": FeatureFlags.is_crypto_futures_live_enabled,
    "kimp_strategy":       FeatureFlags.is_kimp_strategy_enabled,
}


def supported_feature_names() -> tuple[str, ...]:
    """assert_feature_allowed() 가 인식하는 feature 이름 목록."""
    return tuple(_FEATURE_CHECKERS.keys())


def assert_feature_allowed(
    feature_name: str,
    *,
    flags: Optional[FeatureFlags] = None,
) -> None:
    """기능 사용 *직전* 차단용. 허용되지 않으면 FeatureDisabledError raise.

    향후 execution 모듈이 broker.place_order 호출 *직전* 본 함수를 호출하도록
    설계. 예외 메시지에는 feature 이름 + 차단 사유 라벨만 carry — Secret 0건.

    Args:
        feature_name: 지원 목록은 supported_feature_names() — "live_trading" /
            "ai_execution" / "crypto_futures_live" / "kimp_strategy".
        flags: 검사 대상 FeatureFlags. None 이면 get_feature_flags() 캐시 사용.

    Raises:
        FeatureDisabledError:
            - feature_name 이 지원 목록에 없음 ("unknown feature: ...")
            - 검사 통과 실패 ("feature '...' is disabled: ...")
    """
    if feature_name not in _FEATURE_CHECKERS:
        raise FeatureDisabledError(
            f"unknown feature: {feature_name!r} "
            f"(supported: {', '.join(supported_feature_names())})"
        )

    f = flags if flags is not None else get_feature_flags()
    if not _FEATURE_CHECKERS[feature_name](f):
        # 차단 사유 라벨 — Secret 포함 0건, env 옵트인 / mode / environment
        # 같은 컨텍스트만 carry.
        reason = _disable_reason(feature_name, f)
        raise FeatureDisabledError(
            f"feature {feature_name!r} is disabled: {reason}"
        )


def _disable_reason(feature_name: str, f: FeatureFlags) -> str:
    """차단 사유 라벨 — Secret 0건. 사람이 읽을 수 있는 짧은 진단 문구.

    각 분기는 enable_* / trading_mode / environment / allow_live_trading /
    require_approval_for_live 같은 *플래그 라벨* 만 carry — API key / 계좌번호
    같은 secret 은 *참조하지 않는다*.
    """
    if feature_name == "live_trading":
        if not f.enable_live_trading:
            return "enable_live_trading=False"
        if f.trading_mode != "live":
            return f"trading_mode={f.trading_mode!r} (not 'live')"
        if not f.allow_live_trading:
            return "allow_live_trading=False (mode capability)"
        if not f.require_approval_for_live:
            return "require_approval_for_live=False (safety policy violated)"
        return "unknown reason"
    if feature_name == "ai_execution":
        if not f.enable_ai_execution:
            return "enable_ai_execution=False"
        return "unknown reason"
    if feature_name == "crypto_futures_live":
        if f.environment == "local":
            return "environment='local' (hard-block)"
        if not f.enable_crypto_futures_live:
            return "enable_crypto_futures_live=False"
        if not f.is_live_trading_enabled():
            return "is_live_trading_enabled()=False"
        return "unknown reason"
    if feature_name == "kimp_strategy":
        if not f.enable_kimp_strategy:
            return "enable_kimp_strategy=False"
        return "unknown reason"
    return "unknown reason"


# ----------------------------------------------------------------------
# 외부 노출 (UI / API)
# ----------------------------------------------------------------------

def public_feature_snapshot() -> dict:
    """현재 활성 FeatureFlags 의 안전 스냅샷 — Secret 0건.

    UI / `/api/health` / `/api/status` 같은 외부 노출 경로용. 본 함수는
    get_feature_flags() 의 캐시를 사용 — 테스트에서는 cache_clear() 후 호출.
    """
    return get_feature_flags().public_snapshot()
