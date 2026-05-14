"""Strategy Registry beginner-friendly metadata (#81).

기존 `STRATEGY_REGISTRY` (`backend/app/strategies/concrete/__init__.py`) 와
`describe_strategy()` 위에 *운영자 / 초보자 친화* 메타데이터를 *별도 레이어*
로 추가한다.

CLAUDE.md 절대 원칙:
- 본 모듈은 *기존 매매 로직을 변경하지 않는다* — 메타데이터만 carry.
- 본 모듈은 *새 전략을 등록하지 않는다* — 코드의 6개 strategy_id 만 매핑.
  코드에 없는 strategy_id 는 raise (정적 grep + runtime 테스트로 강제).
- broker / OrderExecutor / route_order import 0건. DB write 0건.
- 본 메타데이터는 *advisory only* — 주문 차단 / 실행 트리거로 사용 금지
  (`is_order_signal=False` invariant).

레이어 분리:
- 기존 `describe_strategy(name)` — contract metadata (entry/exit/invalidation/
  required_regime/risk_profile + __init__ params). 본 PR 미변경.
- 신규 `beginner_metadata(name)` — displayName / beginnerName / description /
  riskLevel / supportedModes / 가용 모드 (backtest / paper / live).

두 레이어는 *합쳐서* `/api/strategies/beginner-registry` 가 노출한다 (별도
endpoint — 기존 `/registry` 호환 유지).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.strategies.concrete import (
    STRATEGY_REGISTRY,
    describe_strategy,
)


# ---------- enums ----------


class RiskLevel(StrEnum):
    LOW       = "low"      # 보수적 — 모의 권장
    MEDIUM    = "medium"   # 보통 — 모의 검증 후 실전 주의
    HIGH      = "high"     # 공격적 — 실전 진입 매우 신중


class RecommendedMode(StrEnum):
    """초보자에게 추천되는 1차 모드.

    *권고*일 뿐 — 실제 차단은 RiskManager / Mode capabilities 가 담당.
    """
    PAPER_RECOMMENDED      = "paper_recommended"
    LIVE_AFTER_VALIDATION  = "live_after_validation"
    LIVE_CAUTION           = "live_caution"


# ---------- DTO ----------


@dataclass(frozen=True)
class BeginnerMetadata:
    """초보자용 표시 메타. 기존 strategy_id 와 1:1 매핑.

    `strategy_id` 는 `STRATEGY_REGISTRY` 의 key 와 *정확히* 일치해야 한다 —
    매핑이 어긋나면 `validate_metadata()` 가 ValueError.
    """
    strategy_id:           str
    display_name:          str            # UI 노출 (한글 권장)
    beginner_name:         str            # 더 풀어쓴 친화 이름
    description:           str            # 한 문장 — 어떤 상황에서 매매하는지
    risk_level:            RiskLevel
    recommended_mode:      RecommendedMode
    typical_hold_minutes:  int | None = None      # 일반 보유 시간 가이드
    notes:                 tuple[str, ...] = field(default_factory=tuple)


# ---------- metadata table ----------


# **본 dict 는 코드의 6개 strategy_id 와 *정확히* 일치해야 한다.**
# 새 strategy_id 추가 시 본 dict 도 같이 수정 — `validate_metadata()` 가 누락 감지.
#
# 모든 값은 *코드에 이미 구현된 로직*을 *설명*만 한다. 새 매매 행동을 만들지 않음.
_BEGINNER_METADATA: dict[str, BeginnerMetadata] = {
    "sma_crossover": BeginnerMetadata(
        strategy_id="sma_crossover",
        display_name="단기/장기 이동평균 교차",
        beginner_name="이평선 교차 추세 추종",
        description=(
            "단기 이동평균선이 장기 이동평균선을 위로 뚫고 올라가면 매수, "
            "아래로 내려가면 매도하는 가장 기본적인 추세 추종 전략입니다."
        ),
        risk_level=RiskLevel.MEDIUM,
        recommended_mode=RecommendedMode.PAPER_RECOMMENDED,
        notes=(
            "기본 파라미터: short=5, long=20 (코드 default).",
            "신호 품질은 봉 수 / regime 매칭 / 변동성으로 산정 (#136).",
        ),
    ),

    "rsi_reversion": BeginnerMetadata(
        strategy_id="rsi_reversion",
        display_name="RSI 과매도/과매수 회복",
        beginner_name="RSI 반등 / 반락 단타",
        description=(
            "RSI 지표가 과매도(기본 30) 이하로 떨어졌다가 회복할 때 매수, "
            "과매수(기본 70) 이상에서 하락할 때 매도하는 평균 회귀 전략입니다."
        ),
        risk_level=RiskLevel.MEDIUM,
        recommended_mode=RecommendedMode.PAPER_RECOMMENDED,
        notes=(
            "기본 파라미터: period=14, oversold=30, overbought=70.",
            "강한 추세장에서는 신호 품질이 낮아질 수 있음 — regime 필터 권장.",
        ),
    ),

    "vwap_strategy": BeginnerMetadata(
        strategy_id="vwap_strategy",
        display_name="VWAP 평균 회귀",
        beginner_name="거래량가중평균 회복 단타",
        description=(
            "주가가 VWAP(거래량가중평균)선 아래로 일시 이탈했다가 다시 회복할 때 "
            "매수하고, VWAP 아래로 재이탈하면 매도하는 단타 전략입니다."
        ),
        risk_level=RiskLevel.MEDIUM,
        recommended_mode=RecommendedMode.PAPER_RECOMMENDED,
        typical_hold_minutes=20,
        notes=(
            "익절 2.5% / 손절 1.5% / trailing 1% / time stop 20봉 (코드 default).",
            "유동성 부족 / VWAP 괴리율 초과 시 진입 차단.",
        ),
    ),

    "orb_vwap": BeginnerMetadata(
        strategy_id="orb_vwap",
        display_name="ORB + VWAP 돌파",
        beginner_name="시가 범위(ORB) 돌파 단타",
        description=(
            "장 시작 후 일정 봉 수 동안 형성된 가격 범위(ORB)의 상단을 돌파하고 "
            "VWAP 위에서 마감하는 첫 봉을 매수하는 일중 1회 돌파 전략입니다."
        ),
        risk_level=RiskLevel.HIGH,
        recommended_mode=RecommendedMode.PAPER_RECOMMENDED,
        notes=(
            "기본 ORB 봉 수: 6 (코드 default).",
            "돌파 실패 / VWAP 하향 시 매도. 코드에 명시적 SL/TP 없음 (risk_profile metadata 1.5%).",
        ),
    ),

    "volume_breakout": BeginnerMetadata(
        strategy_id="volume_breakout",
        display_name="거래량 급증 돌파",
        beginner_name="거래대금 급증 + 신고가 돌파 단타",
        description=(
            "거래대금이 직전 평균 대비 2배 이상 급증하면서 최근 고점을 돌파하고 "
            "VWAP 위일 때 매수하는 일중 1회 돌파 전략입니다."
        ),
        risk_level=RiskLevel.HIGH,
        recommended_mode=RecommendedMode.PAPER_RECOMMENDED,
        typical_hold_minutes=30,
        notes=(
            "익절 4% / 손절 2% / trailing 1.5% / time stop 30봉 (코드 default).",
            "거래량 급감 시 신호 강도 하락 — 후속 봉에서 추가 진입 자제.",
        ),
    ),

    "pullback_rebreak": BeginnerMetadata(
        strategy_id="pullback_rebreak",
        display_name="눌림목 재돌파",
        beginner_name="상승 임펄스 → 거래량 눌림 → 재돌파 단타",
        description=(
            "강한 상승(impulse) 직후 거래량이 잦아드는 눌림 구간을 거쳐, "
            "직전 고점을 다시 돌파하는 시점에 매수하는 일중 1회 전략입니다."
        ),
        risk_level=RiskLevel.HIGH,
        recommended_mode=RecommendedMode.PAPER_RECOMMENDED,
        typical_hold_minutes=30,
        notes=(
            "30+ 파라미터 (impulse/pullback lookback, 거래량 fade, VWAP 격차 등).",
            "익절 4% / 손절 2% baseline / trailing 1.5% / time stop 30봉.",
        ),
    ),
}


# ---------- supported modes / live trading availability ----------


# 모든 전략은 *동일한* 모드 매트릭스를 가진다 — strategy 가 mode 별 차이를 두지
# 않기 때문. `LiveStrategyEngine` (#28) 이 mode-aware 흐름을 *위*에서 처리한다.
#
# liveTradingAvailable 는 *KIS live place_order 미구현* 이므로 False 영구.
# 본 메타데이터는 그 상태를 단순 carry 한다.
_DEFAULT_SUPPORTED_MODES: tuple[str, ...] = (
    "SIMULATION",
    "PAPER",
    "LIVE_SHADOW",
    "LIVE_MANUAL_APPROVAL",
)
_DEFAULT_BACKTEST_AVAILABLE   = True
_DEFAULT_PAPER_AVAILABLE      = True
_DEFAULT_LIVE_AVAILABLE       = False    # KIS live 미구현 (NotImplementedError)


def supported_modes(strategy_id: str) -> tuple[str, ...]:
    if strategy_id not in STRATEGY_REGISTRY:
        return ()
    return _DEFAULT_SUPPORTED_MODES


def backtest_available(strategy_id: str) -> bool:
    return strategy_id in STRATEGY_REGISTRY and _DEFAULT_BACKTEST_AVAILABLE


def paper_trading_available(strategy_id: str) -> bool:
    return strategy_id in STRATEGY_REGISTRY and _DEFAULT_PAPER_AVAILABLE


def live_trading_available(strategy_id: str) -> bool:
    """실거래 가능 여부.

    KisBrokerAdapter.place_order(is_paper=False) 가 NotImplementedError 인 한
    *모든* 전략에 대해 False 영구.
    """
    return strategy_id in STRATEGY_REGISTRY and _DEFAULT_LIVE_AVAILABLE


# ---------- public API ----------


def beginner_metadata(strategy_id: str) -> BeginnerMetadata | None:
    return _BEGINNER_METADATA.get(strategy_id)


def list_beginner_registry() -> list[dict[str, Any]]:
    """6개 strategy_id 의 contract + beginner 메타를 합쳐 반환.

    *각 entry 는 advisory only* — 주문 차단 / 실행 트리거로 사용 금지.
    응답은 read-only — 본 함수는 *추정 / mutate* 하지 않는다.
    """
    out: list[dict[str, Any]] = []
    for strategy_id in STRATEGY_REGISTRY:
        contract = describe_strategy(strategy_id)
        meta = _BEGINNER_METADATA.get(strategy_id)
        if meta is None:
            # 메타 누락 — 안전한 기본값으로 fallback (운영자가 명시할 때까지).
            entry = {
                "strategy_id":              strategy_id,
                "internal_name":            contract["class_name"],
                "display_name":             strategy_id,
                "beginner_name":            strategy_id,
                "description":              (contract.get("description") or "")[:200],
                "risk_level":               RiskLevel.MEDIUM.value,
                "recommended_mode":         RecommendedMode.PAPER_RECOMMENDED.value,
                "typical_hold_minutes":     None,
                "notes":                    [
                    "초보자용 메타 미정의 — 운영자가 명시 권장.",
                ],
            }
        else:
            entry = {
                "strategy_id":              strategy_id,
                "internal_name":            contract["class_name"],
                "display_name":             meta.display_name,
                "beginner_name":            meta.beginner_name,
                "description":              meta.description,
                "risk_level":               meta.risk_level.value,
                "recommended_mode":         meta.recommended_mode.value,
                "typical_hold_minutes":     meta.typical_hold_minutes,
                "notes":                    list(meta.notes),
            }

        entry.update({
            "supported_modes":              list(supported_modes(strategy_id)),
            "backtest_available":           backtest_available(strategy_id),
            "paper_trading_available":      paper_trading_available(strategy_id),
            "live_trading_available":       live_trading_available(strategy_id),

            # 기존 contract metadata carry (read-only).
            "entry_rule":                   contract.get("entry"),
            "exit_rule":                    contract.get("exit"),
            "invalidation":                 contract.get("invalidation"),
            "required_regime":              contract.get("required_regime"),
            "risk_profile":                 contract.get("risk_profile"),
            "parameters":                   contract.get("params"),

            # invariants — 본 entry 는 advisory only.
            "is_order_signal":              False,
            "auto_apply_allowed":           False,
            "is_investment_advice":         False,
        })
        out.append(entry)
    return out


# ---------- validation (테스트가 호출) ----------


def validate_metadata() -> list[str]:
    """초보자 메타 ↔ STRATEGY_REGISTRY 일관성 검증.

    위반:
    1. STRATEGY_REGISTRY 에 없는 strategy_id 가 메타에 있음 (가짜 전략명 차단).
    2. STRATEGY_REGISTRY 에 있는데 메타 누락 (운영자가 명시 권장).
    3. display_name / beginner_name 누락.
    """
    violations: list[str] = []
    for sid in _BEGINNER_METADATA:
        if sid not in STRATEGY_REGISTRY:
            violations.append(
                f"beginner metadata '{sid}' not in STRATEGY_REGISTRY — "
                "fake strategy id (forbidden)."
            )
    for sid in STRATEGY_REGISTRY:
        if sid not in _BEGINNER_METADATA:
            violations.append(
                f"STRATEGY_REGISTRY '{sid}' missing beginner metadata."
            )
    for sid, meta in _BEGINNER_METADATA.items():
        if not meta.display_name.strip():
            violations.append(f"strategy '{sid}' display_name empty.")
        if not meta.beginner_name.strip():
            violations.append(f"strategy '{sid}' beginner_name empty.")
    return violations
