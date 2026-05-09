"""#48: Futures margin / leverage / liquidation rule tests.

Coverage:
- `LeverageLimitRule`: 양수 / 정책 한도 / contract 한도 / 작은 값 효력
- `FuturesMarginRule`: initial margin / max_margin_used / maintenance buffer / preview
- `LiquidationRiskRule`: 3% / 7% threshold / opposite-side close 의도 skip
- `FuturesRiskManager.evaluate_virtual_order` integration:
  - 기존 reason substring 보존 ("leverage", "max_leverage", "margin_available",
    "max_margin_used", "contracts", "daily futures loss")
  - 신규 liquidation 차단
  - warnings + metrics carry
- `/api/futures/margin/preview` read-only endpoint (broker 호출 0건, audit row 0건)
- 정적 가드: margin_rules 모듈은 broker / OrderExecutor / route_order import 0건
- 정적 가드: 강제청산 *주문* 발신 코드 0건
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.futures.margin_rules import (
    FuturesMarginRule,
    LeverageLimitRule,
    LiquidationRiskRule,
    MarginPreview,
    MarginRuleDecision,
)
from app.futures.risk import (
    FuturesRiskDecision,
    FuturesRiskManager,
    FuturesRiskPolicy,
)
from app.futures.types import (
    FuturesOrderRequest,
    FuturesOrderType,
    FuturesPosition,
    FuturesPositionSide,
    FuturesSide,
)


def _order(qty: int = 1, side: FuturesSide = FuturesSide.BUY) -> FuturesOrderRequest:
    return FuturesOrderRequest(
        contract="KOSPI200_2503",
        side=side,
        quantity=qty,
        order_type=FuturesOrderType.MARKET,
    )


# ====================================================================
# 1. LeverageLimitRule
# ====================================================================


def test_leverage_rule_passes_within_policy():
    rule = LeverageLimitRule(policy_max_leverage=10.0)
    res = rule.check(5.0)
    assert res.decision == MarginRuleDecision.PASS
    assert res.reasons == []
    assert res.metrics["effective_max"] == 10.0


def test_leverage_rule_blocks_zero_or_negative():
    rule = LeverageLimitRule(policy_max_leverage=10.0)
    for bad in (0.0, -1.0, -5.0):
        res = rule.check(bad)
        assert res.decision == MarginRuleDecision.BLOCK
        # 기존 reason substring "leverage" 보존
        assert any("leverage" in r for r in res.reasons)


def test_leverage_rule_blocks_above_policy_max():
    rule = LeverageLimitRule(policy_max_leverage=5.0)
    res = rule.check(10.0)
    assert res.decision == MarginRuleDecision.BLOCK
    # 기존 호환: "leverage" + "max_leverage" 모두 존재.
    msg = " ".join(res.reasons)
    assert "leverage" in msg
    assert "max_leverage" in msg


def test_leverage_rule_blocks_above_contract_max_when_more_conservative():
    """contract 시장 한도가 정책 한도보다 작으면 contract 한도가 효력."""
    rule = LeverageLimitRule(policy_max_leverage=20.0, contract_leverage_max=5.0)
    assert rule.effective_max == 5.0
    res = rule.check(10.0)
    assert res.decision == MarginRuleDecision.BLOCK
    assert any("contract" in r and "leverage_max" in r for r in res.reasons)


def test_leverage_rule_uses_policy_when_contract_more_permissive():
    """contract 한도가 정책보다 크면 정책 한도가 효력 (작은 값 효력)."""
    rule = LeverageLimitRule(policy_max_leverage=5.0, contract_leverage_max=20.0)
    assert rule.effective_max == 5.0
    res = rule.check(10.0)
    assert res.decision == MarginRuleDecision.BLOCK
    assert any("max_leverage" in r for r in res.reasons)


def test_leverage_rule_blocks_nonfinite():
    rule = LeverageLimitRule(policy_max_leverage=10.0)
    for bad in (float("inf"), float("nan")):
        res = rule.check(bad)
        assert res.decision == MarginRuleDecision.BLOCK


# ====================================================================
# 2. FuturesMarginRule
# ====================================================================


def test_margin_rule_preview_returns_consistent_metrics():
    rule = FuturesMarginRule(max_margin_used=1_000_000)
    prev = rule.preview(
        order=_order(1), margin_used=0, margin_available=10_000_000,
        mark_price=1_000_000, leverage=5.0,
    )
    assert isinstance(prev, MarginPreview)
    # notional = 1,000,000 * 1 = 1,000,000. initial = 1,000,000/5 = 200,000.
    assert prev.notional == 1_000_000
    assert prev.initial_margin == 200_000
    # default maintenance_margin_pct = 10% → 100,000
    assert prev.maintenance_margin == 100_000
    assert prev.margin_used_after == 200_000
    assert prev.margin_available_after == 9_800_000
    assert prev.headroom_pct == pytest.approx(80.0)


def test_margin_rule_passes_within_limits():
    rule = FuturesMarginRule(max_margin_used=1_000_000)
    res = rule.check(
        order=_order(1), margin_used=0, margin_available=10_000_000,
        mark_price=1_000_000, leverage=5.0,
    )
    assert res.decision == MarginRuleDecision.PASS


def test_margin_rule_blocks_when_margin_available_too_low():
    rule = FuturesMarginRule(max_margin_used=1_000_000)
    res = rule.check(
        order=_order(1), margin_used=0, margin_available=100,
        mark_price=1_000_000, leverage=5.0,
    )
    assert res.decision == MarginRuleDecision.BLOCK
    assert any("margin_available" in r for r in res.reasons)


def test_margin_rule_blocks_when_max_margin_used_exceeded():
    rule = FuturesMarginRule(max_margin_used=100_000)
    res = rule.check(
        order=_order(1), margin_used=0, margin_available=10_000_000,
        mark_price=1_000_000, leverage=5.0,
    )
    assert res.decision == MarginRuleDecision.BLOCK
    assert any("max_margin_used" in r for r in res.reasons)


def test_margin_rule_warns_when_maintenance_buffer_thin():
    """initial margin은 충당 가능하지만, 그 후 잔여 available이 maintenance margin
    보다 적으면 WARN. notional 1,000,000, initial 200,000 → available_after =
    margin_available - 200,000. maintenance = 100,000 (10%)."""
    rule = FuturesMarginRule(max_margin_used=10_000_000)
    res = rule.check(
        order=_order(1),
        margin_used=0,
        margin_available=250_000,    # initial 200,000 충당 가능, 잔여 50,000 < 100,000
        mark_price=1_000_000, leverage=5.0,
    )
    assert res.decision == MarginRuleDecision.WARN
    assert any("maintenance" in w for w in res.warnings)


def test_margin_rule_blocks_on_zero_or_negative_mark():
    rule = FuturesMarginRule(max_margin_used=1_000_000)
    res = rule.check(
        order=_order(1), margin_used=0, margin_available=10_000_000,
        mark_price=0, leverage=5.0,
    )
    assert res.decision == MarginRuleDecision.BLOCK
    assert any("mark_price" in r for r in res.reasons)


# ====================================================================
# 3. LiquidationRiskRule
# ====================================================================


def test_liquidation_rule_passes_when_far_from_liquidation():
    """leverage 5x + maintenance 10% → loss buffer 10%. mark = entry → distance
    = liquidation buffer ≈ 10% > 7% → PASS."""
    rule = LiquidationRiskRule()
    res = rule.check(
        order=_order(1), positions=[],
        mark_price=1_000_000, leverage=5.0,
    )
    assert res.decision == MarginRuleDecision.PASS
    assert res.metrics["distance_pct"] == pytest.approx(10.0, abs=0.5)


def test_liquidation_rule_blocks_when_distance_critical():
    """leverage 100x + maintenance 10% → loss_buffer = 0.01 - 0.1 = max(0, -0.09) = 0.
    이 경우 liquidation = entry, distance = 0 → BLOCK (≤ 3%)."""
    rule = LiquidationRiskRule()
    res = rule.check(
        order=_order(1), positions=[],
        mark_price=1_000_000, leverage=100.0,
    )
    assert res.decision == MarginRuleDecision.BLOCK
    assert any("liquidation" in r for r in res.reasons)
    assert res.metrics["distance_pct"] <= 3.0


def test_liquidation_rule_warns_in_warning_band():
    """leverage 11x + maintenance 4% → loss_buffer = 1/11 - 0.04 ≈ 0.0509 (5.09%).
    distance ≈ 5.09% in (3%, 7%] → WARN."""
    rule = LiquidationRiskRule(maintenance_margin_pct=4.0)
    res = rule.check(
        order=_order(1), positions=[],
        mark_price=1_000_000, leverage=11.0,
    )
    assert res.decision == MarginRuleDecision.WARN
    assert any("liquidation" in w for w in res.warnings)
    assert 3.0 < res.metrics["distance_pct"] <= 7.0


def test_liquidation_rule_skips_opposite_side_close_intent():
    """기존 LONG 보유 + SELL 주문 = close 의도 → 본 Rule은 PASS (skip)."""
    long_pos = FuturesPosition(
        contract="KOSPI200_2503", side=FuturesPositionSide.LONG, quantity=1,
        entry_price=1_000_000, market_price=1_000_000, margin_used=200_000,
    )
    rule = LiquidationRiskRule()
    res = rule.check(
        order=_order(1, side=FuturesSide.SELL),
        positions=[long_pos],
        mark_price=1_000_000, leverage=100.0,  # 보통이면 BLOCK인 위험
    )
    assert res.decision == MarginRuleDecision.PASS
    assert res.metrics.get("skipped") == "opposite-side close intent"


def test_liquidation_rule_blocks_on_invalid_mark():
    rule = LiquidationRiskRule()
    res = rule.check(
        order=_order(1), positions=[],
        mark_price=0, leverage=5.0,
    )
    assert res.decision == MarginRuleDecision.BLOCK


def test_liquidation_rule_uses_blended_entry_for_existing_same_side():
    """existing LONG 1계약 entry 1,000,000 + 신규 LONG 1계약 mark 1,200,000.
    blended_entry ≈ 1,100,000. 본 산출이 metric에 carry 되어야 한다."""
    long_pos = FuturesPosition(
        contract="KOSPI200_2503", side=FuturesPositionSide.LONG, quantity=1,
        entry_price=1_000_000, market_price=1_200_000, margin_used=200_000,
    )
    rule = LiquidationRiskRule()
    res = rule.check(
        order=_order(1, side=FuturesSide.BUY), positions=[long_pos],
        mark_price=1_200_000, leverage=5.0,
    )
    # blended = (1,000,000 + 1,200,000) / 2 = 1,100,000.
    assert res.metrics["blended_entry_price"] == 1_100_000


# ====================================================================
# 4. FuturesRiskManager integration — backwards-compatible reasons
# ====================================================================


def test_evaluate_virtual_order_keeps_existing_reason_substrings():
    """리팩터 후에도 기존 테스트가 의존하던 reason substring들이 그대로 등장.

    - "leverage" / "max_leverage"
    - "margin_available"
    - "max_margin_used"
    - "contracts"
    - "daily futures loss"
    """
    risk = FuturesRiskManager(
        FuturesRiskPolicy(max_leverage=5.0, max_margin_used=100_000,
                           max_contracts=1, max_daily_loss=10_000),
        daily_realized_pnl=-50_000,
    )
    # 모든 가드를 동시에 위반시키는 주문.
    pos = FuturesPosition(
        contract="KOSPI200_2503", side=FuturesPositionSide.LONG, quantity=1,
        entry_price=1_000_000, market_price=1_000_000, margin_used=200_000,
    )
    res = risk.evaluate_virtual_order(
        order=_order(1), positions=[pos],
        margin_used=200_000, margin_available=100,
        mark_price=1_000_000, leverage=10.0,
    )
    assert res.decision == FuturesRiskDecision.REJECTED
    msg = " ".join(res.reasons)
    assert "leverage" in msg
    assert "max_leverage" in msg
    assert "margin_available" in msg
    assert "max_margin_used" in msg
    assert "contracts" in msg
    assert "daily futures loss" in msg


def test_evaluate_virtual_order_blocks_critical_liquidation_risk():
    """leverage 100x — liquidation distance ≈ 0 → 신규 가드(LiquidationRiskRule)가
    REJECTED를 트리거. 기존 max_leverage 가드도 함께 잡지만, 본 테스트는 본 PR
    신규 가드의 작동을 검증."""
    risk = FuturesRiskManager(FuturesRiskPolicy(max_leverage=200.0))  # leverage 가드 우회
    res = risk.evaluate_virtual_order(
        order=_order(1), positions=[],
        margin_used=0, margin_available=10_000_000,
        mark_price=1_000_000, leverage=100.0,
    )
    assert res.decision == FuturesRiskDecision.REJECTED
    assert any("liquidation" in r for r in res.reasons)


def test_evaluate_virtual_order_carries_warnings_and_metrics():
    """maintenance buffer thin이면 warnings에, liquidation_price/distance_pct는
    metrics에 carry."""
    risk = FuturesRiskManager(FuturesRiskPolicy(max_margin_used=10_000_000))
    res = risk.evaluate_virtual_order(
        order=_order(1), positions=[],
        margin_used=0, margin_available=250_000,
        mark_price=1_000_000, leverage=5.0,
    )
    # initial 200,000 충당 가능 + max_margin_used 통과 + leverage 5.0 통과 →
    # APPROVED지만 maintenance buffer thin (잔여 50,000 < 100,000) → warnings.
    assert res.decision == FuturesRiskDecision.APPROVED
    assert any("maintenance" in w for w in res.warnings)
    # liquidation metric이 carry.
    assert "liquidation_price" in res.metrics
    assert "distance_pct" in res.metrics


def test_evaluate_virtual_order_uses_contract_leverage_max():
    """contract spec leverage_max를 호출자가 주입하면 정책 한도와 함께 작은 값 효력."""
    risk = FuturesRiskManager(FuturesRiskPolicy(max_leverage=20.0))
    res = risk.evaluate_virtual_order(
        order=_order(1), positions=[],
        margin_used=0, margin_available=10_000_000,
        mark_price=1_000_000, leverage=10.0,
        contract_leverage_max=5.0,
    )
    assert res.decision == FuturesRiskDecision.REJECTED
    assert any("contract" in r and "leverage_max" in r for r in res.reasons)


# ====================================================================
# 5. Live evaluate_order still REJECTED — invariant unchanged
# ====================================================================


def test_live_evaluate_order_still_rejects_with_flag_off():
    """본 PR이 live 경로를 활성화하지 않는다 — invariant lock."""
    risk = FuturesRiskManager(FuturesRiskPolicy())
    res = risk.evaluate_order(
        order=_order(1), positions=[],
        margin_used=0, margin_available=10_000_000,
    )
    assert res.decision == FuturesRiskDecision.REJECTED
    assert any("ENABLE_FUTURES_LIVE_TRADING" in r for r in res.reasons)


def test_live_evaluate_order_still_rejects_even_with_flag_on():
    """flag=True여도 live evaluation이 구현되지 않은 상태 — 본 PR 미변경."""
    risk = FuturesRiskManager(FuturesRiskPolicy(enable_futures_live_trading=True))
    res = risk.evaluate_order(
        order=_order(1), positions=[],
        margin_used=0, margin_available=10_000_000,
    )
    assert res.decision == FuturesRiskDecision.REJECTED


# ====================================================================
# 6. /api/futures/margin/preview endpoint
# ====================================================================


def test_api_margin_preview_returns_pass_for_safe_order(client):
    res = client.post("/api/futures/margin/preview", json={
        "contract": "KOSPI200_2503",
        "side": "BUY",
        "quantity": 1,
        "order_type": "MARKET",
        "mark_price": 1_000_000,
        "leverage": 5.0,
        "margin_used": 0,
        "margin_available": 10_000_000,
        "positions": [],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["overall"] == "PASS"
    assert body["leverage"]["decision"] == "PASS"
    assert body["margin"]["decision"] == "PASS"
    assert body["liquidation"]["decision"] == "PASS"
    assert "broker 호출 0건" in body["notice"]


def test_api_margin_preview_blocks_excess_leverage(client):
    res = client.post("/api/futures/margin/preview", json={
        "contract": "KOSPI200_2503", "side": "BUY", "quantity": 1,
        "mark_price": 1_000_000, "leverage": 50.0,
        "margin_used": 0, "margin_available": 10_000_000, "positions": [],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["overall"] == "BLOCK"
    assert body["leverage"]["decision"] == "BLOCK"


def test_api_margin_preview_blocks_critical_liquidation(client):
    """leverage 100x + 정책 max_leverage 100x로 우회 → liquidation_critical_pct 트리거."""
    res = client.post("/api/futures/margin/preview", json={
        "contract": "KOSPI200_2503", "side": "BUY", "quantity": 1,
        "mark_price": 1_000_000, "leverage": 100.0,
        "margin_used": 0, "margin_available": 10_000_000, "positions": [],
    })
    body = res.json()
    # leverage > 정책 max(10) → leverage rule이 BLOCK. liquidation rule도 BLOCK.
    assert body["overall"] == "BLOCK"
    assert body["liquidation"]["decision"] == "BLOCK"


def test_api_margin_preview_does_not_create_audit_or_orders(client):
    """preview는 read-only — DB / audit / approval row 변경 0건."""
    from app.db.models import FuturesOrderAuditLog, OrderAuditLog, PendingApproval

    client.post("/api/futures/margin/preview", json={
        "contract": "KOSPI200_2503", "side": "BUY", "quantity": 1,
        "mark_price": 1_000_000, "leverage": 5.0,
        "margin_used": 0, "margin_available": 10_000_000, "positions": [],
    })
    with client.test_db_factory() as db:
        assert db.execute(select(OrderAuditLog)).scalars().all() == []
        assert db.execute(select(PendingApproval)).scalars().all() == []
        assert db.execute(select(FuturesOrderAuditLog)).scalars().all() == []


def test_api_margin_preview_validates_quantity_positive(client):
    res = client.post("/api/futures/margin/preview", json={
        "contract": "KOSPI200_2503", "side": "BUY", "quantity": 0,
        "mark_price": 1_000_000, "leverage": 5.0,
        "margin_used": 0, "margin_available": 10_000_000,
    })
    assert res.status_code == 422


# ====================================================================
# 7. Static guards — no broker / executor / route_order imports
# ====================================================================


def test_margin_rules_module_does_not_import_broker_or_executor():
    import app.futures.margin_rules as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers",
        "import app.brokers",
        "from app.execution.executor",
        "from app.execution.order_router",
        "broker.place_order(",
        ".place_order(",
        "broker.cancel_order(",
        ".cancel_order(",
        "force_liquidate_if_needed(",  # 자동 청산 *주문* 발신 금지
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.futures.margin_rules must not contain '{snippet}' — "
            "rules are read-only decision functions."
        )


def test_margin_rules_does_not_emit_force_liquidate_orders():
    """본 PR이 자동 강제청산 주문 경로를 추가하지 않는다 — 정적 가드.

    docstring에서 force_liquidate_if_needed를 *언급*하는 것은 OK (정책 문서),
    실제 함수 *호출* (`force_liquidate_if_needed(...)` / `.force_liquidate(`)
    이 등장하면 차단."""
    import app.futures.margin_rules as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden_calls = (
        "force_liquidate_if_needed(",  # MockFuturesBroker 메서드 호출
        ".force_liquidate(",            # 임의 객체의 force_liquidate 메서드 호출
    )
    for snippet in forbidden_calls:
        assert snippet not in src, (
            f"margin_rules must not contain '{snippet}' — only compute risk, "
            "never trigger automatic liquidation orders."
        )


# ====================================================================
# 8. ENABLE_FUTURES_LIVE_TRADING invariant unchanged
# ====================================================================


def test_settings_default_keeps_futures_live_trading_disabled():
    from app.core.config import get_settings
    assert get_settings().enable_futures_live_trading is False


def test_futures_risk_policy_default_keeps_live_trading_disabled():
    p = FuturesRiskPolicy()
    assert p.enable_futures_live_trading is False
    # #48 신규 default도 보수적인지 확인.
    assert p.maintenance_margin_pct == 10.0
    assert p.liquidation_critical_pct == 3.0
    assert p.liquidation_warning_pct == 7.0
