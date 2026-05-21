"""P-12: 중복 보유 방지 — 단위 + 정적 + API + P-07/P-10/P-11 통합 테스트.

검증 항목 (사용자 요청서 §8 1-20):
 1. BUY + 보유 0 → allowed=True
 2. BUY + 보유 > 0 + allow=False → allowed=False
 3. 차단 reason_code = DUPLICATE_POSITION_BUY_BLOCKED
 4. 메시지에 "이미 보유 중인 종목이라 추가 매수 차단" 포함
 5. BUY + 보유 > 0 + allow=True → allowed=True
 6. allow_additional_buy 기본값 = False
 7. SELL → NOT_APPLICABLE
 8. HOLD → NOT_APPLICABLE
 9. NO_ACTION → NOT_APPLICABLE
 10. 보유 0 → 보유 중 아님
 11. 보유 < 0 → INVALID_POSITION_QUANTITY
 12. symbol="" → INVALID_SYMBOL
 13. rejected / cancelled / blocked 제외
 14. accepted/filled BUY open quantity > 0 → 보유 중
 15. SELL 전량 청산 → 보유 0
 16. allow=True 여도 P-07 cash 별도 적용
 17. allow=True 여도 P-10 daily 별도 적용
 18. allow=True 여도 P-11 weight 별도 적용
 19. 차단된 BUY 는 cash / 포지션 / daily 미반영 (pure function)
 20. 정적 grep — 실거래 미변경
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
    DEFAULT_ALLOW_ADDITIONAL_BUY,
    reset_paper_capital_for_tests,
    resolve_additional_buy_policy,
)
from app.auto_paper.capital_state import (
    DUPLICATE_POSITION_ADDITIONAL_BUY_ALLOWED,
    DUPLICATE_POSITION_BUY_BLOCKED,
    DUPLICATE_POSITION_CHECK_NOT_APPLICABLE,
    DUPLICATE_POSITION_INVALID_QUANTITY,
    DUPLICATE_POSITION_INVALID_SYMBOL,
    DUPLICATE_POSITION_NO_EXISTING,
    DuplicatePositionResult,
    calculate_current_position_quantity,
    check_duplicate_position_buy,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "capital_state.py"
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
            DuplicatePositionResult(
                allowed=True, reason_code=DUPLICATE_POSITION_NO_EXISTING,
                reason_message="x",
                is_paper_only=False,  # type: ignore[arg-type]
            )

    def test_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            DuplicatePositionResult(
                allowed=True, reason_code=DUPLICATE_POSITION_NO_EXISTING,
                reason_message="x",
                is_order_signal=True,  # type: ignore[arg-type]
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            DuplicatePositionResult(
                allowed=True, reason_code=DUPLICATE_POSITION_NO_EXISTING,
                reason_message="x",
                is_live_authorization=True,  # type: ignore[arg-type]
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. 핵심 매트릭스 (검증 1-6)
# ─────────────────────────────────────────────────────────────────────────────


class TestCoreMatrix:
    def test_buy_no_position_allowed(self):
        # 검증 1: BUY + 보유 0 → allowed=True (NO_EXISTING_POSITION).
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=0,
            allow_additional_buy=False,
        )
        assert r.allowed is True
        assert r.reason_code == DUPLICATE_POSITION_NO_EXISTING
        assert r.current_position_quantity == 0
        assert r.allow_additional_buy is False

    def test_buy_existing_position_default_blocked(self):
        # 검증 2 & 3 & 6 (기본 False).
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=10,
            # allow_additional_buy 미지정 → default False.
        )
        assert r.allowed is False
        assert r.reason_code == DUPLICATE_POSITION_BUY_BLOCKED
        # 검증 4.
        assert "이미 보유 중인 종목이라 추가 매수 차단" in r.reason_message
        assert r.current_position_quantity == 10
        assert r.allow_additional_buy is False

    def test_default_allow_additional_buy_is_false(self):
        # 검증 6: function signature default False.
        import inspect
        sig = inspect.signature(check_duplicate_position_buy)
        param = sig.parameters["allow_additional_buy"]
        assert param.default is False
        # capital_config 측 default 도 False.
        assert DEFAULT_ALLOW_ADDITIONAL_BUY is False

    def test_buy_existing_position_with_optin_allowed(self):
        # 검증 5: allow=True 면 보유 중이어도 ALLOW.
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=10,
            allow_additional_buy=True,
        )
        assert r.allowed is True
        assert r.reason_code == DUPLICATE_POSITION_ADDITIONAL_BUY_ALLOWED
        assert r.allow_additional_buy is True
        # 메시지 — 다른 안전 layer 가 별도 적용됨을 명시.
        assert "현금" in r.reason_message
        assert "일일" in r.reason_message
        assert "RiskManager" in r.reason_message

    def test_to_dict_full_payload(self):
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=5,
        )
        d = r.to_dict()
        assert d["allowed"] is False
        assert d["reason_code"] == "DUPLICATE_POSITION_BUY_BLOCKED"
        assert d["symbol"] == "005930"
        assert d["current_position_quantity"] == 5
        assert d["allow_additional_buy"] is False
        assert d["blocked"] is True
        assert d["is_duplicate_blocked"] is True
        assert d["is_paper_only"] is True
        assert d["is_order_signal"] is False
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. NOT_APPLICABLE (검증 7-9)
# ─────────────────────────────────────────────────────────────────────────────


class TestNotApplicable:
    @pytest.mark.parametrize(
        "side",
        ["SELL", "EXIT", "CLOSE", "HOLD", "NO_OP", "NO_ACTION",
         None, "", "sell", "hold"],
    )
    def test_non_buy_skipped_even_with_position(self, side):
        # 검증 7-9: SELL / HOLD / NO_ACTION 등 → NOT_APPLICABLE.
        r = check_duplicate_position_buy(
            side=side, symbol="005930",
            current_position_quantity=100,    # 충분히 큰 보유
            allow_additional_buy=False,
        )
        assert r.allowed is True
        assert r.reason_code == DUPLICATE_POSITION_CHECK_NOT_APPLICABLE
        assert "BUY 가 아니므로" in r.reason_message

    def test_buy_synonyms_accepted(self):
        for tok in ("BUY", "buy", "OPEN", "open_long", "long",
                    "ENTER", "entry"):
            r = check_duplicate_position_buy(
                side=tok, symbol="005930",
                current_position_quantity=10,
            )
            assert r.reason_code == DUPLICATE_POSITION_BUY_BLOCKED, tok


# ─────────────────────────────────────────────────────────────────────────────
# 4. 보유 수량 경계 (검증 10, 11)
# ─────────────────────────────────────────────────────────────────────────────


class TestPositionQuantityBoundary:
    def test_zero_quantity_not_held(self):
        # 검증 10: 0 = 보유 안 함.
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=0,
        )
        assert r.allowed is True
        assert r.reason_code == DUPLICATE_POSITION_NO_EXISTING

    def test_one_quantity_held(self):
        # 1주만 보유해도 보유 중으로 본다.
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=1,
        )
        assert r.allowed is False
        assert r.reason_code == DUPLICATE_POSITION_BUY_BLOCKED

    def test_negative_quantity_invalid(self):
        # 검증 11.
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=-5,
        )
        assert r.allowed is False
        assert r.reason_code == DUPLICATE_POSITION_INVALID_QUANTITY

    def test_nan_invalid(self):
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=float("nan"),
        )
        assert r.reason_code == DUPLICATE_POSITION_INVALID_QUANTITY

    def test_inf_invalid(self):
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=float("inf"),
        )
        assert r.reason_code == DUPLICATE_POSITION_INVALID_QUANTITY

    def test_bool_invalid(self):
        # bool 은 int 의 서브타입 — 의도치 않은 입력 거부.
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=True,    # type: ignore[arg-type]
        )
        assert r.reason_code == DUPLICATE_POSITION_INVALID_QUANTITY

    def test_non_numeric_invalid(self):
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity="ten",   # type: ignore[arg-type]
        )
        assert r.reason_code == DUPLICATE_POSITION_INVALID_QUANTITY

    def test_float_with_fraction_truncates(self):
        # 10.7 → int(10.7) = 10. 보유 중으로 인식.
        r = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=10.7,
        )
        assert r.current_position_quantity == 10
        assert r.allowed is False


# ─────────────────────────────────────────────────────────────────────────────
# 5. INVALID_SYMBOL (검증 12)
# ─────────────────────────────────────────────────────────────────────────────


class TestInvalidSymbol:
    @pytest.mark.parametrize("symbol", ["", "  ", None])
    def test_empty_symbol_invalid(self, symbol):
        r = check_duplicate_position_buy(
            side="BUY", symbol=symbol,
            current_position_quantity=10,
        )
        assert r.allowed is False
        assert r.reason_code == DUPLICATE_POSITION_INVALID_SYMBOL

    def test_symbol_with_whitespace_stripped(self):
        r = check_duplicate_position_buy(
            side="BUY", symbol="  005930  ",
            current_position_quantity=0,
        )
        assert r.symbol == "005930"


# ─────────────────────────────────────────────────────────────────────────────
# 6. calculate_current_position_quantity (검증 13-15)
# ─────────────────────────────────────────────────────────────────────────────


class TestCalculateCurrentPositionQuantity:
    def test_filled_buy_counts_as_position(self):
        # 검증 14.
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "quantity": 10},
        ]
        assert calculate_current_position_quantity(
            orders, symbol="005930",
        ) == 10

    def test_accepted_buy_counts(self):
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "ACCEPTED",
             "quantity": 5},
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "quantity": 5},
        ]
        assert calculate_current_position_quantity(
            orders, symbol="005930",
        ) == 10

    def test_rejected_cancelled_blocked_excluded(self):
        # 검증 13.
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "quantity": 10},
            {"symbol": "005930", "side": "BUY", "status": "REJECTED",
             "quantity": 100},
            {"symbol": "005930", "side": "BUY", "status": "CANCELLED",
             "quantity": 200},
            {"symbol": "005930", "side": "BUY", "status": "BLOCKED",
             "quantity": 300},
        ]
        assert calculate_current_position_quantity(
            orders, symbol="005930",
        ) == 10

    def test_pending_status_excluded(self):
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "PENDING",
             "quantity": 100},
        ]
        assert calculate_current_position_quantity(
            orders, symbol="005930",
        ) == 0

    def test_status_none_excluded(self):
        orders = [
            {"symbol": "005930", "side": "BUY", "quantity": 100},
        ]
        assert calculate_current_position_quantity(
            orders, symbol="005930",
        ) == 0

    def test_sell_fully_liquidates(self):
        # 검증 15.
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "quantity": 10},
            {"symbol": "005930", "side": "SELL", "status": "FILLED",
             "quantity": 10},
        ]
        assert calculate_current_position_quantity(
            orders, symbol="005930",
        ) == 0

    def test_partial_sell_keeps_remainder(self):
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "quantity": 10},
            {"symbol": "005930", "side": "SELL", "status": "FILLED",
             "quantity": 3},
        ]
        assert calculate_current_position_quantity(
            orders, symbol="005930",
        ) == 7

    def test_sell_only_clamped_to_zero(self):
        # 비정상 케이스 — SELL 만 있음. 0 clamp.
        orders = [
            {"symbol": "005930", "side": "SELL", "status": "FILLED",
             "quantity": 5},
        ]
        assert calculate_current_position_quantity(
            orders, symbol="005930",
        ) == 0

    def test_different_symbol_excluded(self):
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "quantity": 10},
            {"symbol": "000660", "side": "BUY", "status": "FILLED",
             "quantity": 999},
        ]
        assert calculate_current_position_quantity(
            orders, symbol="005930",
        ) == 10

    def test_empty_orders_returns_zero(self):
        assert calculate_current_position_quantity(
            [], symbol="005930",
        ) == 0
        assert calculate_current_position_quantity(
            None, symbol="005930",
        ) == 0

    def test_works_with_dataclass_like_objects(self):
        from types import SimpleNamespace
        orders = [
            SimpleNamespace(
                symbol="005930", side="BUY", status="FILLED", quantity=10,
            ),
            SimpleNamespace(
                symbol="005930", side="SELL", status="FILLED", quantity=3,
            ),
        ]
        assert calculate_current_position_quantity(
            orders, symbol="005930",
        ) == 7

    def test_uses_filled_quantity_when_available(self):
        # filled_quantity 가 있으면 우선 사용.
        orders = [
            {"symbol": "005930", "side": "BUY", "status": "FILLED",
             "filled_quantity": 5, "quantity": 999},
        ]
        assert calculate_current_position_quantity(
            orders, symbol="005930",
        ) == 5


# ─────────────────────────────────────────────────────────────────────────────
# 7. resolve_additional_buy_policy (검증 6)
# ─────────────────────────────────────────────────────────────────────────────


class TestResolvePolicy:
    def test_default_system_false(self):
        allow, source = resolve_additional_buy_policy()
        assert allow is False
        assert source == "system_default"

    def test_manual_true(self):
        allow, source = resolve_additional_buy_policy(
            manual_allow_additional_buy=True,
        )
        assert allow is True
        assert source == "manual"

    def test_manual_false_explicit(self):
        allow, source = resolve_additional_buy_policy(
            manual_allow_additional_buy=False,
        )
        # explicit False 도 manual.
        assert allow is False
        assert source == "manual"


# ─────────────────────────────────────────────────────────────────────────────
# 8. P-07 / P-10 / P-11 통합 — allow=True 여도 다른 안전 layer 별도 적용 (검증 16-18)
# ─────────────────────────────────────────────────────────────────────────────


class TestOtherLayersStillApply:
    def test_allow_additional_buy_does_not_bypass_cash_check(self):
        # 검증 16: allow=True 여도 P-07 cash check 별도 적용.
        from app.auto_paper.capital_state import (
            CashCheckVerdict,
            check_buy_cash_sufficient,
        )

        # P-12: 보유 중 + allow=True → ADDITIONAL_BUY_ALLOWED.
        dup = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=10,
            allow_additional_buy=True,
        )
        assert dup.allowed is True

        # P-07: cash 부족 → 별도 차단.
        cash = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=100_000, quantity=10,
            available_cash_krw=100_000,    # 부족
        )
        assert cash.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH

    def test_allow_additional_buy_does_not_bypass_daily_limit(self):
        # 검증 17.
        from app.risk.loss_limits import (
            DAILY_BUY_LIMIT_EXCEEDED,
            check_daily_buy_limit,
        )

        dup = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=10,
            allow_additional_buy=True,
        )
        assert dup.allowed is True

        # P-10: 일일 한도 초과 → 별도 차단.
        daily = check_daily_buy_limit(
            side="BUY", price=100_000, quantity=7,
            today_buy_used_amount=2_500_000,
            max_daily_buy_amount=3_000_000,
        )
        assert daily.reason_code == DAILY_BUY_LIMIT_EXCEEDED

    def test_allow_additional_buy_does_not_bypass_symbol_weight(self):
        # 검증 18.
        from app.risk.position_limits import (
            SYMBOL_WEIGHT_LIMIT_EXCEEDED,
            check_symbol_weight_limit,
        )

        dup = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=10,
            allow_additional_buy=True,
        )
        assert dup.allowed is True

        # P-11: 종목 비중 초과 → 별도 차단.
        weight = check_symbol_weight_limit(
            side="BUY", symbol="005930",
            price=100_000, quantity=7,
            total_paper_equity=10_000_000,
            current_symbol_exposure_amount=1_600_000,
            max_symbol_weight_pct=0.20,
        )
        assert weight.reason_code == SYMBOL_WEIGHT_LIMIT_EXCEEDED


class TestBlockedBuyDoesNotMutateOtherState:
    def test_blocked_buy_pure_function_no_state_change(self):
        # 검증 19: pure function — 호출해도 cash / position 등 상태 변경 0건.
        # caller 가 차단된 BUY 를 *반영하지 않으면* 다음 호출에서 같은 결과.
        r1 = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=10,
        )
        r2 = check_duplicate_position_buy(
            side="BUY", symbol="005930",
            current_position_quantity=10,
        )
        assert r1.allowed is False
        assert r2.allowed is False
        assert r1.reason_code == r2.reason_code


# ─────────────────────────────────────────────────────────────────────────────
# 9. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestApiEndpoints:
    def test_preview_no_existing_position_allowed(self, api_client):
        r = api_client.post(
            "/api/auto-paper/duplicate-position/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "current_position_quantity": 0,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["allowed"] is True
        assert body["reason_code"] == "NO_EXISTING_POSITION"
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False

    def test_preview_duplicate_default_blocked(self, api_client):
        r = api_client.post(
            "/api/auto-paper/duplicate-position/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "current_position_quantity": 10,
            },
        )
        body = r.json()
        assert body["allowed"] is False
        assert body["reason_code"] == "DUPLICATE_POSITION_BUY_BLOCKED"
        assert "이미 보유 중인 종목이라 추가 매수 차단" in body["reason_message"]
        assert body["allow_additional_buy"] is False

    def test_preview_with_explicit_optin_allows(self, api_client):
        r = api_client.post(
            "/api/auto-paper/duplicate-position/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "current_position_quantity": 10,
                "allow_additional_buy": True,
            },
        )
        body = r.json()
        assert body["allowed"] is True
        assert body["reason_code"] == "ADDITIONAL_BUY_ALLOWED"
        assert body["allow_additional_buy"] is True

    def test_preview_sell_not_applicable(self, api_client):
        r = api_client.post(
            "/api/auto-paper/duplicate-position/preview",
            json={
                "side": "SELL", "symbol": "005930",
                "current_position_quantity": 100,
            },
        )
        body = r.json()
        assert body["allowed"] is True
        assert body["reason_code"] == "DUPLICATE_POSITION_CHECK_NOT_APPLICABLE"

    def test_preview_resolves_from_manual_when_not_explicit(self, api_client):
        # allow_additional_buy 미주입 + manual=True → resolved manual.
        r = api_client.post(
            "/api/auto-paper/duplicate-position/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "current_position_quantity": 10,
                "manual_allow_additional_buy": True,
            },
        )
        body = r.json()
        assert body["resolved_source"] == "manual"
        assert body["allow_additional_buy"] is True
        assert body["allowed"] is True

    def test_preview_falls_back_to_system_default(self, api_client):
        r = api_client.post(
            "/api/auto-paper/duplicate-position/preview",
            json={
                "side": "BUY", "symbol": "005930",
                "current_position_quantity": 10,
            },
        )
        body = r.json()
        assert body["resolved_source"] == "system_default"
        assert body["allow_additional_buy"] is False
        assert body["default_allow"] is False

    def test_preview_invalid_symbol(self, api_client):
        r = api_client.post(
            "/api/auto-paper/duplicate-position/preview",
            json={
                "side": "BUY", "symbol": "",
                "current_position_quantity": 10,
            },
        )
        body = r.json()
        assert body["allowed"] is False
        assert body["reason_code"] == "INVALID_SYMBOL"

    def test_resolve_endpoint_default(self, api_client):
        r = api_client.post(
            "/api/auto-paper/duplicate-position/resolve",
            json={},
        )
        body = r.json()
        assert body["allow_additional_buy"] is False
        assert body["source"] == "system_default"
        assert body["default_allow"] is False
        assert body["is_paper_only"] is True

    def test_resolve_endpoint_manual(self, api_client):
        r = api_client.post(
            "/api/auto-paper/duplicate-position/resolve",
            json={"manual_allow_additional_buy": True},
        )
        body = r.json()
        assert body["allow_additional_buy"] is True
        assert body["source"] == "manual"


# ─────────────────────────────────────────────────────────────────────────────
# 10. 정적 가드 (검증 20)
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_module_file_exists(self):
        assert _MODULE_PATH.exists()

    def test_no_broker_call_patterns_in_p12_section(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        marker = "# P-12: 중복 보유 방지 (Duplicate Position Buy Guard)"
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
                f"P-12 코드 영역 금지 패턴: /{pat}/"
            )

    def test_no_safety_flag_mutations(self):
        text = _MODULE_PATH.read_text(encoding="utf-8")
        marker = "# P-12: 중복 보유 방지 (Duplicate Position Buy Guard)"
        section = text[text.find(marker):]
        for pat in [
            r"ENABLE_LIVE_TRADING\s*=\s*True",
            r"ENABLE_AI_EXECUTION\s*=\s*True",
            r"KIS_IS_PAPER\s*=\s*False",
        ]:
            assert not re.search(pat, section), (
                f"P-12 안전 flag mutation 의심: /{pat}/"
            )

    def test_p12_helpers_exported(self):
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
        }
        for required in [
            "DuplicatePositionResult",
            "check_duplicate_position_buy",
            "calculate_current_position_quantity",
        ]:
            assert required in names

    def test_p07_p10_p11_modules_preserved(self):
        # 회귀: 기존 P-07/P-10/P-11 함수 그대로 import 가능.
        from app.auto_paper.capital_state import check_buy_cash_sufficient
        from app.risk.loss_limits import check_daily_buy_limit
        from app.risk.position_limits import check_symbol_weight_limit
        # 호출 가능 검증.
        assert callable(check_buy_cash_sufficient)
        assert callable(check_daily_buy_limit)
        assert callable(check_symbol_weight_limit)
