"""P-04: Paper BUY affordability check — 단위 + 정적 + API 테스트.

검증 항목 (사용자 요청서 §6, §9):
- 가격 180만원, 종목당 한도 100만원 → PRICE_OVER_CAP (BUY 차단)
- 가격 90만원, 한도 100만원 → AFFORDABLE + affordable_quantity=1
- 가격 0 / 음수 → INVALID_PRICE
- 가격 None → MISSING_PRICE
- 현금 부족 → INSUFFICIENT_CASH
- floor(min/price) < 1 → BELOW_MIN_LOT (corner case)
- max_concurrent_positions 초과 → MAX_POSITIONS_REACHED
- SELL/EXIT/HOLD → SKIP_NON_BUY
- is_paper_only=true / is_live_authorization=false / 실거래 호출 0건
- affordability_check 모듈 broker/KIS/OrderExecutor/HTTP/settings import 0건
- API POST /affordability/preview 동작
- reason_ko 가 사람이 이해 가능한 한국어
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

from app.auto_paper.affordability_check import (
    AffordabilityResult,
    AffordabilityVerdict,
    check_paper_affordability,
)
from app.auto_paper.capital_config import (
    reset_paper_capital_for_tests,
    set_max_concurrent_positions,
    set_paper_capital_config,
    set_per_symbol_allocation,
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
# 1. AffordabilityResult dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestDataclassInvariants:
    def test_default_paper_only(self):
        r = check_paper_affordability(
            action="BUY", symbol="005930", price=70_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        assert r.is_paper_only is True
        assert r.is_order_signal is False
        assert r.is_live_authorization is False

    def test_invariant_is_order_signal_false_only(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            AffordabilityResult(
                verdict=AffordabilityVerdict.AFFORDABLE,
                is_order_signal=True,  # type: ignore[arg-type]
            )

    def test_invariant_is_live_authorization_false_only(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            AffordabilityResult(
                verdict=AffordabilityVerdict.AFFORDABLE,
                is_live_authorization=True,  # type: ignore[arg-type]
            )

    def test_invariant_is_paper_only_true(self):
        with pytest.raises(ValueError, match="is_paper_only"):
            AffordabilityResult(
                verdict=AffordabilityVerdict.AFFORDABLE,
                is_paper_only=False,  # type: ignore[arg-type]
            )

    def test_negative_affordable_quantity_rejected(self):
        with pytest.raises(ValueError, match="affordable_quantity"):
            AffordabilityResult(
                verdict=AffordabilityVerdict.AFFORDABLE,
                affordable_quantity=-1,
            )

    def test_to_dict_contract(self):
        r = check_paper_affordability(
            action="BUY", symbol="005930", price=70_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        d = r.to_dict()
        for key in (
            "verdict", "symbol", "action", "price",
            "effective_per_symbol_cap_krw", "available_cash_krw",
            "affordable_quantity", "current_held_unique_symbols",
            "max_concurrent_positions", "is_existing_position",
            "reason_ko", "risk_flag", "metadata",
            "is_affordable", "is_order_signal",
            "is_live_authorization", "is_paper_only",
        ):
            assert key in d, f"missing key: {key}"
        assert d["is_affordable"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. check_paper_affordability — verdict 매트릭스 (사용자 요청서 §6/§9)
# ─────────────────────────────────────────────────────────────────────────────


class TestVerdictMatrix:
    def test_skhynix_180m_blocked_by_cap_1m(self):
        # 사용자 요청서 예시 — 종목당 한도 100만원 + 1주 180만원 = PRICE_OVER_CAP.
        r = check_paper_affordability(
            action="BUY", symbol="000660", price=1_800_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        assert r.verdict == AffordabilityVerdict.PRICE_OVER_CAP
        assert r.affordable_quantity == 0
        assert r.risk_flag == "price_over_per_symbol_cap"
        # 사람이 읽을 수 있는 한국어 사유.
        assert "1주 가격이 종목당 투자금 한도" in r.reason_ko
        assert "1,000,000" in r.reason_ko

    def test_price_900k_under_cap_1m_affordable(self):
        r = check_paper_affordability(
            action="BUY", symbol="005930", price=900_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        assert r.verdict == AffordabilityVerdict.AFFORDABLE
        # min(1M, 10M) / 900k = 1.11 → 1주.
        assert r.affordable_quantity == 1

    def test_price_zero_rejected(self):
        r = check_paper_affordability(
            action="BUY", symbol="X", price=0,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        assert r.verdict == AffordabilityVerdict.INVALID_PRICE
        assert r.risk_flag == "invalid_price"

    def test_price_negative_rejected(self):
        r = check_paper_affordability(
            action="BUY", symbol="X", price=-100,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        assert r.verdict == AffordabilityVerdict.INVALID_PRICE

    def test_price_none_rejected(self):
        r = check_paper_affordability(
            action="BUY", symbol="X", price=None,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        assert r.verdict == AffordabilityVerdict.MISSING_PRICE
        assert r.risk_flag == "missing_price"

    def test_insufficient_cash(self):
        r = check_paper_affordability(
            action="BUY", symbol="005930", price=500_000,
            available_cash_krw=100_000,    # 1주도 못 사는 현금
            effective_per_symbol_cap_krw=1_000_000,
        )
        assert r.verdict == AffordabilityVerdict.INSUFFICIENT_CASH
        assert r.affordable_quantity == 0

    def test_below_min_lot_corner(self):
        # price=600,000 + cap=500,000 < price 라 PRICE_OVER_CAP 으로 먼저 잡힘.
        # BELOW_MIN_LOT 은 cap > 0, price > 0, cap >= price, cash >= price 인데
        # min(cap, cash) // price < 1 인 경우 → 실질적으로 cap < price + cash >= price
        # 발생하기 어려운 케이스. cap=0 (cap 없음) + cash >= price 면 min=cash.
        # 따라서 BELOW_MIN_LOT 은 cap=0 이고 cash < price (이 경우 5번에서 잡힘).
        # 본 verdict 는 *방어선* — price 정상이지만 budget 0 같은 corner.
        r = check_paper_affordability(
            action="BUY", symbol="X", price=1_000_000,
            available_cash_krw=0,         # 현금 0 — 5번에서 INSUFFICIENT_CASH
            effective_per_symbol_cap_krw=2_000_000,
        )
        # cash < price 가 먼저 매칭 → INSUFFICIENT_CASH.
        assert r.verdict == AffordabilityVerdict.INSUFFICIENT_CASH

    def test_below_min_lot_when_budget_too_small(self):
        # cap=500 + cash=1_000_000 + price=400 → min=500, 500//400 = 1 → ALLOW.
        # cap=300 + price=400 → cap < price → PRICE_OVER_CAP.
        # 진짜 BELOW_MIN_LOT 발생 조건: price <= cap, price <= cash, 그러나
        # min(cap, cash) // price < 1 — 수학적으로 불가능 (price <= min => >= 1주).
        # 따라서 BELOW_MIN_LOT 은 *방어선* 일 뿐 일반 케이스에서 매칭되지 않음.
        # 본 테스트는 그 *논리적 invariant* (다른 verdict 가 먼저 잡음) 만 확인.
        # cap = price = cash 의 deg corner — min=cap=price, qty=1, ALLOW.
        r = check_paper_affordability(
            action="BUY", symbol="X", price=100_000,
            available_cash_krw=100_000,
            effective_per_symbol_cap_krw=100_000,
        )
        assert r.verdict == AffordabilityVerdict.AFFORDABLE
        assert r.affordable_quantity == 1

    def test_max_positions_reached_blocks_new_symbol(self):
        r = check_paper_affordability(
            action="BUY", symbol="005930", price=70_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
            current_held_symbols=["000660", "035720", "035420"],
            max_concurrent_positions=3,
        )
        assert r.verdict == AffordabilityVerdict.MAX_POSITIONS_REACHED
        assert r.current_held_unique_symbols == 3
        assert r.risk_flag == "max_positions_reached"

    def test_existing_position_bypasses_max_positions(self):
        # 이미 보유 중인 종목의 추가 매수 — 한도 도달이어도 ALLOW.
        r = check_paper_affordability(
            action="BUY", symbol="005930", price=70_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
            current_held_symbols=["005930", "035720", "035420"],
            max_concurrent_positions=3,
        )
        assert r.verdict == AffordabilityVerdict.AFFORDABLE
        assert r.is_existing_position is True

    @pytest.mark.parametrize("action", ["SELL", "EXIT", "HOLD", "NO_OP", "WATCH"])
    def test_non_buy_actions_skip(self, action):
        r = check_paper_affordability(
            action=action, symbol="005930", price=1_800_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        assert r.verdict == AffordabilityVerdict.SKIP_NON_BUY
        # 한도 초과 가격이어도 SELL/EXIT 은 차단되지 않음 확인.
        assert "청산" in r.reason_ko or "관망" in r.reason_ko or "BUY 가 아닌" in r.reason_ko

    def test_case_insensitive_buy(self):
        r1 = check_paper_affordability(
            action="buy", symbol="005930", price=70_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        r2 = check_paper_affordability(
            action="BUY", symbol="005930", price=70_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        assert r1.verdict == r2.verdict == AffordabilityVerdict.AFFORDABLE

    def test_affordable_quantity_calculation(self):
        # cap=1M, cash=10M, price=200k → min=1M, 1M//200k = 5주.
        r = check_paper_affordability(
            action="BUY", symbol="005930", price=200_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        assert r.verdict == AffordabilityVerdict.AFFORDABLE
        assert r.affordable_quantity == 5

    def test_affordable_quantity_capped_by_cash(self):
        # cap=10M, cash=500k, price=100k → min=500k, 500k//100k = 5주.
        r = check_paper_affordability(
            action="BUY", symbol="005930", price=100_000,
            available_cash_krw=500_000,
            effective_per_symbol_cap_krw=10_000_000,
        )
        assert r.verdict == AffordabilityVerdict.AFFORDABLE
        assert r.affordable_quantity == 5

    def test_held_dedup(self):
        # 중복 held → set 으로 dedupe → 1건 보유로 카운트.
        r = check_paper_affordability(
            action="BUY", symbol="005930", price=70_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
            current_held_symbols=["000660", "000660", "000660"],
            max_concurrent_positions=3,
        )
        assert r.current_held_unique_symbols == 1
        assert r.verdict == AffordabilityVerdict.AFFORDABLE


# ─────────────────────────────────────────────────────────────────────────────
# 3. 정적 import 가드 — broker / KIS / OrderExecutor / settings 0건
# ─────────────────────────────────────────────────────────────────────────────


class TestNoBrokerOrLiveImports:
    def test_module_no_banned_imports(self):
        src = (
            Path(__file__).resolve().parent.parent
            / "app" / "auto_paper" / "affordability_check.py"
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
                        f"affordability_check.py 가 금지 모듈 '{m}' import"
                    )

    def test_no_broker_call_patterns(self):
        src = (
            Path(__file__).resolve().parent.parent
            / "app" / "auto_paper" / "affordability_check.py"
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
                f"affordability_check.py 에 금지 패턴: /{pat}/"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 4. API endpoint
# ─────────────────────────────────────────────────────────────────────────────


class TestAffordabilityApi:
    def test_preview_uses_current_config_by_default(self, api_client):
        # default config: cap=1M, max_positions=3.
        res = api_client.post(
            "/api/auto-paper/affordability/preview",
            json={
                "action": "BUY",
                "symbol": "000660",
                "price": 1_800_000,
                "available_cash_krw": 10_000_000,
                "current_held_symbols": [],
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["verdict"] == "PRICE_OVER_CAP"
        assert body["effective_per_symbol_cap_krw"] == 1_000_000
        assert body["max_concurrent_positions"] == 3
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False
        assert "한도" in body["reason_ko"]

    def test_preview_affordable_with_default_config(self, api_client):
        res = api_client.post(
            "/api/auto-paper/affordability/preview",
            json={
                "action": "BUY",
                "symbol": "005930",
                "price": 70_000,
                "available_cash_krw": 10_000_000,
                "current_held_symbols": [],
            },
        )
        body = res.json()
        assert body["verdict"] == "AFFORDABLE"
        # min(1M, 10M) // 70k = 14주.
        assert body["affordable_quantity"] == 14

    def test_preview_uses_updated_cap(self, api_client):
        # cap 을 PCT_OF_EQUITY 10% × 30M = 3M 로 바꾸면 180만원 가능.
        api_client.post(
            "/api/auto-paper/capital-config",
            json={"initial_cash": 30_000_000},
        )
        api_client.post(
            "/api/auto-paper/per-symbol-allocation",
            json={"mode": "PCT_OF_EQUITY"},
        )
        res = api_client.post(
            "/api/auto-paper/affordability/preview",
            json={
                "action": "BUY",
                "symbol": "000660",
                "price": 1_800_000,
                "available_cash_krw": 30_000_000,
                "current_held_symbols": [],
            },
        )
        body = res.json()
        assert body["effective_per_symbol_cap_krw"] == 3_000_000
        assert body["verdict"] == "AFFORDABLE"
        # min(3M, 30M) // 1.8M = 1.
        assert body["affordable_quantity"] == 1

    def test_preview_explicit_override(self, api_client):
        # body 에 explicit cap 주면 config 무시.
        res = api_client.post(
            "/api/auto-paper/affordability/preview",
            json={
                "action": "BUY",
                "symbol": "005930",
                "price": 1_800_000,
                "available_cash_krw": 10_000_000,
                "current_held_symbols": [],
                "effective_per_symbol_cap_krw": 5_000_000,
                "max_concurrent_positions": 10,
            },
        )
        body = res.json()
        assert body["effective_per_symbol_cap_krw"] == 5_000_000
        assert body["max_concurrent_positions"] == 10
        assert body["verdict"] == "AFFORDABLE"

    def test_preview_sell_skipped(self, api_client):
        res = api_client.post(
            "/api/auto-paper/affordability/preview",
            json={
                "action": "SELL",
                "symbol": "000660",
                "price": 1_800_000,
                "available_cash_krw": 10_000_000,
            },
        )
        assert res.json()["verdict"] == "SKIP_NON_BUY"

    def test_preview_missing_price(self, api_client):
        res = api_client.post(
            "/api/auto-paper/affordability/preview",
            json={
                "action": "BUY",
                "symbol": "005930",
                "price": None,
                "available_cash_krw": 10_000_000,
            },
        )
        assert res.json()["verdict"] == "MISSING_PRICE"

    def test_preview_max_positions_reached(self, api_client):
        res = api_client.post(
            "/api/auto-paper/affordability/preview",
            json={
                "action": "BUY",
                "symbol": "005930",
                "price": 70_000,
                "available_cash_krw": 10_000_000,
                "current_held_symbols": ["000660", "035720", "035420"],
            },
        )
        body = res.json()
        assert body["verdict"] == "MAX_POSITIONS_REACHED"
        assert body["current_held_unique_symbols"] == 3

    def test_preview_payload_no_secrets(self, api_client):
        import json
        res = api_client.post(
            "/api/auto-paper/affordability/preview",
            json={
                "action": "BUY", "symbol": "005930", "price": 70_000,
                "available_cash_krw": 10_000_000,
            },
        )
        text = json.dumps(res.json()).lower()
        for needle in ("kis_app_key", "kis_app_secret", "anthropic_api_key",
                       "openai_api_key", "telegram_bot_token", "sk-",
                       "bearer ", "kis_account_no"):
            assert needle not in text

    def test_preview_notice_paper_only(self, api_client):
        res = api_client.post(
            "/api/auto-paper/affordability/preview",
            json={
                "action": "BUY", "symbol": "005930", "price": 70_000,
                "available_cash_krw": 10_000_000,
            },
        )
        body = res.json()
        assert "Paper 전용" in body["notice"]
        assert "advisory" in body["notice"]


# ─────────────────────────────────────────────────────────────────────────────
# 5. 안전 flag mutate invariant
# ─────────────────────────────────────────────────────────────────────────────


class TestSafetyFlagsUntouched:
    def test_check_does_not_mutate_settings(self):
        from app.core.config import get_settings
        before = get_settings()
        check_paper_affordability(
            action="BUY", symbol="005930", price=70_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=1_000_000,
        )
        after = get_settings()
        assert before.enable_live_trading == after.enable_live_trading is False
        assert before.enable_ai_execution == after.enable_ai_execution is False
        assert before.enable_futures_live_trading == after.enable_futures_live_trading is False
        assert before.kis_is_paper == after.kis_is_paper is True


# ─────────────────────────────────────────────────────────────────────────────
# 6. P-02 / P-03 통합 — 실제 store 값과 연동되는지
# ─────────────────────────────────────────────────────────────────────────────


class TestIntegrationWithCapitalConfig:
    def test_pct_mode_changes_effective_cap_propagates(self):
        # 시드머니 30M + PCT_OF_EQUITY 10% → effective cap = 3M.
        # 그 한도 아래 가격 = AFFORDABLE, 초과 = PRICE_OVER_CAP.
        set_paper_capital_config(30_000_000)
        set_per_symbol_allocation(mode="PCT_OF_EQUITY")
        from app.auto_paper.capital_config import get_paper_capital_config
        cfg = get_paper_capital_config()
        assert cfg.effective_per_symbol_cap_krw == 3_000_000

        r_ok = check_paper_affordability(
            action="BUY", symbol="000660", price=2_900_000,
            available_cash_krw=30_000_000,
            effective_per_symbol_cap_krw=cfg.effective_per_symbol_cap_krw,
        )
        assert r_ok.verdict == AffordabilityVerdict.AFFORDABLE

        r_blocked = check_paper_affordability(
            action="BUY", symbol="000660", price=3_100_000,
            available_cash_krw=30_000_000,
            effective_per_symbol_cap_krw=cfg.effective_per_symbol_cap_krw,
        )
        assert r_blocked.verdict == AffordabilityVerdict.PRICE_OVER_CAP

    def test_max_positions_propagates_from_p03(self):
        set_max_concurrent_positions(5)
        from app.auto_paper.capital_config import get_paper_capital_config
        cfg = get_paper_capital_config()
        # 보유 5종목 = 한도 도달 → 신규 BUY 차단.
        r = check_paper_affordability(
            action="BUY", symbol="005930", price=70_000,
            available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=cfg.effective_per_symbol_cap_krw,
            current_held_symbols=["a", "b", "c", "d", "e"],
            max_concurrent_positions=cfg.max_concurrent_positions,
        )
        assert r.verdict == AffordabilityVerdict.MAX_POSITIONS_REACHED
