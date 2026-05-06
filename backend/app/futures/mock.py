"""MockFuturesBroker — 가상 선물 거래 환경 (151, MUST).

CLAUDE.md 절대 원칙:
- 라이브 선물 거래는 `enable_futures_live_trading=False` 기본값으로 영구 비활성.
- 본 모듈은 *가상* 거래용으로만 동작 — 어떤 실거래 broker endpoint도 호출하지 않는다.

호출자는 `FuturesRiskManager.evaluate_virtual_order`를 통과한 주문만 본 broker에
전달해야 하며, 본 broker가 그 가드 체인을 우회하는 일은 없다 (호출자 책임).

내부 상태:
- cash: 가상 KRW 잔고
- positions: contract → FuturesPosition (한 계약에 한 포지션 가정 — 양 방향
  동시 보유 미지원, 단순화)
- orders: order_id → FuturesOrderResult 마지막 상태
- prices: contract → 현재 mark price (테스트가 set_mark_price로 강제)
"""

from datetime import datetime, timezone
from uuid import uuid4

from app.futures.base import FuturesBrokerAdapter
from app.futures.simulation import (
    FuturesSimulationParams,
    apply_slippage,
    compute_fee,
    compute_initial_margin,
    compute_liquidation_price,
    realized_pnl_on_close,
    should_force_liquidate,
)
from app.futures.types import (
    FuturesBalance,
    FuturesOrderRequest,
    FuturesOrderResult,
    FuturesOrderStatus,
    FuturesPosition,
    FuturesPositionSide,
    FuturesQuote,
    FuturesSide,
)


class MockFuturesBroker(FuturesBrokerAdapter):
    """In-memory 선물 시뮬레이션 broker.

    사용 패턴:
        broker = MockFuturesBroker(initial_cash=20_000_000)
        broker.set_mark_price("KOSPI200_2503", 350_000)
        broker.set_leverage(5.0)
        result = await broker.place_order(FuturesOrderRequest(
            contract="KOSPI200_2503", side=FuturesSide.BUY, quantity=1,
        ))
    """

    def __init__(
        self,
        initial_cash: int = 20_000_000,
        params:       FuturesSimulationParams | None = None,
    ):
        self.cash      = initial_cash
        self.positions: dict[str, FuturesPosition] = {}
        self.orders:    dict[str, FuturesOrderResult] = {}
        self.prices:    dict[str, int] = {
            # 테스트 기본값. 실 운영은 set_mark_price로 강제.
            "KOSPI200_2503": 350_000,
            "KOSPI200_2506": 348_000,
        }
        self.params    = params or FuturesSimulationParams()
        self.leverage  = self.params.default_leverage
        self.realized_pnl_today = 0  # 외부에서 reset 가능 (FuturesRiskManager용)

    # ---------- helpers (테스트 / 운영자) ----------

    def set_mark_price(self, contract: str, price: int) -> None:
        if price <= 0:
            raise ValueError("price must be positive")
        self.prices[contract] = price
        # 보유 포지션의 market_price 갱신 (mark-to-market).
        if contract in self.positions:
            pos = self.positions[contract]
            self.positions[contract] = pos.model_copy(update={"market_price": price})

    def set_leverage(self, leverage: float) -> None:
        if leverage <= 0 or leverage > self.params.max_leverage:
            raise ValueError(
                f"leverage must be in (0, {self.params.max_leverage}]"
            )
        self.leverage = leverage

    def margin_used(self) -> int:
        return sum(p.margin_used for p in self.positions.values())

    def equity(self) -> int:
        # 단순 cash + 보유 포지션의 unrealized PnL.
        unrealized = 0
        for p in self.positions.values():
            unrealized += realized_pnl_on_close(
                side=p.side, quantity=p.quantity,
                entry_price=p.entry_price, exit_price=p.market_price,
            )
        return self.cash + unrealized

    def force_liquidate_if_needed(self, contract: str) -> FuturesOrderResult | None:
        """현재 mark price에서 강제청산 조건 충족 시 자동 청산. 반환된 OrderResult가
        있으면 청산이 발생한 것."""
        pos = self.positions.get(contract)
        if pos is None:
            return None
        mark = self.prices.get(contract, pos.market_price)
        if not should_force_liquidate(pos, mark):
            return None
        # 강제청산 = 반대 방향 시장가 청산. liquidation_price를 사용하여 PnL 산출.
        return self._close_position(contract,
                                     reason=FuturesOrderStatus.FILLED,
                                     fill_price=pos.liquidation_price or mark,
                                     forced=True)

    # ---------- broker interface ----------

    async def get_quote(self, contract_code: str) -> FuturesQuote:
        price = self.prices.get(contract_code)
        if price is None:
            # 운영 환경에서 unknown contract 호출은 사실상 misconfig — 안전 측 default.
            price = 100_000
        return FuturesQuote(
            contract=contract_code,
            price=price,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="mock",
        )

    async def get_balance(self) -> FuturesBalance:
        margin = self.margin_used()
        return FuturesBalance(
            cash=self.cash,
            margin_used=margin,
            margin_available=max(0, self.cash - margin),
            equity=self.equity(),
        )

    async def get_positions(self) -> list[FuturesPosition]:
        return list(self.positions.values())

    async def place_order(self, order: FuturesOrderRequest) -> FuturesOrderResult:
        # 본 broker는 RiskManager 통과 후 호출되는 가상 환경 — 추가 가드는
        # caller(FuturesRiskManager.evaluate_virtual_order) 책임이지만 일관성을
        # 위해 cash/포지션 검증을 한 번 더.
        order_id = str(uuid4())
        mark = self.prices.get(order.contract, 100_000)

        # MARKET — 슬리피지 적용. LIMIT은 cross 검증 후 그대로.
        if order.order_type.value == "MARKET":
            fill_price = apply_slippage(
                price=mark, side=order.side.value,
                slippage_bps=self.params.slippage_bps,
            )
        else:
            limit = order.limit_price or mark
            # BUY는 mark ≤ limit, SELL은 mark ≥ limit이어야 체결.
            crossed = (
                order.side == FuturesSide.BUY  and mark <= limit
            ) or (
                order.side == FuturesSide.SELL and mark >= limit
            )
            if not crossed:
                result = FuturesOrderResult(
                    order_id=order_id, status=FuturesOrderStatus.RECEIVED,
                    contract=order.contract, side=order.side, quantity=order.quantity,
                    message="limit_not_crossed",
                )
                self.orders[order_id] = result
                return result
            fill_price = limit

        notional = fill_price * order.quantity
        init_margin = compute_initial_margin(notional=notional, leverage=self.leverage)
        fee = compute_fee(notional=notional, fee_bps=self.params.fee_bps)

        # 진입 / 추가 / 청산 분기.
        existing = self.positions.get(order.contract)
        if existing is None:
            # 신규 진입.
            if self.cash < init_margin + fee:
                result = FuturesOrderResult(
                    order_id=order_id, status=FuturesOrderStatus.REJECTED,
                    contract=order.contract, side=order.side, quantity=order.quantity,
                    message="insufficient_cash",
                )
                self.orders[order_id] = result
                return result
            position_side = (
                FuturesPositionSide.LONG if order.side == FuturesSide.BUY
                else FuturesPositionSide.SHORT
            )
            self.cash -= (init_margin + fee)
            liq_price = compute_liquidation_price(
                side=position_side,
                entry_price=fill_price,
                leverage=self.leverage,
                maintenance_margin_pct=self.params.maintenance_margin_pct,
            )
            self.positions[order.contract] = FuturesPosition(
                contract=order.contract,
                side=position_side,
                quantity=order.quantity,
                entry_price=fill_price,
                market_price=fill_price,
                margin_used=init_margin,
                liquidation_price=liq_price,
            )
            result = FuturesOrderResult(
                order_id=order_id, status=FuturesOrderStatus.FILLED,
                contract=order.contract, side=order.side, quantity=order.quantity,
                filled_quantity=order.quantity, avg_fill_price=fill_price,
                margin_delta=init_margin, message="virtual_open",
            )
            self.orders[order_id] = result
            return result

        # 기존 포지션 존재 — 청산 또는 동일 방향 추가.
        same_side = (
            (existing.side == FuturesPositionSide.LONG  and order.side == FuturesSide.BUY)
            or (existing.side == FuturesPositionSide.SHORT and order.side == FuturesSide.SELL)
        )
        if same_side:
            # 추가 매수/매도 — 평단 갱신 + 추가 margin.
            if self.cash < init_margin + fee:
                result = FuturesOrderResult(
                    order_id=order_id, status=FuturesOrderStatus.REJECTED,
                    contract=order.contract, side=order.side, quantity=order.quantity,
                    message="insufficient_cash",
                )
                self.orders[order_id] = result
                return result
            self.cash -= (init_margin + fee)
            new_qty = existing.quantity + order.quantity
            new_entry = int(round(
                (existing.entry_price * existing.quantity + fill_price * order.quantity)
                / new_qty
            ))
            new_margin = existing.margin_used + init_margin
            new_liq = compute_liquidation_price(
                side=existing.side,
                entry_price=new_entry,
                leverage=self.leverage,
                maintenance_margin_pct=self.params.maintenance_margin_pct,
            )
            self.positions[order.contract] = existing.model_copy(update={
                "quantity":          new_qty,
                "entry_price":       new_entry,
                "market_price":      fill_price,
                "margin_used":       new_margin,
                "liquidation_price": new_liq,
            })
            result = FuturesOrderResult(
                order_id=order_id, status=FuturesOrderStatus.FILLED,
                contract=order.contract, side=order.side, quantity=order.quantity,
                filled_quantity=order.quantity, avg_fill_price=fill_price,
                margin_delta=init_margin, message="virtual_add",
            )
            self.orders[order_id] = result
            return result

        # 반대 방향 — 청산.
        return self._close_position(
            order.contract, reason=FuturesOrderStatus.FILLED,
            fill_price=fill_price, close_qty=order.quantity, forced=False,
        )

    async def cancel_order(self, order_id: str) -> FuturesOrderResult:
        result = self.orders.get(order_id)
        if not result:
            return FuturesOrderResult(
                order_id=order_id, status=FuturesOrderStatus.REJECTED,
                contract="UNKNOWN", side=FuturesSide.BUY, quantity=0,
                message="order_not_found",
            )
        # 이미 FILLED면 cancel 불가.
        if result.status == FuturesOrderStatus.FILLED:
            return result.model_copy(update={"message": "already_filled"})
        canceled = result.model_copy(update={
            "status": FuturesOrderStatus.CANCELED,
            "message": "virtual_cancel",
        })
        self.orders[order_id] = canceled
        return canceled

    async def get_order_status(self, order_id: str) -> FuturesOrderResult:
        result = self.orders.get(order_id)
        if not result:
            return FuturesOrderResult(
                order_id=order_id, status=FuturesOrderStatus.REJECTED,
                contract="UNKNOWN", side=FuturesSide.BUY, quantity=0,
                message="order_not_found",
            )
        return result

    # ---------- internals ----------

    def _close_position(
        self,
        contract:    str,
        *,
        reason:      FuturesOrderStatus,
        fill_price:  int,
        close_qty:   int | None = None,
        forced:      bool = False,
    ) -> FuturesOrderResult:
        pos = self.positions.get(contract)
        if pos is None:
            return FuturesOrderResult(
                order_id=str(uuid4()), status=FuturesOrderStatus.REJECTED,
                contract=contract, side=FuturesSide.SELL, quantity=0,
                message="no_position",
            )
        qty = close_qty if close_qty is not None else pos.quantity
        qty = min(qty, pos.quantity)
        notional = fill_price * qty
        fee = compute_fee(notional=notional, fee_bps=self.params.fee_bps)

        pnl = realized_pnl_on_close(
            side=pos.side, quantity=qty,
            entry_price=pos.entry_price, exit_price=fill_price,
        )
        # margin 환수: 청산 비율만큼 비례 환수.
        ratio = qty / pos.quantity
        margin_release = int(round(pos.margin_used * ratio))
        self.cash += margin_release + pnl - fee
        self.realized_pnl_today += (pnl - fee)

        if qty == pos.quantity:
            del self.positions[contract]
        else:
            self.positions[contract] = pos.model_copy(update={
                "quantity":     pos.quantity - qty,
                "margin_used":  pos.margin_used - margin_release,
                "market_price": fill_price,
            })

        order_id = str(uuid4())
        # 청산은 반대 side로 나타냄 — broker 응답 의미 명확화.
        opposite = FuturesSide.SELL if pos.side == FuturesPositionSide.LONG else FuturesSide.BUY
        msg = "virtual_force_liquidate" if forced else "virtual_close"
        result = FuturesOrderResult(
            order_id=order_id, status=reason,
            contract=contract, side=opposite, quantity=qty,
            filled_quantity=qty, avg_fill_price=fill_price,
            margin_delta=-margin_release, message=msg,
        )
        self.orders[order_id] = result
        return result
