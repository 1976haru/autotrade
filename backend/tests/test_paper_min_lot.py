"""P-05: Paper 최소 1주 매수 조건 가드 — 단위 + 정적 + API 테스트.

검증 항목 (사용자 요청서):
- quantity=0 → BLOCKED_ZERO_QUANTITY
- quantity=1 → ALLOWED
- quantity>1 → ALLOWED
- 소수점 (0.5, 1.5, 2.7) → BLOCKED_FRACTIONAL_QUANTITY (소수점 주식 불가)
- 음수 → BLOCKED_NEGATIVE_QUANTITY
- None → BLOCKED_ZERO_QUANTITY
- 비-숫자 (str, bool) → BLOCKED_FRACTIONAL_QUANTITY (type 거부)
- floor 사용 검증 (compute_paper_affordable_lot)
- 반올림 금지 — 항상 내림
- BUY/HOLD/SELL/EXIT 분리 (BUY 만 검사, 나머지 SKIP_NON_BUY)
- is_paper_only=true / is_live_authorization=false / 실거래 호출 0건
- 회귀 테스트 — P-01/02/03/04 기능 영향 없음
"""

from __future__ import annotations

import ast
import math
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auto_paper.capital_config import (
    reset_paper_capital_for_tests,
    set_paper_capital_config,
    set_per_symbol_allocation,
)
from app.auto_paper.min_lot_check import (
    MinLotCheckResult,
    MinLotVerdict,
    compute_paper_affordable_lot,
    validate_paper_min_lot,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app


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
# 1. MinLotCheckResult dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestDataclassInvariants:
    def test_is_paper_only_must_be_true(self):
        with pytest.raises(ValueError, match="is_paper_only"):
            MinLotCheckResult(
                verdict=MinLotVerdict.ALLOWED,
                requested_quantity=1, floored_quantity=1,
                is_paper_only=False,  # type: ignore[arg-type]
            )

    def test_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            MinLotCheckResult(
                verdict=MinLotVerdict.ALLOWED,
                requested_quantity=1, floored_quantity=1,
                is_order_signal=True,  # type: ignore[arg-type]
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            MinLotCheckResult(
                verdict=MinLotVerdict.ALLOWED,
                requested_quantity=1, floored_quantity=1,
                is_live_authorization=True,  # type: ignore[arg-type]
            )

    def test_negative_floored_quantity_rejected(self):
        with pytest.raises(ValueError, match="floored_quantity"):
            MinLotCheckResult(
                verdict=MinLotVerdict.BLOCKED_ZERO_QUANTITY,
                requested_quantity=0, floored_quantity=-1,
            )

    def test_to_dict_contract(self):
        r = validate_paper_min_lot(action="BUY", quantity=3)
        d = r.to_dict()
        for key in (
            "verdict", "requested_quantity", "floored_quantity", "action",
            "symbol", "reason_ko", "risk_flag", "metadata",
            "is_allowed", "is_order_signal", "is_live_authorization",
            "is_paper_only",
        ):
            assert key in d
        assert d["is_allowed"] is True
        assert d["floored_quantity"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# 2. validate_paper_min_lot — verdict 매트릭스 (사용자 요청서)
# ─────────────────────────────────────────────────────────────────────────────


class TestVerdictMatrix:
    def test_quantity_zero_blocked(self):
        r = validate_paper_min_lot(action="BUY", quantity=0)
        assert r.verdict == MinLotVerdict.BLOCKED_ZERO_QUANTITY
        assert r.floored_quantity == 0
        assert r.risk_flag == "zero_quantity"
        assert "0주" in r.reason_ko

    def test_quantity_one_allowed(self):
        r = validate_paper_min_lot(action="BUY", quantity=1)
        assert r.verdict == MinLotVerdict.ALLOWED
        assert r.floored_quantity == 1
        assert r.is_allowed is True

    @pytest.mark.parametrize("q", [2, 3, 5, 10, 100, 9999])
    def test_quantity_above_one_allowed(self, q):
        r = validate_paper_min_lot(action="BUY", quantity=q)
        assert r.verdict == MinLotVerdict.ALLOWED
        assert r.floored_quantity == q

    @pytest.mark.parametrize("q", [0.5, 1.5, 2.7, 100.001])
    def test_fractional_quantity_blocked(self, q):
        r = validate_paper_min_lot(action="BUY", quantity=q)
        assert r.verdict == MinLotVerdict.BLOCKED_FRACTIONAL_QUANTITY
        assert r.risk_flag == "fractional_quantity"
        assert "소수점" in r.reason_ko
        # 보수적 floor 가 carry 되어 있음.
        assert r.floored_quantity == int(math.floor(q))

    def test_float_integer_value_allowed(self):
        # 1.0 같은 *정수 값 float* 은 허용 — 운영자가 float 으로 보내도 정수 의도.
        r = validate_paper_min_lot(action="BUY", quantity=2.0)
        assert r.verdict == MinLotVerdict.ALLOWED
        assert r.floored_quantity == 2

    def test_negative_blocked(self):
        r = validate_paper_min_lot(action="BUY", quantity=-1)
        assert r.verdict == MinLotVerdict.BLOCKED_NEGATIVE_QUANTITY
        assert r.risk_flag == "negative_quantity"

    def test_negative_float_blocked(self):
        r = validate_paper_min_lot(action="BUY", quantity=-0.5)
        # 음수가 fractional 보다 먼저 매칭 (verdict 우선순위 §3).
        assert r.verdict == MinLotVerdict.BLOCKED_NEGATIVE_QUANTITY

    def test_none_treated_as_zero(self):
        r = validate_paper_min_lot(action="BUY", quantity=None)
        assert r.verdict == MinLotVerdict.BLOCKED_ZERO_QUANTITY

    def test_bool_quantity_rejected(self):
        # bool 은 int 의 서브타입이지만 정수 의도가 아니라 거부.
        r = validate_paper_min_lot(action="BUY", quantity=True)  # type: ignore[arg-type]
        assert r.verdict == MinLotVerdict.BLOCKED_FRACTIONAL_QUANTITY
        assert r.risk_flag == "invalid_quantity_type"

    def test_string_quantity_rejected(self):
        r = validate_paper_min_lot(action="BUY", quantity="3")  # type: ignore[arg-type]
        assert r.verdict == MinLotVerdict.BLOCKED_FRACTIONAL_QUANTITY
        assert r.risk_flag == "invalid_quantity_type"

    def test_nan_rejected(self):
        r = validate_paper_min_lot(action="BUY", quantity=float("nan"))
        assert r.verdict == MinLotVerdict.BLOCKED_FRACTIONAL_QUANTITY
        assert r.risk_flag == "invalid_quantity_nonfinite"

    def test_inf_rejected(self):
        r = validate_paper_min_lot(action="BUY", quantity=float("inf"))
        assert r.verdict == MinLotVerdict.BLOCKED_FRACTIONAL_QUANTITY
        assert r.risk_flag == "invalid_quantity_nonfinite"

    @pytest.mark.parametrize("action", ["SELL", "EXIT", "HOLD", "NO_OP", "WATCH"])
    def test_non_buy_actions_skip(self, action):
        # SELL/EXIT 은 quantity=0 이어도 차단되지 않음 — 청산은 자유.
        r = validate_paper_min_lot(action=action, quantity=0)
        assert r.verdict == MinLotVerdict.SKIP_NON_BUY
        # 소수점 quantity 라도 SELL 은 SKIP.
        r2 = validate_paper_min_lot(action=action, quantity=1.5)
        assert r2.verdict == MinLotVerdict.SKIP_NON_BUY

    def test_case_insensitive_buy(self):
        r1 = validate_paper_min_lot(action="buy", quantity=1)
        r2 = validate_paper_min_lot(action="BUY", quantity=1)
        assert r1.verdict == r2.verdict == MinLotVerdict.ALLOWED


# ─────────────────────────────────────────────────────────────────────────────
# 3. compute_paper_affordable_lot — floor 정책
# ─────────────────────────────────────────────────────────────────────────────


class TestComputeAffordableLot:
    @pytest.mark.parametrize("cap,cash,price,expected", [
        # 정상 케이스.
        (1_000_000, 10_000_000, 70_000, 14),    # min=1M, 1M//70k = 14
        (1_000_000, 10_000_000, 200_000, 5),
        (1_000_000, 10_000_000, 900_000, 1),
        (1_000_000, 10_000_000, 1_000_000, 1),  # 정확히 1주.
        # cash 가 cap 보다 작은 경우.
        (10_000_000, 500_000, 100_000, 5),
        # floor 검증 — 1주 가격이 cap 을 약간 초과.
        (1_000_000, 10_000_000, 1_000_001, 0),  # 1.0M//1.0M+1 = 0주.
        # 1주 가격이 cap 직전 — floor 가 적용되어 1주만.
        (1_000_000, 10_000_000, 999_999, 1),
        # cap=0 (cap 무한) 케이스 — cash 만 제약.
        (0, 500_000, 100_000, 5),
        # 0원 가격 → 0 반환.
        (1_000_000, 10_000_000, 0, 0),
        # 음수 가격 → 0 반환.
        (1_000_000, 10_000_000, -1, 0),
    ])
    def test_floor_policy(self, cap, cash, price, expected):
        q = compute_paper_affordable_lot(
            effective_per_symbol_cap_krw=cap,
            available_cash_krw=cash,
            price=price,
        )
        assert q == expected

    def test_none_price_returns_zero(self):
        assert compute_paper_affordable_lot(
            effective_per_symbol_cap_krw=1_000_000,
            available_cash_krw=10_000_000,
            price=None,
        ) == 0

    def test_returns_integer_type(self):
        q = compute_paper_affordable_lot(
            effective_per_symbol_cap_krw=1_000_000,
            available_cash_krw=10_000_000,
            price=70_000,
        )
        assert isinstance(q, int)
        # 절대 float / numpy / Decimal 등 아님 — 호출자가 % 같은 정수 연산 가능.

    def test_floor_not_round(self):
        # 14.99 의 floor 는 14 — 반올림되어 15 가 되면 안 됨.
        q = compute_paper_affordable_lot(
            effective_per_symbol_cap_krw=1_000_000,
            available_cash_krw=10_000_000,
            price=66_711,    # 1M / 66711 ≈ 14.99 → floor=14
        )
        assert q == 14

    def test_negative_cash_clamped_to_zero(self):
        q = compute_paper_affordable_lot(
            effective_per_symbol_cap_krw=1_000_000,
            available_cash_krw=-500_000,
            price=70_000,
        )
        assert q == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. 정적 import 가드
# ─────────────────────────────────────────────────────────────────────────────


class TestNoBrokerOrLiveImports:
    def test_module_no_banned_imports(self):
        src = (
            Path(__file__).resolve().parent.parent
            / "app" / "auto_paper" / "min_lot_check.py"
        )
        text = src.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(src))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                for banned in (
                    "app.brokers.kis", "app.brokers.kis_client",
                    "app.brokers.live_broker",
                    "app.execution.executor", "app.execution.order_router",
                    "app.kis_paper.engine",
                    "anthropic", "openai", "httpx", "requests",
                    "app.core.config",
                ):
                    assert not m.startswith(banned), (
                        f"min_lot_check.py 가 금지 모듈 '{m}' import"
                    )

    def test_no_broker_call_patterns(self):
        src = (
            Path(__file__).resolve().parent.parent
            / "app" / "auto_paper" / "min_lot_check.py"
        )
        text = src.read_text(encoding="utf-8")
        for pat in (
            r"\bbroker\.place_order\s*\(",
            r"\bbroker\.cancel_order\s*\(",
            r"\broute_order\s*\(",
            r"\bKisClient\s*\(",
            r"\bKisBrokerAdapter\s*\(",
            r"OrderExecutor\s*\(",
            r"\.enable_live_trading\s*=",
            r"\.enable_ai_execution\s*=",
        ):
            assert not re.search(pat, text), (
                f"min_lot_check.py 에 금지 패턴: /{pat}/"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 5. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestApi:
    def test_validate_allows_one(self, api_client):
        res = api_client.post(
            "/api/auto-paper/min-lot/validate",
            json={"action": "BUY", "symbol": "005930", "quantity": 1},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["verdict"] == "ALLOWED"
        assert body["floored_quantity"] == 1
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False

    def test_validate_blocks_zero(self, api_client):
        res = api_client.post(
            "/api/auto-paper/min-lot/validate",
            json={"action": "BUY", "quantity": 0},
        )
        body = res.json()
        assert body["verdict"] == "BLOCKED_ZERO_QUANTITY"

    def test_validate_blocks_fractional(self, api_client):
        res = api_client.post(
            "/api/auto-paper/min-lot/validate",
            json={"action": "BUY", "quantity": 1.5},
        )
        body = res.json()
        assert body["verdict"] == "BLOCKED_FRACTIONAL_QUANTITY"
        assert "소수점" in body["reason_ko"]

    def test_validate_sell_skipped(self, api_client):
        res = api_client.post(
            "/api/auto-paper/min-lot/validate",
            json={"action": "SELL", "quantity": 0},
        )
        assert res.json()["verdict"] == "SKIP_NON_BUY"

    def test_preview_examples_with_default_config(self, api_client):
        res = api_client.get("/api/auto-paper/min-lot/preview")
        assert res.status_code == 200
        body = res.json()
        # default config: cap=1M, initial_cash=10M.
        assert body["effective_per_symbol_cap_krw"] == 1_000_000
        assert body["min_lot_quantity"] == 1
        assert body["fractional_share_supported"] is False
        assert body["rounding_policy"] == "floor"
        examples = body["examples"]
        # 6개 예시.
        assert len(examples) == 6
        # 가격 50k → 1M//50k = 20주.
        ex_50k = next(e for e in examples if e["price"] == 50_000)
        assert ex_50k["affordable_quantity"] == 20
        assert ex_50k["is_affordable"] is True
        # 가격 2M → cap=1M 초과 → 0주.
        ex_2m = next(e for e in examples if e["price"] == 2_000_000)
        assert ex_2m["affordable_quantity"] == 0
        assert ex_2m["is_affordable"] is False

    def test_preview_reflects_updated_cap(self, api_client):
        # 시드머니 30M + PCT 10% → cap=3M.
        api_client.post(
            "/api/auto-paper/capital-config",
            json={"initial_cash": 30_000_000},
        )
        api_client.post(
            "/api/auto-paper/per-symbol-allocation",
            json={"mode": "PCT_OF_EQUITY"},
        )
        res = api_client.get("/api/auto-paper/min-lot/preview")
        body = res.json()
        assert body["effective_per_symbol_cap_krw"] == 3_000_000
        # 2M 가격 → 3M//2M = 1주.
        ex_2m = next(e for e in body["examples"] if e["price"] == 2_000_000)
        assert ex_2m["affordable_quantity"] == 1
        assert ex_2m["is_affordable"] is True

    def test_payload_no_secrets(self, api_client):
        import json
        res = api_client.get("/api/auto-paper/min-lot/preview")
        text = json.dumps(res.json()).lower()
        for needle in ("kis_app_key", "kis_app_secret", "anthropic_api_key",
                       "openai_api_key", "telegram_bot_token", "sk-",
                       "bearer ", "kis_account_no"):
            assert needle not in text

    def test_notice_says_paper_only(self, api_client):
        res = api_client.get("/api/auto-paper/min-lot/preview")
        body = res.json()
        assert "Paper 전용" in body["notice"]


# ─────────────────────────────────────────────────────────────────────────────
# 6. 회귀 — P-01/02/03/04 모듈 영향 없음
# ─────────────────────────────────────────────────────────────────────────────


class TestRegressionWithPriorP:
    """P-05 추가가 기존 P-01~04 모듈에 영향을 주지 않음을 확인."""

    def test_capital_config_default_intact(self):
        # P-01 default 그대로.
        from app.auto_paper.capital_config import (
            DEFAULT_PAPER_INITIAL_CASH,
            ALLOWED_PAPER_INITIAL_CASH,
            get_paper_capital_config,
        )
        assert DEFAULT_PAPER_INITIAL_CASH == 10_000_000
        assert ALLOWED_PAPER_INITIAL_CASH == (10_000_000, 30_000_000, 50_000_000)
        cfg = get_paper_capital_config()
        assert cfg.initial_cash == 10_000_000

    def test_per_symbol_default_intact(self):
        # P-02 default 그대로.
        from app.auto_paper.capital_config import (
            DEFAULT_PER_SYMBOL_MAX_KRW,
            ALLOWED_PER_SYMBOL_MAX_KRW,
            DEFAULT_PER_SYMBOL_MAX_PCT,
        )
        assert DEFAULT_PER_SYMBOL_MAX_KRW == 1_000_000
        assert ALLOWED_PER_SYMBOL_MAX_KRW == (1_000_000, 2_000_000)
        assert DEFAULT_PER_SYMBOL_MAX_PCT == 0.10

    def test_max_concurrent_default_intact(self):
        # P-03 default 그대로.
        from app.auto_paper.capital_config import (
            DEFAULT_MAX_CONCURRENT_POSITIONS,
            ALLOWED_MAX_CONCURRENT_POSITIONS,
        )
        assert DEFAULT_MAX_CONCURRENT_POSITIONS == 3
        assert ALLOWED_MAX_CONCURRENT_POSITIONS == (3, 5, 10)

    def test_affordability_helper_intact(self):
        # P-04 helper 가 그대로 작동 + 본 모듈과 compute 결과 일치.
        from app.auto_paper.affordability_check import check_paper_affordability
        r = check_paper_affordability(
            action="BUY", symbol="005930", price=70_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        assert r.verdict.value == "AFFORDABLE"
        # P-04 의 affordable_quantity 가 P-05 compute 와 *같은 값*.
        assert r.affordable_quantity == compute_paper_affordable_lot(
            effective_per_symbol_cap_krw=1_000_000,
            available_cash_krw=10_000_000,
            price=70_000,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7. 안전 flag mutate invariant
# ─────────────────────────────────────────────────────────────────────────────


class TestSafetyFlagsUntouched:
    def test_validate_does_not_mutate_settings(self):
        from app.core.config import get_settings
        before = get_settings()
        validate_paper_min_lot(action="BUY", quantity=1)
        compute_paper_affordable_lot(
            effective_per_symbol_cap_krw=1_000_000,
            available_cash_krw=10_000_000,
            price=70_000,
        )
        after = get_settings()
        assert before.enable_live_trading == after.enable_live_trading is False
        assert before.enable_ai_execution == after.enable_ai_execution is False
        assert before.enable_futures_live_trading == after.enable_futures_live_trading is False
        assert before.kis_is_paper == after.kis_is_paper is True
