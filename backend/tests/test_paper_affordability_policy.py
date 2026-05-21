"""P-06: 고가주 처리 정책 — 단위 + 정적 + API 테스트.

검증 항목 (사용자 요청서):
- 현재가 <= 종목당 투자금인 경우 정상 수량 계산 → AFFORDABLE
- 현재가 > 종목당 투자금 + policy=EXCLUDE → EXCLUDED + "1주 가격이 투자한도 초과로 제외"
- 현재가 > 종목당 투자금 + policy=HOLD → HELD + "1주 가격이 투자한도 초과로 보류"
- 현재가 > 종목당 투자금 + policy=INCREASE_BUDGET_HINT → BUDGET_HINT + "종목당 투자금 증액 필요"
- 정책 미지정 → default EXCLUDE
- reason_ko 에 "1주 가격이 투자한도 초과" 문구 포함
- is_paper_only / is_live_authorization / 실거래 호출 0건
- 회귀 — P-01/02/03/04/05 기능 영향 없음
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

from app.auto_paper.affordability import (
    DEFAULT_HIGH_PRICE_POLICY,
    HighPriceCheckResult,
    HighPricePolicy,
    HighPriceVerdict,
    compute_affordable_quantity,
    evaluate_high_price,
)
from app.auto_paper.capital_config import (
    reset_paper_capital_for_tests,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app


_HIGH_PRICE_REASON_FRAGMENT = "1주 가격이 투자한도 초과"


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
# 1. HighPriceCheckResult dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestDataclassInvariants:
    def test_is_paper_only_must_be_true(self):
        with pytest.raises(ValueError, match="is_paper_only"):
            HighPriceCheckResult(
                verdict=HighPriceVerdict.AFFORDABLE,
                policy=HighPricePolicy.EXCLUDE,
                is_paper_only=False,  # type: ignore[arg-type]
            )

    def test_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            HighPriceCheckResult(
                verdict=HighPriceVerdict.AFFORDABLE,
                policy=HighPricePolicy.EXCLUDE,
                is_order_signal=True,  # type: ignore[arg-type]
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            HighPriceCheckResult(
                verdict=HighPriceVerdict.AFFORDABLE,
                policy=HighPricePolicy.EXCLUDE,
                is_live_authorization=True,  # type: ignore[arg-type]
            )

    def test_negative_affordable_quantity_rejected(self):
        with pytest.raises(ValueError, match="affordable_quantity"):
            HighPriceCheckResult(
                verdict=HighPriceVerdict.AFFORDABLE,
                policy=HighPricePolicy.EXCLUDE,
                affordable_quantity=-1,
            )

    def test_to_dict_carries_all_fields(self):
        r = HighPriceCheckResult(
            verdict=HighPriceVerdict.EXCLUDED,
            policy=HighPricePolicy.EXCLUDE,
            symbol="000660",
            action="BUY",
            price=120_000.0,
            effective_per_symbol_cap_krw=100_000,
            affordable_quantity=0,
            is_high_price=True,
            reason_ko="1주 가격이 투자한도 초과로 제외",
            risk_flag="high_price_excluded",
            suggested_min_cap_krw=120_000,
        )
        d = r.to_dict()
        assert d["verdict"] == "EXCLUDED"
        assert d["policy"] == "EXCLUDE"
        assert d["is_paper_only"] is True
        assert d["is_order_signal"] is False
        assert d["is_live_authorization"] is False
        assert d["is_excluded"] is True
        assert d["is_held"] is False
        assert d["is_budget_hint"] is False
        assert d["suggested_min_cap_krw"] == 120_000

    def test_default_policy_is_exclude(self):
        assert DEFAULT_HIGH_PRICE_POLICY is HighPricePolicy.EXCLUDE


# ─────────────────────────────────────────────────────────────────────────────
# 2. compute_affordable_quantity helper
# ─────────────────────────────────────────────────────────────────────────────


class TestComputeAffordableQuantity:
    def test_cap_100k_price_50k_returns_2(self):
        assert compute_affordable_quantity(
            effective_per_symbol_cap_krw=100_000, price=50_000,
        ) == 2

    def test_cap_100k_price_120k_returns_0_high_price(self):
        # 사용자 요청서 예시 — 종목당 100k + 현재가 120k → 0주.
        assert compute_affordable_quantity(
            effective_per_symbol_cap_krw=100_000, price=120_000,
        ) == 0

    def test_floor_policy_no_rounding_up(self):
        # 1,000,000 / 333 = 3003.003... → 3003 (반올림 금지).
        assert compute_affordable_quantity(
            effective_per_symbol_cap_krw=1_000_000, price=333,
        ) == 3003

    def test_price_none_returns_0(self):
        assert compute_affordable_quantity(
            effective_per_symbol_cap_krw=100_000, price=None,
        ) == 0

    def test_price_zero_returns_0(self):
        assert compute_affordable_quantity(
            effective_per_symbol_cap_krw=100_000, price=0,
        ) == 0

    def test_price_negative_returns_0(self):
        assert compute_affordable_quantity(
            effective_per_symbol_cap_krw=100_000, price=-1,
        ) == 0

    def test_cap_zero_returns_0(self):
        assert compute_affordable_quantity(
            effective_per_symbol_cap_krw=0, price=50_000,
        ) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. evaluate_high_price — 정상 흐름 (현재가 <= cap)
# ─────────────────────────────────────────────────────────────────────────────


class TestAffordableNormalCase:
    def test_price_below_cap_returns_affordable(self):
        # 사용자 요청서: 현재가 <= 종목당 투자금 → 정상 수량 계산.
        r = evaluate_high_price(
            action="BUY", symbol="005930",
            price=50_000, effective_per_symbol_cap_krw=100_000,
        )
        assert r.verdict == HighPriceVerdict.AFFORDABLE
        assert r.affordable_quantity == 2
        assert r.is_high_price is False
        assert r.is_affordable is True
        assert r.risk_flag is None

    def test_price_equals_cap_returns_affordable_1share(self):
        # 1주는 살 수 있어야 함 (cap == price 경계).
        r = evaluate_high_price(
            action="BUY", symbol="005930",
            price=100_000, effective_per_symbol_cap_krw=100_000,
        )
        assert r.verdict == HighPriceVerdict.AFFORDABLE
        assert r.affordable_quantity == 1
        assert r.is_high_price is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. evaluate_high_price — 고가주 + 3 policy 분기
# ─────────────────────────────────────────────────────────────────────────────


class TestHighPriceExcludePolicy:
    def test_exclude_policy_high_price_returns_excluded(self):
        # 사용자 요청서 예시 — cap 100k + price 120k + EXCLUDE.
        r = evaluate_high_price(
            action="BUY", symbol="000660",
            price=120_000, effective_per_symbol_cap_krw=100_000,
            policy=HighPricePolicy.EXCLUDE,
        )
        assert r.verdict == HighPriceVerdict.EXCLUDED
        assert r.is_excluded is True
        assert r.is_held is False
        assert r.is_budget_hint is False
        assert r.is_high_price is True
        assert r.affordable_quantity == 0
        # 메시지 검증.
        assert "1주 가격이 투자한도 초과로 제외" == r.reason_ko
        assert _HIGH_PRICE_REASON_FRAGMENT in r.reason_ko
        # 운영자 안내 — 최소 한도.
        assert r.suggested_min_cap_krw == 120_000
        assert r.risk_flag == "high_price_excluded"

    def test_exclude_policy_via_string(self):
        r = evaluate_high_price(
            action="BUY", symbol="000660",
            price=120_000, effective_per_symbol_cap_krw=100_000,
            policy="EXCLUDE",
        )
        assert r.verdict == HighPriceVerdict.EXCLUDED


class TestHighPriceHoldPolicy:
    def test_hold_policy_high_price_returns_held(self):
        r = evaluate_high_price(
            action="BUY", symbol="000660",
            price=120_000, effective_per_symbol_cap_krw=100_000,
            policy=HighPricePolicy.HOLD,
        )
        assert r.verdict == HighPriceVerdict.HELD
        assert r.is_held is True
        assert r.is_excluded is False
        assert r.is_budget_hint is False
        assert r.is_high_price is True
        assert r.affordable_quantity == 0
        assert "1주 가격이 투자한도 초과로 보류" == r.reason_ko
        assert _HIGH_PRICE_REASON_FRAGMENT in r.reason_ko
        assert r.risk_flag == "high_price_held"

    def test_hold_policy_via_string(self):
        r = evaluate_high_price(
            action="BUY", symbol="000660",
            price=120_000, effective_per_symbol_cap_krw=100_000,
            policy="HOLD",
        )
        assert r.verdict == HighPriceVerdict.HELD


class TestHighPriceBudgetHintPolicy:
    def test_budget_hint_policy_high_price_returns_budget_hint(self):
        r = evaluate_high_price(
            action="BUY", symbol="000660",
            price=120_000, effective_per_symbol_cap_krw=100_000,
            policy=HighPricePolicy.INCREASE_BUDGET_HINT,
        )
        assert r.verdict == HighPriceVerdict.BUDGET_HINT
        assert r.is_budget_hint is True
        assert r.is_excluded is False
        assert r.is_held is False
        assert r.is_high_price is True
        assert r.affordable_quantity == 0
        assert (
            "1주 가격이 투자한도 초과: 종목당 투자금 증액 필요"
            == r.reason_ko
        )
        assert _HIGH_PRICE_REASON_FRAGMENT in r.reason_ko
        assert "증액" in r.reason_ko
        assert r.risk_flag == "high_price_budget_hint"

    def test_budget_hint_policy_via_string(self):
        r = evaluate_high_price(
            action="BUY", symbol="000660",
            price=120_000, effective_per_symbol_cap_krw=100_000,
            policy="INCREASE_BUDGET_HINT",
        )
        assert r.verdict == HighPriceVerdict.BUDGET_HINT


# ─────────────────────────────────────────────────────────────────────────────
# 5. Default policy — 미지정 → EXCLUDE
# ─────────────────────────────────────────────────────────────────────────────


class TestDefaultPolicy:
    def test_policy_none_defaults_to_exclude(self):
        # 정책 미지정 → 반드시 EXCLUDE 기본값.
        r = evaluate_high_price(
            action="BUY", symbol="000660",
            price=120_000, effective_per_symbol_cap_krw=100_000,
        )
        assert r.verdict == HighPriceVerdict.EXCLUDED
        assert r.policy is HighPricePolicy.EXCLUDE
        assert r.reason_ko == "1주 가격이 투자한도 초과로 제외"

    def test_policy_none_explicit_defaults_to_exclude(self):
        r = evaluate_high_price(
            action="BUY", symbol="000660",
            price=120_000, effective_per_symbol_cap_krw=100_000,
            policy=None,
        )
        assert r.verdict == HighPriceVerdict.EXCLUDED
        assert r.policy is HighPricePolicy.EXCLUDE

    def test_default_constant_is_exclude(self):
        # 코드에 default 가 EXCLUDE 로 고정.
        assert DEFAULT_HIGH_PRICE_POLICY is HighPricePolicy.EXCLUDE
        assert DEFAULT_HIGH_PRICE_POLICY.value == "EXCLUDE"


# ─────────────────────────────────────────────────────────────────────────────
# 6. SKIP / INVALID / MISSING 분기
# ─────────────────────────────────────────────────────────────────────────────


class TestSkipAndInvalidBranches:
    @pytest.mark.parametrize(
        "action", ["SELL", "EXIT", "HOLD", "NO_OP", None, ""],
    )
    def test_non_buy_returns_skip(self, action):
        r = evaluate_high_price(
            action=action, symbol="000660",
            price=120_000, effective_per_symbol_cap_krw=100_000,
        )
        assert r.verdict == HighPriceVerdict.SKIP_NON_BUY
        assert r.is_high_price is False

    def test_buy_lowercase_accepted(self):
        r = evaluate_high_price(
            action="buy", symbol="000660",
            price=120_000, effective_per_symbol_cap_krw=100_000,
        )
        # BUY 토큰 매칭 → 정책 분기 진행.
        assert r.verdict == HighPriceVerdict.EXCLUDED

    def test_price_none_returns_missing(self):
        r = evaluate_high_price(
            action="BUY", symbol="000660",
            price=None, effective_per_symbol_cap_krw=100_000,
        )
        assert r.verdict == HighPriceVerdict.MISSING_PRICE
        assert r.risk_flag == "missing_price"
        assert r.is_high_price is False

    def test_price_zero_returns_invalid(self):
        r = evaluate_high_price(
            action="BUY", symbol="000660",
            price=0, effective_per_symbol_cap_krw=100_000,
        )
        assert r.verdict == HighPriceVerdict.INVALID_PRICE
        assert r.risk_flag == "invalid_price"

    def test_price_negative_returns_invalid(self):
        r = evaluate_high_price(
            action="BUY", symbol="000660",
            price=-100, effective_per_symbol_cap_krw=100_000,
        )
        assert r.verdict == HighPriceVerdict.INVALID_PRICE


class TestInvalidPolicy:
    def test_unknown_policy_string_raises(self):
        with pytest.raises(ValueError, match="unknown high-price policy"):
            evaluate_high_price(
                action="BUY", symbol="000660",
                price=120_000, effective_per_symbol_cap_krw=100_000,
                policy="DELETE_FOREVER",
            )

    def test_invalid_policy_type_raises(self):
        with pytest.raises(TypeError):
            evaluate_high_price(
                action="BUY", symbol="000660",
                price=120_000, effective_per_symbol_cap_krw=100_000,
                policy=42,  # type: ignore[arg-type]
            )


# ─────────────────────────────────────────────────────────────────────────────
# 7. 사용자 요청서 예시 매트릭스 — 정확 검증
# ─────────────────────────────────────────────────────────────────────────────


class TestUserSpecMatrix:
    """사용자 요청서 *세 정책 × 사용자 예시* 매트릭스 — 메시지 정확성."""

    @pytest.mark.parametrize(
        "policy,expected_verdict,expected_reason_substr",
        [
            (HighPricePolicy.EXCLUDE,
             HighPriceVerdict.EXCLUDED,
             "1주 가격이 투자한도 초과로 제외"),
            (HighPricePolicy.HOLD,
             HighPriceVerdict.HELD,
             "1주 가격이 투자한도 초과로 보류"),
            (HighPricePolicy.INCREASE_BUDGET_HINT,
             HighPriceVerdict.BUDGET_HINT,
             "1주 가격이 투자한도 초과: 종목당 투자금 증액 필요"),
        ],
    )
    def test_matrix(self, policy, expected_verdict, expected_reason_substr):
        r = evaluate_high_price(
            action="BUY", symbol="005930",
            price=120_000, effective_per_symbol_cap_krw=100_000,
            policy=policy,
        )
        assert r.verdict is expected_verdict
        assert expected_reason_substr in r.reason_ko
        assert _HIGH_PRICE_REASON_FRAGMENT in r.reason_ko


# ─────────────────────────────────────────────────────────────────────────────
# 8. 정적 import / 안전 invariant — broker / OrderExecutor / settings 결합 없음
# ─────────────────────────────────────────────────────────────────────────────


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "affordability.py"
)


class TestStaticImportGuards:
    def test_module_file_exists(self):
        assert _MODULE_PATH.exists(), f"{_MODULE_PATH} 누락"

    def test_no_broker_imports(self):
        # AST 기반 import 검사 — docstring 안 "OrderExecutor" 같은 *설명* 은
        # 허용 (실제 import / 호출만 차단).
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
                        f"affordability.py 가 금지 모듈 '{m}' import"
                    )

    def test_no_broker_call_patterns(self):
        # 호출/생성 패턴은 regex 로 — docstring 의 단순 언급은 허용.
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
        ):
            assert not re.search(pat, text), (
                f"affordability.py 에 금지 패턴: /{pat}/"
            )

    def test_module_parses(self):
        # 신택스 ok + 함수/클래스 export 확인.
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
        }
        for required in [
            "HighPricePolicy", "HighPriceVerdict", "HighPriceCheckResult",
            "evaluate_high_price", "compute_affordable_quantity",
        ]:
            assert required in names


# ─────────────────────────────────────────────────────────────────────────────
# 9. API endpoint /high-price/preview
# ─────────────────────────────────────────────────────────────────────────────


class TestHighPricePreviewEndpoint:
    def test_default_policy_high_price_excluded(self, api_client):
        # 정책 미지정 → default EXCLUDE.
        # cap=1,000,000 (P-02 default) + price=2,000,000 → 0주.
        r = api_client.post(
            "/api/auto-paper/high-price/preview",
            json={"action": "BUY", "symbol": "005930", "price": 2_000_000},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "EXCLUDED"
        assert body["policy"] == "EXCLUDE"
        assert body["default_policy"] == "EXCLUDE"
        assert "EXCLUDE" in body["allowed_policies"]
        assert "HOLD" in body["allowed_policies"]
        assert "INCREASE_BUDGET_HINT" in body["allowed_policies"]
        assert body["reason_ko"] == "1주 가격이 투자한도 초과로 제외"
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False
        assert body["is_order_signal"] is False

    def test_explicit_hold_policy(self, api_client):
        r = api_client.post(
            "/api/auto-paper/high-price/preview",
            json={
                "action": "BUY", "symbol": "005930", "price": 2_000_000,
                "policy": "HOLD",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "HELD"
        assert body["policy"] == "HOLD"
        assert body["reason_ko"] == "1주 가격이 투자한도 초과로 보류"

    def test_explicit_increase_budget_hint_policy(self, api_client):
        r = api_client.post(
            "/api/auto-paper/high-price/preview",
            json={
                "action": "BUY", "symbol": "005930", "price": 2_000_000,
                "policy": "INCREASE_BUDGET_HINT",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "BUDGET_HINT"
        assert body["policy"] == "INCREASE_BUDGET_HINT"
        assert (
            "1주 가격이 투자한도 초과: 종목당 투자금 증액 필요"
            == body["reason_ko"]
        )

    def test_affordable_passes(self, api_client):
        r = api_client.post(
            "/api/auto-paper/high-price/preview",
            json={"action": "BUY", "symbol": "005930", "price": 50_000},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "AFFORDABLE"
        assert body["is_high_price"] is False
        assert body["affordable_quantity"] >= 1

    def test_invalid_policy_returns_400(self, api_client):
        r = api_client.post(
            "/api/auto-paper/high-price/preview",
            json={
                "action": "BUY", "symbol": "005930", "price": 2_000_000,
                "policy": "DELETE_FOREVER",
            },
        )
        assert r.status_code == 400
        body = r.json()
        assert body["detail"]["error"] == "invalid_high_price_policy"

    def test_non_buy_returns_skip(self, api_client):
        r = api_client.post(
            "/api/auto-paper/high-price/preview",
            json={"action": "SELL", "symbol": "005930", "price": 2_000_000},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "SKIP_NON_BUY"

    def test_explicit_cap_override(self, api_client):
        # cap=100_000 + price=120_000 → high-price 정상 발현.
        r = api_client.post(
            "/api/auto-paper/high-price/preview",
            json={
                "action": "BUY", "symbol": "000660", "price": 120_000,
                "effective_per_symbol_cap_krw": 100_000,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "EXCLUDED"
        assert body["suggested_min_cap_krw"] == 120_000
        assert body["effective_per_symbol_cap_krw"] == 100_000

    def test_high_price_reason_contains_required_fragment(self, api_client):
        # 사용자 요청서: "1주 가격이 투자한도 초과" 문구 포함 검증.
        for policy in ("EXCLUDE", "HOLD", "INCREASE_BUDGET_HINT"):
            r = api_client.post(
                "/api/auto-paper/high-price/preview",
                json={
                    "action": "BUY", "symbol": "005930", "price": 2_000_000,
                    "policy": policy,
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert _HIGH_PRICE_REASON_FRAGMENT in body["reason_ko"], (
                f"policy={policy} 의 reason_ko 에 '{_HIGH_PRICE_REASON_FRAGMENT}' "
                f"누락 — got {body['reason_ko']!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 10. 기존 P-04 회귀 — affordability_check.PRICE_OVER_CAP 변화 없음
# ─────────────────────────────────────────────────────────────────────────────


class TestPriorPaperFeatureRegression:
    def test_p04_affordability_check_still_imports_cleanly(self):
        # P-06 추가가 P-04 모듈 영향 없음 — import OK + verdict enum 유지.
        from app.auto_paper.affordability_check import (
            AffordabilityVerdict,
            check_paper_affordability,
        )
        # P-04 의 verdict 는 그대로 유지.
        assert AffordabilityVerdict.PRICE_OVER_CAP.value == "PRICE_OVER_CAP"
        # P-04 흐름도 그대로 동작.
        r = check_paper_affordability(
            action="BUY", symbol="000660",
            price=120_000, available_cash_krw=10_000_000,
            effective_per_symbol_cap_krw=100_000,
        )
        assert r.verdict == AffordabilityVerdict.PRICE_OVER_CAP

    def test_p05_min_lot_check_still_imports_cleanly(self):
        from app.auto_paper.min_lot_check import (
            MinLotVerdict,
            validate_paper_min_lot,
        )
        # P-05 의 verdict 는 그대로.
        assert MinLotVerdict.ALLOWED.value == "ALLOWED"
        # P-05 흐름도 그대로.
        r = validate_paper_min_lot(action="BUY", quantity=1, symbol="000660")
        assert r.verdict == MinLotVerdict.ALLOWED


# ─────────────────────────────────────────────────────────────────────────────
# 11. CLAUDE.md 절대 원칙 — settings.enable_*_trading 변경 0건
# ─────────────────────────────────────────────────────────────────────────────


class TestAbsoluteSafetyInvariants:
    def test_module_does_not_mutate_safety_flags(self):
        src = _MODULE_PATH.read_text(encoding="utf-8")
        # settings 직접 접근 없음.
        assert "settings.enable_live_trading" not in src
        assert "settings.enable_ai_execution" not in src
        assert "settings.enable_futures_live_trading" not in src
        # = True 형태로 안전 flag 토글 없음.
        for pattern in [
            r"enable_live_trading\s*=\s*True",
            r"enable_ai_execution\s*=\s*True",
            r"enable_futures_live_trading\s*=\s*True",
            r"KIS_IS_PAPER\s*=\s*False",
        ]:
            assert not re.search(pattern, src), (
                f"affordability.py 에 안전 flag mutation 의심 패턴: {pattern!r}"
            )
