"""P-11: 종목별 최대 비중 제한 — 단위 + 정적 + API + 통합 테스트.

검증 항목 (사용자 요청서 §8 1-25):
 1. BUY 한도 이내 → allowed=True
 2. BUY 한도 초과 → allowed=False
 3. 차단 reason_code = SYMBOL_WEIGHT_LIMIT_EXCEEDED
 4. 메시지 "종목별 최대 비중을 초과하여 매수 차단" 포함
 5. max_exposure = total × pct
 6. new_buy_notional = price × quantity
 7. projected = current + new_buy_notional
 8. remaining_symbol_buy_capacity 계산
 9. 1.6M + 700k = 2.3M > 2M → 차단
 10. 1.2M + 700k = 1.9M ≤ 2M → 허용
 11. projected == max → 허용 (경계)
 12. 차단된 BUY 는 노출 미반영
 13. SELL → NOT_APPLICABLE
 14. HOLD → NOT_APPLICABLE
 15. qty=0 → invalid
 16. price≤0 → INVALID_PRICE
 17. total≤0 → INVALID_TOTAL_EQUITY
 18. pct≤0 → INVALID_WEIGHT
 19. pct>1 → INVALID_WEIGHT
 20. rejected / cancelled / blocked 제외
 21. P-07 충돌 없음
 22. P-08 quantity carry
 23. P-09 risk profile 값 사용
 24. P-10 daily limit 과 사유 구분
 25. 정적 grep — 실거래 / LIVE 미변경
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auto_paper.capital_config import (
    DEFAULT_MAX_SYMBOL_WEIGHT_PCT,
    reset_paper_capital_for_tests,
    resolve_symbol_weight_limit_pct,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.risk.position_limits import (
    SYMBOL_WEIGHT_LIMIT_EXCEEDED,
    SYMBOL_WEIGHT_LIMIT_INVALID_PRICE,
    SYMBOL_WEIGHT_LIMIT_INVALID_QUANTITY,
    SYMBOL_WEIGHT_LIMIT_INVALID_TOTAL_EQUITY,
    SYMBOL_WEIGHT_LIMIT_INVALID_WEIGHT,
    SYMBOL_WEIGHT_LIMIT_NOT_APPLICABLE,
    SYMBOL_WEIGHT_LIMIT_OK,
    SymbolWeightLimitResult,
    calculate_current_symbol_exposure_amount,
    check_symbol_weight_limit,
)


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "risk" / "position_limits.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_store():
    reset_paper_capital_for_tests()
    yield
    reset_paper_capital_for_tests()


@pytest.fixture
def api_client():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False,
    )

    def _override_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestDataclassInvariants:
    def test_is_paper_only_must_be_true(self):
        with pytest.raises(ValueError, match="is_paper_only"):
            SymbolWeightLimitResult(
                allowed=True, reason_code=SYMBOL_WEIGHT_LIMIT_OK,
                reason_message="x",
                total_paper_equity=0, max_symbol_weight_pct=0.2,
                max_symbol_exposure_amount=0,
                current_symbol_exposure_amount=0,
                new_buy_notional=0,
                projected_symbol_exposure_amount=0,
                remaining_symbol_buy_capacity=0,
                is_paper_only=False,  # type: ignore[arg-type]
            )

    def test_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            SymbolWeightLimitResult(
                allowed=True, reason_code=SYMBOL_WEIGHT_LIMIT_OK,
                reason_message="x",
                total_paper_equity=0, max_symbol_weight_pct=0.2,
                max_symbol_exposure_amount=0,
                current_symbol_exposure_amount=0,
                new_buy_notional=0,
                projected_symbol_exposure_amount=0,
                remaining_symbol_buy_capacity=0,
                is_order_signal=True,  # type: ignore[arg-type]
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            SymbolWeightLimitResult(
                allowed=True, reason_code=SYMBOL_WEIGHT_LIMIT_OK,
                reason_message="x",
                total_paper_equity=0, max_symbol_weight_pct=0.2,
                max_symbol_exposure_amount=0,
                current_symbol_exposure_amount=0,
                new_buy_notional=0,
                projected_symbol_exposure_amount=0,
                remaining_symbol_buy_capacity=0,
                is_live_authorization=True,  # type: ignore[arg-type]
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. 사용자 요청서 예시 (검증 9-11)
# ─────────────────────────────────────────────────────────────────────────────


class TestUserSpecScenarios:
    def test_user_example_blocks_when_projected_exceeds(self):
        # 검증 9: total=10M, pct=20%, current=1.6M, new=700k → 차단.
        r = check_symbol_weight_limit(
            side="BUY", symbol="005930",
            price=100_000, quantity=7,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=1_600_000,
            max_symbol_weight_pct=0.20,
        )
        assert r.allowed is False
        # 검증 3.
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_EXCEEDED
        # 검증 5: max_exposure = 10M × 0.20 = 2M.
        assert r.max_symbol_exposure_amount == 2_000_000
        # 검증 6: new = 100k × 7 = 700k.
        assert r.new_buy_notional == 700_000
        # 검증 7: projected = 1.6M + 700k = 2.3M.
        assert r.projected_symbol_exposure_amount == 2_300_000
        # 검증 4: 메시지 정확 문구.
        assert "종목별 최대 비중을 초과하여 매수 차단" in r.reason_message

    def test_allows_when_projected_below_max(self):
        # 검증 10: total=10M, pct=20%, current=1.2M, new=700k → 허용.
        r = check_symbol_weight_limit(
            side="BUY", symbol="005930",
            price=100_000, quantity=7,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=1_200_000,
            max_symbol_weight_pct=0.20,
        )
        assert r.allowed is True
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_OK
        # 검증 8: remaining = 2M - 1.9M = 100k.
        assert r.projected_symbol_exposure_amount == 1_900_000
        assert r.remaining_symbol_buy_capacity == 100_000

    def test_exactly_at_max_is_allowed(self):
        # 검증 11: projected == max → 허용.
        r = check_symbol_weight_limit(
            side="BUY", symbol="005930",
            price=100_000, quantity=4,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=1_600_000,
            max_symbol_weight_pct=0.20,
        )
        assert r.allowed is True
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_OK
        assert r.projected_symbol_exposure_amount == 2_000_000
        assert r.remaining_symbol_buy_capacity == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. 계산 정확성 (검증 5-8)
# ─────────────────────────────────────────────────────────────────────────────


class TestCalculation:
    def test_max_exposure_floor(self):
        # 7,777,777 × 0.20 = 1,555,555.4 → floor 1,555,555.
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=1, quantity=1,
            total_paper_equity=7_777_777,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert r.max_symbol_exposure_amount == 1_555_555

    def test_new_buy_notional_int(self):
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=50_000, quantity=10,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert r.new_buy_notional == 500_000

    def test_projected_with_current_zero(self):
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=100_000, quantity=5,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert r.projected_symbol_exposure_amount == 500_000

    def test_negative_current_clamped_to_zero(self):
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=100_000, quantity=1,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=-50_000,
            max_symbol_weight_pct=0.20,
        )
        assert r.current_symbol_exposure_amount == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. NOT_APPLICABLE (검증 13, 14)
# ─────────────────────────────────────────────────────────────────────────────


class TestNotApplicable:
    @pytest.mark.parametrize(
        "side",
        ["SELL", "EXIT", "CLOSE", "HOLD", "NO_OP", None, "", "sell"],
    )
    def test_non_buy_skipped(self, side):
        r = check_symbol_weight_limit(
            side=side, symbol="005930",
            price=100_000, quantity=10,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=1_900_000,
            max_symbol_weight_pct=0.20,
        )
        assert r.allowed is True
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_NOT_APPLICABLE
        assert "BUY 가 아니므로" in r.reason_message


# ─────────────────────────────────────────────────────────────────────────────
# 5. Invalid inputs (검증 15-19)
# ─────────────────────────────────────────────────────────────────────────────


class TestInvalidInputs:
    def test_quantity_zero(self):
        # 검증 15.
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=100_000, quantity=0,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert r.allowed is False
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_INVALID_QUANTITY

    def test_quantity_negative(self):
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=100_000, quantity=-1,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_INVALID_QUANTITY

    def test_quantity_fractional(self):
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=100_000, quantity=1.5,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_INVALID_QUANTITY

    def test_price_zero(self):
        # 검증 16.
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=0, quantity=1,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_INVALID_PRICE

    def test_price_negative(self):
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=-1, quantity=1,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_INVALID_PRICE

    def test_price_none(self):
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=None, quantity=1,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_INVALID_PRICE

    def test_total_equity_zero(self):
        # 검증 17.
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=100_000, quantity=1,
            total_paper_equity=0,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert r.allowed is False
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_INVALID_TOTAL_EQUITY

    def test_total_equity_negative(self):
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=100_000, quantity=1,
            total_paper_equity=-1_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_INVALID_TOTAL_EQUITY

    def test_pct_zero(self):
        # 검증 18.
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=100_000, quantity=1,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0,
        )
        assert r.allowed is False
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_INVALID_WEIGHT

    def test_pct_negative(self):
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=100_000, quantity=1,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=-0.1,
        )
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_INVALID_WEIGHT

    def test_pct_greater_than_one(self):
        # 검증 19.
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=100_000, quantity=1,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=1.5,
        )
        assert r.allowed is False
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_INVALID_WEIGHT

    def test_pct_exactly_1_allowed(self):
        # 경계: pct=1.0 — 100% (단일 종목 몰빵 허용 — degenerate 케이스).
        r = check_symbol_weight_limit(
            side="BUY", symbol="X",
            price=100_000, quantity=1,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=1.0,
        )
        assert r.reason_code == SYMBOL_WEIGHT_LIMIT_OK


# ─────────────────────────────────────────────────────────────────────────────
# 6. 차단된 BUY 가 노출 미반영 (검증 12)
# ─────────────────────────────────────────────────────────────────────────────


class TestBlockedBuyDoesNotIncrement:
    def test_blocked_does_not_affect_current_exposure(self):
        # caller 가 차단된 BUY 를 current 에 *반영하지 않으면* 다음 check 도
        # 동일 current 로 carry — pure function 이므로 caller 책임.
        before = 1_600_000
        r1 = check_symbol_weight_limit(
            side="BUY", symbol="X", price=100_000, quantity=7,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=before,
            max_symbol_weight_pct=0.20,
        )
        assert r1.allowed is False
        # 같은 current 로 더 작은 BUY → 허용.
        r2 = check_symbol_weight_limit(
            side="BUY", symbol="X", price=100_000, quantity=3,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=before,
            max_symbol_weight_pct=0.20,
        )
        assert r2.allowed is True
        assert r2.current_symbol_exposure_amount == before


# ─────────────────────────────────────────────────────────────────────────────
# 7. calculate_current_symbol_exposure_amount (검증 20)
# ─────────────────────────────────────────────────────────────────────────────


class TestCalculateCurrentExposure:
    def test_only_filled_accepted_buy_counted(self):
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "notional_krw": 500_000},
            {"symbol": "005930", "side": "BUY", "status": "ACCEPTED",
             "notional_krw": 300_000},
        ]
        total = calculate_current_symbol_exposure_amount(
            orders, symbol="005930",
        )
        assert total == 800_000

    def test_sell_subtracts_from_exposure(self):
        # BUY 500k + 500k - SELL 300k = 700k.
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "notional_krw": 500_000},
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "notional_krw": 500_000},
            {"symbol": "005930", "side": "SELL", "status": "FILLED",
             "notional_krw": 300_000},
        ]
        total = calculate_current_symbol_exposure_amount(
            orders, symbol="005930",
        )
        assert total == 700_000

    def test_rejected_cancelled_blocked_excluded(self):
        # 검증 20.
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "notional_krw": 500_000},
            {"symbol": "005930", "side": "BUY", "status": "REJECTED",
             "notional_krw": 1_000_000},
            {"symbol": "005930", "side": "BUY", "status": "CANCELLED",
             "notional_krw": 2_000_000},
            {"symbol": "005930", "side": "BUY", "status": "BLOCKED",
             "notional_krw": 3_000_000},
        ]
        total = calculate_current_symbol_exposure_amount(
            orders, symbol="005930",
        )
        assert total == 500_000

    def test_different_symbol_excluded(self):
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "notional_krw": 500_000},
            {"symbol": "000660", "side": "BUY", "status": "FILLED",
             "notional_krw": 999_999},
        ]
        total = calculate_current_symbol_exposure_amount(
            orders, symbol="005930",
        )
        assert total == 500_000

    def test_uses_price_quantity_fallback(self):
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "price": 100_000, "quantity": 5},
        ]
        total = calculate_current_symbol_exposure_amount(
            orders, symbol="005930",
        )
        assert total == 500_000

    def test_last_price_re_evaluation(self):
        # last_price 가 주어지면 quantity × last_price 로 *현재 평가금액* 재계산.
        # filled price 75k × 10 = 750k 였지만 last_price 100k → 1,000,000.
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "price": 75_000, "quantity": 10},
        ]
        total = calculate_current_symbol_exposure_amount(
            orders, symbol="005930", last_price=100_000,
        )
        assert total == 1_000_000

    def test_status_none_excluded(self):
        orders = [
            {"symbol": "005930", "side": "BUY", "notional_krw": 500_000},
        ]
        assert calculate_current_symbol_exposure_amount(
            orders, symbol="005930",
        ) == 0

    def test_empty_orders_returns_zero(self):
        assert calculate_current_symbol_exposure_amount(
            None, symbol="005930",
        ) == 0
        assert calculate_current_symbol_exposure_amount(
            [], symbol="005930",
        ) == 0

    def test_sell_only_does_not_make_negative(self):
        # SELL 만 있고 buy 가 없는 비정상 케이스 — 0 clamp.
        orders = [
            {"symbol": "005930", "side": "SELL", "status": "FILLED",
             "notional_krw": 500_000},
        ]
        total = calculate_current_symbol_exposure_amount(
            orders, symbol="005930",
        )
        assert total == 0

    def test_works_with_dataclass_like_objects(self):
        from types import SimpleNamespace
        orders = [
            SimpleNamespace(
                symbol="005930", side="BUY", status="FILLED",
                notional_krw=500_000,
            ),
        ]
        total = calculate_current_symbol_exposure_amount(
            orders, symbol="005930",
        )
        assert total == 500_000


# ─────────────────────────────────────────────────────────────────────────────
# 8. resolve_symbol_weight_limit_pct
# ─────────────────────────────────────────────────────────────────────────────


class TestResolvePct:
    def test_default_system(self):
        assert DEFAULT_MAX_SYMBOL_WEIGHT_PCT == 0.20
        pct, source = resolve_symbol_weight_limit_pct()
        assert pct == 0.20
        assert source == "system_default"

    def test_manual_priority(self):
        pct, source = resolve_symbol_weight_limit_pct(
            manual_max_symbol_weight_pct=0.15,
            risk_profile="CONSERVATIVE",
        )
        assert pct == 0.15
        assert source == "manual"

    def test_manual_invalid_falls_through(self):
        pct, source = resolve_symbol_weight_limit_pct(
            manual_max_symbol_weight_pct=0,
            risk_profile="AGGRESSIVE",
        )
        assert source == "risk_profile"
        assert pct == 0.30

    def test_manual_over_1_falls_through(self):
        pct, source = resolve_symbol_weight_limit_pct(
            manual_max_symbol_weight_pct=1.5,
            risk_profile="BALANCED",
        )
        assert source == "risk_profile"
        assert pct == 0.20

    def test_risk_profile_mapping(self):
        # 검증 23.
        for profile, expected in [
            ("CONSERVATIVE", 0.10),
            ("BALANCED",     0.20),
            ("AGGRESSIVE",   0.30),
        ]:
            pct, source = resolve_symbol_weight_limit_pct(
                risk_profile=profile,
            )
            assert pct == expected, profile
            assert source == "risk_profile", profile

    def test_unknown_profile_falls_through_to_default(self):
        pct, source = resolve_symbol_weight_limit_pct(
            risk_profile="GAMBLE",
        )
        assert pct == DEFAULT_MAX_SYMBOL_WEIGHT_PCT
        assert source == "system_default"


# ─────────────────────────────────────────────────────────────────────────────
# 9. P-07 / P-08 / P-09 / P-10 통합 (검증 21-24)
# ─────────────────────────────────────────────────────────────────────────────


class TestP07Independence:
    def test_cash_check_and_weight_check_independent(self):
        # 검증 21: P-07 cash 와 P-11 weight 는 별개 reason_code.
        from app.auto_paper.capital_state import (
            CashCheckVerdict,
            check_buy_cash_sufficient,
        )

        # cash 충분 + weight 초과 → P-07 OK / P-11 EXCEEDED.
        cash = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=7,
            available_cash_krw=10_000_000,
        )
        weight = check_symbol_weight_limit(
            side="BUY", symbol="005930",
            price=100_000, quantity=7,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=1_600_000,
            max_symbol_weight_pct=0.20,
        )
        assert cash.verdict == CashCheckVerdict.ALLOWED
        assert weight.allowed is False
        # 두 reason_code 가 *별개*.
        assert weight.reason_code != cash.verdict.value


class TestP08Integration:
    def test_p08_quantity_used_for_weight_check(self):
        # 검증 22: P-08 sizer 의 quantity 를 P-11 입력으로 carry.
        from app.auto_paper.position_sizer import compute_paper_quantity_by_price

        # 종목당 한도 200만 / 1주 100k → 20주.
        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=100_000, max_amount_krw=2_000_000,
        )
        assert sizer.quantity == 20

        # current=500k, new=2M → projected=2.5M > 2M cap → 차단.
        weight = check_symbol_weight_limit(
            side="BUY", symbol="005930",
            price=100_000, quantity=sizer.quantity,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=500_000,
            max_symbol_weight_pct=0.20,
        )
        assert weight.allowed is False
        assert weight.reason_code == SYMBOL_WEIGHT_LIMIT_EXCEEDED


class TestP09Integration:
    def test_aggressive_profile_30_pct(self):
        # 검증 23: P-09 공격형 30%.
        pct, _ = resolve_symbol_weight_limit_pct(risk_profile="AGGRESSIVE")
        # total 10M × 30% = 3M.
        weight = check_symbol_weight_limit(
            side="BUY", symbol="005930",
            price=100_000, quantity=10,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=2_000_000,
            max_symbol_weight_pct=pct,
        )
        # current 2M + new 1M = 3M == 3M → 허용 (경계).
        assert weight.allowed is True
        assert weight.max_symbol_exposure_amount == 3_000_000

    def test_conservative_profile_10_pct_blocks_lower(self):
        pct, _ = resolve_symbol_weight_limit_pct(risk_profile="CONSERVATIVE")
        # total 10M × 10% = 1M.
        weight = check_symbol_weight_limit(
            side="BUY", symbol="005930",
            price=100_000, quantity=5,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=600_000,
            max_symbol_weight_pct=pct,
        )
        # current 600k + new 500k = 1.1M > 1M → 차단.
        assert weight.allowed is False


class TestP10Independence:
    def test_daily_limit_and_weight_limit_distinct_reasons(self):
        # 검증 24: P-10 일일 한도와 P-11 종목 비중은 *별개 reason_code*.
        from app.risk.loss_limits import (
            DAILY_BUY_LIMIT_EXCEEDED,
            check_daily_buy_limit,
        )

        # case A: daily 초과 + weight OK.
        daily_a = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=7,
            today_buy_used_amount=2_500_000,
            max_daily_buy_amount=3_000_000,
        )
        weight_a = check_symbol_weight_limit(
            side="BUY", symbol="005930",
            price=100_000, quantity=7,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=0,
            max_symbol_weight_pct=0.20,
        )
        assert daily_a.reason_code == DAILY_BUY_LIMIT_EXCEEDED
        assert weight_a.allowed is True

        # case B: daily OK + weight 초과.
        daily_b = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=7,
            today_buy_used_amount=0,
            max_daily_buy_amount=3_000_000,
        )
        weight_b = check_symbol_weight_limit(
            side="BUY", symbol="005930",
            price=100_000, quantity=7,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=1_600_000,
            max_symbol_weight_pct=0.20,
        )
        assert daily_b.allowed is True
        assert weight_b.reason_code == SYMBOL_WEIGHT_LIMIT_EXCEEDED
        # 두 reason_code 가 명확히 구분됨.
        assert weight_b.reason_code != DAILY_BUY_LIMIT_EXCEEDED


# ─────────────────────────────────────────────────────────────────────────────
# 10. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestApiEndpoints:
    def test_preview_user_spec_example(self, api_client):
        r = api_client.post(
            "/api/auto-paper/symbol-weight-limit/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "price": 100_000, "quantity": 7,
                "current_symbol_exposure_amount": 1_600_000,
                "total_paper_equity_krw": 10_000_000,
                "max_symbol_weight_pct": 0.20,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["allowed"] is False
        assert body["reason_code"] == "SYMBOL_WEIGHT_LIMIT_EXCEEDED"
        assert body["max_symbol_exposure_amount"] == 2_000_000
        assert body["projected_symbol_exposure_amount"] == 2_300_000
        assert "종목별 최대 비중을 초과하여 매수 차단" in body["reason_message"]
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False

    def test_preview_allowed(self, api_client):
        r = api_client.post(
            "/api/auto-paper/symbol-weight-limit/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "price": 100_000, "quantity": 5,
                "current_symbol_exposure_amount": 0,
                "total_paper_equity_krw": 10_000_000,
                "max_symbol_weight_pct": 0.20,
            },
        )
        body = r.json()
        assert body["allowed"] is True

    def test_preview_uses_p09_when_pct_omitted(self, api_client):
        r = api_client.post(
            "/api/auto-paper/symbol-weight-limit/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "price": 100_000, "quantity": 5,
                "current_symbol_exposure_amount": 0,
                "total_paper_equity_krw": 10_000_000,
                "risk_profile": "AGGRESSIVE",
            },
        )
        body = r.json()
        assert body["resolved_source"] == "risk_profile"
        assert body["max_symbol_weight_pct"] == 0.30
        assert body["max_symbol_exposure_amount"] == 3_000_000

    def test_preview_falls_back_to_system_default(self, api_client):
        r = api_client.post(
            "/api/auto-paper/symbol-weight-limit/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "price": 100_000, "quantity": 1,
                "current_symbol_exposure_amount": 0,
                "total_paper_equity_krw": 10_000_000,
            },
        )
        body = r.json()
        assert body["resolved_source"] == "system_default"
        assert body["max_symbol_weight_pct"] == 0.20

    def test_preview_sell_not_applicable(self, api_client):
        r = api_client.post(
            "/api/auto-paper/symbol-weight-limit/preview",
            json={
                "side": "SELL", "symbol": "005930",
                "price": 100_000, "quantity": 5,
                "current_symbol_exposure_amount": 1_900_000,
                "total_paper_equity_krw": 10_000_000,
                "max_symbol_weight_pct": 0.20,
            },
        )
        body = r.json()
        assert body["allowed"] is True
        assert body["reason_code"] == "SYMBOL_WEIGHT_LIMIT_NOT_APPLICABLE"

    def test_resolve_endpoint(self, api_client):
        r = api_client.post(
            "/api/auto-paper/symbol-weight-limit/resolve",
            json={"risk_profile": "AGGRESSIVE"},
        )
        body = r.json()
        assert body["max_symbol_weight_pct"] == 0.30
        assert body["source"] == "risk_profile"

    def test_resolve_manual_priority(self, api_client):
        r = api_client.post(
            "/api/auto-paper/symbol-weight-limit/resolve",
            json={
                "manual_max_symbol_weight_pct": 0.25,
                "risk_profile": "AGGRESSIVE",
            },
        )
        body = r.json()
        assert body["max_symbol_weight_pct"] == 0.25
        assert body["source"] == "manual"

    def test_preview_uses_initial_cash_when_total_omitted(self, api_client):
        # PaperCapitalConfig default initial_cash = 10,000,000.
        r = api_client.post(
            "/api/auto-paper/symbol-weight-limit/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "price": 100_000, "quantity": 1,
                "current_symbol_exposure_amount": 0,
            },
        )
        body = r.json()
        assert body["total_paper_equity"] == 10_000_000


# ─────────────────────────────────────────────────────────────────────────────
# 11. 정적 가드 (검증 25)
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_module_file_exists(self):
        assert _MODULE_PATH.exists()

    def test_no_broker_call_patterns_in_p11_section(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        marker = "# P-11: 종목별 최대 비중 제한 (Symbol Weight Limit)"
        idx = text.find(marker)
        assert idx >= 0
        section = text[idx:]
        for pat in (
            r"\bbroker\.place_order\s*\(",
            r"\bbroker\.cancel_order\s*\(",
            r"\broute_order\s*\(",
            r"OrderExecutor\s*\(",
            r"OrderRequest\s*\(",
            r"\.enable_live_trading\s*=",
            r"\.enable_ai_execution\s*=",
        ):
            assert not re.search(pat, section), (
                f"P-11 코드 영역 금지 패턴: /{pat}/"
            )

    def test_no_safety_flag_mutations(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        marker = "# P-11: 종목별 최대 비중 제한 (Symbol Weight Limit)"
        section = text[text.find(marker):]
        for pat in [
            r"ENABLE_LIVE_TRADING\s*=\s*True",
            r"ENABLE_AI_EXECUTION\s*=\s*True",
            r"KIS_IS_PAPER\s*=\s*False",
        ]:
            assert not re.search(pat, section), (
                f"P-11 안전 flag mutation 의심: /{pat}/"
            )

    def test_p11_helpers_exported(self):
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
        }
        for required in [
            "SymbolWeightLimitResult",
            "check_symbol_weight_limit",
            "calculate_current_symbol_exposure_amount",
        ]:
            assert required in names, (
                f"{required} 가 position_limits.py 에 없음"
            )

    def test_existing_position_limit_rule_preserved(self):
        # 회귀: 기존 #35 PositionLimitRule / PositionLimitPolicy 보존.
        from app.risk.position_limits import (
            PositionLimitPolicy,
            PositionLimitRule,
        )
        pol = PositionLimitPolicy()
        rule = PositionLimitRule(pol)
        assert rule is not None
