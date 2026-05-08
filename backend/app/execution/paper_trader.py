"""Paper Trader — Paper 모드 broker 선택 + live 차단 helper (#42).

Paper 모드는 실제 시세 + 모의투자 주문으로 전략 오류를 실시간 환경에서 확인
하는 단계다. 본 모듈은 *기존 route_order 흐름을 대체하지 않는다*. 다음 역할
만 담당:

1. **Broker 선택**: `PaperBrokerKind`(MOCK / KIS_PAPER)에 따라 broker 인스턴스
   를 생성. `Settings.paper_broker_kind`(env `PAPER_BROKER_KIND`)로 운영자가
   선택.
2. **Live 차단**: 어떤 broker가 paper 환경에 적합한지 검증
   (`assert_paper_broker(broker)`). KisBrokerAdapter는 `is_paper=True`만 허용
   — `False`면 `NotPaperBrokerError`.
3. **status 표시**: 현재 paper mode / broker / 안전 flag 한 곳에서 surface
   (`build_paper_status`).
4. **표준 흐름 강제 wrapper**: `place_paper_order(order, audit, ...)`은
   *반드시 audit row*를 인자로 받고 OrderExecutor를 호출 — RiskManager 우회
   진입점을 만들지 않는다. audit.decision ∉ APPROVED면 OrderExecutor가
   UnauthorizedOrderError로 즉시 차단 (#34 backstop 그대로).

표준 흐름 (변경 없음):
- Strategy / AI / Manual → route_order
- → OrderGuard.check (#38)
- → RiskManager.check_order (#34)
- → PermissionGate.submit (NEEDS_APPROVAL)
- → OrderExecutor.execute (#40)
- → PaperTrader.assert_paper_broker(broker)  ◄── 검증
- → BrokerAdapter (MockBroker or KIS Paper)에 위임

PaperTrader는 결코 BrokerAdapter의 place_order를 직접 호출하지 않는다 —
OrderExecutor 통해서만 실행. broker가 KIS live (is_paper=False)이면
assert_paper_broker가 거부.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult
from app.brokers.kis import KisBrokerAdapter
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog
from app.execution.executor import OrderExecutor


class PaperBrokerKind(StrEnum):
    """Paper 모드에서 사용할 broker 종류."""
    MOCK      = "MOCK"        # MockBrokerAdapter — 즉시 가상 체결
    KIS_PAPER = "KIS_PAPER"   # KisBrokerAdapter (is_paper=True) — KIS 모의투자


class NotPaperBrokerError(RuntimeError):
    """`assert_paper_broker`가 paper-safe가 아닌 broker를 거부할 때 raise.

    *defense in depth*. `KisBrokerAdapter.place_order`도 `is_paper=False`면
    `NotImplementedError`이지만, 본 검사로 인스턴스 단계에서 사전 차단.
    """


# ---------- broker 분류 ----------


def is_live_broker(broker: BrokerAdapter) -> bool:
    """broker가 live 실거래로 향할 가능성이 있는지.

    - KisBrokerAdapter: `is_paper=False`이면 live.
    - MockBrokerAdapter: 항상 paper-safe.
    - 그 외 어댑터: 보수적으로 True (모르는 broker는 live로 간주).
    """
    if isinstance(broker, MockBrokerAdapter):
        return False
    if isinstance(broker, KisBrokerAdapter):
        return not bool(getattr(broker, "is_paper", False))
    return True  # 모르는 broker는 보수적으로 live 취급


def is_paper_broker(broker: BrokerAdapter) -> bool:
    return not is_live_broker(broker)


def assert_paper_broker(broker: BrokerAdapter) -> None:
    """broker가 paper-safe면 통과, 아니면 `NotPaperBrokerError` raise.

    Paper 모드에서 *어떤 코드*도 live broker를 호출하지 않도록 강제하는
    runtime 가드. 운영자가 `PaperTrader.execute`나 `place_paper_order`로
    실수로 LIVE broker 인스턴스를 넘기면 즉시 차단.
    """
    if is_live_broker(broker):
        raise NotPaperBrokerError(
            f"PaperTrader refuses non-paper broker: {type(broker).__name__} "
            f"(is_paper=False or unknown adapter). Set KIS_IS_PAPER=true or "
            f"use MockBrokerAdapter."
        )


# ---------- broker selection ----------


def make_paper_broker(kind: PaperBrokerKind) -> BrokerAdapter:
    """선택된 broker_kind로 paper-safe broker 인스턴스 생성.

    `KIS_PAPER`는 settings.kis_is_paper=True를 강제. False면 RuntimeError.
    `MOCK`는 항상 안전.

    호출자는 별도로 `assert_paper_broker(broker)`를 한 번 더 호출해 인스턴스
    단계 검증 가능 (defense in depth).
    """
    from app.core.config import get_settings

    settings = get_settings()
    if kind == PaperBrokerKind.MOCK:
        return MockBrokerAdapter()
    if kind == PaperBrokerKind.KIS_PAPER:
        if not settings.kis_is_paper:
            raise RuntimeError(
                "PAPER_BROKER_KIND=KIS_PAPER requires KIS_IS_PAPER=true. "
                "현재 .env가 KIS_IS_PAPER=false라 paper broker를 만들 수 없습니다."
            )
        return KisBrokerAdapter()
    raise ValueError(f"unknown PaperBrokerKind: {kind}")


# ---------- status ----------


@dataclass(frozen=True)
class PaperStatus:
    """현재 Paper 모드 상태 + 안전 flag 스냅샷.

    UI / API가 본 객체를 surface — 운영자가 "지금 paper인지 / 어느 broker
    인지 / live 차단이 켜졌는지" 한눈에 본다.
    """
    mode:                  str
    is_paper_mode:         bool       # mode in {SIMULATION, PAPER}
    paper_broker_kind:     str        # MOCK / KIS_PAPER
    kis_is_paper:          bool
    enable_live_trading:   bool       # False면 LIVE 차단
    enable_ai_execution:   bool
    enable_futures_live_trading: bool
    fill_polling_enabled:  bool

    def to_dict(self) -> dict:
        return {
            "mode":                        self.mode,
            "is_paper_mode":               self.is_paper_mode,
            "paper_broker_kind":           self.paper_broker_kind,
            "kis_is_paper":                self.kis_is_paper,
            "enable_live_trading":         self.enable_live_trading,
            "enable_ai_execution":         self.enable_ai_execution,
            "enable_futures_live_trading": self.enable_futures_live_trading,
            "fill_polling_enabled":        self.fill_polling_enabled,
        }


def build_paper_status() -> PaperStatus:
    """`Settings`로부터 현재 paper 상태 스냅샷 생성. read-only."""
    from app.core.config import get_settings

    s = get_settings()
    paper_modes = {OperationMode.SIMULATION.value, OperationMode.PAPER.value}
    paper_kind = (
        getattr(s, "paper_broker_kind", None)
        or _default_paper_broker_kind(s).value
    )
    return PaperStatus(
        mode=s.default_mode,
        is_paper_mode=s.default_mode in paper_modes,
        paper_broker_kind=paper_kind,
        kis_is_paper=bool(s.kis_is_paper),
        enable_live_trading=bool(s.enable_live_trading),
        enable_ai_execution=bool(s.enable_ai_execution),
        enable_futures_live_trading=bool(s.enable_futures_live_trading),
        fill_polling_enabled=bool(s.enable_fill_polling),
    )


def _default_paper_broker_kind(settings) -> PaperBrokerKind:
    """`paper_broker_kind` 미설정 시 default 추론.

    DEFAULT_MODE=PAPER + KIS_IS_PAPER=true → KIS_PAPER. 그 외는 MOCK.
    """
    if (settings.default_mode == OperationMode.PAPER.value
            and bool(settings.kis_is_paper)):
        return PaperBrokerKind.KIS_PAPER
    return PaperBrokerKind.MOCK


# ---------- safe wrapper for OrderExecutor ----------


class PaperTrader:
    """OrderExecutor를 paper-safe하게 감싸는 wrapper.

    역할: broker가 paper-safe인지 인스턴스 단계 검증 → OrderExecutor.execute에
    위임. RiskManager / route_order 흐름을 *대체하지 않는다*. audit row가
    이미 RiskManager 통과 결정을 carry하고 있어야 broker로 진행.

    호출 흐름 (정상 사용):
        # 일반적으로 route_order이 OrderExecutor를 직접 호출. PaperTrader는
        # paper test API 또는 명시 paper-only orchestration 흐름에서 사용.
        trader = PaperTrader(broker, db)
        result = await trader.execute(order, audit)

    `audit.decision`이 APPROVED/NEEDS_APPROVAL이 아니면 OrderExecutor가
    UnauthorizedOrderError로 즉시 차단 (#34 backstop). broker가 live이면
    인스턴스 단계에서 `NotPaperBrokerError`로 차단.
    """

    def __init__(self, broker: BrokerAdapter, db: Any):
        # 인스턴스 단계 가드 — paper-safe broker만 허용.
        assert_paper_broker(broker)
        self._executor = OrderExecutor(broker, db)
        self.broker    = broker
        self.db        = db

    async def execute(self, order: OrderRequest, audit: OrderAuditLog) -> OrderResult:
        """OrderExecutor.execute에 위임. paper-safe 가드 + 표준 audit 흐름.

        audit이 None / decision이 APPROVED/NEEDS_APPROVAL 외이면 OrderExecutor
        가 raise — 본 wrapper가 새 분기를 만들지 않음.
        """
        # broker는 init 시점에 검증했지만, runtime에 broker.is_paper가 외부
        # 변경되는 경우(예: 테스트)에 대비해 매 호출마다 한 번 더 검증.
        assert_paper_broker(self.broker)
        return await self._executor.execute(order, audit)


# ---------- module invariants ----------
#
# 본 모듈은 broker.place_order, broker.cancel_order 직접 호출 형태를 작성하지
# 않는다. 모든 실 broker 호출은 OrderExecutor 단일 진입점을 경유.
# tests/test_paper_trader.py가 grep으로 invariant를 강제.
