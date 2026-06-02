"""P-03: 최대 동시 보유 종목 수 제한 — 단위 + 정적 + API 테스트.

검증 항목 (사용자 요청서 §9 매트릭스):
- 기본값 3종목
- 5종목 / 10종목 선택 가능
- 허용 외 값 거부 (InvalidMaxConcurrentPositionsError)
- 현재 보유 ≥ 한도 → 신규 BUY (신규 종목) 차단 (BLOCKED_MAX_POSITIONS)
- 보유 < 한도 → 신규 BUY 허용 (ALLOW)
- 추가 매수 (같은 종목) → 보유 수 변화 없음 → 항상 ALLOW
- SELL / EXIT / HOLD → SKIP_NON_BUY (제한 무관)
- is_paper_only=true / is_live_authorization=false / 실거래 호출 0건
- capital_config / concurrent_positions_guard 가 broker / KIS / OrderExecutor /
  외부 HTTP / settings import 0건
- API GET/POST 동작 + 400 검증
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
    ALLOWED_MAX_CONCURRENT_POSITIONS,
    DEFAULT_MAX_CONCURRENT_POSITIONS,
    InvalidMaxConcurrentPositionsError,
    PaperCapitalConfig,
    get_paper_capital_config,
    is_allowed_max_concurrent_positions,
    reset_paper_capital_for_tests,
    set_max_concurrent_positions,
)
from app.auto_paper.concurrent_positions_guard import (
    ConcurrentBuyCheckResult,
    ConcurrentBuyVerdict,
    check_concurrent_buy_allowed,
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
# 1. 상수 + 기본값
# ─────────────────────────────────────────────────────────────────────────────


class TestConstants:
    def test_default_is_three(self):
        assert DEFAULT_MAX_CONCURRENT_POSITIONS == 3

    def test_allowed_options(self):  # now (3,5,8,10)
        assert ALLOWED_MAX_CONCURRENT_POSITIONS == (3, 5, 8, 10)

    def test_default_is_in_allowed(self):
        assert DEFAULT_MAX_CONCURRENT_POSITIONS in ALLOWED_MAX_CONCURRENT_POSITIONS

    def test_is_allowed_helper(self):
        assert is_allowed_max_concurrent_positions(3) is True
        assert is_allowed_max_concurrent_positions(5) is True
        assert is_allowed_max_concurrent_positions(10) is True
        assert is_allowed_max_concurrent_positions(7) is False
        assert is_allowed_max_concurrent_positions(0) is False
        assert is_allowed_max_concurrent_positions(-1) is False
        assert is_allowed_max_concurrent_positions("x") is False  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# 2. PaperCapitalConfig dataclass — max_concurrent_positions field
# ─────────────────────────────────────────────────────────────────────────────


class TestDataclassInvariants:
    def test_default_snapshot_carries_max_positions(self):
        cfg = get_paper_capital_config()
        assert cfg.max_concurrent_positions == 3
        assert cfg.allowed_max_concurrent_positions_options == (3, 5, 8, 10)

    def test_to_dict_includes_p03_fields(self):
        cfg = get_paper_capital_config()
        d = cfg.to_dict()
        assert d["max_concurrent_positions"] == 3
        assert d["allowed_max_concurrent_positions_options"] == [3, 5, 8, 10]

    def test_invalid_value_rejected_at_dataclass(self):
        with pytest.raises(ValueError, match="max_concurrent_positions"):
            PaperCapitalConfig(
                initial_cash=10_000_000,
                allowed_initial_cash_options=(10_000_000, 30_000_000, 50_000_000),
                max_concurrent_positions=7,  # not allowed
            )

    def test_zero_value_rejected(self):
        with pytest.raises(ValueError, match="max_concurrent_positions"):
            PaperCapitalConfig(
                initial_cash=10_000_000,
                allowed_initial_cash_options=(10_000_000, 30_000_000, 50_000_000),
                max_concurrent_positions=0,
            )

    def test_paper_only_invariant_still_holds(self):
        cfg = get_paper_capital_config()
        assert cfg.is_paper_only is True
        assert cfg.is_live_authorization is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. set_max_concurrent_positions 동작
# ─────────────────────────────────────────────────────────────────────────────


class TestSetMaxConcurrentPositions:
    @pytest.mark.parametrize("value", [3, 5, 8, 10])
    def test_allowed_values(self, value):
        cfg, fallback = set_max_concurrent_positions(value)
        assert cfg.max_concurrent_positions == value
        assert fallback is False

    def test_reject_unknown(self):
        with pytest.raises(InvalidMaxConcurrentPositionsError):
            set_max_concurrent_positions(7)

    def test_reject_negative(self):
        with pytest.raises(InvalidMaxConcurrentPositionsError):
            set_max_concurrent_positions(-1)

    def test_reject_huge(self):
        with pytest.raises(InvalidMaxConcurrentPositionsError):
            set_max_concurrent_positions(1_000)

    def test_fallback_to_default(self):
        cfg, fallback = set_max_concurrent_positions(7, fallback_to_default=True)
        assert cfg.max_concurrent_positions == DEFAULT_MAX_CONCURRENT_POSITIONS
        assert fallback is True

    def test_roundtrip(self):
        set_max_concurrent_positions(10)
        assert get_paper_capital_config().max_concurrent_positions == 10

    def test_reset_restores_default(self):
        set_max_concurrent_positions(10)
        reset_paper_capital_for_tests()
        assert get_paper_capital_config().max_concurrent_positions == 3


# ─────────────────────────────────────────────────────────────────────────────
# 4. check_concurrent_buy_allowed — pure helper
# ─────────────────────────────────────────────────────────────────────────────


class TestConcurrentBuyGuard:
    def test_under_limit_allows_new_buy(self):
        r = check_concurrent_buy_allowed(
            action="BUY", symbol="005930",
            current_held_symbols=["000660"],   # 1 held, limit=3
            max_concurrent_positions=3,
        )
        assert r.verdict == ConcurrentBuyVerdict.ALLOW
        assert r.current_unique_symbol_count == 1
        assert r.is_existing_position is False

    def test_at_limit_blocks_new_symbol(self):
        r = check_concurrent_buy_allowed(
            action="BUY", symbol="005930",
            current_held_symbols=["000660", "035720", "035420"],   # 3 held, limit=3
            max_concurrent_positions=3,
        )
        assert r.verdict == ConcurrentBuyVerdict.BLOCKED_MAX_POSITIONS
        assert r.current_unique_symbol_count == 3

    def test_over_limit_blocks_new_symbol(self):
        r = check_concurrent_buy_allowed(
            action="BUY", symbol="005930",
            current_held_symbols=["000660", "035720", "035420", "207940"],
            max_concurrent_positions=3,
        )
        assert r.verdict == ConcurrentBuyVerdict.BLOCKED_MAX_POSITIONS

    def test_existing_position_always_allowed(self):
        r = check_concurrent_buy_allowed(
            action="BUY", symbol="005930",
            current_held_symbols=["005930", "035720", "035420"],   # symbol IS held
            max_concurrent_positions=3,
        )
        assert r.verdict == ConcurrentBuyVerdict.ALLOW
        assert r.is_existing_position is True

    def test_sell_action_skipped(self):
        r = check_concurrent_buy_allowed(
            action="SELL", symbol="005930",
            current_held_symbols=["000660", "035720", "035420"],
            max_concurrent_positions=3,
        )
        assert r.verdict == ConcurrentBuyVerdict.SKIP_NON_BUY

    def test_exit_action_skipped(self):
        r = check_concurrent_buy_allowed(
            action="EXIT", symbol="005930",
            current_held_symbols=["000660", "035720", "035420"],
            max_concurrent_positions=3,
        )
        assert r.verdict == ConcurrentBuyVerdict.SKIP_NON_BUY

    def test_hold_action_skipped(self):
        r = check_concurrent_buy_allowed(
            action="HOLD", symbol="005930",
            current_held_symbols=["000660", "035720", "035420"],
            max_concurrent_positions=3,
        )
        assert r.verdict == ConcurrentBuyVerdict.SKIP_NON_BUY

    @pytest.mark.parametrize("limit,held_count,new_symbol_blocked", [
        (3, 0, False), (3, 1, False), (3, 2, False), (3, 3, True), (3, 4, True),
        (5, 4, False), (5, 5, True), (5, 6, True),
        (10, 9, False), (10, 10, True), (10, 11, True),
    ])
    def test_limit_threshold(self, limit, held_count, new_symbol_blocked):
        held = [f"00{i:04d}" for i in range(held_count)]
        r = check_concurrent_buy_allowed(
            action="BUY", symbol="999999",
            current_held_symbols=held,
            max_concurrent_positions=limit,
        )
        if new_symbol_blocked:
            assert r.verdict == ConcurrentBuyVerdict.BLOCKED_MAX_POSITIONS
        else:
            assert r.verdict == ConcurrentBuyVerdict.ALLOW

    def test_case_insensitive_action(self):
        r1 = check_concurrent_buy_allowed(
            action="buy", symbol="005930",
            current_held_symbols=[], max_concurrent_positions=3,
        )
        r2 = check_concurrent_buy_allowed(
            action="BUY", symbol="005930",
            current_held_symbols=[], max_concurrent_positions=3,
        )
        assert r1.verdict == r2.verdict == ConcurrentBuyVerdict.ALLOW

    def test_no_symbol_with_buy_treated_as_new(self):
        r = check_concurrent_buy_allowed(
            action="BUY", symbol=None,
            current_held_symbols=["000660", "035720", "035420"],
            max_concurrent_positions=3,
        )
        assert r.verdict == ConcurrentBuyVerdict.BLOCKED_MAX_POSITIONS

    def test_duplicate_held_symbols_deduped(self):
        r = check_concurrent_buy_allowed(
            action="BUY", symbol="999999",
            # 같은 종목 중복 → set 으로 dedupe → 실제 1개 보유.
            current_held_symbols=["000660", "000660", "000660"],
            max_concurrent_positions=3,
        )
        assert r.current_unique_symbol_count == 1
        assert r.verdict == ConcurrentBuyVerdict.ALLOW

    def test_result_invariants(self):
        r = check_concurrent_buy_allowed(
            action="BUY", symbol="005930",
            current_held_symbols=[], max_concurrent_positions=3,
        )
        assert r.is_order_signal is False
        assert r.is_live_authorization is False
        assert r.is_paper_only is True

    def test_dataclass_rejects_live_authorization_true(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            ConcurrentBuyCheckResult(
                verdict=ConcurrentBuyVerdict.ALLOW,
                current_unique_symbol_count=0,
                max_concurrent_positions=3,
                is_live_authorization=True,  # type: ignore[arg-type]
            )

    def test_to_dict_payload_contract(self):
        r = check_concurrent_buy_allowed(
            action="BUY", symbol="005930",
            current_held_symbols=["000660"], max_concurrent_positions=3,
        )
        d = r.to_dict()
        for key in (
            "verdict", "current_unique_symbol_count", "max_concurrent_positions",
            "symbol", "action", "reason", "is_existing_position",
            "is_order_signal", "is_live_authorization", "is_paper_only",
        ):
            assert key in d


# ─────────────────────────────────────────────────────────────────────────────
# 5. 정적 import 가드
# ─────────────────────────────────────────────────────────────────────────────


class TestNoBrokerOrLiveImports:
    @pytest.mark.parametrize("module_rel", [
        "app/auto_paper/capital_config.py",
        "app/auto_paper/concurrent_positions_guard.py",
    ])
    def test_no_banned_imports(self, module_rel):
        src = Path(__file__).resolve().parent.parent / module_rel
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
                        f"{module_rel} 가 금지 모듈 '{m}' import"
                    )

    @pytest.mark.parametrize("module_rel", [
        "app/auto_paper/capital_config.py",
        "app/auto_paper/concurrent_positions_guard.py",
    ])
    def test_no_broker_call_patterns(self, module_rel):
        src = Path(__file__).resolve().parent.parent / module_rel
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
                f"{module_rel} 에 금지 패턴: /{pat}/"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 6. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestApiEndpoints:
    def test_get_returns_default_p03_field(self, api_client):
        res = api_client.get("/api/auto-paper/capital-config")
        assert res.status_code == 200
        body = res.json()
        assert body["max_concurrent_positions"] == 3
        assert body["allowed_max_concurrent_positions_options"] == [3, 5, 8, 10]

    @pytest.mark.parametrize("count", [3, 5, 8, 10])
    def test_post_accepts_allowed(self, api_client, count):
        res = api_client.post(
            "/api/auto-paper/max-concurrent-positions",
            json={"max_concurrent_positions": count, "fallback_to_default": False},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["max_concurrent_positions"] == count
        assert body["fallback_used"] is False
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False

    def test_post_rejects_invalid_with_400(self, api_client):
        res = api_client.post(
            "/api/auto-paper/max-concurrent-positions",
            json={"max_concurrent_positions": 7, "fallback_to_default": False},
        )
        assert res.status_code == 400
        detail = res.json().get("detail", {})
        assert detail.get("error") == "invalid_max_concurrent_positions"
        assert detail.get("allowed_max_concurrent_positions_options") == [3, 5, 8, 10]

    def test_post_fallback(self, api_client):
        res = api_client.post(
            "/api/auto-paper/max-concurrent-positions",
            json={"max_concurrent_positions": 7, "fallback_to_default": True},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["fallback_used"] is True
        assert body["max_concurrent_positions"] == DEFAULT_MAX_CONCURRENT_POSITIONS

    def test_preview_allow(self, api_client):
        res = api_client.post(
            "/api/auto-paper/max-concurrent-positions/preview",
            json={
                "action": "BUY",
                "symbol": "005930",
                "current_held_symbols": ["000660", "035720"],
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["verdict"] == "ALLOW"
        assert body["current_unique_symbol_count"] == 2
        assert body["max_concurrent_positions"] == 3
        assert body["is_paper_only"] is True
        assert body["is_live_authorization"] is False

    def test_preview_blocked_at_limit(self, api_client):
        res = api_client.post(
            "/api/auto-paper/max-concurrent-positions/preview",
            json={
                "action": "BUY",
                "symbol": "005930",
                "current_held_symbols": ["000660", "035720", "035420"],
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["verdict"] == "BLOCKED_MAX_POSITIONS"
        assert body["current_unique_symbol_count"] == 3

    def test_preview_sell_skipped(self, api_client):
        res = api_client.post(
            "/api/auto-paper/max-concurrent-positions/preview",
            json={
                "action": "SELL",
                "symbol": "005930",
                "current_held_symbols": ["000660", "035720", "035420"],
            },
        )
        assert res.json()["verdict"] == "SKIP_NON_BUY"

    def test_preview_uses_updated_limit(self, api_client):
        # 한도를 5 로 올리면 4 보유 + 신규 BUY 도 ALLOW.
        api_client.post(
            "/api/auto-paper/max-concurrent-positions",
            json={"max_concurrent_positions": 5},
        )
        res = api_client.post(
            "/api/auto-paper/max-concurrent-positions/preview",
            json={
                "action": "BUY",
                "symbol": "005930",
                "current_held_symbols": [
                    "000660", "035720", "035420", "207940",
                ],
            },
        )
        body = res.json()
        assert body["verdict"] == "ALLOW"
        assert body["max_concurrent_positions"] == 5

    def test_payload_contains_no_secret_strings(self, api_client):
        api_client.post(
            "/api/auto-paper/max-concurrent-positions",
            json={"max_concurrent_positions": 5},
        )
        res = api_client.get("/api/auto-paper/capital-config")
        import json
        text = json.dumps(res.json()).lower()
        for needle in ("kis_app_key", "kis_app_secret", "anthropic_api_key",
                       "openai_api_key", "telegram_bot_token", "sk-",
                       "bearer ", "kis_account_no"):
            assert needle not in text

    def test_notice_says_paper_only(self, api_client):
        res = api_client.post(
            "/api/auto-paper/max-concurrent-positions",
            json={"max_concurrent_positions": 5},
        )
        body = res.json()
        assert "Paper 모의매매 전용" in body["notice"]
        assert "실전 주문 한도가" in body["notice"]


# ─────────────────────────────────────────────────────────────────────────────
# 7. 안전 flag mutate invariant
# ─────────────────────────────────────────────────────────────────────────────


class TestSafetyFlagsUntouched:
    def test_settings_flags_untouched(self):
        from app.core.config import get_settings
        before = get_settings()
        set_max_concurrent_positions(10)
        after = get_settings()
        assert before.enable_live_trading == after.enable_live_trading is False
        assert before.enable_ai_execution == after.enable_ai_execution is False
        assert before.enable_futures_live_trading == after.enable_futures_live_trading is False
        assert before.kis_is_paper == after.kis_is_paper is True
