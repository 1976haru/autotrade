"""P-09: 성향별 자금 배분 — 단위 + 정적 + API + P-06/P-07/P-08 통합 테스트.

검증 항목 (사용자 요청서 §7):
 1. 기본 profile = BALANCED
 2-4. CONSERVATIVE / BALANCED / AGGRESSIVE profile 정상 로드
 5. 잘못된 profile → BALANCED fallback
 6-8. 총 1,000만원 기준 종목당 50만 / 100만 / 200만
 9-11. max_positions 3 / 5 / 8
 12-14. 일일 한도 200만 / 500만 / 800만
 15. 수동값 우선
 16. 수동값 미지정 시 자동값
 17. P-08 position_sizer 가 per_symbol_allocation 을 max_amount 로 사용
 18. P-07 cash check 가 cash 잔액 우선
 19. P-06 고가주 처리와 충돌 없음
 20. 실거래 관련 설정 변경 0건 정적 가드
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

from app.agents.risk_profile import (
    DEFAULT_RISK_PROFILE,
    CapitalAllocationResult,
    RiskProfile,
    RiskProfileCapitalAllocation,
    capital_allocation_for,
    list_capital_allocations,
)
from app.auto_paper.capital_config import reset_paper_capital_for_tests
from app.db.base import Base
from app.db.session import get_db
from app.main import app


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "agents" / "risk_profile.py"
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
# 1. Defaults + profile enum (검증 1-4)
# ─────────────────────────────────────────────────────────────────────────────


class TestRiskProfileDefaults:
    def test_default_profile_is_balanced(self):
        # 검증 1: 기본 profile = BALANCED.
        assert DEFAULT_RISK_PROFILE == RiskProfile.BALANCED
        assert DEFAULT_RISK_PROFILE.value == "BALANCED"

    def test_conservative_loads(self):
        # 검증 2.
        r = capital_allocation_for(
            RiskProfile.CONSERVATIVE, total_paper_capital_krw=10_000_000,
        )
        assert r.profile == RiskProfile.CONSERVATIVE
        assert r.display_name_ko == "보수형"

    def test_balanced_loads(self):
        # 검증 3.
        r = capital_allocation_for(
            RiskProfile.BALANCED, total_paper_capital_krw=10_000_000,
        )
        assert r.profile == RiskProfile.BALANCED
        assert r.display_name_ko == "안정형"

    def test_aggressive_loads(self):
        # 검증 4.
        r = capital_allocation_for(
            RiskProfile.AGGRESSIVE, total_paper_capital_krw=10_000_000,
        )
        assert r.profile == RiskProfile.AGGRESSIVE
        assert r.display_name_ko == "공격형"


class TestRiskProfileInvalid:
    def test_unknown_string_fallback_to_balanced(self):
        # 검증 5: 잘못된 profile → BALANCED fallback.
        r = capital_allocation_for(
            "GAMBLE", total_paper_capital_krw=10_000_000,
        )
        assert r.profile == RiskProfile.BALANCED

    def test_none_fallback_to_balanced(self):
        r = capital_allocation_for(
            None, total_paper_capital_krw=10_000_000,
        )
        assert r.profile == RiskProfile.BALANCED

    def test_empty_string_fallback_to_balanced(self):
        r = capital_allocation_for(
            "", total_paper_capital_krw=10_000_000,
        )
        assert r.profile == RiskProfile.BALANCED

    def test_lowercase_string_normalized(self):
        r = capital_allocation_for(
            "aggressive", total_paper_capital_krw=10_000_000,
        )
        assert r.profile == RiskProfile.AGGRESSIVE


# ─────────────────────────────────────────────────────────────────────────────
# 2. 총 1,000만원 기준 계산 (검증 6-14)
# ─────────────────────────────────────────────────────────────────────────────


class TestCapitalAllocationByProfile:
    """사용자 요청서 §3 예시 정확 일치."""

    def test_conservative_per_symbol_500k(self):
        # 검증 6: 보수형 → 500,000원.
        r = capital_allocation_for(
            RiskProfile.CONSERVATIVE, total_paper_capital_krw=10_000_000,
        )
        assert r.per_symbol_allocation_krw == 500_000

    def test_balanced_per_symbol_1m(self):
        # 검증 7: 안정형 → 1,000,000원.
        r = capital_allocation_for(
            RiskProfile.BALANCED, total_paper_capital_krw=10_000_000,
        )
        assert r.per_symbol_allocation_krw == 1_000_000

    def test_aggressive_per_symbol_2m(self):
        # 검증 8: 공격형 → 2,000,000원.
        r = capital_allocation_for(
            RiskProfile.AGGRESSIVE, total_paper_capital_krw=10_000_000,
        )
        assert r.per_symbol_allocation_krw == 2_000_000

    def test_conservative_max_positions_3(self):
        # 검증 9.
        r = capital_allocation_for(
            RiskProfile.CONSERVATIVE, total_paper_capital_krw=10_000_000,
        )
        assert r.max_positions == 3

    def test_balanced_max_positions_5(self):
        # 검증 10.
        r = capital_allocation_for(
            RiskProfile.BALANCED, total_paper_capital_krw=10_000_000,
        )
        assert r.max_positions == 5

    def test_aggressive_max_positions_8(self):
        # 검증 11.
        r = capital_allocation_for(
            RiskProfile.AGGRESSIVE, total_paper_capital_krw=10_000_000,
        )
        assert r.max_positions == 8

    def test_conservative_daily_buy_2m(self):
        # 검증 12.
        r = capital_allocation_for(
            RiskProfile.CONSERVATIVE, total_paper_capital_krw=10_000_000,
        )
        assert r.max_daily_buy_amount_krw == 2_000_000

    def test_balanced_daily_buy_5m(self):
        # 검증 13.
        r = capital_allocation_for(
            RiskProfile.BALANCED, total_paper_capital_krw=10_000_000,
        )
        assert r.max_daily_buy_amount_krw == 5_000_000

    def test_aggressive_daily_buy_8m(self):
        # 검증 14.
        r = capital_allocation_for(
            RiskProfile.AGGRESSIVE, total_paper_capital_krw=10_000_000,
        )
        assert r.max_daily_buy_amount_krw == 8_000_000

    def test_to_dict_full_payload(self):
        r = capital_allocation_for(
            RiskProfile.BALANCED, total_paper_capital_krw=10_000_000,
        )
        d = r.to_dict()
        # 사용자 요청서 §3 반환 구조 정확 매칭.
        assert d["profile"] == "BALANCED"
        assert d["display_name"] == "안정형"
        assert d["total_paper_capital"] == 10_000_000
        assert d["per_symbol_allocation"] == 1_000_000
        assert d["max_positions"] == 5
        assert d["max_daily_buy_amount"] == 5_000_000
        assert d["is_manual_override"] is False
        assert "안정형" in d["reason_message"]
        # invariants.
        assert d["is_paper_only"] is True
        assert d["is_order_signal"] is False
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. Manual override (검증 15-16)
# ─────────────────────────────────────────────────────────────────────────────


class TestManualOverride:
    def test_manual_value_overrides_profile_auto(self):
        # 검증 15: 사용자 수동값 우선.
        r = capital_allocation_for(
            RiskProfile.BALANCED,
            total_paper_capital_krw=10_000_000,
            manual_per_symbol_krw=300_000,   # 자동값 1M 보다 작게
        )
        assert r.is_manual_override is True
        assert r.per_symbol_allocation_krw == 300_000
        # max_positions / daily_buy 는 profile 그대로.
        assert r.max_positions == 5
        assert r.max_daily_buy_amount_krw == 5_000_000
        # reason 메시지에 manual 안내.
        assert "수동" in r.reason_message_ko

    def test_no_manual_uses_profile_auto(self):
        # 검증 16: 수동값 미지정 시 자동값.
        r = capital_allocation_for(
            RiskProfile.BALANCED, total_paper_capital_krw=10_000_000,
        )
        assert r.is_manual_override is False
        assert r.per_symbol_allocation_krw == 1_000_000

    def test_manual_zero_not_treated_as_override(self):
        # manual_per_symbol_krw=0 은 *비활성* 으로 간주 (잘못된 입력 안전 처리).
        r = capital_allocation_for(
            RiskProfile.BALANCED,
            total_paper_capital_krw=10_000_000,
            manual_per_symbol_krw=0,
        )
        assert r.is_manual_override is False
        assert r.per_symbol_allocation_krw == 1_000_000

    def test_manual_negative_not_treated_as_override(self):
        r = capital_allocation_for(
            RiskProfile.BALANCED,
            total_paper_capital_krw=10_000_000,
            manual_per_symbol_krw=-100,
        )
        assert r.is_manual_override is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. Edge cases — total=0 / 음수 / 매우 작은 값
# ─────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_total_zero_returns_zero_per_symbol(self):
        r = capital_allocation_for(
            RiskProfile.BALANCED, total_paper_capital_krw=0,
        )
        assert r.total_paper_capital_krw == 0
        assert r.per_symbol_allocation_krw == 0
        assert r.max_daily_buy_amount_krw == 0
        # max_positions 는 profile 기준 유지.
        assert r.max_positions == 5

    def test_negative_total_clamped_to_zero(self):
        r = capital_allocation_for(
            RiskProfile.BALANCED, total_paper_capital_krw=-1_000_000,
        )
        assert r.total_paper_capital_krw == 0

    def test_small_total_50m_balanced(self):
        # 안정형 5,000만 → per_symbol = 500만, daily = 2,500만.
        r = capital_allocation_for(
            RiskProfile.BALANCED, total_paper_capital_krw=50_000_000,
        )
        assert r.per_symbol_allocation_krw == 5_000_000
        assert r.max_daily_buy_amount_krw == 25_000_000


# ─────────────────────────────────────────────────────────────────────────────
# 5. Dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestDataclassInvariants:
    def test_result_is_paper_only_lock(self):
        with pytest.raises(ValueError, match="is_paper_only"):
            CapitalAllocationResult(
                profile=RiskProfile.BALANCED,
                display_name_ko="x",
                total_paper_capital_krw=0,
                per_symbol_allocation_krw=0,
                max_positions=1,
                max_daily_buy_amount_krw=0,
                is_manual_override=False,
                reason_message_ko="x",
                is_paper_only=False,  # type: ignore[arg-type]
            )

    def test_result_is_live_authorization_lock(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            CapitalAllocationResult(
                profile=RiskProfile.BALANCED,
                display_name_ko="x",
                total_paper_capital_krw=0,
                per_symbol_allocation_krw=0,
                max_positions=1,
                max_daily_buy_amount_krw=0,
                is_manual_override=False,
                reason_message_ko="x",
                is_live_authorization=True,  # type: ignore[arg-type]
            )

    def test_allocation_invalid_ratio_rejected(self):
        with pytest.raises(ValueError, match="per_symbol_allocation_ratio"):
            RiskProfileCapitalAllocation(
                profile=RiskProfile.BALANCED,
                per_symbol_allocation_ratio=1.5,
                max_positions=5,
                max_daily_buy_ratio=0.5,
            )

    def test_allocation_invalid_max_positions_rejected(self):
        with pytest.raises(ValueError, match="max_positions"):
            RiskProfileCapitalAllocation(
                profile=RiskProfile.BALANCED,
                per_symbol_allocation_ratio=0.1,
                max_positions=0,
                max_daily_buy_ratio=0.5,
            )

    def test_list_capital_allocations(self):
        catalog = list_capital_allocations()
        assert len(catalog) == 3
        assert [c["profile"] for c in catalog] == [
            "CONSERVATIVE", "BALANCED", "AGGRESSIVE",
        ]


# ─────────────────────────────────────────────────────────────────────────────
# 6. P-08 integration (검증 17)
# ─────────────────────────────────────────────────────────────────────────────


class TestP08Integration:
    def test_position_sizer_uses_profile_per_symbol_as_max_amount(self):
        # 검증 17: position_sizer 가 성향별 per_symbol_allocation 을
        # max_amount 로 사용 가능.
        from app.auto_paper.position_sizer import (
            QuantityByPriceVerdict,
            compute_paper_quantity_by_price,
        )

        alloc = capital_allocation_for(
            RiskProfile.BALANCED, total_paper_capital_krw=10_000_000,
        )
        # 1주 75,000원 + 종목당 100만원 (안정형) → 13주 (P-08 spec 예시).
        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=75_000,
            max_amount_krw=alloc.per_symbol_allocation_krw,
        )
        assert sizer.verdict == QuantityByPriceVerdict.OK
        assert sizer.quantity == 13

    def test_conservative_per_symbol_lower_quantity(self):
        # 보수형 (500k) → 1주 75,000원 → 6주.
        from app.auto_paper.position_sizer import compute_paper_quantity_by_price

        alloc = capital_allocation_for(
            RiskProfile.CONSERVATIVE, total_paper_capital_krw=10_000_000,
        )
        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=75_000, max_amount_krw=alloc.per_symbol_allocation_krw,
        )
        assert sizer.quantity == 6  # floor(500_000 / 75_000) = 6

    def test_aggressive_per_symbol_higher_quantity(self):
        # 공격형 (2M) → 1주 75,000원 → 26주.
        from app.auto_paper.position_sizer import compute_paper_quantity_by_price

        alloc = capital_allocation_for(
            RiskProfile.AGGRESSIVE, total_paper_capital_krw=10_000_000,
        )
        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=75_000, max_amount_krw=alloc.per_symbol_allocation_krw,
        )
        assert sizer.quantity == 26  # floor(2_000_000 / 75_000) = 26


# ─────────────────────────────────────────────────────────────────────────────
# 7. P-07 integration (검증 18)
# ─────────────────────────────────────────────────────────────────────────────


class TestP07Integration:
    def test_cash_check_blocks_when_cash_lower_than_profile_allocation(self):
        # 검증 18: profile 자동 종목당 한도가 충분해도 *남은 현금* 이 부족하면
        # P-07 cash check 가 차단.
        from app.auto_paper.capital_state import (
            CashCheckVerdict,
            check_buy_cash_sufficient,
        )
        from app.auto_paper.position_sizer import compute_paper_quantity_by_price

        alloc = capital_allocation_for(
            RiskProfile.AGGRESSIVE, total_paper_capital_krw=10_000_000,
        )
        # 공격형 종목당 200만 → 75k 주가 → 26주 가능. 필요 금액 = 26 × 75k = 1,950,000.
        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=75_000, max_amount_krw=alloc.per_symbol_allocation_krw,
        )
        assert sizer.quantity == 26
        assert sizer.notional_krw == 1_950_000

        # 남은 현금 100만원 — profile 한도 200만보다 적음 → P-07 가 차단.
        cash = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=75_000, quantity=sizer.quantity,
            available_cash_krw=1_000_000,
        )
        assert cash.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH
        assert cash.shortfall_krw == 950_000

    def test_cash_check_passes_when_cash_above_profile_allocation(self):
        from app.auto_paper.capital_state import (
            CashCheckVerdict,
            check_buy_cash_sufficient,
        )
        from app.auto_paper.position_sizer import compute_paper_quantity_by_price

        alloc = capital_allocation_for(
            RiskProfile.BALANCED, total_paper_capital_krw=10_000_000,
        )
        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=75_000, max_amount_krw=alloc.per_symbol_allocation_krw,
        )
        cash = check_buy_cash_sufficient(
            action="BUY", symbol="005930",
            price=75_000, quantity=sizer.quantity,
            available_cash_krw=10_000_000,
        )
        assert cash.verdict == CashCheckVerdict.ALLOWED


# ─────────────────────────────────────────────────────────────────────────────
# 8. P-06 integration (검증 19)
# ─────────────────────────────────────────────────────────────────────────────


class TestP06Integration:
    def test_conservative_high_price_stock_excluded(self):
        # 검증 19: 보수형 종목당 50만원, 현재가 70만원 → 1주 불가.
        # P-06 의 EXCLUDE 정책과 일치하는 진단을 P-08 sizer 도 동일하게 내야 함.
        from app.auto_paper.affordability import (
            HighPricePolicy,
            HighPriceVerdict,
            evaluate_high_price,
        )
        from app.auto_paper.position_sizer import (
            QuantityByPriceVerdict,
            compute_paper_quantity_by_price,
        )

        alloc = capital_allocation_for(
            RiskProfile.CONSERVATIVE, total_paper_capital_krw=10_000_000,
        )
        cap = alloc.per_symbol_allocation_krw   # 500,000
        price = 700_000

        # P-08 sizer.
        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="000660", price=price, max_amount_krw=cap,
        )
        assert sizer.verdict == QuantityByPriceVerdict.MIN_LOT_NOT_AFFORDABLE
        assert sizer.quantity == 0

        # P-06 high-price 정책.
        p06 = evaluate_high_price(
            action="BUY", symbol="000660",
            price=price, effective_per_symbol_cap_krw=cap,
            policy=HighPricePolicy.EXCLUDE,
        )
        assert p06.verdict == HighPriceVerdict.EXCLUDED
        # 두 모듈이 "1주 불가" 동일 진단.
        assert sizer.quantity == p06.affordable_quantity == 0

    def test_aggressive_normal_price_allowed(self):
        from app.auto_paper.position_sizer import (
            QuantityByPriceVerdict,
            compute_paper_quantity_by_price,
        )

        alloc = capital_allocation_for(
            RiskProfile.AGGRESSIVE, total_paper_capital_krw=10_000_000,
        )
        # 공격형 200만 / 50k 주가 → 40주.
        sizer = compute_paper_quantity_by_price(
            action="BUY", symbol="005930",
            price=50_000, max_amount_krw=alloc.per_symbol_allocation_krw,
        )
        assert sizer.verdict == QuantityByPriceVerdict.OK
        assert sizer.quantity == 40


# ─────────────────────────────────────────────────────────────────────────────
# 9. API endpoint
# ─────────────────────────────────────────────────────────────────────────────


class TestApiEndpoints:
    def test_preview_balanced_default(self, api_client):
        r = api_client.post(
            "/api/auto-paper/risk-profile/allocation/preview",
            json={"profile": "BALANCED",
                  "total_paper_capital_krw": 10_000_000},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["profile"] == "BALANCED"
        assert body["per_symbol_allocation"] == 1_000_000
        assert body["max_positions"] == 5
        assert body["max_daily_buy_amount"] == 5_000_000
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False
        assert body["default_profile"] == "BALANCED"
        assert set(body["allowed_profiles"]) == {
            "CONSERVATIVE", "BALANCED", "AGGRESSIVE",
        }

    def test_preview_conservative(self, api_client):
        r = api_client.post(
            "/api/auto-paper/risk-profile/allocation/preview",
            json={"profile": "CONSERVATIVE",
                  "total_paper_capital_krw": 10_000_000},
        )
        body = r.json()
        assert body["per_symbol_allocation"] == 500_000
        assert body["max_positions"] == 3
        assert body["max_daily_buy_amount"] == 2_000_000

    def test_preview_aggressive(self, api_client):
        r = api_client.post(
            "/api/auto-paper/risk-profile/allocation/preview",
            json={"profile": "AGGRESSIVE",
                  "total_paper_capital_krw": 10_000_000},
        )
        body = r.json()
        assert body["per_symbol_allocation"] == 2_000_000
        assert body["max_positions"] == 8
        assert body["max_daily_buy_amount"] == 8_000_000

    def test_preview_manual_override(self, api_client):
        r = api_client.post(
            "/api/auto-paper/risk-profile/allocation/preview",
            json={
                "profile": "BALANCED",
                "total_paper_capital_krw": 10_000_000,
                "manual_per_symbol_krw": 300_000,
            },
        )
        body = r.json()
        assert body["is_manual_override"] is True
        assert body["per_symbol_allocation"] == 300_000
        assert body["max_positions"] == 5    # profile 그대로
        assert "수동" in body["reason_message"]

    def test_preview_invalid_profile_fallback(self, api_client):
        r = api_client.post(
            "/api/auto-paper/risk-profile/allocation/preview",
            json={"profile": "GAMBLE",
                  "total_paper_capital_krw": 10_000_000},
        )
        body = r.json()
        assert body["profile"] == "BALANCED"   # fallback

    def test_preview_uses_initial_cash_when_total_omitted(self, api_client):
        # PaperCapitalConfig default initial_cash = 10,000,000.
        r = api_client.post(
            "/api/auto-paper/risk-profile/allocation/preview",
            json={"profile": "BALANCED"},
        )
        body = r.json()
        assert body["total_paper_capital"] == 10_000_000
        assert body["per_symbol_allocation"] == 1_000_000

    def test_catalog_endpoint(self, api_client):
        r = api_client.get("/api/auto-paper/risk-profile/catalog")
        assert r.status_code == 200
        body = r.json()
        assert body["default_profile"] == "BALANCED"
        assert len(body["profiles"]) == 3
        profile_values = [p["profile"] for p in body["profiles"]]
        assert profile_values == ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]
        for p in body["profiles"]:
            assert p["is_paper_only"] is True
            assert p["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 10. 정적 가드 (검증 20)
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_no_broker_imports_in_risk_profile_module(self):
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
                        f"risk_profile.py 가 금지 모듈 '{m}' import"
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
                f"risk_profile.py 에 금지 패턴: /{pat}/"
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
                f"risk_profile.py 안전 flag mutation 의심: /{pat}/"
            )

    def test_aggressive_is_still_paper_only(self):
        # AGGRESSIVE 도 paper 한정 — is_live_authorization 항상 False.
        from app.agents.risk_profile import is_live_profile
        assert is_live_profile(RiskProfile.AGGRESSIVE) is False
        # CapitalAllocation 도 동일.
        for p in (RiskProfile.CONSERVATIVE, RiskProfile.BALANCED, RiskProfile.AGGRESSIVE):
            r = capital_allocation_for(p, total_paper_capital_krw=10_000_000)
            assert r.is_paper_only is True
            assert r.is_live_authorization is False
            assert r.is_order_signal is False
            assert r.auto_apply_allowed is False

    def test_existing_risk_profile_policy_preserved(self):
        # 회귀: 기존 #4-RiskProfile (RiskProfilePolicy / policy_for) 보존.
        from app.agents.risk_profile import (
            RiskProfilePolicy,
            list_profiles,
            policy_for,
        )
        # 기존 함수가 그대로 호출 가능 + 3 프리셋 returns.
        for p in (RiskProfile.CONSERVATIVE, RiskProfile.BALANCED, RiskProfile.AGGRESSIVE):
            pol = policy_for(p)
            assert isinstance(pol, RiskProfilePolicy)
        catalog = list_profiles()
        assert len(catalog) == 3
