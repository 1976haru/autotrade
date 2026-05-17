"""#47: Futures broker formal contract.

선물 브로커의 *공식* 인터페이스를 한 곳에 정의한다. 본 모듈은 주식
[`app.brokers.base.BrokerAdapter`]와 **별개의 ABC**이며, 어떤 형태로도
주식 BrokerAdapter를 상속하거나 호환 사용하지 않는다 — 선물은 계약 수,
증거금, 만기, 롤오버, 틱가치, 레버리지, 청산위험이 함수 시그니처에 반영
되어야 하기 때문.

## 모듈 분리 원칙

| 모듈 | 역할 |
|---|---|
| `app.brokers.base` | 주식 BrokerAdapter (#34, #40 단일 진입점) |
| `app.brokers.futures_base` | **본 모듈 — 선물 BrokerAdapter 공식 contract** |
| `app.futures.base` | legacy 최소 ABC ([`MockFuturesBroker`]가 직접 implements) |
| `app.futures.types` | legacy Pydantic 모델 (Quote/Position/Balance/OrderRequest/OrderResult) |
| `app.futures.risk` | `FuturesRiskManager` — 주식 RiskManager와 분리 |

본 모듈은 legacy 모델/ABC를 *re-export*하면서 추가로:

- `FuturesContractSpec` — 만기 / 멀티플라이어 / 틱사이즈 / 틱가치 / 레버리지 한도 / 통화 / 거래시간
- `FuturesOrder` — `FuturesOrderRequest`에 audit 필드(strategy / signal_* / ai_decision_meta / trade_reason / client_order_id) 보강
- `FuturesMarginSnapshot` — `FuturesBalance`의 모든 필드 + `maintenance_margin_required`
- 만기/롤오버 helper: `days_to_expiry`, `is_contract_expiring_soon`, `should_rollover`

를 정의해 향후 LIVE 어댑터(KIS / Kiwoom / 해외선물)가 본 contract 위에서
구현되도록 한다 — 본 PR은 LIVE 어댑터를 추가하지 *않는다*.

## 절대 invariant (테스트로 강제)

1. 본 모듈은 `app.brokers.kis` / `app.brokers.kis_client` / `app.brokers.mock_broker`
   어떤 것도 import하지 않는다 — 선물 어댑터가 주식 broker를 우회 호출하는
   경로 0건.
2. `FuturesBrokerAdapter`는 `BrokerAdapter`(주식)를 *상속하지 않는다*.
   MRO에 `app.brokers.base.BrokerAdapter`가 등장하지 않는다.
3. 본 모듈은 helper만 제공할 뿐 어떤 *주문 실행* 코드도 포함하지 않는다 —
   broker.place_order 호출 0건.
4. 자동 롤오버 금지: `should_rollover()`는 advisory bool만 반환 — 실제
   주문은 호출자가 결정하며, 본 모듈은 broker 호출을 트리거하지 않는다.

## 관련 문서

- [`docs/futures_broker_contract.md`](../../../docs/futures_broker_contract.md) — 본 contract의 정책 문서 (#47)
- [`docs/futures_scope.md`](../../../docs/futures_scope.md) — 선물 1차 범위 (#46)
- [`docs/futures_simulation_report.md`](../../../docs/futures_simulation_report.md) — 가상 산식 (#151)
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Sequence

from pydantic import BaseModel, Field

# legacy ABC + Pydantic 모델 re-export — backwards compat (MockFuturesBroker 등).
# 본 모듈을 import하는 신규 코드(향후 LIVE 어댑터)는 본 alias만 보면 충분하다.
from app.futures.base import FuturesBrokerAdapter as _LegacyFuturesBrokerAdapter
from app.futures.types import (
    FuturesBalance,
    FuturesOrderRequest,
    FuturesOrderResult,
    FuturesOrderStatus,
    FuturesOrderType,
    FuturesPosition,
    FuturesPositionSide,
    FuturesQuote,
    FuturesSide,
)


# ====================================================================
# Canonical ABC — 이름 그대로 re-export
# ====================================================================
#
# `app.futures.base.FuturesBrokerAdapter`가 이미 ABC로 정의되어 있고
# `MockFuturesBroker`가 implements 하므로, 본 모듈은 그 클래스를 그대로
# re-export 한다 (별도 ABC를 새로 만들면 MRO가 분기되어 backwards compat
# 깨짐). 향후 LIVE 어댑터는 본 alias를 import해 implements 하면 된다.

FuturesBrokerAdapter = _LegacyFuturesBrokerAdapter
"""선물 브로커 공식 ABC. 주식 BrokerAdapter와 무관 — 별개 계층.

LIVE 어댑터가 추가되면 본 ABC의 6개 메서드를 implements 한다:
- `get_quote(contract_code)` → `FuturesQuote`
- `get_balance()` → `FuturesBalance` (또는 `FuturesMarginSnapshot` 호환)
- `get_positions()` → `list[FuturesPosition]`
- `place_order(order)` → `FuturesOrderResult` (호출 *전* `FuturesRiskManager` 통과 필수)
- `cancel_order(order_id)` → `FuturesOrderResult`
- `get_order_status(order_id)` → `FuturesOrderResult`

본 PR 시점에 implements 한 클래스: `app.futures.mock.MockFuturesBroker` 단 하나.
"""


# ====================================================================
# Richer types (#47 — 본 PR 신규)
# ====================================================================


class FuturesContractSpec(BaseModel):
    """선물 계약 스펙. 거래소 공시 + broker API의 contract 메타데이터를 한
    객체에 모은다. LIVE 어댑터가 `get_contract_spec(code)` 같은 메서드로
    반환하거나, 운영자가 정적 매핑으로 주입한다.

    필드 예시 (KOSPI200 March 2025):
        code="KOSPI200_2503", underlying="KOSPI200",
        expiry=datetime(2025, 3, 13, 15, 45, tzinfo=KST),
        multiplier=250_000, tick_size=Decimal("0.05"),
        tick_value_krw=12_500, leverage_max=10.0, currency="KRW",
        market_open_kst=time(9, 0), market_close_kst=time(15, 45)

    NOTE: 본 PR에서 본 모델은 *contract 인터페이스만* 정의 — 운영자가 실제
    값으로 주입하는 것은 별도 PR (LIVE 어댑터 또는 정적 매핑 테이블).
    """

    code:        str
    underlying:  str
    expiry:      datetime

    # 멀티플라이어: contract 1개의 명목금액 = mark_price * multiplier
    multiplier:  int = Field(gt=0)

    # 틱사이즈 / 틱가치 — 호가 단위. 정수 KRW 기반 시스템이라 tick_value도 정수.
    # 예: KOSPI200 0.05pt × 250,000원 multiplier = tick_value_krw 12,500.
    tick_size_pt:    float = Field(default=0.05, gt=0)
    tick_value_krw:  int   = Field(default=12_500, gt=0)

    # 거래소/브로커 허용 최대 레버리지 (FuturesRiskPolicy.max_leverage와 별개:
    # 본 필드는 *시장* 한도, policy는 *운영* 한도 — 작은 값이 효력).
    leverage_max:    float = Field(default=10.0, gt=0)

    # 결제 통화. 해외선물(USD 등) 도입 시 환율 처리 필요.
    currency:        str   = "KRW"

    # 정규 거래시간 (KST). 야간 / 글로벌 시간대는 별도 spec으로 처리.
    # `enforce_market_hours` 가드와 호환되도록 time 객체.
    market_open_kst:  time = time(9, 0)
    market_close_kst: time = time(15, 45)

    # 자유 메타데이터 — 거래소별 추가 필드 (산출 한도, SQ 정보 등).
    extra: dict | None = None


class FuturesOrder(FuturesOrderRequest):
    """선물 주문 모델 (#47).

    legacy `FuturesOrderRequest`(`contract`, `side`, `quantity`, `order_type`,
    `limit_price`)를 그대로 포함하고, audit / 단일 진입점 / AI 흐름에 필요한
    필드를 추가한다 — 주식 `OrderRequest`(#138, #139, #140, #152)의 audit 필드
    구성과 일관되도록.

    **`quantity`는 *계약 수*** (contract count) — 주식의 *주식 수*와 의미가
    다르다. 명목금액은 `quantity * mark_price * multiplier`로 별도 산출.
    """

    # 140-equivalent: idempotency 키. 호출자가 보낸 그대로 audit row로 carry.
    client_order_id: str | None = None
    # 134-equivalent: 진입/청산 사유 자유 문자열.
    trade_reason:    str | None = None
    # 138-equivalent: 주문을 만든 전략 이름 (또는 'ai_assist').
    strategy:        str | None = None
    # 139-equivalent: 신호 quality (0-100).
    signal_strength:   int | None = Field(default=None, ge=0, le=100)
    signal_confidence: int | None = Field(default=None, ge=0, le=100)
    # 152-equivalent: AI decision metadata (선물 AI 흐름 도입 시).
    ai_decision_meta: dict | None = None


class FuturesMarginSnapshot(BaseModel):
    """선물 증거금 현황 스냅샷.

    `FuturesBalance`의 모든 필드 + maintenance margin / margin call 임계 정보.
    LIVE broker API가 이 형태로 응답하지 않을 수 있어, 어댑터가 자체 산출 후
    반환하는 형식으로 통일.
    """

    cash:                          int
    margin_used:                   int
    margin_available:              int
    equity:                        int
    # maintenance margin: 청산 직전 buffer. notional × maintenance_margin_pct.
    maintenance_margin_required:   int = 0
    # margin call 발생 여부 (advisory — broker마다 정의 상이).
    margin_call:                   bool = False
    currency:                      str  = "KRW"

    @classmethod
    def from_balance(
        cls, balance: FuturesBalance,
        *,
        maintenance_margin_required: int = 0,
        margin_call: bool = False,
    ) -> "FuturesMarginSnapshot":
        """legacy `FuturesBalance` → `FuturesMarginSnapshot` 변환 helper."""
        return cls(
            cash=balance.cash,
            margin_used=balance.margin_used,
            margin_available=balance.margin_available,
            equity=balance.equity,
            maintenance_margin_required=maintenance_margin_required,
            margin_call=margin_call,
            currency=balance.currency,
        )


# ====================================================================
# Expiry / rollover helpers (#47)
# ====================================================================
#
# 본 helper들은 *advisory* — broker 호출 / 자동 주문 실행을 트리거하지 않는다.
# 운영자(또는 향후 strategy)가 본 함수의 boolean / int 결과를 보고 *수동* 결정.
# 자동 롤오버는 `docs/futures_broker_contract.md` §7에 따라 금지.

_KST = timezone(timedelta(hours=9))


def _to_aware(dt: datetime) -> datetime:
    """naive datetime은 KST로 가정 — 거래소 시간대와 일치시키기 위함."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_KST)
    return dt


def days_to_expiry(expiry: datetime, *, now: datetime | None = None) -> int:
    """만기까지 남은 *영업일 수* 근사 — 단순 calendar day 계산.

    실제 영업일 캘린더(주말/휴장일/SQ 처리)는 후속 PR에서 별도 데이터 소스로
    제공해야 한다 (`docs/futures_scope.md` §6, `docs/futures_broker_contract.md`
    §5). 본 helper는 *근사*이며, expiry 임박 경고 용도로만 쓴다.

    반환:
    - expiry > now → 양수 (남은 일수, 정수 floor)
    - expiry == now → 0
    - expiry < now (이미 만료) → 음수 (caller가 만료 분기 식별 가능)
    """
    expiry = _to_aware(expiry)
    cur    = now or datetime.now(_KST)
    cur    = _to_aware(cur)
    delta  = expiry - cur
    # ceil로 가까운 만기 +1 day로 잡는다. 음수는 그대로 floor.
    seconds = delta.total_seconds()
    if seconds == 0:
        return 0
    if seconds > 0:
        return max(0, int(seconds // 86400))
    return -int((-seconds) // 86400) - (1 if (-seconds) % 86400 else 0)


def is_contract_expiring_soon(
    spec: FuturesContractSpec,
    *,
    now: datetime | None = None,
    threshold_days: int = 5,
) -> bool:
    """만기 임박 (≤ threshold_days)이면 True. 신규 진입 차단 advisory 시그널.

    `threshold_days` 기본 5일 — KOSPI200 SQ 주(만기일 포함 한 주)는 변동성이
    크므로 신규 진입을 회피하는 정책. 운영자가 contract spec별로 override 가능.
    """
    if threshold_days < 0:
        raise ValueError(f"threshold_days must be >= 0, got {threshold_days}")
    return days_to_expiry(spec.expiry, now=now) <= threshold_days


def should_rollover(
    spec: FuturesContractSpec,
    *,
    now: datetime | None = None,
    threshold_days: int = 5,
) -> bool:
    """근월물에서 차월물로 *롤오버해야 하는가* (advisory only).

    True를 반환해도 어댑터는 아무 주문도 실행하지 않는다 — 운영자가
    `FuturesRiskManager` + `PermissionGate`를 거쳐 명시적으로 close + open
    페어를 보내야 한다 (자동 롤오버 금지, `docs/futures_broker_contract.md` §7).

    임계는 `is_contract_expiring_soon`과 동일 default를 공유 — 만기 임박 =
    롤오버 시그널.
    """
    return is_contract_expiring_soon(spec, now=now, threshold_days=threshold_days)


# ====================================================================
# Public re-exports
# ====================================================================
#
# 신규 코드(향후 LIVE 어댑터 등)는 본 모듈만 import하면 충분하도록 enum / 모델
# 모두 재노출. 직접 `app.futures.types` import도 허용 — 양쪽 호환.

__all__: Sequence[str] = (
    # ABC
    "FuturesBrokerAdapter",
    # legacy types (re-exported)
    "FuturesBalance",
    "FuturesOrderRequest",
    "FuturesOrderResult",
    "FuturesOrderStatus",
    "FuturesOrderType",
    "FuturesPosition",
    "FuturesPositionSide",
    "FuturesQuote",
    "FuturesSide",
    # richer #47 types
    "FuturesContractSpec",
    "FuturesOrder",
    "FuturesMarginSnapshot",
    # helpers
    "days_to_expiry",
    "is_contract_expiring_soon",
    "should_rollover",
)
