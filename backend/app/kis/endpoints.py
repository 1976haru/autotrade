"""#71 / 9-02: KIS Paper / KIS Live endpoint·TR·account mode 완전 분리 (정책).

모의 설정이 실전으로 새는 사고를 방지한다. Paper host/TR/account 와 Live host/TR/
account 는 *별도 값* 이며, 어느 한쪽이 다른 쪽의 fallback 이 되어선 안 된다.

핵심 규칙:
1. `KIS_IS_PAPER=true` → Paper path 만 사용 (host=PAPER_HOST, TR=V-prefix).
2. `KIS_IS_PAPER=false` *라도* explicit live gate 없으면 **BLOCKED** (live path 미개방).
3. Paper path 가 Live path fallback 이 되지 않고, 그 반대도 아니다.
4. host/TR/account 라벨은 carry 하되 **secret/계좌번호 원문은 출력하지 않는다**.

호스트/TR 상수는 `app.brokers.kis_client` 의 *단일 진실* 을 재사용한다(테스트로 일치
검증). 본 모듈은 *선택 정책* 만 — 네트워크 호출 / 주문 생성 0건.

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker 주문/취소/route 호출 0건 (place·cancel·route 경로 미사용).
- 외부 HTTP (httpx/requests 직접 호출) 0건, 주문 생성 0건.
- `KisEndpointResolution.broker_order_sent=False` / `order_created=False` /
  `is_live_authorization=False` / `contains_secret=False` 불변.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# 단일 진실 — kis_client 의 host 상수를 재사용 (일치 테스트로 lock).
from app.brokers.kis_client import LIVE_HOST, PAPER_HOST

# TR-ID prefix: 모의(V) vs 실전(T) — kis_client._*_tr_id 와 정합 (테스트로 lock).
PAPER_TR_PREFIX = "V"
LIVE_TR_PREFIX = "T"
# 대표 주문 TR (공개 코드 — secret 아님).
PAPER_ORDER_TR_BUY = "VTTC0802U"
LIVE_ORDER_TR_BUY = "TTTC0802U"

# account mode 라벨 (계좌번호 원문 아님 — 모드 라벨만).
PAPER_ACCOUNT_MODE = "PAPER"
LIVE_ACCOUNT_MODE = "LIVE"


class KisEndpointMode(StrEnum):
    PAPER = "PAPER"
    LIVE = "LIVE"
    BLOCKED = "BLOCKED"   # KIS_IS_PAPER=false + explicit live gate 없음.


# reason_code.
KIS_PAPER_PATH_SELECTED = "KIS_PAPER_PATH_SELECTED"
KIS_LIVE_GATE_REQUIRED = "KIS_LIVE_GATE_REQUIRED"
KIS_LIVE_PATH_SELECTED_GATED = "KIS_LIVE_PATH_SELECTED_GATED"
KIS_PAPER_LIVE_SEPARATED = "KIS_PAPER_LIVE_SEPARATED"

_MESSAGES = {
    KIS_PAPER_PATH_SELECTED:
        "KIS 모의(Paper) 경로를 사용합니다. 실전 host/TR 은 사용하지 않습니다.",
    KIS_LIVE_GATE_REQUIRED:
        "KIS_IS_PAPER=false 이지만 explicit live gate 가 없어 차단되었습니다. "
        "Paper 경로로 fallback 하지 않습니다.",
    KIS_LIVE_PATH_SELECTED_GATED:
        "explicit live gate 통과로 Live 경로가 *선택* 되었으나, 실제 주문은 여전히 "
        "별도 승인 Gate + place_order 가드로 차단됩니다.",
}


@dataclass(frozen=True)
class KisEndpointResolution:
    """KIS endpoint 선택 결과 — secret/계좌 원문 0건."""

    selected_mode:        str            # PAPER / LIVE / BLOCKED
    host:                 str | None     # PAPER_HOST / LIVE_HOST / None(BLOCKED)
    tr_prefix:            str | None     # V / T / None
    order_tr_id:          str | None
    account_mode:         str | None     # PAPER / LIVE / None
    paper_live_separated: bool
    live_gate_required:   bool
    live_gate_passed:     bool
    allowed:              bool           # endpoint 선택 가능 여부 (주문 허가 아님!)
    reason_code:          str
    message_ko:           str

    # 불변 — 본 모듈은 주문을 만들지 않는다.
    broker_order_sent:     bool = False
    order_created:         bool = False
    is_live_authorization: bool = False
    contains_secret:       bool = False

    def __post_init__(self) -> None:
        for name in ("broker_order_sent", "order_created",
                     "is_live_authorization", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (endpoint 정책은 주문 아님)")
        if self.paper_live_separated is not True:
            raise ValueError("paper_live_separated must be True")
        # fallback 금지 불변: PAPER 모드면 절대 LIVE host 아님, LIVE 모드면 절대 PAPER host 아님.
        if self.selected_mode == KisEndpointMode.PAPER.value and self.host == LIVE_HOST:
            raise ValueError("PAPER mode must not use LIVE host (no fallback)")
        if self.selected_mode == KisEndpointMode.LIVE.value and self.host == PAPER_HOST:
            raise ValueError("LIVE mode must not use PAPER host (no fallback)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_mode":        self.selected_mode,
            "host":                 self.host,
            "tr_prefix":            self.tr_prefix,
            "order_tr_id":          self.order_tr_id,
            "account_mode":         self.account_mode,
            "paper_live_separated": True,
            "live_gate_required":   bool(self.live_gate_required),
            "live_gate_passed":     bool(self.live_gate_passed),
            "allowed":              bool(self.allowed),
            "reason_code":          self.reason_code,
            "message_ko":           self.message_ko,
            "broker_order_sent":     False,
            "order_created":         False,
            "is_live_authorization": False,
            "contains_secret":       False,
        }


def resolve_kis_endpoint(
    *,
    kis_is_paper: bool = True,
    explicit_live_gate_passed: bool = False,
) -> KisEndpointResolution:
    """KIS endpoint 선택 — Paper/Live 분리 + KIS_IS_PAPER=false gate.

    - kis_is_paper=True  → PAPER (live gate 무관).
    - kis_is_paper=False + explicit_live_gate_passed=False → **BLOCKED** (fallback 없음).
    - kis_is_paper=False + explicit_live_gate_passed=True  → LIVE *선택* (주문은 별도 차단).
    """
    if kis_is_paper:
        return KisEndpointResolution(
            selected_mode=KisEndpointMode.PAPER.value,
            host=PAPER_HOST, tr_prefix=PAPER_TR_PREFIX, order_tr_id=PAPER_ORDER_TR_BUY,
            account_mode=PAPER_ACCOUNT_MODE, paper_live_separated=True,
            live_gate_required=False, live_gate_passed=False, allowed=True,
            reason_code=KIS_PAPER_PATH_SELECTED,
            message_ko=_MESSAGES[KIS_PAPER_PATH_SELECTED],
        )
    if not explicit_live_gate_passed:
        # KIS_IS_PAPER=false 만으로는 live path 를 열지 않는다 — fallback 금지, BLOCKED.
        return KisEndpointResolution(
            selected_mode=KisEndpointMode.BLOCKED.value,
            host=None, tr_prefix=None, order_tr_id=None, account_mode=None,
            paper_live_separated=True, live_gate_required=True, live_gate_passed=False,
            allowed=False, reason_code=KIS_LIVE_GATE_REQUIRED,
            message_ko=_MESSAGES[KIS_LIVE_GATE_REQUIRED],
        )
    # explicit live gate 통과 — Live 경로 *선택* (실제 주문은 place_order 가드 + 승인 Gate).
    return KisEndpointResolution(
        selected_mode=KisEndpointMode.LIVE.value,
        host=LIVE_HOST, tr_prefix=LIVE_TR_PREFIX, order_tr_id=LIVE_ORDER_TR_BUY,
        account_mode=LIVE_ACCOUNT_MODE, paper_live_separated=True,
        live_gate_required=True, live_gate_passed=True, allowed=True,
        reason_code=KIS_LIVE_PATH_SELECTED_GATED,
        message_ko=_MESSAGES[KIS_LIVE_PATH_SELECTED_GATED],
    )


__all__ = [
    "PAPER_HOST", "LIVE_HOST", "PAPER_TR_PREFIX", "LIVE_TR_PREFIX",
    "PAPER_ACCOUNT_MODE", "LIVE_ACCOUNT_MODE",
    "KisEndpointMode", "KisEndpointResolution", "resolve_kis_endpoint",
    "KIS_PAPER_PATH_SELECTED", "KIS_LIVE_GATE_REQUIRED",
    "KIS_LIVE_PATH_SELECTED_GATED", "KIS_PAPER_LIVE_SEPARATED",
]
