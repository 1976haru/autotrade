"""#4-RiskProfile: AI Paper 운용 성향 (보수적 / 안정적 / 공격적) 프리셋.

사용자가 Paper 자동매매에 들어가기 전, **AI 의 위험 성향** 을 셋 중 하나로
선택할 수 있게 한다. 선택된 프리셋은 다음 두 요소를 *동시에* 조정:

1. 4-08 `PositionSizingPolicy` — 가상 수량 산정 임계값
2. 4-09 `RiskVetoSeverity` — 신규 진입 차단의 *엄격함* (risk_flags 허용 개수)

본 모듈은 *advisory preset 카탈로그* 만 제공한다. 운영자가 명시적으로 프리셋을
선택하지 않으면 `BALANCED` (안정적) 가 default. **공격적(`AGGRESSIVE`)
프리셋이라도** 실거래 안전장치 (RiskManager / PermissionGate / OrderExecutor /
`ENABLE_LIVE_TRADING=false` default / KIS LIVE `NotImplementedError`) 를 **절대
우회하지 않는다** — 본 모듈은 *threshold 만* 조정하고 broker / OrderExecutor /
route_order 를 *직접 import 하지 않는다*.

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. broker / OrderExecutor / route_order import 0건 (정적 grep + AST 가드).
2. `RiskProfilePolicy.is_order_signal=False` / `auto_apply_allowed=False` /
   `is_live_authorization=False` 불변.
3. **AGGRESSIVE 도 `is_live_authorization=False`** — 공격적 성향은 *Paper*
   허들만 낮추며 실거래 권한을 주지 *않는다*.
4. 외부 HTTP / AI SDK / LLM import 0건.
5. DB write 0건 — 순수 dataclass + 함수.

## 사용

```python
from app.agents.risk_profile import (
    RiskProfile, DEFAULT_RISK_PROFILE, policy_for,
    sizing_policy_for, risk_veto_policy_for,
)

profile = RiskProfile.BALANCED        # 사용자 선택 (default)
preset = policy_for(profile)          # full RiskProfilePolicy
sizing = sizing_policy_for(profile)   # 4-08 PositionSizingPolicy 인스턴스
veto = risk_veto_policy_for(profile)  # dict-style 4-09 임계값
```
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.auto_paper.position_sizer import PositionSizingPolicy


RISK_PROFILE_SCHEMA_VERSION = "1.0"


class RiskProfile(StrEnum):
    """AI 운용 성향 — *주문 방향 0개*, advisory preset 라벨."""
    CONSERVATIVE = "CONSERVATIVE"   # 보수적
    BALANCED     = "BALANCED"       # 안정적 (default)
    AGGRESSIVE   = "AGGRESSIVE"     # 공격적


DEFAULT_RISK_PROFILE: RiskProfile = RiskProfile.BALANCED


_PROFILE_LABEL_KO: dict[RiskProfile, str] = {
    RiskProfile.CONSERVATIVE: "보수적",
    RiskProfile.BALANCED:     "안정적 (기본값)",
    RiskProfile.AGGRESSIVE:   "공격적",
}


_PROFILE_SUMMARY_KO: dict[RiskProfile, str] = {
    RiskProfile.CONSERVATIVE: (
        "손실 방어 우선 — confidence 임계 높게, risk_flags 허용 적게, "
        "position size 축소, 거래 후보 적게."
    ),
    RiskProfile.BALANCED: (
        "기본값 — confidence / risk_flags / position size 가 중간 수준. "
        "신규 진입 제한과 거래 기회 사이의 균형."
    ),
    RiskProfile.AGGRESSIVE: (
        "거래 기회를 넓게 — confidence 임계 낮춤, risk_flags 허용 늘림, "
        "position size 확대. **실거래 안전장치는 절대 우회 못함** — "
        "Paper 단계에서만 유효하며 LIVE 게이트를 별도로 통과해야 함."
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# RiskProfilePolicy — 단일 프리셋의 *모든 threshold* 카탈로그.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskProfilePolicy:
    """단일 프리셋의 운용 threshold 묶음 — *advisory only*.

    필드는 *대표 값* 으로 정해진 deterministic preset. 운영자가 본 프리셋 위에
    추가 override 를 원하면 별도 PR / 옵트인 흐름으로 진행.
    """

    profile:                  RiskProfile

    # 4-08 sizing — 1회 거래 손실 한도 / 종목 비중 / KRW cap / 최소 confidence.
    max_risk_per_trade_pct:   float
    default_stop_loss_pct:    float
    max_position_pct:         float
    max_position_krw:         int
    min_confidence_threshold: float
    max_risk_flags:           int        # 이 값 *이상* 이면 sizing 0 (4-08).
    min_unit_quantity:        int        = 1

    # 4-09 risk veto — 신규 BUY 허용 risk_flag 최대 개수 (이보다 많으면 차단).
    risk_veto_max_flags:      int        = 0

    # 운용자 친화 라벨.
    label_ko:                 str        = ""
    summary_ko:               str        = ""

    # 절대 invariant.
    is_order_signal:          bool = False
    auto_apply_allowed:       bool = False
    is_live_authorization:    bool = False

    # 후보 차단 / 진입 카운트 — 운영자가 *총 보유 종목* 을 제한 가능 (Paper).
    max_concurrent_candidates: int       = 0   # 0=비활성

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"RiskProfilePolicy.{name} must be False.")
        if not isinstance(self.profile, RiskProfile):
            raise ValueError("profile must be RiskProfile enum.")
        # 4-08 invariants — sizing 와 동일한 검증.
        if not (0.0 < self.max_risk_per_trade_pct <= 1.0):
            raise ValueError(
                f"max_risk_per_trade_pct must be in (0,1], got {self.max_risk_per_trade_pct}"
            )
        if not (0.0 < self.default_stop_loss_pct <= 1.0):
            raise ValueError(
                f"default_stop_loss_pct must be in (0,1], got {self.default_stop_loss_pct}"
            )
        if not (0.0 < self.max_position_pct <= 1.0):
            raise ValueError(
                f"max_position_pct must be in (0,1], got {self.max_position_pct}"
            )
        if self.max_position_krw <= 0:
            raise ValueError("max_position_krw must be > 0")
        if not (0.0 <= self.min_confidence_threshold <= 1.0):
            raise ValueError("min_confidence_threshold must be in [0,1]")
        if self.max_risk_flags < 0:
            raise ValueError("max_risk_flags must be >= 0")
        if self.min_unit_quantity < 1:
            raise ValueError("min_unit_quantity must be >= 1")
        if self.risk_veto_max_flags < 0:
            raise ValueError("risk_veto_max_flags must be >= 0")
        if self.max_concurrent_candidates < 0:
            raise ValueError("max_concurrent_candidates must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile":                   self.profile.value,
            "label_ko":                  self.label_ko,
            "summary_ko":                self.summary_ko,
            "max_risk_per_trade_pct":    float(self.max_risk_per_trade_pct),
            "default_stop_loss_pct":     float(self.default_stop_loss_pct),
            "max_position_pct":          float(self.max_position_pct),
            "max_position_krw":          int(self.max_position_krw),
            "min_confidence_threshold":  float(self.min_confidence_threshold),
            "max_risk_flags":            int(self.max_risk_flags),
            "min_unit_quantity":         int(self.min_unit_quantity),
            "risk_veto_max_flags":       int(self.risk_veto_max_flags),
            "max_concurrent_candidates": int(self.max_concurrent_candidates),
            "is_order_signal":           False,
            "auto_apply_allowed":        False,
            "is_live_authorization":     False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Presets — 3 deterministic policies.
# ─────────────────────────────────────────────────────────────────────────────
#
# 설계 원칙:
#  - CONSERVATIVE (보수적):
#      손실 방어 우선. confidence 매우 높아야, risk_flag 거의 못 허용, 1회 거래
#      손실 한도 작음, 종목 비중 작음, 동시 보유 후보 적음.
#  - BALANCED (안정적, default):
#      4-08 module 의 기존 default 와 일치 — 운영 기본값. 평균 수준.
#  - AGGRESSIVE (공격적):
#      거래 기회 넓게. confidence 임계 낮춰 더 많은 후보 통과, risk_flag 더
#      허용, position size 확대. *실거래는 *불가* — 본 PR 은 Paper 한정.

_PRESETS: dict[RiskProfile, RiskProfilePolicy] = {
    RiskProfile.CONSERVATIVE: RiskProfilePolicy(
        profile=RiskProfile.CONSERVATIVE,
        # sizing 임계 — 작게.
        max_risk_per_trade_pct=0.005,      # 0.5% (BALANCED 의 절반)
        default_stop_loss_pct=0.02,        # 2.0% — 좁은 stop, 작은 base qty
        max_position_pct=0.10,             # 10% (BALANCED 의 절반)
        max_position_krw=3_000_000,        # 300만원
        min_confidence_threshold=0.60,     # 60% 이상만 진입
        max_risk_flags=2,                  # 2개 이상 시 sizing 0
        min_unit_quantity=1,
        # 4-09 veto — risk_flag 0개 까지만 신규 진입 허용 (= 1개라도 있으면 차단).
        risk_veto_max_flags=0,
        max_concurrent_candidates=2,       # Paper 동시 후보 2종목 까지.
        label_ko=_PROFILE_LABEL_KO[RiskProfile.CONSERVATIVE],
        summary_ko=_PROFILE_SUMMARY_KO[RiskProfile.CONSERVATIVE],
    ),
    RiskProfile.BALANCED: RiskProfilePolicy(
        profile=RiskProfile.BALANCED,
        max_risk_per_trade_pct=0.01,       # 1.0% (4-08 default)
        default_stop_loss_pct=0.03,        # 3.0%
        max_position_pct=0.20,             # 20%
        max_position_krw=5_000_000,        # 500만원
        min_confidence_threshold=0.40,
        max_risk_flags=3,
        min_unit_quantity=1,
        risk_veto_max_flags=1,             # 1개까지 허용 (이상이면 차단).
        max_concurrent_candidates=3,
        label_ko=_PROFILE_LABEL_KO[RiskProfile.BALANCED],
        summary_ko=_PROFILE_SUMMARY_KO[RiskProfile.BALANCED],
    ),
    RiskProfile.AGGRESSIVE: RiskProfilePolicy(
        profile=RiskProfile.AGGRESSIVE,
        max_risk_per_trade_pct=0.02,       # 2.0% (BALANCED 의 2배)
        default_stop_loss_pct=0.05,        # 5.0% — 넓은 stop, 작은 base qty
        max_position_pct=0.30,             # 30%
        max_position_krw=8_000_000,        # 800만원
        min_confidence_threshold=0.30,     # 임계 낮춰 더 많은 후보 진입
        max_risk_flags=4,                  # 4개 까지는 허용
        min_unit_quantity=1,
        risk_veto_max_flags=2,             # 2개까지 허용.
        max_concurrent_candidates=5,
        label_ko=_PROFILE_LABEL_KO[RiskProfile.AGGRESSIVE],
        summary_ko=_PROFILE_SUMMARY_KO[RiskProfile.AGGRESSIVE],
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────


def policy_for(profile: RiskProfile | str | None) -> RiskProfilePolicy:
    """프리셋 → `RiskProfilePolicy` lookup.

    None / 빈 문자열 / 알 수 없는 값 → `DEFAULT_RISK_PROFILE` (BALANCED).
    """
    if profile is None or profile == "":
        return _PRESETS[DEFAULT_RISK_PROFILE]
    if isinstance(profile, str):
        try:
            profile = RiskProfile(profile.strip().upper())
        except ValueError:
            return _PRESETS[DEFAULT_RISK_PROFILE]
    if not isinstance(profile, RiskProfile):
        return _PRESETS[DEFAULT_RISK_PROFILE]
    return _PRESETS[profile]


def sizing_policy_for(profile: RiskProfile | str | None) -> PositionSizingPolicy:
    """프리셋 → 4-08 `PositionSizingPolicy` 변환.

    *broker 호출 0건* — 단순 dataclass 변환.
    """
    p = policy_for(profile)
    return PositionSizingPolicy(
        max_risk_per_trade_pct=p.max_risk_per_trade_pct,
        default_stop_loss_pct=p.default_stop_loss_pct,
        max_position_pct=p.max_position_pct,
        max_position_krw=p.max_position_krw,
        min_confidence_threshold=p.min_confidence_threshold,
        max_risk_flags=p.max_risk_flags,
        min_unit_quantity=p.min_unit_quantity,
    )


def risk_veto_policy_for(profile: RiskProfile | str | None) -> dict[str, int]:
    """프리셋 → 4-09 veto 임계값.

    현재 4-09 RiskVeto 는 entry 의 모든 risk_flag 매핑을 *어떤 한 개라도* 발견
    시 `BLOCK_NEW_ENTRY` 로 가지만, *프리셋별 허용 개수* 를 carry 해 후속
    PR 에서 본 값을 4-09 evaluator 에 주입할 수 있다 (예: BUY 차단 임계).
    """
    p = policy_for(profile)
    return {
        "risk_veto_max_flags":       p.risk_veto_max_flags,
        "max_concurrent_candidates": p.max_concurrent_candidates,
    }


def list_profiles() -> list[dict[str, Any]]:
    """UI / API 카탈로그 — 모든 프리셋 + 라벨 + 요약.

    *read-only* — broker / DB / 외부 호출 0건.
    """
    return [_PRESETS[p].to_dict() for p in
            (RiskProfile.CONSERVATIVE, RiskProfile.BALANCED, RiskProfile.AGGRESSIVE)]


def is_live_profile(_profile: RiskProfile | str | None) -> bool:
    """**항상 False** — 어떤 프리셋도 실거래 허가가 아니다 (invariant).

    AGGRESSIVE 도 Paper 한정 — 본 함수가 True 를 반환하는 일은 *없다*.
    """
    return False


__all__ = [
    "RISK_PROFILE_SCHEMA_VERSION",
    "RiskProfile",
    "DEFAULT_RISK_PROFILE",
    "RiskProfilePolicy",
    "policy_for",
    "sizing_policy_for",
    "risk_veto_policy_for",
    "list_profiles",
    "is_live_profile",
]
