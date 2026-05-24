"""#55 / 7-03: Paper / KIS Paper 포트폴리오 source 통일 테스트.

핵심 invariant:
- Paper snapshot source=PAPER_SIMULATED, KIS Paper 성공 source=KIS_PAPER_ACCOUNT.
- **API 실패 → source=UNAVAILABLE, cash=None, total_asset=None** (0원 fallback 금지).
- 실제 cash=0, status=OK 이면 0원 표시 가능 (조회 실패와 구분).
- mixed source 차단(MIXED_BLOCKED).
- secret / 계좌번호 carry 0건, broker / OrderExecutor / route_order import 0건.
"""

from __future__ import annotations

import json

import pytest

from app.portfolio import portfolio_snapshot as ps
from app.portfolio.portfolio_snapshot import (
    PortfolioSource,
    PortfolioStatus,
    PortfolioSourceSnapshot,
    build_paper_simulated_snapshot,
    build_kis_paper_account_snapshot,
    build_portfolio_source_report,
    mixed_blocked_snapshot,
    assert_single_source,
)


# ── 가짜 Paper snapshot (build_portfolio_state 대체) ─────────────────────────


class _FakePaper:
    def __init__(self, *, cash=9_025_000, equity=10_000_000, count=2):
        self.current_cash = cash
        self.total_equity = equity
        self.starting_cash = 10_000_000
        self.total_position_value = equity - cash
        self.total_unrealized_pnl = 12_345
        self.position_count = count
        self.invested_krw = equity - cash
        self.realized_pnl = 5_000
        self.last_event_at = "2026-05-24T01:00:00+00:00"
        self.positions = [
            {"symbol": "005930", "quantity": 10, "average_price": 70_000,
             "current_price": 71_000, "market_value": 710_000,
             "unrealized_pnl": 10_000, "portfolio_weight_pct": 0.07,
             # secret-like 키 — sanitize 로 제거되어야 함.
             "kis_account_no": "12345678-01", "access_token": "sk-secret"},
        ]


def _ok_paper_builder(db, *, last_prices=None, now=None):
    return _FakePaper()


def _raising_builder(db, *, last_prices=None, now=None):
    raise RuntimeError("DB unavailable")


# ── 1. Paper snapshot source ────────────────────────────────────────────────


def test_paper_snapshot_source_is_paper_simulated():
    s = build_paper_simulated_snapshot(db=None, builder=_ok_paper_builder)
    assert s.source == PortfolioSource.PAPER_SIMULATED.value
    assert s.status == PortfolioStatus.OK.value
    assert s.reason_code == ps.PORTFOLIO_SOURCE_PAPER_SIMULATED
    assert s.cash == 9_025_000
    assert s.total_asset == 10_000_000
    assert s.position_count == 2
    assert s.last_updated is not None


def test_paper_snapshot_strips_secret_keys_from_positions():
    s = build_paper_simulated_snapshot(db=None, builder=_ok_paper_builder)
    for pos in s.positions:
        assert "kis_account_no" not in pos
        assert "access_token" not in pos
    # 그래도 표시 필드는 carry.
    assert s.positions[0]["symbol"] == "005930"


# ── 2. KIS Paper snapshot source ────────────────────────────────────────────


def test_kis_paper_snapshot_source_on_success():
    def fetcher():
        return {"cash": 5_000_000, "total_asset": 5_500_000,
                "position_count": 1, "positions": [{"symbol": "000660"}]}
    s = build_kis_paper_account_snapshot(credentials_present=True, balance_fetcher=fetcher)
    assert s.source == PortfolioSource.KIS_PAPER_ACCOUNT.value
    assert s.status == PortfolioStatus.OK.value
    assert s.cash == 5_000_000
    assert s.total_asset == 5_500_000


# ── 3~7. API 실패 → UNAVAILABLE, 값 None, 0 fallback 금지 ────────────────────


def test_paper_api_failure_is_unavailable_no_zero():
    s = build_paper_simulated_snapshot(db=None, builder=_raising_builder)
    assert s.source == PortfolioSource.UNAVAILABLE.value          # (3)
    assert s.cash is None                                          # (4)
    assert s.total_asset is None                                   # (5)
    assert s.position_count is None
    assert s.zero_fallback_used is False                           # (6)
    assert s.reason_code == ps.PORTFOLIO_API_FAILED_NO_ZERO_FALLBACK  # (7)
    # attempted_source 로 어떤 조회였는지는 안다.
    assert s.attempted_source == PortfolioSource.PAPER_SIMULATED.value


def test_kis_no_credentials_is_credentials_missing_no_zero():
    s = build_kis_paper_account_snapshot(credentials_present=False)
    assert s.source == PortfolioSource.UNAVAILABLE.value
    assert s.status == PortfolioStatus.CREDENTIALS_MISSING.value
    assert s.cash is None and s.total_asset is None
    assert s.reason_code == ps.PORTFOLIO_CREDENTIALS_MISSING


def test_kis_no_fetcher_is_not_configured_no_zero():
    s = build_kis_paper_account_snapshot(credentials_present=True, balance_fetcher=None)
    assert s.source == PortfolioSource.UNAVAILABLE.value
    assert s.status == PortfolioStatus.NOT_CONFIGURED.value
    assert s.cash is None and s.total_asset is None


def test_kis_fetcher_raises_is_api_unavailable_no_zero():
    def boom():
        raise ConnectionError("kis paper down")
    s = build_kis_paper_account_snapshot(credentials_present=True, balance_fetcher=boom)
    assert s.source == PortfolioSource.UNAVAILABLE.value
    assert s.status == PortfolioStatus.API_UNAVAILABLE.value
    assert s.cash is None and s.total_asset is None


def test_kis_fetcher_missing_value_does_not_zero_fill():
    # fetcher 가 cash 만 주고 total_asset 누락 → 0 으로 채우지 않고 UNAVAILABLE.
    s = build_kis_paper_account_snapshot(
        credentials_present=True, balance_fetcher=lambda: {"cash": 100},
    )
    assert s.source == PortfolioSource.UNAVAILABLE.value
    assert s.cash is None and s.total_asset is None


# ── 실제 0원과 조회 실패 구분 ────────────────────────────────────────────────


def test_real_zero_balance_is_displayable():
    # status=OK, cash=0 은 *실제 0원* — 표시 허용.
    s = build_paper_simulated_snapshot(
        db=None, builder=lambda db, last_prices=None, now=None: _FakePaper(cash=0, equity=0, count=0),
    )
    assert s.status == PortfolioStatus.OK.value
    assert s.cash == 0
    assert s.total_asset == 0
    assert s.value_available is True


def test_failure_value_not_available():
    s = build_paper_simulated_snapshot(db=None, builder=_raising_builder)
    assert s.value_available is False


# ── 0 fallback 강제 차단 (dataclass invariant) ───────────────────────────────


def test_constructing_failure_with_zero_value_raises():
    # ERROR 상태인데 cash=0 으로 구성하려 하면 ValueError (0원 fallback 금지).
    with pytest.raises(ValueError):
        PortfolioSourceSnapshot(
            source=PortfolioSource.UNAVAILABLE.value,
            status=PortfolioStatus.ERROR.value,
            reason_code=ps.PORTFOLIO_API_FAILED_NO_ZERO_FALLBACK,
            cash=0,
        )


def test_zero_fallback_used_must_be_false():
    with pytest.raises(ValueError):
        PortfolioSourceSnapshot(
            source=PortfolioSource.PAPER_SIMULATED.value,
            status=PortfolioStatus.OK.value,
            reason_code=ps.PORTFOLIO_SOURCE_PAPER_SIMULATED,
            zero_fallback_used=True,
        )


@pytest.mark.parametrize("flag", [
    "is_order_signal", "auto_apply_allowed", "is_live_authorization", "contains_secret",
])
def test_safety_invariants_locked(flag):
    with pytest.raises(ValueError):
        PortfolioSourceSnapshot(
            source=PortfolioSource.PAPER_SIMULATED.value,
            status=PortfolioStatus.OK.value,
            reason_code=ps.PORTFOLIO_SOURCE_PAPER_SIMULATED,
            **{flag: True},
        )


def test_is_paper_only_must_be_true():
    with pytest.raises(ValueError):
        PortfolioSourceSnapshot(
            source=PortfolioSource.PAPER_SIMULATED.value,
            status=PortfolioStatus.OK.value,
            reason_code=ps.PORTFOLIO_SOURCE_PAPER_SIMULATED,
            is_paper_only=False,
        )


# ── mixed source 차단 ────────────────────────────────────────────────────────


def test_mixed_blocked_snapshot():
    m = mixed_blocked_snapshot([PortfolioSource.PAPER_SIMULATED.value,
                                PortfolioSource.KIS_PAPER_ACCOUNT.value])
    assert m.source == PortfolioSource.MIXED_BLOCKED.value
    assert m.reason_code == ps.PORTFOLIO_MIXED_SOURCE_BLOCKED
    assert m.cash is None


def test_assert_single_source_blocks_two_value_sources():
    paper = build_paper_simulated_snapshot(db=None, builder=_ok_paper_builder)
    kis = build_kis_paper_account_snapshot(
        credentials_present=True,
        balance_fetcher=lambda: {"cash": 1, "total_asset": 2},
    )
    blocked = assert_single_source([paper, kis])
    assert blocked is not None
    assert blocked.source == PortfolioSource.MIXED_BLOCKED.value


def test_assert_single_source_ok_when_one_value_source():
    paper = build_paper_simulated_snapshot(db=None, builder=_ok_paper_builder)
    kis_unavail = build_kis_paper_account_snapshot(credentials_present=False)
    # 값을 가진 source 가 1개뿐(paper) → 차단 없음.
    assert assert_single_source([paper, kis_unavail]) is None


# ── report 조합 (섞지 않음) ──────────────────────────────────────────────────


def test_report_keeps_sources_separate():
    rep = build_portfolio_source_report(
        db=None, kis_credentials_present=False, paper_builder=_ok_paper_builder,
    )
    assert rep.paper_simulated.source == PortfolioSource.PAPER_SIMULATED.value
    assert rep.kis_paper_account.source == PortfolioSource.UNAVAILABLE.value
    assert rep.primary_source == PortfolioSource.PAPER_SIMULATED.value
    d = rep.to_dict()
    assert len(d["snapshots"]) == 2
    assert d["is_live_authorization"] is False
    assert d["contains_secret"] is False


# ── secret 미노출 / import 가드 ──────────────────────────────────────────────


def test_module_has_no_forbidden_imports():
    src = open(ps.__file__, encoding="utf-8").read()
    forbidden = (
        "from app.brokers", "from app.execution.order_router",
        "from app.execution.executor", "from app.execution.order_executor",
        "from app.execution.paper_trader", "from app.ai.assist",
        "from app.ai.client", "import anthropic", "import openai",
        "import httpx", "import requests",
        "from app.core.config import get_settings", "get_settings(",
        ".place_order(", ".cancel_order(", "route_order(",
    )
    for tok in forbidden:
        assert tok not in src, f"forbidden token in module: {tok}"


def test_no_secret_in_snapshot_dict():
    s = build_paper_simulated_snapshot(db=None, builder=_ok_paper_builder)
    text = json.dumps(s.to_dict(), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "access_token", "12345678-01",
                      "Bearer ", "sk-ant-", "sk-secret"):
        assert forbidden not in text


# ── API endpoint ─────────────────────────────────────────────────────────────


def test_api_portfolio_source_fields(client):
    r = client.get("/api/auto-paper/portfolio-source")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "snapshots" in body and len(body["snapshots"]) == 2
    paper = body["paper_simulated"]
    for k in ("source", "status", "reason_code", "cash", "total_asset",
              "position_count", "last_updated", "value_available"):
        assert k in paper, f"missing {k}"
    assert body["is_live_authorization"] is False
    assert body["contains_secret"] is False


def test_api_kis_paper_unavailable_not_zero(client):
    # 테스트 환경엔 KIS 잔고 fetcher 미연결 → KIS snapshot 은 값 없음(0 아님).
    body = client.get("/api/auto-paper/portfolio-source").json()
    kis = body["kis_paper_account"]
    assert kis["source"] == PortfolioSource.UNAVAILABLE.value
    assert kis["cash"] is None
    assert kis["total_asset"] is None
    assert kis["value_available"] is False
    assert kis["attempted_source"] == PortfolioSource.KIS_PAPER_ACCOUNT.value


def test_api_no_broker_order(client):
    client.get("/api/auto-paper/portfolio-source")
    assert len(client.test_broker.orders) == 0


def test_api_no_secret_in_response(client):
    text = json.dumps(client.get("/api/auto-paper/portfolio-source").json(),
                      ensure_ascii=False)
    for forbidden in ("kis_app_secret", "Bearer ", "sk-ant-", "access_token="):
        assert forbidden not in text
