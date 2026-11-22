"""Kill Switch 단계별 *주문 정책* — KIS-PAPER-P0-SAFETY-FIX-V1 (2026-10-24).

배경 (P0): LEVEL_1/2/3 이 모두 `RiskManager.emergency_stop=True` 하나로 매핑되어, 안내
("LEVEL_1 = 신규 매수만 차단, 청산 허용")와 달리 보유 청산 매도·장마감 강제청산·미체결
매수 취소까지 전부 막혔다. 본 모듈이 단계별 허용 범위의 *단일 진실* 이다.

정책 (policy_version 2):
  OFF      — 기존 주문 정책.
  LEVEL_1  — 신규 BUY 전면 차단. 보유 이내 reduce-only SELL(정상 청산·EOD 강제청산)과
             미체결 BUY 취소는 허용 — PAPER/SIMULATION 에서만.
  LEVEL_2/3 — 기존 강한 정지: 자동 주문·취소 전부 중단, 운영자가 KIS MTS/HTS 에서 직접 조치.

호환 (자동 완화 금지):
  policy_version < 2 로 기록된 정지(이전 정책에서 켜진 LEVEL_1 포함), level 미상 boolean 정지
  (`set_emergency_stop(True)`, 연속 거부 자동정지)는 *legacy strict* — 모든 주문 차단 유지.
  운영자가 새 정책으로 LEVEL_1 을 다시 설정해야 완화된다.

본 모듈은 DB / broker / route_order 를 import 하지 않는다 (순수 판정).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

KILL_SWITCH_POLICY_VERSION = 2

# check_order 가 "emergency stop" 키워드로 BLOCKED 매핑 — 모든 사유가 이 접두를 포함한다.
STRICT_REASON = "emergency stop is enabled"

_EXIT_MODES = {"PAPER", "SIMULATION"}


@dataclass(frozen=True)
class EmergencyOrderPolicy:
    active:                  bool
    level:                   str    # OFF / LEVEL_1 / LEVEL_2 / LEVEL_3 / UNKNOWN
    legacy_strict:           bool   # 이전 정책·level 미상 정지 → 모든 주문 차단 유지
    blocks_new_buy:          bool
    allows_reduce_only_sell: bool
    allows_buy_cancel:       bool
    allows_eod_forced_exit:  bool
    operator_action_ko:      str

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active, "level": self.level, "legacy_strict": self.legacy_strict,
            "blocks_new_buy": self.blocks_new_buy,
            "allows_reduce_only_sell": self.allows_reduce_only_sell,
            "allows_buy_cancel": self.allows_buy_cancel,
            "allows_eod_forced_exit": self.allows_eod_forced_exit,
            "operator_action_ko": self.operator_action_ko,
            "policy_version": KILL_SWITCH_POLICY_VERSION,
        }


def _level_str(risk: Any) -> str:
    raw = getattr(risk, "kill_switch_level", None)
    raw = getattr(raw, "value", raw)
    return str(raw) if raw else ""


def emergency_order_policy(risk: Any) -> EmergencyOrderPolicy:
    if not bool(getattr(risk, "emergency_stop", False)):
        return EmergencyOrderPolicy(
            active=False, level="OFF", legacy_strict=False, blocks_new_buy=False,
            allows_reduce_only_sell=True, allows_buy_cancel=True,
            allows_eod_forced_exit=True, operator_action_ko="")
    level = _level_str(risk)
    version = int(getattr(risk, "kill_switch_policy_version", 0) or 0)
    if level == "LEVEL_1" and version >= KILL_SWITCH_POLICY_VERSION:
        return EmergencyOrderPolicy(
            active=True, level=level, legacy_strict=False, blocks_new_buy=True,
            allows_reduce_only_sell=True, allows_buy_cancel=True, allows_eod_forced_exit=True,
            operator_action_ko="신규 매수만 차단 — 보유 청산·장마감 강제청산·미체결 매수 취소는 계속됩니다.")
    strict_legacy = level not in ("LEVEL_2", "LEVEL_3")
    return EmergencyOrderPolicy(
        active=True, level=level or "UNKNOWN", legacy_strict=strict_legacy,
        blocks_new_buy=True, allows_reduce_only_sell=False, allows_buy_cancel=False,
        allows_eod_forced_exit=False,
        operator_action_ko=(
            "모든 자동 주문·취소가 중단됐습니다. 남은 보유·미체결은 KIS 모의투자 MTS/HTS 에서 "
            "직접 정리하세요."
            + (" (이전 정책으로 켜진 긴급정지 — 청산을 허용하려면 LEVEL 1 을 다시 설정하세요.)"
               if strict_legacy else "")))


def exits_allowed(risk: Any) -> bool:
    """정상 청산·EOD 강제청산·미체결 BUY 취소 가능 여부 (정지 OFF 이거나 새 정책 LEVEL_1)."""
    return emergency_order_policy(risk).allows_eod_forced_exit


def _held(positions: Iterable[Any] | None, symbol: str) -> tuple[int, int | None]:
    for p in positions or ():
        if str(getattr(p, "symbol", "")) == str(symbol):
            qty = int(getattr(p, "quantity", 0) or 0)
            sellable = getattr(p, "sellable_quantity", None)
            return qty, (int(sellable) if sellable is not None else None)
    return 0, None


def emergency_block_reason(risk: Any, *, side: str, symbol: str, quantity: int,
                           positions: Iterable[Any] | None, mode: str) -> str | None:
    """정지 중 주문 차단 사유 (None = 정지 정책상 통과 — 이후 일반 RiskManager 평가 계속).

    LEVEL_1 SELL 은 *브로커가 확인한* 보유 이내만 허용: 보유 0 / 조회 실패(빈 목록) /
    주문가능수량 초과 / 실계좌 모드는 거부 — 숏 생성·과매도 0.
    """
    pol = emergency_order_policy(risk)
    if not pol.active:
        return None
    if not pol.allows_reduce_only_sell:
        return STRICT_REASON
    tag = f"{STRICT_REASON} (LEVEL_1)"
    if str(side).upper() != "SELL":
        return f"{tag}: new BUY blocked"
    if str(getattr(mode, "value", mode)).upper() not in _EXIT_MODES:
        return f"{tag}: exits allowed only in PAPER/SIMULATION (mode={getattr(mode, 'value', mode)})"
    held, sellable = _held(positions, symbol)
    cap = held if sellable is None else min(held, sellable)
    if quantity <= 0 or cap <= 0 or quantity > cap:
        return (f"{tag}: SELL only within broker-confirmed holding "
                f"(held={held}, sellable={sellable}, requested={quantity})")
    return None
