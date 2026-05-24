"""INTRADAY-DATA-02 — KIS 분봉 read-only collector (안전 placeholder, 구조 전용).

KIS 의 분봉(historical minute) 시세 endpoint / TR ID 는 **공식 문서로 확인되기 전까지**
production 코드에 확정값처럼 하드코딩하지 않는다. 따라서 본 collector 는 기본적으로
`NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION` 상태를 반환하며 **실제 네트워크 요청을 보내지
않는다.**

절대 원칙 (본 모듈):
- broker / OrderExecutor / route_order / KIS 주문 endpoint import·호출 0건.
- 주문류(POST place/cancel) 호출 0건.
- httpx / requests import 0건 — *구조적으로* 호출이 불가능하게 둔다.
- 공식 확인된 read-only quotation endpoint 가 명시적으로 설정되기 전에는 실제 요청 0건.
- 응답/상태에 secret / app_key / app_secret / account 원문 0건 (present 여부 bool 만).

환경변수 (기본 안전값):
- KIS_INTRADAY_ENABLED   = false   (활성화하지 않음)
- KIS_INTRADAY_ENDPOINT  = ""      (공식 확인 전 비움)
- KIS_INTRADAY_TR_ID     = ""      (공식 확인 전 비움)
- KIS_INTRADAY_READ_ONLY = true    (read-only 강제 — false 면 BLOCKED)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 상태 상수.
NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION = "NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION"
DISABLED = "DISABLED"
BLOCKED_NOT_READ_ONLY = "BLOCKED_NOT_READ_ONLY"
READY_READ_ONLY = "READY_READ_ONLY"  # 구조상 가능 — 단, 실제 fetch 는 본 PR 미구현


@dataclass(frozen=True)
class KisIntradayCollectorStatus:
    status: str
    enabled: bool
    read_only: bool
    endpoint_present: bool       # 원문 아님 — 설정 여부만
    tr_id_present: bool          # 원문 아님 — 설정 여부만
    can_collect: bool            # True 는 READY_READ_ONLY 일 때만 (그래도 fetch 미구현)
    reasons: tuple[str, ...] = ()
    # invariants (항상 안전값).
    is_order_endpoint: bool = False
    contains_secret: bool = False

    def __post_init__(self) -> None:
        if self.is_order_endpoint:
            raise ValueError("KIS intraday collector must never be an order endpoint")
        if self.contains_secret:
            raise ValueError("contains_secret must be False (present-only bools)")
        if self.can_collect and self.status != READY_READ_ONLY:
            raise ValueError("can_collect True only when READY_READ_ONLY")


def _as_bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def evaluate_kis_intraday_collector(env: dict[str, Any] | None = None) -> KisIntradayCollectorStatus:
    """KIS_INTRADAY_* 환경값으로 collector 상태를 평가 (실제 호출 없음).

    공식 endpoint/TR ID 미확인(빈 값) → NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION (기본).
    """
    env = env or {}
    enabled = _as_bool(env.get("KIS_INTRADAY_ENABLED"), False)
    read_only = _as_bool(env.get("KIS_INTRADAY_READ_ONLY"), True)
    endpoint = str(env.get("KIS_INTRADAY_ENDPOINT", "") or "").strip()
    tr_id = str(env.get("KIS_INTRADAY_TR_ID", "") or "").strip()
    endpoint_present = bool(endpoint)
    tr_id_present = bool(tr_id)

    reasons: list[str] = []
    # 1) 공식 endpoint/TR ID 미확인 → 최우선 차단(기본 경로).
    if not (endpoint_present and tr_id_present):
        reasons.append(
            "공식 KIS 분봉 quotation endpoint / TR ID 가 확인되지 않았습니다. "
            "확정 전까지 실제 호출하지 않습니다 (NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION).")
        return KisIntradayCollectorStatus(
            NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION, enabled, read_only,
            endpoint_present, tr_id_present, can_collect=False, reasons=tuple(reasons))
    # 2) read-only 강제 — false 면 차단.
    if not read_only:
        reasons.append("KIS_INTRADAY_READ_ONLY=false — read-only 분봉 수집만 허용, 차단.")
        return KisIntradayCollectorStatus(
            BLOCKED_NOT_READ_ONLY, enabled, read_only,
            endpoint_present, tr_id_present, can_collect=False, reasons=tuple(reasons))
    # 3) 비활성.
    if not enabled:
        reasons.append("KIS_INTRADAY_ENABLED=false — 비활성(기본).")
        return KisIntradayCollectorStatus(
            DISABLED, enabled, read_only, endpoint_present, tr_id_present,
            can_collect=False, reasons=tuple(reasons))
    # 4) endpoint+tr_id 확인 + read_only + enabled → 구조상 가능(단, fetch 미구현).
    reasons.append(
        "공식 endpoint/TR ID + read-only + enabled — 구조상 수집 가능하나, 실제 fetch 는 "
        "운영자 검토 후 별도 PR 에서 구현 (본 PR 은 placeholder).")
    return KisIntradayCollectorStatus(
        READY_READ_ONLY, enabled, read_only, endpoint_present, tr_id_present,
        can_collect=True, reasons=tuple(reasons))


@dataclass(frozen=True)
class KisIntradayCollectResult:
    status: str
    bars: tuple[Any, ...]
    requested: bool          # 실제 네트워크 요청을 보냈는지 — 본 PR 에서 항상 False
    reason: str
    contains_secret: bool = False

    def __post_init__(self) -> None:
        if self.requested:
            raise ValueError("KIS intraday collector must not send a request in this PR")
        if self.contains_secret:
            raise ValueError("contains_secret must be False")


def collect_intraday_via_kis(
    symbol: str, *, bar_size: str = "5m", env: dict[str, Any] | None = None,
) -> KisIntradayCollectResult:
    """KIS 분봉 수집 시도 — **본 PR 에서 어떤 경우에도 실제 요청을 보내지 않는다.**

    공식 endpoint 미확인이면 NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION 으로 즉시 반환. 설정이
    완비(READY_READ_ONLY)되어도 실제 fetch 는 미구현이라 빈 결과 + 안내를 반환한다.
    httpx / requests 를 import 하지 않으므로 구조적으로 호출이 불가능하다.
    """
    st = evaluate_kis_intraday_collector(env)
    if st.status != READY_READ_ONLY:
        return KisIntradayCollectResult(
            st.status, (), requested=False,
            reason="; ".join(st.reasons) or "수집 불가")
    return KisIntradayCollectResult(
        READY_READ_ONLY, (), requested=False,
        reason="공식 endpoint 확인됨 — 실제 read-only fetch 는 운영자 검토 후 별도 PR 구현. "
               "본 PR 은 호출하지 않음.")


def to_dict(s: KisIntradayCollectorStatus) -> dict[str, Any]:
    return {
        "status": s.status, "enabled": s.enabled, "read_only": s.read_only,
        "endpoint_present": s.endpoint_present, "tr_id_present": s.tr_id_present,
        "can_collect": s.can_collect, "reasons": list(s.reasons),
        "is_order_endpoint": s.is_order_endpoint, "contains_secret": s.contains_secret,
    }
