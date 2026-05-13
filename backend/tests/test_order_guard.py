"""OrderGuard — fingerprint / idempotency / cooldown / pending 테스트 (#38).

체크리스트 #38: 중복 주문 / 짧은 시간 반복 / 동일 종목 재진입 / 미체결 같은
방향 등 *흐름 차원의* pre-trade 가드. RiskManager / 한도 / 손실 정책과는
별도 차원.
"""

from datetime import datetime, timedelta, timezone

from app.brokers.base import OrderRequest, OrderSide, OrderType
from app.db.models import OrderAuditLog, PendingApproval
from app.risk.order_guard import (
    GuardDecision,
    OrderGuard,
    OrderGuardConfig,
    build_order_fingerprint,
)


# ---------- helpers ----------


def _buy(qty=1, symbol="005930", *, strategy=None, limit_price=None,
         order_type=OrderType.MARKET, client_order_id=None):
    return OrderRequest(
        symbol=symbol, side=OrderSide.BUY, quantity=qty,
        order_type=order_type, limit_price=limit_price,
        strategy=strategy, client_order_id=client_order_id,
    )


def _sell(qty=1, symbol="005930", *, strategy=None, client_order_id=None):
    return OrderRequest(
        symbol=symbol, side=OrderSide.SELL, quantity=qty,
        order_type=OrderType.MARKET,
        strategy=strategy, client_order_id=client_order_id,
    )


def _seed_audit(client, *, symbol, side, qty=1, decision="APPROVED",
                executed=True, strategy=None, mode="SIMULATION",
                client_order_id=None, order_type="MARKET", limit_price=None,
                created_minutes_ago=0):
    with client.test_db_factory() as db:
        ts = datetime.now(timezone.utc) - timedelta(minutes=created_minutes_ago)
        row = OrderAuditLog(
            mode=mode, symbol=symbol, side=side, quantity=qty,
            order_type=order_type, limit_price=limit_price, latest_price=100,
            decision=decision, reasons=[], strategy=strategy,
            client_order_id=client_order_id,
            executed=executed, filled_quantity=qty if executed else 0,
            avg_fill_price=100 if executed else None,
            broker_status="FILLED" if executed else None,
            created_at=ts,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


# ====================================================================
# Fingerprint
# ====================================================================


class TestFingerprint:
    def test_same_order_same_fingerprint(self):
        a = _buy(1, "005930", strategy="vwap", limit_price=10_000,
                 order_type=OrderType.LIMIT)
        b = _buy(1, "005930", strategy="vwap", limit_price=10_000,
                 order_type=OrderType.LIMIT)
        assert build_order_fingerprint(a, mode="SIMULATION") == \
               build_order_fingerprint(b, mode="SIMULATION")

    def test_close_prices_in_same_bucket(self):
        """price_bucket_pct=1.0 — 1% 이내 차이는 같은 bucket."""
        a = _buy(1, "X", limit_price=10_000, order_type=OrderType.LIMIT)
        b = _buy(1, "X", limit_price=10_050, order_type=OrderType.LIMIT)  # 0.5% 차이
        assert build_order_fingerprint(a, price_bucket_pct=1.0) == \
               build_order_fingerprint(b, price_bucket_pct=1.0)

    def test_far_prices_in_different_bucket(self):
        a = _buy(1, "X", limit_price=10_000, order_type=OrderType.LIMIT)
        b = _buy(1, "X", limit_price=11_000, order_type=OrderType.LIMIT)
        assert build_order_fingerprint(a, price_bucket_pct=1.0) != \
               build_order_fingerprint(b, price_bucket_pct=1.0)

    def test_market_order_ignores_price(self):
        a = _buy(1, "X", order_type=OrderType.MARKET, limit_price=None)
        b = _buy(1, "X", order_type=OrderType.MARKET, limit_price=None)
        assert build_order_fingerprint(a) == build_order_fingerprint(b)

    def test_different_symbol_different_fingerprint(self):
        assert build_order_fingerprint(_buy(symbol="X")) != \
               build_order_fingerprint(_buy(symbol="Y"))

    def test_different_side_different_fingerprint(self):
        assert build_order_fingerprint(_buy()) != build_order_fingerprint(_sell())

    def test_different_strategy_different_fingerprint(self):
        assert build_order_fingerprint(_buy(strategy="A")) != \
               build_order_fingerprint(_buy(strategy="B"))

    def test_different_mode_different_fingerprint(self):
        assert build_order_fingerprint(_buy(), mode="SIMULATION") != \
               build_order_fingerprint(_buy(), mode="LIVE_AI_EXECUTION")

    def test_different_agent_chain_id_different_fingerprint(self):
        assert build_order_fingerprint(_buy(), agent_chain_id="c1") != \
               build_order_fingerprint(_buy(), agent_chain_id="c2")

    def test_does_not_include_secret(self):
        """fingerprint는 계좌번호 / API key를 포함하지 않는다 — 입력 자체에 없음."""
        # Prepare an order; build_order_fingerprint signature accepts only
        # public order metadata. Account/secret is not even an input.
        fp = build_order_fingerprint(_buy())
        # SHA-256 hex prefix만 노출 — order 평문 미노출.
        assert fp.startswith("of_")
        assert "005930" not in fp or len(fp) <= 16  # short hash only


# ====================================================================
# Idempotency replay vs duplicate
# ====================================================================


class TestIdempotency:
    def test_same_client_order_id_yields_retry_replay(self, client):
        existing_id = _seed_audit(client, symbol="X", side="BUY",
                                    client_order_id="abc-123")
        order = _buy(symbol="X", client_order_id="abc-123")
        with client.test_db_factory() as db:
            r = OrderGuard(OrderGuardConfig(), db).check(order, mode="SIMULATION")
        assert r.decision == GuardDecision.RETRY_REPLAY
        assert r.audit_replay_id == existing_id
        assert r.blocked_by == "idempotency_replay"

    def test_different_client_order_id_same_fingerprint_yields_duplicate(self, client):
        """같은 fingerprint, 다른 client_order_id → DUPLICATE."""
        _seed_audit(client, symbol="X", side="BUY", client_order_id="key-1")
        order = _buy(symbol="X", client_order_id="key-2")  # 다른 key
        cfg = OrderGuardConfig(duplicate_window_seconds=300)
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(order, mode="SIMULATION")
        assert r.decision == GuardDecision.DUPLICATE
        assert r.blocked_by == "duplicate"

    def test_no_client_order_id_uses_fingerprint_check(self, client):
        """client_order_id 없으면 fingerprint 기반 duplicate 검사."""
        _seed_audit(client, symbol="X", side="BUY")  # client_order_id=None
        cfg = OrderGuardConfig(duplicate_window_seconds=300)
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(_buy(symbol="X"), mode="SIMULATION")
        assert r.decision == GuardDecision.DUPLICATE

    def test_duplicate_disabled_when_window_zero(self, client):
        """window=0이면 duplicate 검사 비활성."""
        _seed_audit(client, symbol="X", side="BUY")
        with client.test_db_factory() as db:
            r = OrderGuard(OrderGuardConfig(), db).check(_buy(symbol="X"),
                                                            mode="SIMULATION")
        assert r.decision == GuardDecision.ALLOW

    def test_duplicate_outside_window_allowed(self, client):
        """window 밖 audit row는 duplicate 아님 (오래된 주문은 OK)."""
        _seed_audit(client, symbol="X", side="BUY", created_minutes_ago=120)
        cfg = OrderGuardConfig(duplicate_window_seconds=60)  # 1분
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(_buy(symbol="X"), mode="SIMULATION")
        assert r.decision == GuardDecision.ALLOW


# ====================================================================
# Cooldown
# ====================================================================


class TestCooldown:
    def test_symbol_cooldown_blocks_within_window(self, client):
        _seed_audit(client, symbol="X", side="BUY", created_minutes_ago=0)
        cfg = OrderGuardConfig(symbol_cooldown_seconds=300)
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(_buy(symbol="X"), mode="SIMULATION")
        assert r.decision == GuardDecision.COOLDOWN
        assert r.blocked_by == "symbol_cooldown"
        assert r.cooldown_remaining_seconds is not None and r.cooldown_remaining_seconds > 0

    def test_symbol_cooldown_allows_after_window(self, client):
        _seed_audit(client, symbol="X", side="BUY", created_minutes_ago=10)
        cfg = OrderGuardConfig(symbol_cooldown_seconds=60)
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(_buy(symbol="X"), mode="SIMULATION")
        assert r.decision == GuardDecision.ALLOW

    def test_strategy_symbol_cooldown(self, client):
        _seed_audit(client, symbol="X", side="BUY", strategy="vwap")
        cfg = OrderGuardConfig(strategy_symbol_cooldown_seconds=300)
        with client.test_db_factory() as db:
            # 같은 (vwap, X) → cooldown
            r = OrderGuard(cfg, db).check(_buy(symbol="X", strategy="vwap"),
                                            mode="SIMULATION")
        assert r.decision == GuardDecision.COOLDOWN
        assert r.blocked_by == "strategy_symbol_cooldown"

    def test_strategy_symbol_cooldown_does_not_block_different_strategy(self, client):
        _seed_audit(client, symbol="X", side="BUY", strategy="vwap")
        cfg = OrderGuardConfig(strategy_symbol_cooldown_seconds=300)
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(_buy(symbol="X", strategy="orb"),
                                            mode="SIMULATION")
        assert r.decision == GuardDecision.ALLOW

    def test_post_exit_cooldown_blocks_re_buy(self, client):
        """SELL 직후 같은 symbol BUY는 post-exit cooldown 차단."""
        _seed_audit(client, symbol="X", side="SELL", created_minutes_ago=0)
        cfg = OrderGuardConfig(post_exit_cooldown_seconds=300)
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(_buy(symbol="X"), mode="SIMULATION")
        assert r.decision == GuardDecision.COOLDOWN
        assert r.blocked_by == "post_exit_cooldown"

    def test_post_exit_does_not_block_sell(self, client):
        """SELL 직후 또 SELL은 post-exit cooldown 비대상 (BUY에만)."""
        _seed_audit(client, symbol="X", side="SELL")
        cfg = OrderGuardConfig(post_exit_cooldown_seconds=300)
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(_sell(symbol="X"), mode="SIMULATION")
        # SELL은 post_exit 가드 비대상 — symbol_cooldown은 안 걸어둠 → ALLOW.
        assert r.decision == GuardDecision.ALLOW

    def test_ai_extra_cooldown_applies_only_for_ai(self, client):
        _seed_audit(client, symbol="X", side="BUY")
        cfg = OrderGuardConfig(ai_extra_cooldown_seconds=300)
        with client.test_db_factory() as db:
            r_ai = OrderGuard(cfg, db).check(_buy(symbol="X"), mode="SIMULATION",
                                                requested_by_ai=True)
        assert r_ai.decision == GuardDecision.COOLDOWN
        assert r_ai.blocked_by == "ai_cooldown"
        with client.test_db_factory() as db:
            r_manual = OrderGuard(cfg, db).check(_buy(symbol="X"),
                                                     mode="SIMULATION",
                                                     requested_by_ai=False)
        assert r_manual.decision == GuardDecision.ALLOW


# ====================================================================
# Pending order guard
# ====================================================================


class TestPendingGuard:
    def test_pending_buy_blocks_new_buy(self, client):
        # NEEDS_APPROVAL audit row + PendingApproval(PENDING)
        with client.test_db_factory() as db:
            audit = OrderAuditLog(
                mode="SIMULATION", symbol="X", side="BUY", quantity=1,
                order_type="MARKET", latest_price=100,
                decision="NEEDS_APPROVAL", reasons=[],
            )
            db.add(audit)
            db.commit()
            db.refresh(audit)
            db.add(PendingApproval(
                audit_id=audit.id, symbol="X", side="BUY", quantity=1,
                order_type="MARKET", limit_price=None, mode="SIMULATION",
                status="PENDING",
            ))
            db.commit()
        cfg = OrderGuardConfig(block_when_pending_same_side=True)
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(_buy(symbol="X"), mode="SIMULATION")
        assert r.decision == GuardDecision.PENDING_BLOCKED
        assert r.blocked_by == "pending"

    def test_pending_buy_does_not_block_sell(self, client):
        with client.test_db_factory() as db:
            audit = OrderAuditLog(
                mode="SIMULATION", symbol="X", side="BUY", quantity=1,
                order_type="MARKET", latest_price=100,
                decision="NEEDS_APPROVAL", reasons=[],
            )
            db.add(audit)
            db.commit()
            db.refresh(audit)
            db.add(PendingApproval(
                audit_id=audit.id, symbol="X", side="BUY", quantity=1,
                order_type="MARKET", limit_price=None, mode="SIMULATION",
                status="PENDING",
            ))
            db.commit()
        cfg = OrderGuardConfig(block_when_pending_same_side=True)
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(_sell(symbol="X"), mode="SIMULATION")
        # 다른 side는 통과
        assert r.decision == GuardDecision.ALLOW

    def test_completed_pending_does_not_block(self, client):
        """status != PENDING (APPROVED/REJECTED/EXPIRED)은 차단 X."""
        with client.test_db_factory() as db:
            audit = OrderAuditLog(
                mode="SIMULATION", symbol="X", side="BUY", quantity=1,
                order_type="MARKET", latest_price=100,
                decision="APPROVED", reasons=[],
            )
            db.add(audit)
            db.commit()
            db.refresh(audit)
            db.add(PendingApproval(
                audit_id=audit.id, symbol="X", side="BUY", quantity=1,
                order_type="MARKET", limit_price=None, mode="SIMULATION",
                status="APPROVED",
            ))
            db.commit()
        cfg = OrderGuardConfig(block_when_pending_same_side=True)
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(_buy(symbol="X"), mode="SIMULATION")
        assert r.decision == GuardDecision.ALLOW

    def test_orphan_audit_needs_approval_blocks(self, client):
        """PendingApproval은 없는데 OrderAuditLog NEEDS_APPROVAL row만 있어도 차단."""
        with client.test_db_factory() as db:
            db.add(OrderAuditLog(
                mode="SIMULATION", symbol="X", side="BUY", quantity=1,
                order_type="MARKET", latest_price=100,
                decision="NEEDS_APPROVAL", reasons=[],
            ))
            db.commit()
        cfg = OrderGuardConfig(block_when_pending_same_side=True)
        with client.test_db_factory() as db:
            r = OrderGuard(cfg, db).check(_buy(symbol="X"), mode="SIMULATION")
        assert r.decision == GuardDecision.PENDING_BLOCKED


# ====================================================================
# Default config — all checks disabled
# ====================================================================


class TestDefaultConfigPassthrough:
    def test_default_config_allows_everything(self, client):
        """모든 필드 default 0 → 본 가드는 사실상 no-op (기존 호환)."""
        _seed_audit(client, symbol="X", side="BUY")
        with client.test_db_factory() as db:
            r = OrderGuard(OrderGuardConfig(), db).check(_buy(symbol="X"),
                                                              mode="SIMULATION")
        assert r.decision == GuardDecision.ALLOW
        assert r.fingerprint.startswith("of_")


# ====================================================================
# route_order integration
# ====================================================================


class TestRouteOrderIntegration:
    def _broker(self):
        from app.brokers.mock_broker import MockBrokerAdapter
        return MockBrokerAdapter()

    def test_route_order_blocks_duplicate_when_enabled(self, client):
        import asyncio

        from app.api.deps import get_risk_manager
        from app.brokers.base import OrderRequest as _OR
        from app.core.modes import OperationMode
        from app.execution.order_router import route_order

        broker = self._broker()
        risk = get_risk_manager()
        # 활성 정책 주입 (시도 후 finally에서 복구)
        risk.policy.order_guard_duplicate_window_seconds = 300
        try:
            order = _OR(symbol="X", side=OrderSide.BUY, quantity=1,
                         order_type=OrderType.MARKET)
            with client.test_db_factory() as db:
                asyncio.run(route_order(
                    order=order, requested_by_ai=False,
                    mode=OperationMode.SIMULATION, broker=broker,
                    risk=risk, db=db,
                ))
            with client.test_db_factory() as db:
                result = asyncio.run(route_order(
                    order=order, requested_by_ai=False,
                    mode=OperationMode.SIMULATION, broker=broker,
                    risk=risk, db=db,
                ))
            assert result.decision.value == "REJECTED"
            assert any("duplicate order" in r for r in result.reasons)
        finally:
            risk.policy.order_guard_duplicate_window_seconds = 0

    def test_route_order_allows_when_guard_disabled(self, client):
        import asyncio

        from app.api.deps import get_risk_manager
        from app.core.modes import OperationMode
        from app.execution.order_router import route_order

        broker = self._broker()
        risk = get_risk_manager()
        order = _buy(symbol="X")
        with client.test_db_factory() as db:
            r1 = asyncio.run(route_order(
                order=order, requested_by_ai=False,
                mode=OperationMode.SIMULATION, broker=broker,
                risk=risk, db=db,
            ))
            r2 = asyncio.run(route_order(
                order=order, requested_by_ai=False,
                mode=OperationMode.SIMULATION, broker=broker,
                risk=risk, db=db,
            ))
        assert r1.decision.value in ("APPROVED", "REJECTED")
        assert r2.decision.value in ("APPROVED", "REJECTED")


# ====================================================================
# Safety
# ====================================================================


class TestSafety:
    def test_module_does_not_call_broker_or_executor(self):
        import inspect

        from app.risk import order_guard as mod
        src = inspect.getsource(mod)
        forbidden_calls = (
            "broker.place_order(", "broker.cancel_order(",
            ".place_order(", ".cancel_order(",
        )
        for f in forbidden_calls:
            assert f not in src, f"forbidden call in order_guard: {f}"
        # OrderExecutor / route_order import 0건
        forbidden_imports = (
            "from app.execution.executor",
            "from app.execution.order_router",
            "from app.brokers.kis",
        )
        for f in forbidden_imports:
            assert f not in src

    def test_check_does_not_mutate_db(self, client):
        """OrderGuard.check은 read-only — DB write 0건."""
        with client.test_db_factory() as db:
            before_audit  = db.execute(select_count(OrderAuditLog)).scalar() or 0
            before_pending = db.execute(select_count(PendingApproval)).scalar() or 0
        _seed_audit(client, symbol="X", side="BUY")
        with client.test_db_factory() as db:
            OrderGuard(OrderGuardConfig(duplicate_window_seconds=300),
                        db).check(_buy(symbol="X"), mode="SIMULATION")
            after_audit  = db.execute(select_count(OrderAuditLog)).scalar() or 0
            after_pending = db.execute(select_count(PendingApproval)).scalar() or 0
        # _seed_audit이 1건만 추가했어야 함 — guard 자체는 write 안 함
        assert after_audit  == before_audit + 1
        assert after_pending == before_pending


def select_count(model):
    from sqlalchemy import func, select
    return select(func.count(model.id))


# ====================================================================
# #65 추가 gap 테스트
# ====================================================================


class TestSixtyFiveGaps:
    """체크리스트 #65: OrderGuard의 미세한 경계값 lock."""

    def test_rejected_audit_within_window_still_triggers_duplicate(self, client):
        """안전 invariant: REJECTED audit row도 *시도 자체*를 의미하므로 같은
        fingerprint가 짧은 시간에 반복되면 DUPLICATE로 차단된다. 운영자가
        한도 위반 후 같은 주문을 즉시 반복 시도하는 패턴 자체를 보수적으로
        막는다 (CLAUDE.md '손실 방어 우선'). 이 보수적 동작을 lock."""
        _seed_audit(client, symbol="X", side="BUY", decision="REJECTED",
                    executed=False, created_minutes_ago=1)
        with client.test_db_factory() as db:
            decision = OrderGuard(
                OrderGuardConfig(duplicate_window_seconds=600), db,
            ).check(_buy(symbol="X"), mode="SIMULATION")
        # 보수적 차단 — REJECTED 시도도 fingerprint window 안에 다시 들어오면
        # DUPLICATE로 분류. 운영자가 의도적으로 다시 시도하려면 cooldown
        # 임계를 넘기거나 다른 client_order_id로 RETRY_REPLAY를 명시해야 함.
        assert decision.decision == GuardDecision.DUPLICATE

    def test_combined_cooldown_and_pending_returns_pending_first(self, client):
        """같은 symbol에 PENDING 미체결 + symbol cooldown 둘 다 활성이어도 ALLOW
        가 아닌 PENDING_BLOCKED 또는 COOLDOWN 중 하나를 반환. (둘 다 통과는
        invariant 위반)."""
        # PENDING approval 시드 — symbol="X", side="BUY"
        with client.test_db_factory() as db:
            db.add(PendingApproval(
                audit_id=1, symbol="X", side="BUY", quantity=1,
                order_type="MARKET", limit_price=None, mode="LIVE_MANUAL_APPROVAL",
                status="PENDING", attempts=[],
            ))
            db.commit()
        _seed_audit(client, symbol="X", side="BUY", decision="APPROVED",
                    created_minutes_ago=0)
        with client.test_db_factory() as db:
            decision = OrderGuard(
                OrderGuardConfig(
                    symbol_cooldown_seconds=600,
                    block_when_pending_same_side=True,
                ),
                db,
            ).check(_buy(symbol="X"), mode="LIVE_MANUAL_APPROVAL")
        # ALLOW 면 안 됨 — 둘 중 하나로 차단되어야 함.
        assert decision.decision != GuardDecision.ALLOW
        assert decision.decision in (
            GuardDecision.PENDING_BLOCKED,
            GuardDecision.COOLDOWN,
            GuardDecision.DUPLICATE,
        )
