"""P-08: Paper position sizing by price — 단위 + 정적 + API + integration 테스트.

검증 항목 (사용자 요청서):
1. max_amount=1,000,000 / price=75,000 → quantity=13
2. notional = price * quantity 확인
3. remainder_cash = max_amount - notional 확인
4. price > max_amount → quantity=0 (MIN_LOT_NOT_AFFORDABLE)
5. quantity=0 시 reason_code=MIN_LOT_NOT_AFFORDABLE
6. price=0 → INVALID_PRICE
7. price<0 → INVALID_PRICE
8. max_amount=0 → INVALID_MAX_AMOUNT
9. max_amount<0 → INVALID_MAX_AMOUNT
10. quantity 는 항상 int
11. 소수점 나눗셈 결과도 floor 처리
12. P-06 고가주 처리 정책과 연결 시 1주 불가 케이스 깨지지 않음
13. P-07 Paper cash check 와 연결 시 quantity 계산 후 현금 부족 판단
14. BUY 가 아닌 SELL/HOLD 에는 sizing 강제 적용하지 않음
15. 정적 grep — 실거래 / LIVE 설정 미변경
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
from app.auto_paper.position_sizer import (
    QuantityByPriceResult,
    QuantityByPriceVerdict,
    compute_paper_quantity_by_price,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "position_sizer.py"
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
# 1. dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestQuantityByPriceResultInvariants:
    def test_is_paper_only_must_be_true(self):
        with pytest.raises(ValueError, match="is_paper_only"):
            QuantityByPriceResult(
                verdict=QuantityByPriceVerdict.OK,
                quantity=1, notional_krw=0, remainder_krw=0,
                is_paper_only=False,  # type: ignore[arg-type]
            )

    def test_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            QuantityByPriceResult(
                verdict=QuantityByPriceVerdict.OK,
                quantity=1, notional_krw=0, remainder_krw=0,
                is_order_signal=True,  # type: ignore[arg-type]
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            QuantityByPriceResult(
                verdict=QuantityByPriceVerdict.OK,
                quantity=1, notional_krw=0, remainder_krw=0,
                is_live_authorization=True,  # type: ignore[arg-type]
            )

    def test_quantity_must_be_int(self):
        with pytest.raises(ValueError, match="quantity must be int"):
            QuantityByPriceResult(
                verdict=QuantityByPriceVerdict.OK,
                quantity=1.5,  # type: ignore[arg-type]
                notional_krw=0, remainder_krw=0,
            )

    def test_negative_quantity_rejected(self):
        with pytest.raises(ValueError, match="quantity must be >="):
            QuantityByPriceResult(
                verdict=QuantityByPriceVerdict.OK,
                quantity=-1, notional_krw=0, remainder_krw=0,
            )

    def test_to_dict_carries_all_fields(self):
        r = QuantityByPriceResult(
            verdict=QuantityByPriceVerdict.OK,
            quantity=13, notional_krw=975_000, remainder_krw=25_000,
            symbol="005930", action="BUY",
            price=75_000.0, max_amount_krw=1_000_000,
            reason_code="OK", reason_ko="13주 매수 가능",
        )
        d = r.to_dict()
        assert d["verdict"] == "OK"
        assert d["quantity"] == 13
        assert d["notional_krw"] == 975_000
        assert d["remainder_krw"] == 25_000
        assert d["is_ok"] is True
        assert d["is_min_lot_not_affordable"] is False
        assert d["is_paper_only"] is True
        assert d["is_order_signal"] is False
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. 사용자 요청서 예시 + 기본 계산
# ─────────────────────────────────────────────────────────────────────────────


class TestUserSpecExample:
    def test_user_spec_example_75k_into_1m_returns_13_shares(self):
        # 사용자 요청서: max_amount=1,000,000 / price=75,000 → quantity=13.
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=75_000, max_amount_krw=1_000_000,
        )
        assert r.verdict == QuantityByPriceVerdict.OK
        assert r.quantity == 13
        assert isinstance(r.quantity, int)
        # 검증 2/3: notional + remainder.
        assert r.notional_krw == 13 * 75_000   # = 975,000
        assert r.notional_krw == 975_000
        assert r.remainder_krw == 1_000_000 - 975_000  # = 25,000
        assert r.remainder_krw == 25_000

    def test_quantity_is_always_int_type(self):
        # 검증 10: quantity 는 항상 int 타입.
        for cap, price in [(1_000_000, 75_000), (500_000, 50_000),
                            (10_000_000, 100_000), (1, 0.5)]:
            r = compute_paper_quantity_by_price(
                action="BUY", symbol="X",
                price=price, max_amount_krw=cap,
            )
            assert isinstance(r.quantity, int), (
                f"quantity must be int, got {type(r.quantity)} for "
                f"cap={cap} price={price}"
            )
            # bool subclass 도 거부.
            assert not isinstance(r.quantity, bool)

    def test_floor_not_round(self):
        # 검증 11: 소수점 나눗셈 결과도 floor.
        # 999,999 / 1,000 = 999.999 → 999 (round 면 1000).
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=1_000, max_amount_krw=999_999,
        )
        assert r.verdict == QuantityByPriceVerdict.OK
        assert r.quantity == 999

    def test_floor_no_rounding_up_at_boundary(self):
        # 1,000,000 / 333 = 3003.003... → 3003.
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=333, max_amount_krw=1_000_000,
        )
        assert r.verdict == QuantityByPriceVerdict.OK
        assert r.quantity == 3003

    def test_exact_division_yields_full_amount(self):
        # 1,000,000 / 100,000 = 10 (정확).
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=100_000, max_amount_krw=1_000_000,
        )
        assert r.quantity == 10
        assert r.notional_krw == 1_000_000
        assert r.remainder_krw == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. MIN_LOT_NOT_AFFORDABLE
# ─────────────────────────────────────────────────────────────────────────────


class TestMinLotNotAffordable:
    def test_price_over_max_amount_quantity_zero(self):
        # 검증 4: price > max_amount → quantity=0.
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=120_000, max_amount_krw=100_000,
        )
        assert r.quantity == 0
        # 검증 5: reason_code = MIN_LOT_NOT_AFFORDABLE.
        assert r.verdict == QuantityByPriceVerdict.MIN_LOT_NOT_AFFORDABLE
        assert r.reason_code == "MIN_LOT_NOT_AFFORDABLE"

    def test_zero_quantity_message_contains_paper_phrase(self):
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=120_000, max_amount_krw=100_000,
        )
        # 사용자 요청서 표시 문구: "1주도 살 수 없" 포함.
        assert "1주도 살 수 없" in r.reason_ko or "1주" in r.reason_ko
        assert r.notional_krw == 0
        assert r.remainder_krw == 100_000  # cap 전체가 잔여

    def test_exactly_one_won_short(self):
        # cap=99_999 / price=100_000 → 0주.
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=100_000, max_amount_krw=99_999,
        )
        assert r.quantity == 0
        assert r.verdict == QuantityByPriceVerdict.MIN_LOT_NOT_AFFORDABLE

    def test_exactly_one_won_enough(self):
        # cap=100_001 / price=100_000 → 1주 가능.
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=100_000, max_amount_krw=100_001,
        )
        assert r.quantity == 1
        assert r.verdict == QuantityByPriceVerdict.OK


# ─────────────────────────────────────────────────────────────────────────────
# 4. INVALID_PRICE
# ─────────────────────────────────────────────────────────────────────────────


class TestInvalidPrice:
    def test_price_zero(self):
        # 검증 6: price=0 → INVALID_PRICE.
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=0, max_amount_krw=1_000_000,
        )
        assert r.verdict == QuantityByPriceVerdict.INVALID_PRICE
        assert r.reason_code == "INVALID_PRICE"
        assert r.quantity == 0

    def test_price_negative(self):
        # 검증 7: price<0 → INVALID_PRICE.
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=-1, max_amount_krw=1_000_000,
        )
        assert r.verdict == QuantityByPriceVerdict.INVALID_PRICE

    def test_price_none(self):
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=None, max_amount_krw=1_000_000,
        )
        assert r.verdict == QuantityByPriceVerdict.INVALID_PRICE
        assert "현재가" in r.reason_ko

    def test_price_inf(self):
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=float("inf"), max_amount_krw=1_000_000,
        )
        assert r.verdict == QuantityByPriceVerdict.INVALID_PRICE

    def test_price_nan(self):
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=float("nan"), max_amount_krw=1_000_000,
        )
        assert r.verdict == QuantityByPriceVerdict.INVALID_PRICE

    def test_price_unparseable_string_safely_rejected(self):
        # 잘못된 타입도 안전하게 INVALID_PRICE 처리.
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price="abc",  # type: ignore[arg-type]
            max_amount_krw=1_000_000,
        )
        assert r.verdict == QuantityByPriceVerdict.INVALID_PRICE


# ─────────────────────────────────────────────────────────────────────────────
# 5. INVALID_MAX_AMOUNT
# ─────────────────────────────────────────────────────────────────────────────


class TestInvalidMaxAmount:
    def test_max_amount_zero(self):
        # 검증 8: max_amount=0 → INVALID_MAX_AMOUNT.
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=100_000, max_amount_krw=0,
        )
        assert r.verdict == QuantityByPriceVerdict.INVALID_MAX_AMOUNT
        assert r.reason_code == "INVALID_MAX_AMOUNT"
        assert r.quantity == 0
        # 표시 문구.
        assert "0 이하" in r.reason_ko

    def test_max_amount_negative(self):
        # 검증 9: max_amount<0 → INVALID_MAX_AMOUNT.
        r = compute_paper_quantity_by_price(
            action="BUY", symbol="X",
            price=100_000, max_amount_krw=-1,
        )
        assert r.verdict == QuantityByPriceVerdict.INVALID_MAX_AMOUNT


# ─────────────────────────────────────────────────────────────────────────────
# 6. SKIP_NON_BUY — 검증 14
# ─────────────────────────────────────────────────────────────────────────────


class TestSkipNonBuy:
    @pytest.mark.parametrize(
        "action", ["SELL", "EXIT", "CLOSE", "HOLD", "NO_OP", None, "",
                   "sell", "exit", "hold"],
    )
    def test_non_buy_skipped(self, action):
        # 검증 14: BUY 가 아닌 SELL/HOLD 에는 sizing 강제 적용 안 함.
        r = compute_paper_quantity_by_price(
            action=action, symbol="X",
            price=100_000, max_amount_krw=1_000_000,
        )
        assert r.verdict == QuantityByPriceVerdict.SKIP_NON_BUY
        assert r.quantity == 0
        assert r.reason_code == "SKIP_NON_BUY"
        # 메시지 명시: "sizing 강제 적용 안 함" 류.
        assert "강제 적용 안 함" in r.reason_ko or "무관" in r.reason_ko

    def test_buy_lowercase_accepted(self):
        r = compute_paper_quantity_by_price(
            action="buy", symbol="X",
            price=100_000, max_amount_krw=1_000_000,
        )
        assert r.verdict == QuantityByPriceVerdict.OK
        assert r.quantity == 10

    def test_buy_synonyms_accepted(self):
        for tok in ("OPEN", "open_long", "long", "ENTER", "entry"):
            r = compute_paper_quantity_by_price(
                action=tok, symbol="X",
                price=100_000, max_amount_krw=1_000_000,
            )
            assert r.verdict == QuantityByPriceVerdict.OK, f"action={tok}"


# ─────────────────────────────────────────────────────────────────────────────
# 7. P-06 (고가주 정책) integration — 검증 12
# ─────────────────────────────────────────────────────────────────────────────


class TestP06HighPriceIntegration:
    def test_high_price_case_consistent_with_p06_exclude(self):
        # P-06 의 high-price 정책과 동일 결과:
        # cap=100_000 / price=120_000 → 1주 불가.
        from app.auto_paper.affordability import (
            HighPricePolicy,
            HighPriceVerdict,
            evaluate_high_price,
        )

        # P-08 sizer 결과.
        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=120_000, max_amount_krw=100_000,
        )
        assert sizer.verdict == QuantityByPriceVerdict.MIN_LOT_NOT_AFFORDABLE
        assert sizer.quantity == 0

        # P-06 결과 — 같은 입력에 대해 EXCLUDE / HELD / BUDGET_HINT 분기.
        p06 = evaluate_high_price(
            action="BUY", symbol="005930",
            price=120_000, effective_per_symbol_cap_krw=100_000,
            policy=HighPricePolicy.EXCLUDE,
        )
        assert p06.verdict == HighPriceVerdict.EXCLUDED
        assert p06.affordable_quantity == 0
        # 두 모듈이 "1주 불가" 라는 동일한 *진단* 을 내야 한다.
        assert sizer.quantity == p06.affordable_quantity == 0

    def test_normal_price_consistent_with_p06_affordable(self):
        from app.auto_paper.affordability import evaluate_high_price

        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=50_000, max_amount_krw=100_000,
        )
        p06 = evaluate_high_price(
            action="BUY", symbol="005930",
            price=50_000, effective_per_symbol_cap_krw=100_000,
        )
        # cap=100k / price=50k → 2주 (sizer) AND P-06 도 affordable.
        assert sizer.quantity == 2
        # P-06 의 affordable_quantity 도 동일 산정 (floor 같은 정책).
        assert p06.affordable_quantity == 2


# ─────────────────────────────────────────────────────────────────────────────
# 8. P-07 (Paper cash check) integration — 검증 13
# ─────────────────────────────────────────────────────────────────────────────


class TestP07CashCheckIntegration:
    def test_quantity_then_cash_check_passes(self):
        # P-08 가 quantity=5 를 산정 → P-07 가 cash 충분 확인 → ALLOWED.
        from app.auto_paper.capital_state import (
            CashCheckVerdict,
            check_buy_cash_sufficient,
        )

        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=100_000, max_amount_krw=500_000,
        )
        assert sizer.quantity == 5

        cash = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=sizer.quantity,
            available_cash_krw=1_000_000,
        )
        assert cash.verdict == CashCheckVerdict.ALLOWED
        # P-08 의 notional 과 P-07 의 required_krw 일치 검증.
        assert sizer.notional_krw == cash.required_krw == 500_000

    def test_quantity_then_cash_check_insufficient(self):
        # 사용자 요청서 §13: sizing 후에도 cash 가 부족하면 P-07 차단.
        from app.auto_paper.capital_state import (
            CashCheckVerdict,
            check_buy_cash_sufficient,
        )

        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=100_000, max_amount_krw=500_000,
        )
        assert sizer.quantity == 5
        assert sizer.notional_krw == 500_000

        # 현금 잔고는 300k 뿐 — P-07 가 차단해야 함.
        cash = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=sizer.quantity,
            available_cash_krw=300_000,
        )
        assert cash.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH
        # P-07 가 P-08 의 sizing 후 추가 검사를 *수행* — 두 단계가 독립적.
        assert cash.required_krw == sizer.notional_krw
        assert cash.shortfall_krw == 200_000


# ─────────────────────────────────────────────────────────────────────────────
# 9. API endpoint
# ─────────────────────────────────────────────────────────────────────────────


class TestSizingApi:
    def test_user_spec_example_via_api(self, api_client):
        r = api_client.post(
            "/api/auto-paper/sizing/preview",
            json={
                "action": "BUY", "symbol": "005930",
                "price": 75_000, "max_amount_krw": 1_000_000,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "OK"
        assert body["quantity"] == 13
        assert body["notional_krw"] == 975_000
        assert body["remainder_krw"] == 25_000
        assert body["reason_code"] == "OK"
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False
        assert "OK" in body["allowed_reason_codes"]
        assert "MIN_LOT_NOT_AFFORDABLE" in body["allowed_reason_codes"]

    def test_uses_per_symbol_cap_when_max_amount_omitted(self, api_client):
        # PaperCapitalConfig default per_symbol_cap = 1,000,000.
        r = api_client.post(
            "/api/auto-paper/sizing/preview",
            json={"action": "BUY", "symbol": "005930", "price": 50_000},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["quantity"] == 20   # 1M / 50k = 20

    def test_invalid_price_via_api(self, api_client):
        r = api_client.post(
            "/api/auto-paper/sizing/preview",
            json={"action": "BUY", "symbol": "X", "price": 0,
                  "max_amount_krw": 1_000_000},
        )
        body = r.json()
        assert body["verdict"] == "INVALID_PRICE"
        assert body["reason_code"] == "INVALID_PRICE"

    def test_invalid_max_amount_via_api(self, api_client):
        r = api_client.post(
            "/api/auto-paper/sizing/preview",
            json={"action": "BUY", "symbol": "X", "price": 100,
                  "max_amount_krw": 0},
        )
        body = r.json()
        assert body["verdict"] == "INVALID_MAX_AMOUNT"

    def test_min_lot_not_affordable_via_api(self, api_client):
        r = api_client.post(
            "/api/auto-paper/sizing/preview",
            json={"action": "BUY", "symbol": "X", "price": 200_000,
                  "max_amount_krw": 100_000},
        )
        body = r.json()
        assert body["verdict"] == "MIN_LOT_NOT_AFFORDABLE"
        assert "1주" in body["reason_ko"]

    def test_non_buy_skipped_via_api(self, api_client):
        r = api_client.post(
            "/api/auto-paper/sizing/preview",
            json={"action": "SELL", "symbol": "X", "price": 100_000,
                  "max_amount_krw": 1_000_000},
        )
        body = r.json()
        assert body["verdict"] == "SKIP_NON_BUY"

    def test_response_carries_advisory_invariants(self, api_client):
        r = api_client.post(
            "/api/auto-paper/sizing/preview",
            json={"action": "BUY", "symbol": "X", "price": 100,
                  "max_amount_krw": 1_000},
        )
        body = r.json()
        assert body["is_order_signal"] is False
        assert body["is_live_authorization"] is False
        assert body["is_paper_only"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 10. 정적 import / 안전 invariant — 검증 15
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_module_file_exists(self):
        assert _MODULE_PATH.exists(), f"{_MODULE_PATH} 누락"

    def test_no_broker_imports_in_position_sizer(self):
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                for banned in (
                    "app.brokers", "app.brokers.kis",
                    "app.execution", "app.execution.executor",
                    "app.execution.order_router",
                    "app.kis_paper.engine",
                    "anthropic", "openai", "httpx", "requests", "urllib3",
                ):
                    assert not m.startswith(banned), (
                        f"position_sizer.py 가 금지 모듈 '{m}' import"
                    )

    def test_no_broker_call_patterns(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        for pat in (
            r"\bbroker\.place_order\s*\(",
            r"\bbroker\.cancel_order\s*\(",
            r"\broute_order\s*\(",
            r"OrderExecutor\s*\(",
            r"OrderRequest\s*\(",
            r"KisClient\s*\(",
            r"KisBrokerAdapter\s*\(",
        ):
            assert not re.search(pat, text), (
                f"position_sizer.py 에 금지 패턴: /{pat}/"
            )

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
                f"position_sizer.py 안전 flag mutation 의심: /{pat}/"
            )

    def test_p08_helpers_exported(self):
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
        }
        for required in [
            "QuantityByPriceVerdict",
            "QuantityByPriceResult",
            "compute_paper_quantity_by_price",
        ]:
            assert required in names, f"{required} 가 position_sizer.py 에 없음"

    def test_p08_does_not_break_p4_p5_p6_p7(self):
        # 회귀: 기존 모듈들이 그대로 import 가능 + 핵심 verdict 라벨 보존.
        from app.auto_paper.affordability import HighPriceVerdict
        from app.auto_paper.affordability_check import AffordabilityVerdict
        from app.auto_paper.capital_state import CashCheckVerdict
        from app.auto_paper.min_lot_check import MinLotVerdict
        # 별개 verdict 라벨 유지 — 충돌 없음.
        assert AffordabilityVerdict.INSUFFICIENT_CASH.value == "INSUFFICIENT_CASH"
        assert MinLotVerdict.ALLOWED.value == "ALLOWED"
        assert HighPriceVerdict.EXCLUDED.value == "EXCLUDED"
        assert CashCheckVerdict.INSUFFICIENT_PAPER_CASH.value == "INSUFFICIENT_PAPER_CASH"
        # P-08 의 verdict 도 별개 enum.
        assert QuantityByPriceVerdict.OK.value == "OK"
        assert QuantityByPriceVerdict.MIN_LOT_NOT_AFFORDABLE.value == "MIN_LOT_NOT_AFFORDABLE"

    def test_existing_risk_based_sizer_preserved(self):
        # 회귀: #4-08 risk-based `compute_position_size` 가 그대로 동작.
        from app.auto_paper.position_sizer import (
            PositionSizingPolicy,
            SizingInput,
            compute_position_size,
        )
        pol = PositionSizingPolicy()
        # 최소 input 으로 호출만 검증 — 구체 결과는 #4-08 테스트에서.
        result = compute_position_size(
            SizingInput(
                strategy="test", symbol="X",
                price=10_000.0, account_equity=1_000_000.0,
                confidence=0.7, risk_flag_count=0,
                market_regime="TREND_UP", loop_state="RUNNING",
                stop_loss_pct=0.02,
            ),
            pol,
        )
        assert hasattr(result, "verdict")
