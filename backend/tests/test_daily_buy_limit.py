"""P-10: 일일 최대 신규 매수금액 한도 — 단위 + 정적 + API + 통합 테스트.

검증 항목 (사용자 요청서 §8 1-23):
 1. BUY + 한도 이내 → allowed=True
 2. BUY + 한도 초과 → allowed=False
 3. 차단 reason_code = DAILY_BUY_LIMIT_EXCEEDED
 4. 메시지에 "일일 최대 매수금액을 초과하여 매수 차단" 포함
 5. buy_notional = price * quantity
 6. projected = today_used + buy_notional
 7. remaining_daily_buy_amount 계산
 8. today=2.5M / new=700k / max=3M → 차단
 9. today=2.3M / new=700k / max=3M → 허용
 10. projected == max → 허용 (경계)
 11. 차단된 BUY 는 today_buy_used_amount 미반영
 12. SELL → NOT_APPLICABLE
 13. HOLD → NOT_APPLICABLE
 14. quantity=0 → invalid
 15. price<=0 → invalid
 16. max<=0 → 안전 차단
 17. KST 날짜 기준 합산
 18. rejected / cancelled / blocked 제외
 19. filled / accepted BUY 만 합산
 20. P-07 과 충돌 없음
 21. P-08 결과 quantity 사용 가능
 22. P-09 risk profile 값 사용 가능
 23. 정적 grep — 실거래 / LIVE 미변경
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
    DEFAULT_DAILY_BUY_LIMIT_KRW,
    reset_paper_capital_for_tests,
    resolve_daily_buy_limit,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.risk.loss_limits import (
    DAILY_BUY_LIMIT_EXCEEDED,
    DAILY_BUY_LIMIT_INVALID_INPUT,
    DAILY_BUY_LIMIT_NOT_APPLICABLE,
    DAILY_BUY_LIMIT_OK,
    DailyBuyLimitResult,
    calculate_today_buy_used_amount,
    check_daily_buy_limit,
)


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "risk" / "loss_limits.py"
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
# 1. DailyBuyLimitResult dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestDataclassInvariants:
    def test_is_paper_only_must_be_true(self):
        with pytest.raises(ValueError, match="is_paper_only"):
            DailyBuyLimitResult(
                allowed=True, reason_code=DAILY_BUY_LIMIT_OK,
                reason_message="x",
                max_daily_buy_amount=0, today_buy_used_amount=0,
                new_buy_notional=0, projected_today_buy_amount=0,
                remaining_daily_buy_amount=0,
                is_paper_only=False,  # type: ignore[arg-type]
            )

    def test_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            DailyBuyLimitResult(
                allowed=True, reason_code=DAILY_BUY_LIMIT_OK,
                reason_message="x",
                max_daily_buy_amount=0, today_buy_used_amount=0,
                new_buy_notional=0, projected_today_buy_amount=0,
                remaining_daily_buy_amount=0,
                is_order_signal=True,  # type: ignore[arg-type]
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            DailyBuyLimitResult(
                allowed=True, reason_code=DAILY_BUY_LIMIT_OK,
                reason_message="x",
                max_daily_buy_amount=0, today_buy_used_amount=0,
                new_buy_notional=0, projected_today_buy_amount=0,
                remaining_daily_buy_amount=0,
                is_live_authorization=True,  # type: ignore[arg-type]
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. 사용자 요청서 예시 (검증 8, 9, 10) — 핵심 매트릭스
# ─────────────────────────────────────────────────────────────────────────────


class TestUserSpecScenarios:
    def test_user_example_blocks_when_projected_exceeds(self):
        # 검증 8: today=2.5M, new=700k, max=3M → 차단.
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=7,
            today_buy_used_amount=2_500_000,
            max_daily_buy_amount=3_000_000,
        )
        assert r.allowed is False
        # 검증 3.
        assert r.reason_code == DAILY_BUY_LIMIT_EXCEEDED
        # 검증 5: buy_notional = 100k × 7 = 700k.
        assert r.new_buy_notional == 700_000
        # 검증 6: projected = 2.5M + 700k = 3.2M.
        assert r.projected_today_buy_amount == 3_200_000
        # 검증 4: 메시지 정확 문구.
        assert "일일 최대 매수금액을 초과하여 매수 차단" in r.reason_message

    def test_allows_when_projected_below_max(self):
        # 검증 9: today=2.3M, new=700k, max=3M → 허용.
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=7,
            today_buy_used_amount=2_300_000,
            max_daily_buy_amount=3_000_000,
        )
        assert r.allowed is True
        assert r.reason_code == DAILY_BUY_LIMIT_OK
        # 검증 7: remaining = 3M - (2.3M + 700k) = 0.
        assert r.remaining_daily_buy_amount == 0

    def test_exactly_at_max_is_allowed(self):
        # 검증 10: projected == max → 허용 (경계 정책).
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=5,
            today_buy_used_amount=2_500_000,
            max_daily_buy_amount=3_000_000,
        )
        assert r.allowed is True
        assert r.reason_code == DAILY_BUY_LIMIT_OK
        assert r.projected_today_buy_amount == 3_000_000
        assert r.remaining_daily_buy_amount == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. 계산 정확성 (검증 5-7)
# ─────────────────────────────────────────────────────────────────────────────


class TestCalculation:
    def test_buy_notional_is_price_times_quantity(self):
        r = check_daily_buy_limit(
            side="BUY", price=50_000, quantity=10,
            today_buy_used_amount=0, max_daily_buy_amount=1_000_000,
        )
        assert r.new_buy_notional == 500_000

    def test_projected_calculation(self):
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=3,
            today_buy_used_amount=500_000,
            max_daily_buy_amount=1_000_000,
        )
        assert r.projected_today_buy_amount == 500_000 + 300_000  # 800_000

    def test_remaining_daily_buy_amount(self):
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=3,
            today_buy_used_amount=400_000,
            max_daily_buy_amount=1_000_000,
        )
        assert r.remaining_daily_buy_amount == 1_000_000 - (400_000 + 300_000)


# ─────────────────────────────────────────────────────────────────────────────
# 4. SELL / HOLD / NO_ACTION → NOT_APPLICABLE (검증 12, 13)
# ─────────────────────────────────────────────────────────────────────────────


class TestNotApplicableForNonBuy:
    @pytest.mark.parametrize(
        "side",
        ["SELL", "EXIT", "CLOSE", "HOLD", "NO_OP", "NO_ACTION",
         None, "", "sell", "hold"],
    )
    def test_non_buy_returns_not_applicable(self, side):
        # 검증 12 / 13.
        r = check_daily_buy_limit(
            side=side, price=100_000, quantity=10,
            today_buy_used_amount=2_500_000,
            max_daily_buy_amount=3_000_000,
        )
        assert r.allowed is True
        assert r.reason_code == DAILY_BUY_LIMIT_NOT_APPLICABLE
        assert "BUY 가 아니므로" in r.reason_message

    def test_buy_synonyms_accepted(self):
        # 동일 의미 토큰도 BUY 처리.
        for tok in ("OPEN", "open_long", "long", "ENTER", "entry"):
            r = check_daily_buy_limit(
                side=tok, price=100_000, quantity=1,
                today_buy_used_amount=0,
                max_daily_buy_amount=1_000_000,
            )
            assert r.reason_code == DAILY_BUY_LIMIT_OK


# ─────────────────────────────────────────────────────────────────────────────
# 5. Invalid inputs (검증 14, 15, 16)
# ─────────────────────────────────────────────────────────────────────────────


class TestInvalidInputs:
    def test_quantity_zero(self):
        # 검증 14.
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=0,
            today_buy_used_amount=0, max_daily_buy_amount=1_000_000,
        )
        assert r.reason_code == DAILY_BUY_LIMIT_INVALID_INPUT
        assert r.allowed is False

    def test_quantity_negative(self):
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=-1,
            today_buy_used_amount=0, max_daily_buy_amount=1_000_000,
        )
        assert r.reason_code == DAILY_BUY_LIMIT_INVALID_INPUT

    def test_quantity_fractional(self):
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=1.5,
            today_buy_used_amount=0, max_daily_buy_amount=1_000_000,
        )
        assert r.reason_code == DAILY_BUY_LIMIT_INVALID_INPUT

    def test_price_zero(self):
        # 검증 15.
        r = check_daily_buy_limit(
            side="BUY", price=0, quantity=1,
            today_buy_used_amount=0, max_daily_buy_amount=1_000_000,
        )
        assert r.reason_code == DAILY_BUY_LIMIT_INVALID_INPUT

    def test_price_negative(self):
        r = check_daily_buy_limit(
            side="BUY", price=-100, quantity=1,
            today_buy_used_amount=0, max_daily_buy_amount=1_000_000,
        )
        assert r.reason_code == DAILY_BUY_LIMIT_INVALID_INPUT

    def test_price_none(self):
        r = check_daily_buy_limit(
            side="BUY", price=None, quantity=1,
            today_buy_used_amount=0, max_daily_buy_amount=1_000_000,
        )
        assert r.reason_code == DAILY_BUY_LIMIT_INVALID_INPUT

    def test_max_zero_safely_blocks(self):
        # 검증 16: max<=0 → 안전 차단.
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=1,
            today_buy_used_amount=0, max_daily_buy_amount=0,
        )
        assert r.allowed is False
        assert r.reason_code == DAILY_BUY_LIMIT_INVALID_INPUT

    def test_max_negative_safely_blocks(self):
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=1,
            today_buy_used_amount=0, max_daily_buy_amount=-1,
        )
        assert r.allowed is False
        assert r.reason_code == DAILY_BUY_LIMIT_INVALID_INPUT

    def test_negative_today_used_clamped(self):
        # today_used 가 음수면 0 으로 clamp + 진행.
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=1,
            today_buy_used_amount=-500_000,
            max_daily_buy_amount=1_000_000,
        )
        assert r.allowed is True
        assert r.today_buy_used_amount == 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. 차단된 BUY 가 today_buy_used_amount 미반영 (검증 11)
# ─────────────────────────────────────────────────────────────────────────────


class TestBlockedBuyDoesNotIncrement:
    def test_blocked_buy_does_not_affect_today_used(self):
        # 검증 11: caller 가 차단된 BUY 를 today_used 에 *반영하지 않으면*
        # 다음 check 에서도 동일 today_used 가 carry 됨. 본 모듈은 pure
        # function — caller 가 결정.
        before_used = 2_500_000
        r1 = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=7,
            today_buy_used_amount=before_used,
            max_daily_buy_amount=3_000_000,
        )
        assert r1.allowed is False
        # 차단된 BUY 는 today_used 에 더해지지 않음 — 같은 today_used 로
        # 다른 (더 작은) BUY 호출하면 정상 허용.
        r2 = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=4,
            today_buy_used_amount=before_used,  # *변화 없음*
            max_daily_buy_amount=3_000_000,
        )
        assert r2.allowed is True
        assert r2.today_buy_used_amount == before_used


# ─────────────────────────────────────────────────────────────────────────────
# 7. calculate_today_buy_used_amount (검증 17-19)
# ─────────────────────────────────────────────────────────────────────────────


class TestCalculateTodayBuyUsed:
    def test_only_filled_buy_orders_counted(self):
        # 검증 19: filled / accepted BUY 만 합산.
        orders = [
            {"side": "BUY", "status": "FILLED", "notional_krw": 500_000,
             "kst_date": "2026-05-22"},
            {"side": "BUY", "status": "ACCEPTED", "notional_krw": 300_000,
             "kst_date": "2026-05-22"},
        ]
        total = calculate_today_buy_used_amount(orders, today_kst="2026-05-22")
        assert total == 800_000

    def test_rejected_cancelled_blocked_excluded(self):
        # 검증 18.
        orders = [
            {"side": "BUY", "status": "FILLED", "notional_krw": 500_000,
             "kst_date": "2026-05-22"},
            {"side": "BUY", "status": "REJECTED", "notional_krw": 1_000_000,
             "kst_date": "2026-05-22"},
            {"side": "BUY", "status": "CANCELLED", "notional_krw": 2_000_000,
             "kst_date": "2026-05-22"},
            {"side": "BUY", "status": "BLOCKED", "notional_krw": 3_000_000,
             "kst_date": "2026-05-22"},
        ]
        total = calculate_today_buy_used_amount(orders, today_kst="2026-05-22")
        assert total == 500_000

    def test_different_date_excluded(self):
        # 검증 17: 오늘 (KST) 만 합산.
        orders = [
            {"side": "BUY", "status": "FILLED", "notional_krw": 500_000,
             "kst_date": "2026-05-22"},
            {"side": "BUY", "status": "FILLED", "notional_krw": 9_999_999,
             "kst_date": "2026-05-21"},   # 어제
            {"side": "BUY", "status": "FILLED", "notional_krw": 9_999_999,
             "kst_date": "2026-05-23"},   # 내일
        ]
        total = calculate_today_buy_used_amount(orders, today_kst="2026-05-22")
        assert total == 500_000

    def test_sell_excluded(self):
        orders = [
            {"side": "BUY",  "status": "FILLED", "notional_krw": 500_000,
             "kst_date": "2026-05-22"},
            {"side": "SELL", "status": "FILLED", "notional_krw": 1_000_000,
             "kst_date": "2026-05-22"},
        ]
        total = calculate_today_buy_used_amount(orders, today_kst="2026-05-22")
        assert total == 500_000

    def test_uses_price_quantity_fallback(self):
        # notional 명시 안 되면 price × quantity 로 계산.
        orders = [
            {"side": "BUY", "status": "FILLED",
             "price": 100_000, "quantity": 5,
             "kst_date": "2026-05-22"},
        ]
        total = calculate_today_buy_used_amount(orders, today_kst="2026-05-22")
        assert total == 500_000

    def test_empty_orders_returns_zero(self):
        assert calculate_today_buy_used_amount([], today_kst="2026-05-22") == 0
        assert calculate_today_buy_used_amount(None, today_kst="2026-05-22") == 0

    def test_status_none_excluded(self):
        # status 미상 — 보수적으로 제외.
        orders = [
            {"side": "BUY", "notional_krw": 500_000, "kst_date": "2026-05-22"},
        ]
        total = calculate_today_buy_used_amount(orders, today_kst="2026-05-22")
        assert total == 0

    def test_works_with_dataclass_like_objects(self):
        from types import SimpleNamespace
        orders = [
            SimpleNamespace(
                side="BUY", status="FILLED",
                notional_krw=500_000, kst_date="2026-05-22",
            ),
            SimpleNamespace(
                side="SELL", status="FILLED",
                notional_krw=1_000_000, kst_date="2026-05-22",
            ),
        ]
        total = calculate_today_buy_used_amount(orders, today_kst="2026-05-22")
        assert total == 500_000

    def test_created_at_iso_datetime_parsed(self):
        # created_at ISO 형태 — 앞 10자 (YYYY-MM-DD) 만 비교.
        orders = [
            {"side": "BUY", "status": "FILLED", "notional_krw": 500_000,
             "created_at": "2026-05-22T01:23:45+09:00"},
            {"side": "BUY", "status": "FILLED", "notional_krw": 999_999,
             "created_at": "2026-05-21T01:23:45+09:00"},
        ]
        total = calculate_today_buy_used_amount(orders, today_kst="2026-05-22")
        assert total == 500_000


# ─────────────────────────────────────────────────────────────────────────────
# 8. resolve_daily_buy_limit (capital_config layer)
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveDailyBuyLimit:
    def test_default_system_value(self):
        # 사용자 요청서 §2: 시스템 기본값 3,000,000원.
        assert DEFAULT_DAILY_BUY_LIMIT_KRW == 3_000_000
        amount, source = resolve_daily_buy_limit()
        assert amount == 3_000_000
        assert source == "system_default"

    def test_manual_value_takes_priority(self):
        amount, source = resolve_daily_buy_limit(
            manual_daily_buy_limit_krw=5_000_000,
            risk_profile="CONSERVATIVE",
            total_paper_capital_krw=10_000_000,
        )
        assert amount == 5_000_000
        assert source == "manual"

    def test_manual_zero_or_negative_falls_through(self):
        # 0 / 음수는 미주입과 동일 — fallthrough.
        amount, source = resolve_daily_buy_limit(
            manual_daily_buy_limit_krw=0,
            risk_profile="BALANCED",
            total_paper_capital_krw=10_000_000,
        )
        assert source == "risk_profile"
        assert amount == 5_000_000  # P-09 안정형 1,000만 × 50%

    def test_p09_risk_profile_when_no_manual(self):
        # 검증 22: P-09 risk profile 값 사용 가능.
        for profile, expected in [
            ("CONSERVATIVE", 2_000_000),
            ("BALANCED",     5_000_000),
            ("AGGRESSIVE",   8_000_000),
        ]:
            amount, source = resolve_daily_buy_limit(
                risk_profile=profile,
                total_paper_capital_krw=10_000_000,
            )
            assert amount == expected, profile
            assert source == "risk_profile", profile

    def test_unknown_profile_falls_through_to_default(self):
        # invalid profile 은 P-09 가 BALANCED fallback 반환 → 5M.
        amount, source = resolve_daily_buy_limit(
            risk_profile="GAMBLE",
            total_paper_capital_krw=10_000_000,
        )
        assert source == "risk_profile"
        assert amount == 5_000_000


# ─────────────────────────────────────────────────────────────────────────────
# 9. P-08 / P-09 통합 (검증 21, 22)
# ─────────────────────────────────────────────────────────────────────────────


class TestP08P09Integration:
    def test_p08_quantity_used_for_daily_check(self):
        # 검증 21: P-08 sizing 결과 quantity 를 daily check 에 carry.
        from app.auto_paper.position_sizer import compute_paper_quantity_by_price

        # 안정형 종목당 100만 → 1주 100k → 10주.
        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=100_000, max_amount_krw=1_000_000,
        )
        assert sizer.quantity == 10

        # 일일 한도 5,000,000원 + today_used 4,500,000원 → 새 1,000,000 시도 → 차단.
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=sizer.quantity,
            today_buy_used_amount=4_500_000,
            max_daily_buy_amount=5_000_000,
        )
        assert r.allowed is False
        assert r.reason_code == DAILY_BUY_LIMIT_EXCEEDED

    def test_p09_balanced_profile_resolves_5m(self):
        # 검증 22: P-09 안정형 5M / 총 1천만.
        amount, source = resolve_daily_buy_limit(
            risk_profile="BALANCED",
            total_paper_capital_krw=10_000_000,
        )
        # 본 한도로 BUY 검사.
        r = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=20,
            today_buy_used_amount=4_000_000,
            max_daily_buy_amount=amount,
        )
        assert r.allowed is False  # 4M + 2M = 6M > 5M
        assert r.reason_code == DAILY_BUY_LIMIT_EXCEEDED


# ─────────────────────────────────────────────────────────────────────────────
# 10. P-07 충돌 없음 (검증 20)
# ─────────────────────────────────────────────────────────────────────────────


class TestP07Independence:
    def test_daily_check_independent_of_cash_check(self):
        # 검증 20: cash check (P-07) 와 daily check (P-10) 는 독립적.
        # 두 check 모두 *별개* reason_code 로 결과 반환.
        from app.auto_paper.capital_state import (
            CashCheckVerdict,
            check_buy_cash_sufficient,
        )

        # case A: cash 충분 + daily 초과 → P-07 OK / P-10 EXCEEDED.
        cash = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=7,
            available_cash_krw=10_000_000,
        )
        daily = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=7,
            today_buy_used_amount=2_500_000,
            max_daily_buy_amount=3_000_000,
        )
        assert cash.verdict == CashCheckVerdict.ALLOWED
        assert daily.allowed is False
        # 두 reason_code 가 *별개* (구분된 enum) 임 검증.
        assert daily.reason_code != cash.verdict.value
        assert daily.reason_code == DAILY_BUY_LIMIT_EXCEEDED

        # case B: cash 부족 + daily 통과 → P-07 INSUFFICIENT / P-10 OK.
        cash2 = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=7,
            available_cash_krw=100_000,
        )
        daily2 = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=7,
            today_buy_used_amount=0,
            max_daily_buy_amount=3_000_000,
        )
        assert cash2.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH
        assert daily2.allowed is True


# ─────────────────────────────────────────────────────────────────────────────
# 11. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestApiEndpoints:
    def test_preview_user_spec_example(self, api_client):
        # 사용자 요청서 §1 예시 — 1주 100k × 7 = 700k.
        r = api_client.post(
            "/api/auto-paper/daily-buy-limit/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "price": 100_000, "quantity": 7,
                "today_buy_used_amount_krw": 2_500_000,
                "max_daily_buy_amount_krw":  3_000_000,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["allowed"] is False
        assert body["reason_code"] == "DAILY_BUY_LIMIT_EXCEEDED"
        assert body["new_buy_notional"] == 700_000
        assert body["projected_today_buy_amount"] == 3_200_000
        assert "일일 최대 매수금액을 초과하여 매수 차단" in body["reason_message"]
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False

    def test_preview_allowed(self, api_client):
        r = api_client.post(
            "/api/auto-paper/daily-buy-limit/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "price": 100_000, "quantity": 5,
                "today_buy_used_amount_krw": 0,
                "max_daily_buy_amount_krw":  3_000_000,
            },
        )
        body = r.json()
        assert body["allowed"] is True
        assert body["reason_code"] == "DAILY_BUY_LIMIT_OK"

    def test_preview_uses_p09_when_max_omitted(self, api_client):
        # max 미주입 → P-09 risk_profile 자동 적용.
        r = api_client.post(
            "/api/auto-paper/daily-buy-limit/preview",
            json={
                "side": "BUY", "price": 100_000, "quantity": 5,
                "today_buy_used_amount_krw": 0,
                "risk_profile": "CONSERVATIVE",
                "total_paper_capital_krw": 10_000_000,
            },
        )
        body = r.json()
        assert body["resolved_source"] == "risk_profile"
        assert body["max_daily_buy_amount"] == 2_000_000

    def test_preview_falls_back_to_system_default(self, api_client):
        # max / risk_profile 모두 미주입 → system default.
        r = api_client.post(
            "/api/auto-paper/daily-buy-limit/preview",
            json={
                "side": "BUY", "price": 100_000, "quantity": 1,
                "today_buy_used_amount_krw": 0,
            },
        )
        body = r.json()
        assert body["resolved_source"] == "system_default"
        assert body["max_daily_buy_amount"] == 3_000_000

    def test_preview_sell_not_applicable(self, api_client):
        r = api_client.post(
            "/api/auto-paper/daily-buy-limit/preview",
            json={
                "side": "SELL", "price": 100_000, "quantity": 5,
                "today_buy_used_amount_krw": 2_900_000,
                "max_daily_buy_amount_krw":  3_000_000,
            },
        )
        body = r.json()
        assert body["reason_code"] == "DAILY_BUY_LIMIT_NOT_APPLICABLE"
        assert body["allowed"] is True

    def test_resolve_endpoint(self, api_client):
        r = api_client.post(
            "/api/auto-paper/daily-buy-limit/resolve",
            json={
                "risk_profile": "AGGRESSIVE",
                "total_paper_capital_krw": 10_000_000,
            },
        )
        body = r.json()
        assert body["max_daily_buy_amount_krw"] == 8_000_000
        assert body["source"] == "risk_profile"
        assert body["is_paper_only"] is True

    def test_resolve_manual_priority(self, api_client):
        r = api_client.post(
            "/api/auto-paper/daily-buy-limit/resolve",
            json={
                "manual_daily_buy_limit_krw": 5_555_555,
                "risk_profile": "AGGRESSIVE",
                "total_paper_capital_krw": 10_000_000,
            },
        )
        body = r.json()
        assert body["max_daily_buy_amount_krw"] == 5_555_555
        assert body["source"] == "manual"


# ─────────────────────────────────────────────────────────────────────────────
# 12. 정적 가드 (검증 23)
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_module_file_exists(self):
        assert _MODULE_PATH.exists()

    def test_no_broker_call_patterns_in_p10_code(self):
        # P-10 코드 영역 (마지막 추가) 에 broker / route_order 호출 0건.
        text = _MODULE_PATH.read_text(encoding="utf-8")
        # P-10 마커 기준으로 추출 — 이후 텍스트 검사.
        marker = "# P-10: 일일 최대 신규 매수금액 제한 (Daily Buy Limit)"
        idx = text.find(marker)
        assert idx >= 0
        p10_section = text[idx:]
        for pat in (
            r"\bbroker\.place_order\s*\(",
            r"\bbroker\.cancel_order\s*\(",
            r"\broute_order\s*\(",
            r"OrderExecutor\s*\(",
            r"OrderRequest\s*\(",
            r"\.enable_live_trading\s*=",
            r"\.enable_ai_execution\s*=",
        ):
            assert not re.search(pat, p10_section), (
                f"P-10 코드 영역 금지 패턴: /{pat}/"
            )

    def test_no_safety_flag_mutations(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        marker = "# P-10: 일일 최대 신규 매수금액 제한 (Daily Buy Limit)"
        p10_section = text[text.find(marker):]
        for pat in [
            r"ENABLE_LIVE_TRADING\s*=\s*True",
            r"ENABLE_AI_EXECUTION\s*=\s*True",
            r"KIS_IS_PAPER\s*=\s*False",
        ]:
            assert not re.search(pat, p10_section), (
                f"P-10 안전 flag mutation 의심: /{pat}/"
            )

    def test_p10_helpers_exported(self):
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
        }
        for required in [
            "DailyBuyLimitResult",
            "check_daily_buy_limit",
            "calculate_today_buy_used_amount",
        ]:
            assert required in names, f"{required} 가 loss_limits.py 에 없음"
