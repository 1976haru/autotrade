"""P-07: Paper 현금 잔고 (CapitalState) — 단위 + 정적 + API 테스트.

검증 항목 (사용자 요청서):
- Paper 현금이 충분하면 BUY 허용
- Paper 현금이 부족하면 BUY 차단
- 차단 사유 코드: INSUFFICIENT_PAPER_CASH
- 차단 메시지에 "남은 Paper 현금" 문구
- 종목당 한도는 충분하지만 현금 부족이면 BUY 차단
- BUY 가 아닌 SELL / HOLD 는 현금 부족 차단 미적용
- 주문 금액 = 현재가 × 수량
- 현금 차감은 commit_buy 시점에만
- 차단된 BUY 는 현금 차감 0건
- broker / OrderExecutor / route_order 호출 0건 (정적 + AST 가드)
- settings.enable_*_trading mutate 0건
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
    reset_paper_capital_for_tests,
)
from app.auto_paper.capital_state import (
    CapitalState,
    CapitalStateSnapshot,
    CashCheckResult,
    CashCheckVerdict,
    InsufficientPaperCashError,
    check_buy_cash_sufficient,
    compute_required_krw,
    get_capital_state,
    reset_capital_state_for_tests,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app


_INSUFFICIENT_REASON_FRAGMENT = "남은 Paper 현금"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_store():
    reset_paper_capital_for_tests()
    reset_capital_state_for_tests()
    yield
    reset_paper_capital_for_tests()
    reset_capital_state_for_tests()


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
# 1. dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestDataclassInvariants:
    def test_is_paper_only_must_be_true(self):
        with pytest.raises(ValueError, match="is_paper_only"):
            CashCheckResult(
                verdict=CashCheckVerdict.ALLOWED,
                is_paper_only=False,  # type: ignore[arg-type]
            )

    def test_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            CashCheckResult(
                verdict=CashCheckVerdict.ALLOWED,
                is_order_signal=True,  # type: ignore[arg-type]
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            CashCheckResult(
                verdict=CashCheckVerdict.ALLOWED,
                is_live_authorization=True,  # type: ignore[arg-type]
            )

    def test_negative_required_rejected(self):
        with pytest.raises(ValueError, match="required_krw"):
            CashCheckResult(
                verdict=CashCheckVerdict.ALLOWED,
                required_krw=-1,
            )

    def test_negative_shortfall_rejected(self):
        with pytest.raises(ValueError, match="shortfall_krw"):
            CashCheckResult(
                verdict=CashCheckVerdict.ALLOWED,
                shortfall_krw=-1,
            )

    def test_snapshot_negative_cash_rejected(self):
        with pytest.raises(ValueError, match="available_cash_krw"):
            CapitalStateSnapshot(
                initial_cash_krw=100, available_cash_krw=-1,
                invested_krw=0, realized_pnl_krw=0,
                buy_count=0, sell_count=0,
            )

    def test_to_dict_carries_all_fields(self):
        r = CashCheckResult(
            verdict=CashCheckVerdict.INSUFFICIENT_PAPER_CASH,
            symbol="005930", action="BUY",
            price=100_000.0, quantity=5,
            required_krw=500_000, available_cash_krw=300_000,
            shortfall_krw=200_000,
            reason_ko="남은 Paper 현금이 부족하여 매수 차단",
            risk_flag="insufficient_paper_cash",
        )
        d = r.to_dict()
        assert d["verdict"] == "INSUFFICIENT_PAPER_CASH"
        assert d["required_krw"] == 500_000
        assert d["available_cash_krw"] == 300_000
        assert d["shortfall_krw"] == 200_000
        assert d["is_insufficient"] is True
        assert d["is_allowed"] is False
        assert d["is_paper_only"] is True
        assert d["is_order_signal"] is False
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. compute_required_krw helper
# ─────────────────────────────────────────────────────────────────────────────


class TestComputeRequiredKrw:
    def test_basic_multiplication(self):
        # 사용자 요청서: 주문 금액 계산이 현재가 × 수량.
        assert compute_required_krw(price=100_000, quantity=5) == 500_000

    def test_one_share(self):
        assert compute_required_krw(price=50_000, quantity=1) == 50_000

    def test_zero_quantity_returns_zero(self):
        assert compute_required_krw(price=100_000, quantity=0) == 0

    def test_none_price_returns_zero(self):
        assert compute_required_krw(price=None, quantity=5) == 0

    def test_negative_price_returns_zero(self):
        assert compute_required_krw(price=-1, quantity=5) == 0

    def test_fractional_price_floor(self):
        # 33.5 × 100 = 3350 정수 KRW.
        assert compute_required_krw(price=33.5, quantity=100) == 3350


# ─────────────────────────────────────────────────────────────────────────────
# 3. check_buy_cash_sufficient — 매트릭스
# ─────────────────────────────────────────────────────────────────────────────


class TestCashCheckHappyPath:
    def test_sufficient_cash_allows_buy(self):
        # 필요 금액 (100k × 3 = 300k) < 현금 1M → ALLOWED.
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=3,
            available_cash_krw=1_000_000,
        )
        assert r.verdict == CashCheckVerdict.ALLOWED
        assert r.is_allowed is True
        assert r.required_krw == 300_000
        assert r.shortfall_krw == 0
        assert r.risk_flag is None

    def test_equal_cash_allows_buy(self):
        # 필요 == 현금 — 경계.
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=5,
            available_cash_krw=500_000,
        )
        assert r.verdict == CashCheckVerdict.ALLOWED
        assert r.required_krw == 500_000


class TestCashCheckInsufficient:
    def test_user_scenario_example_blocks_buy(self):
        # 사용자 요청서 예시:
        # 종목당 한도: 1M (이 검사에서는 무관) / 현금: 300k / 100k × 5주 = 500k
        # → 현금 부족으로 BUY 차단.
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=5,
            available_cash_krw=300_000,
        )
        assert r.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH
        assert r.is_insufficient is True
        assert r.is_allowed is False
        assert r.required_krw == 500_000
        assert r.available_cash_krw == 300_000
        assert r.shortfall_krw == 200_000
        assert r.risk_flag == "insufficient_paper_cash"

    def test_block_reason_message_contains_required_fragment(self):
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=5,
            available_cash_krw=300_000,
        )
        # 사용자 요청서: "남은 Paper 현금" 문구 + 매수 차단 + 필요/남은 금액.
        assert _INSUFFICIENT_REASON_FRAGMENT in r.reason_ko
        assert "매수 차단" in r.reason_ko
        assert "필요 금액" in r.reason_ko
        assert "500,000" in r.reason_ko
        assert "300,000" in r.reason_ko

    def test_insufficient_verdict_string_value(self):
        # 차단 사유 코드는 INSUFFICIENT_PAPER_CASH 문자열.
        assert CashCheckVerdict.INSUFFICIENT_PAPER_CASH.value == "INSUFFICIENT_PAPER_CASH"

    def test_per_symbol_cap_independence(self):
        # 사용자 요청서: 종목당 한도와 현금 잔고는 *별개*.
        # 한도가 매우 커도 현금이 부족하면 차단.
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=5,
            available_cash_krw=300_000,  # 한도 무관, 현금만 봄
        )
        assert r.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH


class TestCashCheckSkipNonBuy:
    @pytest.mark.parametrize(
        "action", ["SELL", "EXIT", "CLOSE", "HOLD", "NO_OP", None, "",
                   "sell", "exit"],
    )
    def test_non_buy_returns_skip_even_with_zero_cash(self, action):
        # 사용자 요청서: SELL/HOLD/관망은 현금 부족 차단 미적용.
        r = check_buy_cash_sufficient(
            action=action, symbol="005930",
            price=100_000, quantity=5,
            available_cash_krw=0,  # 현금 0원이어도 SKIP.
        )
        assert r.verdict == CashCheckVerdict.SKIP_NON_BUY
        # blocked 분류가 *아니어야* 함.
        assert r.is_insufficient is False
        assert r.is_allowed is False

    def test_sell_with_zero_cash_not_blocked(self):
        r = check_buy_cash_sufficient(
            action="SELL", symbol="005930",
            price=100_000, quantity=5,
            available_cash_krw=0,
        )
        assert r.verdict == CashCheckVerdict.SKIP_NON_BUY
        # 사용자 메시지 — 청산/관망은 차단되지 않는다 명시.
        assert "차단되지 않습니다" in r.reason_ko or "무관" in r.reason_ko

    def test_hold_with_zero_cash_not_blocked(self):
        r = check_buy_cash_sufficient(
            action="HOLD", symbol="005930",
            price=100_000, quantity=5,
            available_cash_krw=0,
        )
        assert r.verdict == CashCheckVerdict.SKIP_NON_BUY

    def test_no_action_not_blocked(self):
        r = check_buy_cash_sufficient(
            action="NO_ACTION", symbol="005930",
            price=100_000, quantity=5,
            available_cash_krw=0,
        )
        assert r.verdict == CashCheckVerdict.SKIP_NON_BUY


class TestCashCheckInvalidInput:
    def test_missing_price(self):
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=None, quantity=5,
            available_cash_krw=1_000_000,
        )
        assert r.verdict == CashCheckVerdict.MISSING_PRICE
        assert r.risk_flag == "missing_price"

    def test_zero_price(self):
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=0, quantity=5,
            available_cash_krw=1_000_000,
        )
        assert r.verdict == CashCheckVerdict.INVALID_PRICE

    def test_negative_price(self):
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=-100, quantity=5,
            available_cash_krw=1_000_000,
        )
        assert r.verdict == CashCheckVerdict.INVALID_PRICE

    def test_zero_quantity(self):
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=0,
            available_cash_krw=1_000_000,
        )
        assert r.verdict == CashCheckVerdict.INVALID_QUANTITY

    def test_negative_quantity(self):
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=-1,
            available_cash_krw=1_000_000,
        )
        assert r.verdict == CashCheckVerdict.INVALID_QUANTITY

    def test_fractional_quantity(self):
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=1.5,
            available_cash_krw=1_000_000,
        )
        assert r.verdict == CashCheckVerdict.INVALID_QUANTITY

    def test_negative_cash_clamped_to_zero(self):
        # 음수 입력은 0 으로 clamp → required > 0 면 INSUFFICIENT.
        r = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=1,
            available_cash_krw=-100,
        )
        assert r.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH
        assert r.available_cash_krw == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. CapitalState — 상태 변경 (commit_buy / commit_sell)
# ─────────────────────────────────────────────────────────────────────────────


class TestCapitalStateLifecycle:
    def test_initial_snapshot(self):
        s = CapitalState(initial_cash_krw=1_000_000)
        snap = s.snapshot()
        assert snap.initial_cash_krw == 1_000_000
        assert snap.available_cash_krw == 1_000_000
        assert snap.invested_krw == 0
        assert snap.realized_pnl_krw == 0
        assert snap.buy_count == 0
        assert snap.sell_count == 0

    def test_negative_initial_cash_rejected(self):
        with pytest.raises(ValueError, match="initial_cash_krw"):
            CapitalState(initial_cash_krw=-1)

    def test_commit_buy_deducts_cash(self):
        s = CapitalState(initial_cash_krw=1_000_000)
        snap = s.commit_buy(symbol="005930", price=100_000, quantity=3)
        # 차감 = 100k × 3 = 300k.
        assert snap.available_cash_krw == 700_000
        assert snap.invested_krw == 300_000
        assert snap.buy_count == 1

    def test_commit_buy_insufficient_raises_and_no_state_change(self):
        # 사용자 요청서: 차단된 BUY 는 Paper 현금을 차감하지 않음 (backstop).
        s = CapitalState(initial_cash_krw=100_000)
        with pytest.raises(InsufficientPaperCashError):
            s.commit_buy(symbol="005930", price=100_000, quantity=5)
        # state 보존 — 현금/카운트 그대로.
        snap = s.snapshot()
        assert snap.available_cash_krw == 100_000
        assert snap.invested_krw == 0
        assert snap.buy_count == 0

    def test_precheck_buy_no_state_change(self):
        # 사용자 요청서: 현금 차감은 commit_buy 시점에만.
        s = CapitalState(initial_cash_krw=1_000_000)
        for _ in range(5):
            r = s.precheck_buy(
                action="BUY", symbol="005930",
                price=100_000, quantity=3,
            )
            assert r.verdict == CashCheckVerdict.ALLOWED
        # 5번 precheck 후에도 잔고 그대로.
        snap = s.snapshot()
        assert snap.available_cash_krw == 1_000_000
        assert snap.buy_count == 0

    def test_commit_buy_invalid_inputs_raise(self):
        s = CapitalState(initial_cash_krw=1_000_000)
        with pytest.raises(ValueError):
            s.commit_buy(symbol=None, price=None, quantity=1)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            s.commit_buy(symbol=None, price=0, quantity=1)
        with pytest.raises(ValueError):
            s.commit_buy(symbol=None, price=100, quantity=0)
        with pytest.raises(ValueError):
            s.commit_buy(symbol=None, price=100, quantity=1.5)

    def test_commit_sell_adds_cash(self):
        s = CapitalState(initial_cash_krw=500_000)
        s.commit_buy(symbol="005930", price=100_000, quantity=3)
        # 매도 — 110k × 3 = 330k 현금 가산 + cost_basis 300k → 차익 30k.
        snap = s.commit_sell(
            symbol="005930", price=110_000, quantity=3,
            cost_basis_krw=300_000,
        )
        # 매수 후 200k → 매도 후 +330k = 530k.
        assert snap.available_cash_krw == 530_000
        assert snap.invested_krw == 0
        assert snap.realized_pnl_krw == 30_000
        assert snap.sell_count == 1

    def test_commit_sell_does_not_block_on_zero_cash(self):
        # 매도는 현금 부족으로 차단되지 않는다.
        s = CapitalState(initial_cash_krw=0)
        snap = s.commit_sell(symbol="005930", price=100_000, quantity=1)
        # 현금 0 → 100k 증가.
        assert snap.available_cash_krw == 100_000

    def test_reset_clears_all_state(self):
        s = CapitalState(initial_cash_krw=1_000_000)
        s.commit_buy(symbol="005930", price=100_000, quantity=3)
        snap = s.reset(initial_cash_krw=500_000)
        assert snap.initial_cash_krw == 500_000
        assert snap.available_cash_krw == 500_000
        assert snap.invested_krw == 0
        assert snap.buy_count == 0


class TestCapitalStateIntegration:
    def test_per_symbol_cap_vs_cash_independence(self):
        # 사용자 요청서: 종목당 한도와 현금 잔고는 별개.
        # 시드 1M → 100k × 5주 = 500k BUY 한 번 → 현금 500k 남음.
        # 다음 BUY 100k × 6주 = 600k 시도 → 현금 부족.
        s = CapitalState(initial_cash_krw=1_000_000)
        snap1 = s.commit_buy(symbol="A", price=100_000, quantity=5)
        assert snap1.available_cash_krw == 500_000

        # 한도 자체는 충분 (per_symbol_cap 미적용 — 본 모듈은 현금만 본다).
        r = s.precheck_buy(
            action="BUY", symbol="B", price=100_000, quantity=6,
        )
        assert r.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH
        assert r.required_krw == 600_000
        assert r.available_cash_krw == 500_000

    def test_blocked_buy_does_not_deduct_cash(self):
        # 차단된 BUY 는 현금을 차감하지 않는다 (사용자 요청서 §10).
        s = CapitalState(initial_cash_krw=300_000)
        precheck = s.precheck_buy(
            action="BUY", symbol="005930",
            price=100_000, quantity=5,
        )
        assert precheck.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH
        # caller 가 precheck 결과를 보고 commit_buy 를 호출하지 *않으면* —
        # state 변경 0건.
        snap = s.snapshot()
        assert snap.available_cash_krw == 300_000
        assert snap.invested_krw == 0
        assert snap.buy_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. Singleton — get_capital_state / reset_capital_state_for_tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_capital_state_returns_same_instance(self):
        s1 = get_capital_state()
        s2 = get_capital_state()
        assert s1 is s2

    def test_reset_for_tests_initializes_from_config(self):
        # PaperCapitalConfig default initial_cash = 10,000,000.
        reset_capital_state_for_tests()
        snap = get_capital_state().snapshot()
        assert snap.available_cash_krw == 10_000_000

    def test_reset_for_tests_with_explicit_value(self):
        reset_capital_state_for_tests(initial_cash_krw=5_000_000)
        snap = get_capital_state().snapshot()
        assert snap.initial_cash_krw == 5_000_000
        assert snap.available_cash_krw == 5_000_000


# ─────────────────────────────────────────────────────────────────────────────
# 6. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestCashCheckPreviewEndpoint:
    def test_sufficient_returns_allowed(self, api_client):
        r = api_client.post(
            "/api/auto-paper/cash-check/preview",
            json={
                "action": "BUY", "symbol": "005930",
                "price": 100_000, "quantity": 3,
                "available_cash_krw": 1_000_000,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "ALLOWED"
        assert body["required_krw"] == 300_000
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False

    def test_insufficient_returns_block_reason(self, api_client):
        r = api_client.post(
            "/api/auto-paper/cash-check/preview",
            json={
                "action": "BUY", "symbol": "005930",
                "price": 100_000, "quantity": 5,
                "available_cash_krw": 300_000,
            },
        )
        assert r.status_code == 200
        body = r.json()
        # 차단 사유 코드: INSUFFICIENT_PAPER_CASH.
        assert body["verdict"] == "INSUFFICIENT_PAPER_CASH"
        assert body["required_krw"] == 500_000
        assert body["available_cash_krw"] == 300_000
        assert body["shortfall_krw"] == 200_000
        # 사용자 표시 문구 검증.
        assert _INSUFFICIENT_REASON_FRAGMENT in body["reason_ko"]
        assert "매수 차단" in body["reason_ko"]

    def test_sell_with_zero_cash_not_blocked(self, api_client):
        r = api_client.post(
            "/api/auto-paper/cash-check/preview",
            json={
                "action": "SELL", "symbol": "005930",
                "price": 100_000, "quantity": 5,
                "available_cash_krw": 0,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "SKIP_NON_BUY"

    def test_hold_with_zero_cash_not_blocked(self, api_client):
        r = api_client.post(
            "/api/auto-paper/cash-check/preview",
            json={
                "action": "HOLD", "symbol": "005930",
                "price": 100_000, "quantity": 5,
                "available_cash_krw": 0,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "SKIP_NON_BUY"

    def test_preview_does_not_mutate_state(self, api_client):
        # advisory preview 는 state 변경 X — 5번 호출해도 cash-state 동일.
        before = api_client.get("/api/auto-paper/cash-state").json()
        for _ in range(5):
            r = api_client.post(
                "/api/auto-paper/cash-check/preview",
                json={
                    "action": "BUY", "symbol": "005930",
                    "price": 100_000, "quantity": 3,
                },
            )
            assert r.status_code == 200
        after = api_client.get("/api/auto-paper/cash-state").json()
        assert before["available_cash_krw"] == after["available_cash_krw"]
        assert before["buy_count"] == after["buy_count"]

    def test_uses_singleton_cash_when_not_provided(self, api_client):
        # available_cash_krw 미주입 → singleton state 자동 사용.
        # default initial_cash = 10M → 1주 100k 가능.
        r = api_client.post(
            "/api/auto-paper/cash-check/preview",
            json={
                "action": "BUY", "symbol": "005930",
                "price": 100_000, "quantity": 1,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "ALLOWED"


class TestCashStateEndpoint:
    def test_get_state_returns_snapshot(self, api_client):
        r = api_client.get("/api/auto-paper/cash-state")
        assert r.status_code == 200
        body = r.json()
        assert "available_cash_krw" in body
        assert "invested_krw" in body
        assert "buy_count" in body
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False

    def test_reset_endpoint(self, api_client):
        r = api_client.post("/api/auto-paper/cash-state/reset")
        assert r.status_code == 200
        body = r.json()
        assert body["buy_count"] == 0
        assert body["sell_count"] == 0
        assert body["invested_krw"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 7. 정적 import / 안전 invariant 가드
# ─────────────────────────────────────────────────────────────────────────────


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "capital_state.py"
)


class TestStaticImportGuards:
    def test_module_file_exists(self):
        assert _MODULE_PATH.exists(), f"{_MODULE_PATH} 누락"

    def test_no_broker_imports(self):
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                for banned in (
                    "app.brokers", "app.brokers.kis", "app.brokers.kis_client",
                    "app.brokers.live_broker",
                    "app.execution", "app.execution.executor",
                    "app.execution.order_router",
                    "app.kis_paper.engine",
                    "app.core.config",
                    "anthropic", "openai", "httpx", "requests", "urllib3",
                ):
                    assert not m.startswith(banned), (
                        f"capital_state.py 가 금지 모듈 '{m}' import"
                    )

    def test_no_broker_call_patterns(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in (
            r"\bbroker\.place_order\s*\(",
            r"\bbroker\.cancel_order\s*\(",
            r"\broute_order\s*\(",
            r"\bKisClient\s*\(",
            r"\bKisBrokerAdapter\s*\(",
            r"OrderExecutor\s*\(",
            r"OrderRequest\s*\(",
            r"\.enable_live_trading\s*=",
            r"\.enable_ai_execution\s*=",
            r"\.enable_futures_live_trading\s*=",
        ):
            assert not re.search(pat, text), (
                f"capital_state.py 에 금지 패턴: /{pat}/"
            )

    def test_module_parses_and_exports(self):
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
        }
        for required in [
            "CashCheckVerdict", "CashCheckResult", "CapitalState",
            "CapitalStateSnapshot", "InsufficientPaperCashError",
            "check_buy_cash_sufficient", "compute_required_krw",
            "get_capital_state", "reset_capital_state_for_tests",
        ]:
            assert required in names


# ─────────────────────────────────────────────────────────────────────────────
# 8. CLAUDE.md 절대 원칙 — safety flag mutation 0건
# ─────────────────────────────────────────────────────────────────────────────


class TestSafetyFlagInvariants:
    def test_no_safety_flag_mutations(self):
        src = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in [
            r"settings\.enable_live_trading\s*=",
            r"settings\.enable_ai_execution\s*=",
            r"settings\.enable_futures_live_trading\s*=",
            r"ENABLE_LIVE_TRADING\s*=\s*True",
            r"ENABLE_AI_EXECUTION\s*=\s*True",
            r"ENABLE_FUTURES_LIVE_TRADING\s*=\s*True",
            r"KIS_IS_PAPER\s*=\s*False",
        ]:
            assert not re.search(pat, src), (
                f"capital_state.py 에 안전 flag mutation 의심 패턴: {pat!r}"
            )

    def test_p04_affordability_unchanged(self):
        # P-04 의 INSUFFICIENT_CASH (1주 가격 vs cash) 와 본 PR 의
        # INSUFFICIENT_PAPER_CASH (price × qty vs cash) 는 *별개 verdict*.
        from app.auto_paper.affordability_check import AffordabilityVerdict
        assert AffordabilityVerdict.INSUFFICIENT_CASH.value == "INSUFFICIENT_CASH"
        # P-07 의 verdict 는 다른 이름으로 carry (혼동 방지).
        assert CashCheckVerdict.INSUFFICIENT_PAPER_CASH.value == "INSUFFICIENT_PAPER_CASH"
        assert CashCheckVerdict.INSUFFICIENT_PAPER_CASH.value != AffordabilityVerdict.INSUFFICIENT_CASH.value


# ─────────────────────────────────────────────────────────────────────────────
# D4/D6: cash-state 의 realized_pnl 은 *실체결(order_audit_log)* 에서 교정
# ─────────────────────────────────────────────────────────────────────────────

def test_cash_state_realized_pnl_from_order_audit_log():
    """체결(BUY→SELL)이 있으면 cash-state 의 realized_pnl_krw 가 실체결 FIFO
    기준값으로 나온다(가상 ledger 0 이 아니라). '거래 시작 전' 라벨 정직화."""
    from datetime import datetime, timezone
    from app.db.models import OrderAuditLog

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    s = TS()
    for side, px in (("BUY", 70_000), ("SELL", 75_000)):
        s.add(OrderAuditLog(
            created_at=now, mode="PAPER", requested_by_ai=False,
            symbol="005930", side=side, quantity=1, order_type="MARKET",
            decision="APPROVED", executed=True, broker_order_id="X",
            broker_status="FILLED", filled_quantity=1, avg_fill_price=px,
            limit_price=px, latest_price=px, trade_reason="kis_paper_auto",
        ))
    s.commit(); s.close()

    def _ov():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _ov
    try:
        with TestClient(app) as c:
            r = c.get("/api/auto-paper/cash-state")
        assert r.status_code == 200
        body = r.json()
        assert body["realized_pnl_source"] == "order_audit_log"
        assert body["realized_pnl_krw"] == 5_000   # (75,000 − 70,000) × 1
    finally:
        app.dependency_overrides.pop(get_db, None)
