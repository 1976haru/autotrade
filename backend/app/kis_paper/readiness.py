"""KIS Paper readiness — preflight check for one-click test (#89).

본 모듈은 실 broker 를 호출하지 *않는다* — `Settings` 값만 읽어서 *코드 단*
가드를 evaluate 한다. 실제 KIS 연결 확인은 engine 단계의 read-only ping 에서.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class BlockedReason(StrEnum):
    """test 진입 차단 사유. 본 enum 의 어떤 값도 *주문 결정 라벨이 아니다*."""
    OK                            = "OK"
    KIS_IS_PAPER_FALSE            = "KIS_IS_PAPER_FALSE"
    ENABLE_LIVE_TRADING_TRUE      = "ENABLE_LIVE_TRADING_TRUE"
    ENABLE_AI_EXECUTION_TRUE      = "ENABLE_AI_EXECUTION_TRUE"
    ENABLE_FUTURES_LIVE_TRUE      = "ENABLE_FUTURES_LIVE_TRUE"
    KIS_KEY_MISSING               = "KIS_KEY_MISSING"
    KIS_SECRET_MISSING            = "KIS_SECRET_MISSING"
    KIS_ACCOUNT_MISSING           = "KIS_ACCOUNT_MISSING"
    DEFAULT_MODE_LIVE             = "DEFAULT_MODE_LIVE"


@dataclass(frozen=True)
class KisPaperReadiness:
    """preflight 결과. 본 객체는 *주문이 아니다* — UI / API 가 표시용으로 carry."""
    ready:               bool
    can_run_kis_paper:   bool          # KIS 모드 (quick / slow) 가능 여부
    can_run_mock:        bool          # 내부 mock 고속 가능 여부 (대부분 True)
    blocked_reasons:     tuple[BlockedReason, ...] = ()
    detail_messages:     tuple[str, ...] = ()
    safety_flags:        dict = field(default_factory=dict)
    # 진단용: secret 자체는 출력 안 하고 *존재 여부* 만 carry (frontend Secret 노출 0건).
    kis_key_present:     bool = False
    kis_secret_present:  bool = False
    kis_account_present: bool = False
    # 응답 안전 — invariant
    is_order_intent:     bool = False
    is_order_signal:     bool = False

    def __post_init__(self) -> None:
        if self.is_order_intent is not False:
            raise ValueError("KisPaperReadiness.is_order_intent must be False")
        if self.is_order_signal is not False:
            raise ValueError("KisPaperReadiness.is_order_signal must be False")

    def to_dict(self) -> dict:
        return {
            "ready":                self.ready,
            "can_run_kis_paper":    self.can_run_kis_paper,
            "can_run_mock":         self.can_run_mock,
            "blocked_reasons":      [r.value for r in self.blocked_reasons],
            "detail_messages":      list(self.detail_messages),
            "safety_flags":         dict(self.safety_flags),
            "kis_key_present":      self.kis_key_present,
            "kis_secret_present":   self.kis_secret_present,
            "kis_account_present":  self.kis_account_present,
            "is_order_intent":      False,
            "is_order_signal":      False,
        }


def evaluate_readiness(settings) -> KisPaperReadiness:
    """`Settings` (또는 dict-like) 입력으로 preflight 결과 반환.

    본 함수는 broker 를 호출하지 *않는다* — flag / key 존재 여부만 검사.

    Args:
        settings: `app.core.config.Settings` 인스턴스 또는 dict.
    """
    def _get(key: str, default=None):
        if hasattr(settings, key):
            return getattr(settings, key)
        if isinstance(settings, dict):
            return settings.get(key, default)
        return default

    kis_is_paper          = bool(_get("kis_is_paper", True))
    enable_live           = bool(_get("enable_live_trading", False))
    enable_ai_exec        = bool(_get("enable_ai_execution", False))
    enable_futures_live   = bool(_get("enable_futures_live_trading", False))
    default_mode          = str(_get("default_mode", "SIMULATION") or "SIMULATION")

    kis_key     = str(_get("kis_app_key", "") or "")
    kis_secret  = str(_get("kis_app_secret", "") or "")
    kis_account = str(_get("kis_account_no", "") or "")

    blockers: list[BlockedReason] = []
    details: list[str] = []

    # 1. live flag 차단 — 가장 중요.
    if enable_live:
        blockers.append(BlockedReason.ENABLE_LIVE_TRADING_TRUE)
        details.append(
            "ENABLE_LIVE_TRADING=true 가 켜져 있습니다. 본 테스트는 모의투자 "
            "전용 — backend/.env 에서 false 로 변경 후 재시작하세요."
        )
    if enable_ai_exec:
        blockers.append(BlockedReason.ENABLE_AI_EXECUTION_TRUE)
        details.append(
            "ENABLE_AI_EXECUTION=true 가 켜져 있습니다. 본 테스트는 AI 자동 "
            "실행이 *비활성* 인 상태에서만 진행."
        )
    if enable_futures_live:
        blockers.append(BlockedReason.ENABLE_FUTURES_LIVE_TRUE)
        details.append(
            "ENABLE_FUTURES_LIVE_TRADING=true 가 켜져 있습니다. 본 테스트는 "
            "선물 LIVE 가 *비활성* 인 상태에서만 진행."
        )

    # 2. KIS paper 모드 진입 가드.
    if not kis_is_paper:
        blockers.append(BlockedReason.KIS_IS_PAPER_FALSE)
        details.append(
            "KIS_IS_PAPER=false 입니다. 본 테스트는 모의투자 전용 — true 로 "
            "변경하지 않으면 KIS paper 주문이 차단됩니다."
        )

    # 3. default_mode 가 LIVE_* 면 위험.
    if default_mode.upper().startswith("LIVE_") and default_mode.upper() not in (
        "LIVE_SHADOW",
    ):
        blockers.append(BlockedReason.DEFAULT_MODE_LIVE)
        details.append(
            f"DEFAULT_MODE={default_mode} 가 실거래 계열입니다. 본 테스트는 "
            "SIMULATION / PAPER / LIVE_SHADOW 에서만 진행 권장."
        )

    # 4. KIS key 존재 여부 (KIS 모드 가능 판정용 — blocker 아님, 단순 capability).
    kis_key_present     = bool(kis_key.strip())
    kis_secret_present  = bool(kis_secret.strip())
    kis_account_present = bool(kis_account.strip())

    can_run_kis_paper = (
        not enable_live
        and not enable_ai_exec
        and kis_is_paper
        and kis_key_present
        and kis_secret_present
        and kis_account_present
    )

    # 5. mock 모드 — live flag 만 검사. KIS key 없어도 가능.
    can_run_mock = not enable_live and not enable_ai_exec

    if not kis_key_present:
        details.append(
            "KIS_APP_KEY 미설정 — KIS paper 모드는 비활성. mock 모드만 사용 "
            "가능합니다. backend/.env 에 모의투자 키를 채우면 KIS 모드 활성."
        )
    if not kis_secret_present:
        details.append("KIS_APP_SECRET 미설정 — KIS paper 모드 비활성.")
    if not kis_account_present:
        details.append("KIS_ACCOUNT_NO 미설정 — KIS paper 모드 비활성.")

    ready = (
        not enable_live
        and not enable_ai_exec
        and not enable_futures_live
        and kis_is_paper
        and not (default_mode.upper().startswith("LIVE_")
                 and default_mode.upper() != "LIVE_SHADOW")
    )

    safety_flags = {
        "default_mode":                  default_mode,
        "enable_live_trading":           enable_live,
        "enable_ai_execution":           enable_ai_exec,
        "enable_futures_live_trading":   enable_futures_live,
        "kis_is_paper":                  kis_is_paper,
    }

    return KisPaperReadiness(
        ready=ready,
        can_run_kis_paper=can_run_kis_paper,
        can_run_mock=can_run_mock,
        blocked_reasons=tuple(blockers),
        detail_messages=tuple(details),
        safety_flags=safety_flags,
        kis_key_present=kis_key_present,
        kis_secret_present=kis_secret_present,
        kis_account_present=kis_account_present,
    )
