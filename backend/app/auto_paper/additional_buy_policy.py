"""P-13: 물타기 / 추가매수 / 피라미딩 정책 — Paper advisory.

본 모듈은 *정책 + 메시지* 의 단일 진실. 실제 BUY 차단 흐름은 P-12
`check_duplicate_position_buy` (`DUPLICATE_POSITION_BUY_BLOCKED`) 에서
이미 수행되며, 본 모듈은:

  1. 시스템 default 가 *항상* False 임을 보장하는 상수
  2. UI / API 가 사용자에게 보여줄 한국어 메시지
  3. reason_code 상수 (`ADDITIONAL_BUY_DISABLED` / `AVERAGING_DOWN_DISABLED` /
     `PYRAMIDING_DISABLED`)
  4. `resolve_capital_allocation_policy()` — 사용자 / risk profile / 시스템
     default 우선순위로 단일 정책 객체 조립

`docs/capital_allocation_policy.md` §5 (본 PR) 와 1:1 매칭. 사용자 요청서
§1 정책: *기본 금지*, 공격형 도 자동 허용 안 함.

CLAUDE.md 절대 원칙 (테스트로 lock):
- broker / OrderExecutor / route_order import 0건
- KIS / Anthropic / OpenAI / httpx / requests import 0건
- app.core.config.get_settings import 0건
- settings.enable_*_trading mutation 0건
- CapitalAllocationPolicyResult.is_paper_only = True 영구
- is_order_signal / is_live_authorization = False 영구
- 본 모듈의 *시스템 default* 는 *영구 False*
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# P-12 의 default 를 *단일 진실* 로 carry (re-export 가 아닌 import — 본 모듈
# 은 capital_config 가 보장하는 default 를 그대로 사용).
from app.auto_paper.capital_config import (
    DEFAULT_ALLOW_AVERAGING_DOWN,
    DEFAULT_ALLOW_PYRAMIDING,
    resolve_additional_buy_policy,
)

# `DEFAULT_ALLOW_ADDITIONAL_BUY` 는 `resolve_additional_buy_policy` 가
# 캡슐화해 사용 — 본 모듈에서는 직접 참조하지 않음.


# ============================================================================
# reason_code 상수 (사용자 요청서 §9 정확)
# ============================================================================


ADDITIONAL_BUY_DISABLED  = "ADDITIONAL_BUY_DISABLED"
AVERAGING_DOWN_DISABLED  = "AVERAGING_DOWN_DISABLED"
PYRAMIDING_DISABLED      = "PYRAMIDING_DISABLED"


# ============================================================================
# 사용자 표시 메시지 (사용자 요청서 §3 정확)
# ============================================================================


ADDITIONAL_BUY_DISABLED_MESSAGE_KO  = (
    "동일 종목 추가매수는 기본 금지입니다."
)
AVERAGING_DOWN_DISABLED_MESSAGE_KO  = (
    "물타기는 손실 확대 위험으로 기본 금지입니다."
)
PYRAMIDING_DISABLED_MESSAGE_KO      = (
    "피라미딩은 기본 금지입니다."
)

ADDITIONAL_BUY_ENABLED_MESSAGE_KO   = (
    "동일 종목 추가매수가 옵트인되었습니다. 단, Paper 현금 / 일일 한도 / "
    "종목 비중 / RiskManager / PermissionGate 는 별도 적용됩니다."
)
AVERAGING_DOWN_ENABLED_MESSAGE_KO   = (
    "물타기가 옵트인되었습니다. 단, 본 정책은 Paper 검증 단계에서만 유효 — "
    "실거래 자동 추가매수는 별도 promotion gate 필요."
)
PYRAMIDING_ENABLED_MESSAGE_KO       = (
    "피라미딩이 옵트인되었습니다. 단, 전략별 검증 / RiskManager / 일일 한도 "
    "등은 별도 적용됩니다."
)


def policy_message_for(*, key: str, allowed: bool) -> str:
    """policy key 에 대한 한국어 메시지 lookup.

    key: "additional_buy" / "averaging_down" / "pyramiding".
    allowed: True 면 enabled 메시지, False 면 disabled 메시지.
    """
    if key == "additional_buy":
        return (ADDITIONAL_BUY_ENABLED_MESSAGE_KO if allowed
                else ADDITIONAL_BUY_DISABLED_MESSAGE_KO)
    if key == "averaging_down":
        return (AVERAGING_DOWN_ENABLED_MESSAGE_KO if allowed
                else AVERAGING_DOWN_DISABLED_MESSAGE_KO)
    if key == "pyramiding":
        return (PYRAMIDING_ENABLED_MESSAGE_KO if allowed
                else PYRAMIDING_DISABLED_MESSAGE_KO)
    raise ValueError(f"unknown policy key: {key}")


def reason_code_for(*, key: str, allowed: bool) -> str | None:
    """policy key 에 대한 reason_code lookup.

    allowed=True 면 차단 사유가 *없으므로* None.
    allowed=False 면 ADDITIONAL_BUY_DISABLED / AVERAGING_DOWN_DISABLED /
    PYRAMIDING_DISABLED 중 하나.
    """
    if allowed:
        return None
    if key == "additional_buy":
        return ADDITIONAL_BUY_DISABLED
    if key == "averaging_down":
        return AVERAGING_DOWN_DISABLED
    if key == "pyramiding":
        return PYRAMIDING_DISABLED
    raise ValueError(f"unknown policy key: {key}")


# ============================================================================
# Policy result dataclass
# ============================================================================


@dataclass(frozen=True)
class CapitalAllocationPolicyResult:
    """추가매수 / 물타기 / 피라미딩 정책 응답 — *advisory*.

    `is_paper_only=True` / `is_order_signal=False` / `is_live_authorization=
    False` 영구 (dataclass __post_init__ 가드).
    """

    allow_additional_buy:                bool
    allow_averaging_down:                bool
    allow_pyramiding:                    bool
    additional_buy_policy_message:       str
    averaging_down_policy_message:       str
    pyramiding_policy_message:           str
    additional_buy_reason_code:          str | None
    averaging_down_reason_code:          str | None
    pyramiding_reason_code:              str | None
    risk_profile:                        str | None = None
    source:                              str = "system_default"

    is_paper_only:                       bool = True
    is_order_signal:                     bool = False
    is_live_authorization:               bool = False

    def __post_init__(self) -> None:
        if self.is_paper_only is not True:
            raise ValueError(
                "CapitalAllocationPolicyResult.is_paper_only must be True"
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "CapitalAllocationPolicyResult.is_order_signal must be False"
            )
        if self.is_live_authorization is not False:
            raise ValueError(
                "CapitalAllocationPolicyResult.is_live_authorization must be False"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_additional_buy":           bool(self.allow_additional_buy),
            "allow_averaging_down":           bool(self.allow_averaging_down),
            "allow_pyramiding":               bool(self.allow_pyramiding),
            "additional_buy_policy_message":  self.additional_buy_policy_message,
            "averaging_down_policy_message":  self.averaging_down_policy_message,
            "pyramiding_policy_message":      self.pyramiding_policy_message,
            "additional_buy_reason_code":     self.additional_buy_reason_code,
            "averaging_down_reason_code":     self.averaging_down_reason_code,
            "pyramiding_reason_code":         self.pyramiding_reason_code,
            "risk_profile":                   self.risk_profile,
            "source":                         self.source,
            "is_paper_only":                  self.is_paper_only,
            "is_order_signal":                self.is_order_signal,
            "is_live_authorization":          self.is_live_authorization,
        }


# ============================================================================
# Resolve — 사용자 / risk profile / 시스템 default 우선순위
# ============================================================================
#
# 사용자 요청서 §4 정확 매트릭스 (모든 risk profile 에서 default False):
#   CONSERVATIVE: F / F / F
#   BALANCED:    F / F / F
#   AGGRESSIVE:  F / F / F


def resolve_capital_allocation_policy(
    *,
    risk_profile:                  str | None  = None,
    manual_allow_additional_buy:   bool | None = None,
    manual_allow_averaging_down:   bool | None = None,
    manual_allow_pyramiding:       bool | None = None,
) -> CapitalAllocationPolicyResult:
    """추가매수 / 물타기 / 피라미딩 통합 정책 resolve.

    우선순위 (사용자 요청서 §7):
      1. 사용자 명시 (`manual_*` 가 None 이 아닌 경우)
      2. risk profile 기본 (현재는 모든 profile 에서 False — §4 명시)
      3. 시스템 default (영구 False — §7 명시)

    공격형 risk profile 도 자동 허용 사용 안 함 (사용자 요청서 §4 주의).

    Returns:
        CapitalAllocationPolicyResult — broker / route_order 호출 0건.
    """
    # 1. additional_buy: P-12 의 resolve helper 재사용 (manual → system default).
    add_buy, add_buy_source = resolve_additional_buy_policy(
        manual_allow_additional_buy=manual_allow_additional_buy,
    )

    # 2. averaging_down / pyramiding: 동일 규칙 (manual → system default).
    avg_source = "system_default"
    if manual_allow_averaging_down is not None:
        avg_down = bool(manual_allow_averaging_down)
        avg_source = "manual"
    else:
        avg_down = DEFAULT_ALLOW_AVERAGING_DOWN

    pyr_source = "system_default"
    if manual_allow_pyramiding is not None:
        pyr = bool(manual_allow_pyramiding)
        pyr_source = "manual"
    else:
        pyr = DEFAULT_ALLOW_PYRAMIDING

    # source 라벨 — 어느 한쪽이라도 manual 이면 "manual", 아니면 system_default.
    # (risk_profile 기반 자동 적용은 본 PR 시점에 사용하지 않음 — 사용자 요청서
    # §4: 공격형 도 자동 허용 안 함. 향후 PR 에서 strategy / risk_profile 별
    # 옵트인 추가 시 source 도 확장.)
    if any(s == "manual" for s in (add_buy_source, avg_source, pyr_source)):
        source = "manual"
    else:
        source = "system_default"

    return CapitalAllocationPolicyResult(
        allow_additional_buy=add_buy,
        allow_averaging_down=avg_down,
        allow_pyramiding=pyr,
        additional_buy_policy_message=policy_message_for(
            key="additional_buy", allowed=add_buy,
        ),
        averaging_down_policy_message=policy_message_for(
            key="averaging_down", allowed=avg_down,
        ),
        pyramiding_policy_message=policy_message_for(
            key="pyramiding", allowed=pyr,
        ),
        additional_buy_reason_code=reason_code_for(
            key="additional_buy", allowed=add_buy,
        ),
        averaging_down_reason_code=reason_code_for(
            key="averaging_down", allowed=avg_down,
        ),
        pyramiding_reason_code=reason_code_for(
            key="pyramiding", allowed=pyr,
        ),
        risk_profile=(
            str(risk_profile).strip().upper() if risk_profile else None
        ),
        source=source,
    )


__all__ = [
    "ADDITIONAL_BUY_DISABLED",
    "AVERAGING_DOWN_DISABLED",
    "PYRAMIDING_DISABLED",
    "ADDITIONAL_BUY_DISABLED_MESSAGE_KO",
    "AVERAGING_DOWN_DISABLED_MESSAGE_KO",
    "PYRAMIDING_DISABLED_MESSAGE_KO",
    "ADDITIONAL_BUY_ENABLED_MESSAGE_KO",
    "AVERAGING_DOWN_ENABLED_MESSAGE_KO",
    "PYRAMIDING_ENABLED_MESSAGE_KO",
    "policy_message_for",
    "reason_code_for",
    "CapitalAllocationPolicyResult",
    "resolve_capital_allocation_policy",
]
