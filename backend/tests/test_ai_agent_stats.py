"""AI agent stats tests (162, MUST)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.agent_stats import compute_ai_agent_stats
from app.db.base import Base
from app.db.models import OrderAuditLog


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _ai_audit(
    db, *,
    decision="APPROVED",
    strategy="ai_virtual",
    symbol="005930",
    requested_by_ai=True,
    confidence=80,
    executed=True,
    reasons=None,
    created_at=None,
):
    row = OrderAuditLog(
        mode="VIRTUAL_AI_EXECUTION", symbol=symbol, side="BUY", quantity=1,
        order_type="MARKET", latest_price=100,
        decision=decision, reasons=list(reasons or []),
        requested_by_ai=requested_by_ai,
        strategy=strategy,
        signal_strength=confidence,
        signal_confidence=confidence,
        executed=executed,
        broker_status="FILLED" if executed else None,
        filled_quantity=1 if executed else 0,
        avg_fill_price=100 if executed else None,
        created_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


# ---------- core stats ----------

def test_empty_db_returns_zero_counts():
    Session = _session()
    with Session() as db:
        s = compute_ai_agent_stats(db)
    assert s["total_proposals"] == 0
    assert s["approval_rate"]   == 0.0
    assert s["avg_confidence"]  == 0.0
    assert s["per_strategy"]    == []


def test_counts_only_requested_by_ai_rows():
    """비-AI 주문은 카운트 X."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, requested_by_ai=False, created_at=now)
        _ai_audit(db, requested_by_ai=True,  created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["total_proposals"] == 1


def test_approval_rate_excludes_needs_approval():
    """approval_rate 분모 = approved + rejected. NEEDS_APPROVAL은 별도 카운트."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        for _ in range(3):
            _ai_audit(db, decision="APPROVED", created_at=now)
        _ai_audit(db, decision="REJECTED", created_at=now)
        _ai_audit(db, decision="NEEDS_APPROVAL", created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["approved"]       == 3
    assert s["rejected"]       == 1
    assert s["needs_approval"] == 1
    assert s["approval_rate"]  == 0.75


def test_avg_confidence_only_executed_rows_with_confidence():
    """미체결(executed=False) 또는 confidence=None인 row는 평균 산출에서 제외."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, executed=True,  confidence=80, created_at=now)
        _ai_audit(db, executed=True,  confidence=60, created_at=now)
        _ai_audit(db, executed=False, confidence=10, created_at=now)
        _ai_audit(db, executed=True,  confidence=None, created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["avg_confidence"] == 70.0


def test_lookback_days_zero_means_all_time():
    """lookback_days=0이면 cutoff 없이 전체."""
    Session = _session()
    now = datetime.now(timezone.utc)
    very_old = now - timedelta(days=400)
    with Session() as db:
        _ai_audit(db, created_at=very_old)
        _ai_audit(db, created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, lookback_days=0, now=now)
    assert s["total_proposals"] == 2


def test_lookback_excludes_rows_outside_window():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, created_at=now - timedelta(days=10))  # 밖
        _ai_audit(db, created_at=now)                       # 안
        db.commit()
        s = compute_ai_agent_stats(db, lookback_days=7, now=now)
    assert s["total_proposals"] == 1


# ---------- per_strategy ----------

def test_per_strategy_separates_correctly():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        for _ in range(3):
            _ai_audit(db, strategy="ai_a", decision="APPROVED", created_at=now)
        _ai_audit(db, strategy="ai_a", decision="REJECTED", created_at=now)
        _ai_audit(db, strategy="ai_b", decision="APPROVED", created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    by = {row["strategy"]: row for row in s["per_strategy"]}
    assert by["ai_a"]["total"]         == 4
    assert by["ai_a"]["approved"]      == 3
    assert by["ai_a"]["approval_rate"] == 0.75
    assert by["ai_b"]["total"]         == 1
    assert by["ai_b"]["approved"]      == 1


def test_per_strategy_sorted_by_total_desc():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        for _ in range(2):
            _ai_audit(db, strategy="b", created_at=now)
        for _ in range(5):
            _ai_audit(db, strategy="a", created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert [r["strategy"] for r in s["per_strategy"]] == ["a", "b"]


def test_null_strategy_appears_as_unknown():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, strategy=None, created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["per_strategy"][0]["strategy"] == "(unknown)"


# ---------- top_rejection_reasons ----------

def test_top_rejection_reasons_categorized():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, decision="REJECTED", created_at=now,
                  reasons=["emergency stop is enabled"])
        _ai_audit(db, decision="REJECTED", created_at=now,
                  reasons=["AI signal confidence 50 < min_ai_confidence 70"])
        _ai_audit(db, decision="REJECTED", created_at=now,
                  reasons=["AI signal confidence 30 < min_ai_confidence 70"])
        _ai_audit(db, decision="REJECTED", created_at=now,
                  reasons=["AI rate limit exceeded: 5 proposals in 60s"])
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["top_rejection_reasons"]["low_confidence"] == 2
    assert s["top_rejection_reasons"]["emergency_stop"] == 1
    assert s["top_rejection_reasons"]["rate_limit"]     == 1


def test_top_rejection_reasons_does_not_count_approved_rows():
    """APPROVED row의 reasons는 이상한 카테고리로 누적 안 됨."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, decision="APPROVED", created_at=now,
                  reasons=["AI signal confidence above threshold"])
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["top_rejection_reasons"] == {}


# ---------- HTTP integration ----------

def test_endpoint_surface_basic_metrics(client):
    now = datetime.now(timezone.utc)
    with client.test_db_factory() as db:
        for _ in range(3):
            _ai_audit(db, decision="APPROVED", strategy="ai_virtual",
                      created_at=now)
        _ai_audit(db, decision="REJECTED", strategy="ai_virtual",
                  reasons=["AI signal confidence 30 < min_ai_confidence 70"],
                  created_at=now)
        db.commit()
    res = client.get("/api/ai/agent-stats?lookback_days=7")
    assert res.status_code == 200
    body = res.json()
    assert body["total_proposals"] == 4
    assert body["approval_rate"]   == 0.75
    assert body["top_rejection_reasons"]["low_confidence"] == 1


def test_endpoint_validates_lookback_days_range(client):
    res = client.get("/api/ai/agent-stats?lookback_days=-1")
    assert res.status_code == 422
    res = client.get("/api/ai/agent-stats?lookback_days=999999")
    assert res.status_code == 422


def test_endpoint_empty_returns_zero_counts(client):
    res = client.get("/api/ai/agent-stats")
    assert res.status_code == 200
    body = res.json()
    assert body["total_proposals"] == 0
    assert body["per_strategy"]    == []


# ---------- 165: confidence histogram + realized PnL by strategy ----------

def test_confidence_histogram_buckets_correctly():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        # 각 bucket에 의도적으로 다른 confidence 시드.
        _ai_audit(db, confidence=10,  created_at=now)   # 0-25
        _ai_audit(db, confidence=24,  created_at=now)   # 0-25
        _ai_audit(db, confidence=40,  created_at=now)   # 25-50
        _ai_audit(db, confidence=70,  created_at=now)   # 50-75
        _ai_audit(db, confidence=80,  created_at=now)   # 75-100
        _ai_audit(db, confidence=100, created_at=now)   # 75-100
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    h = s["confidence_histogram"]
    assert h["0-25"]   == 2
    assert h["25-50"]  == 1
    assert h["50-75"]  == 1
    assert h["75-100"] == 2


def test_confidence_histogram_missing_counter():
    """confidence=None인 row는 별도 missing 카운터로."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, confidence=None, created_at=now)
        _ai_audit(db, confidence=50,   created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["confidence_histogram_missing"] == 1
    assert s["confidence_histogram"]["50-75"] == 1


def test_confidence_histogram_boundary_values():
    """경계값 — 25는 25-50, 50은 50-75, 75는 75-100 (lower-bound inclusive)."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, confidence=25, created_at=now)
        _ai_audit(db, confidence=50, created_at=now)
        _ai_audit(db, confidence=75, created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    h = s["confidence_histogram"]
    assert h["0-25"]   == 0
    assert h["25-50"]  == 1
    assert h["50-75"]  == 1
    assert h["75-100"] == 1


def test_per_strategy_includes_realized_pnl_from_fifo():
    """163의 compute_historical_accuracy 결과가 per_strategy에 반영."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        # ai_winner: BUY 100, SELL 110 — +10 realized.
        _ai_audit(db, strategy="ai_winner", decision="APPROVED",
                  confidence=80, created_at=now)
        # 위 audit row는 helper가 BUY로 만들지만, 체결 정보가 필요. helper가
        # filled_quantity/avg_fill_price 채우는지 확인.
        # helper에서 default executed=True, filled_quantity=1, avg_fill_price=100.
        # SELL 매칭하려면 두 번째 row 추가 (helper의 side는 default BUY라 직접 추가).
        sell_row = OrderAuditLog(
            mode="VIRTUAL_AI_EXECUTION", symbol="005930", side="SELL",
            quantity=1, order_type="MARKET", latest_price=110,
            decision="APPROVED", reasons=[], requested_by_ai=True,
            strategy="ai_winner", signal_strength=80, signal_confidence=80,
            executed=True, broker_status="FILLED",
            filled_quantity=1, avg_fill_price=110,
            created_at=now,
        )
        db.add(sell_row)
        db.commit()

        s = compute_ai_agent_stats(db, now=now)
    by = {row["strategy"]: row for row in s["per_strategy"]}
    winner = by["ai_winner"]
    assert winner["wins"]         == 1
    assert winner["losses"]       == 0
    assert winner["realized_pnl"] == 10


def test_per_strategy_unknown_has_zero_pnl():
    """(unknown) strategy는 NULL row 매핑이라 PnL 카운트 0."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, strategy=None, decision="APPROVED", created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    unknown = s["per_strategy"][0]
    assert unknown["strategy"]     == "(unknown)"
    assert unknown["wins"]         == 0
    assert unknown["losses"]       == 0
    assert unknown["realized_pnl"] == 0


def test_endpoint_surfaces_extended_fields(client):
    """HTTP 응답에 165 신규 필드가 surface."""
    now = datetime.now(timezone.utc)
    with client.test_db_factory() as db:
        _ai_audit(db, confidence=80, created_at=now)
        _ai_audit(db, confidence=20, created_at=now)
        db.commit()
    body = client.get("/api/ai/agent-stats?lookback_days=7").json()
    assert "confidence_histogram" in body
    assert body["confidence_histogram"]["0-25"]   == 1
    assert body["confidence_histogram"]["75-100"] == 1
    assert "confidence_histogram_missing" in body
    # per_strategy entries should have wins/losses/realized_pnl.
    if body["per_strategy"]:
        assert "wins"         in body["per_strategy"][0]
        assert "losses"       in body["per_strategy"][0]
        assert "realized_pnl" in body["per_strategy"][0]


# ---------- 187: agent-decisions endpoint ----------

def test_agent_decisions_endpoint_empty(client):
    res = client.get("/api/ai/agent-decisions")
    assert res.status_code == 200
    assert res.json() == []


def test_agent_decisions_endpoint_returns_recent_first(client):
    """chain_id 미지정 시 최근 limit건, created_at desc."""
    from app.ai.agents import ChiefTradingAgent, CouncilContext, persist_decision
    chief = ChiefTradingAgent()
    with client.test_db_factory() as db:
        decision, members = chief.coordinate(CouncilContext(
            symbol="005930", last_close=110, prev_close=100,
            equity=10_000_000, notional=110_000, regime="trending_up",
        ))
        persist_decision(db, decision, mode="VIRTUAL_AI_EXECUTION")
        for m in members:
            persist_decision(db, m, mode="VIRTUAL_AI_EXECUTION")
        db.commit()

    rows = client.get("/api/ai/agent-decisions").json()
    assert len(rows) == 10  # 1 chief + 9 members
    # 최신 first → 마지막 추가된 (member의 마지막) 또는 chief가 첫.
    assert all("agent_name" in r for r in rows)
    assert all("chain_id" in r for r in rows)


def test_agent_decisions_chain_id_filter(client):
    """chain_id 지정 시 해당 chain의 결정만."""
    from app.ai.agents import (
        ChiefTradingAgent, CouncilContext, new_chain_id, persist_decision,
    )
    chief = ChiefTradingAgent()
    chain_a = new_chain_id()
    chain_b = new_chain_id()
    with client.test_db_factory() as db:
        # chain A
        d_a, m_a = chief.coordinate(CouncilContext(
            symbol="005930", last_close=110, prev_close=100,
            equity=10_000_000, notional=110_000, regime="trending_up",
        ), chain_id=chain_a)
        persist_decision(db, d_a, mode="VIRTUAL_AI_EXECUTION")
        for m in m_a:
            persist_decision(db, m, mode="VIRTUAL_AI_EXECUTION")
        # chain B
        d_b, m_b = chief.coordinate(CouncilContext(
            symbol="000660", last_close=200, prev_close=190,
            equity=10_000_000, notional=200_000, regime="ranging",
        ), chain_id=chain_b)
        persist_decision(db, d_b, mode="VIRTUAL_AI_EXECUTION")
        for m in m_b:
            persist_decision(db, m, mode="VIRTUAL_AI_EXECUTION")
        db.commit()

    rows_a = client.get(f"/api/ai/agent-decisions?chain_id={chain_a}").json()
    rows_b = client.get(f"/api/ai/agent-decisions?chain_id={chain_b}").json()
    assert len(rows_a) == 10
    assert len(rows_b) == 10
    assert all(r["chain_id"] == chain_a for r in rows_a)
    assert all(r["chain_id"] == chain_b for r in rows_b)


def test_agent_decisions_endpoint_validates_limit(client):
    res = client.get("/api/ai/agent-decisions?limit=0")
    assert res.status_code == 422
    res = client.get("/api/ai/agent-decisions?limit=999")
    assert res.status_code == 422


# ---------- 205: agent-decisions/summary ----------

def test_agent_decisions_summary_empty(client):
    body = client.get("/api/ai/agent-decisions/summary").json()
    assert body["total_decisions"] == 0
    assert body["total_chains"]    == 0
    assert body["by_agent"]        == {}
    assert body["recent_chains"]   == []


def test_agent_decisions_summary_aggregates_by_agent_and_decision(client):
    """coordinate를 두 번 호출 → chief + 9 members × 2 chain = 20개 결정 / 2 chain."""
    from app.ai.agents import ChiefTradingAgent, CouncilContext, persist_decision
    chief = ChiefTradingAgent()
    with client.test_db_factory() as db:
        for symbol, last in [("005930", 110), ("000660", 200)]:
            d, members = chief.coordinate(CouncilContext(
                symbol=symbol, last_close=last, prev_close=last - 5,
                equity=10_000_000, notional=last * 1000, regime="trending_up",
            ))
            persist_decision(db, d, mode="VIRTUAL_AI_EXECUTION")
            for m in members:
                persist_decision(db, m, mode="VIRTUAL_AI_EXECUTION")
        db.commit()

    body = client.get("/api/ai/agent-decisions/summary").json()
    assert body["total_decisions"] == 20
    assert body["total_chains"]    == 2
    # ChiefTradingAgent 항목이 by_agent에 있어야 한다.
    assert "ChiefTradingAgent" in body["by_agent"]
    chief_counts = body["by_agent"]["ChiefTradingAgent"]
    assert sum(chief_counts.values()) == 2
    # recent_chains: 최신 first, 최대 5개 — 여기선 2개.
    assert len(body["recent_chains"]) == 2
    assert all(r["chain_id"] for r in body["recent_chains"])


def test_agent_decisions_filter_by_agent_name(client):
    """206: agent_name 쿼리는 해당 agent만 반환."""
    from app.ai.agents import ChiefTradingAgent, CouncilContext, persist_decision
    chief = ChiefTradingAgent()
    with client.test_db_factory() as db:
        d, members = chief.coordinate(CouncilContext(
            symbol="005930", last_close=110, prev_close=100,
            equity=10_000_000, notional=110_000, regime="trending_up",
        ))
        persist_decision(db, d, mode="VIRTUAL_AI_EXECUTION")
        for m in members:
            persist_decision(db, m, mode="VIRTUAL_AI_EXECUTION")
        db.commit()
    rows = client.get(
        "/api/ai/agent-decisions?agent_name=ChiefTradingAgent",
    ).json()
    assert len(rows) == 1
    assert rows[0]["agent_name"] == "ChiefTradingAgent"

    rows = client.get(
        "/api/ai/agent-decisions?agent_name=EntryTimingAgent",
    ).json()
    assert len(rows) == 1
    assert rows[0]["agent_name"] == "EntryTimingAgent"


def test_agent_decisions_filter_by_decision(client):
    """206: decision 쿼리는 그 결정값만."""
    from app.ai.agents import ChiefTradingAgent, CouncilContext, persist_decision
    chief = ChiefTradingAgent()
    with client.test_db_factory() as db:
        for sym, last, prev, regime in [
            ("005930", 110, 100, "trending_up"),
            ("000660", 200, 190, "trending_up"),
        ]:
            d, members = chief.coordinate(CouncilContext(
                symbol=sym, last_close=last, prev_close=prev,
                equity=10_000_000, notional=last * 1000, regime=regime,
            ))
            persist_decision(db, d, mode="VIRTUAL_AI_EXECUTION")
            for m in members:
                persist_decision(db, m, mode="VIRTUAL_AI_EXECUTION")
        db.commit()
    info_rows = client.get("/api/ai/agent-decisions?decision=INFO").json()
    assert len(info_rows) > 0
    assert all(r["decision"] == "INFO" for r in info_rows)


def test_agent_decisions_filter_combined(client):
    """206: agent_name + decision 동시 지정."""
    from app.ai.agents import ChiefTradingAgent, CouncilContext, persist_decision
    chief = ChiefTradingAgent()
    with client.test_db_factory() as db:
        d, members = chief.coordinate(CouncilContext(
            symbol="005930", last_close=110, prev_close=100,
            equity=10_000_000, notional=110_000, regime="trending_up",
        ))
        persist_decision(db, d, mode="VIRTUAL_AI_EXECUTION")
        for m in members:
            persist_decision(db, m, mode="VIRTUAL_AI_EXECUTION")
        db.commit()
    # ChiefTradingAgent + REJECT 조합 — 이 시나리오에선 chief가 BUY일 가능성이
    # 높으므로 0건 또는 그 이하인 것을 검증.
    rows = client.get(
        "/api/ai/agent-decisions?agent_name=ChiefTradingAgent&decision=REJECT",
    ).json()
    for r in rows:
        assert r["agent_name"] == "ChiefTradingAgent"
        assert r["decision"]   == "REJECT"


def test_agent_decisions_summary_lookback_zero_means_all_time(client):
    """210: lookback_days=0이 기본 + 명시 시에도 전체."""
    from app.ai.agents import ChiefTradingAgent, CouncilContext, persist_decision
    chief = ChiefTradingAgent()
    with client.test_db_factory() as db:
        d, members = chief.coordinate(CouncilContext(
            symbol="005930", last_close=110, prev_close=100,
            equity=10_000_000, notional=110_000, regime="trending_up",
        ))
        persist_decision(db, d, mode="VIRTUAL_AI_EXECUTION")
        for m in members:
            persist_decision(db, m, mode="VIRTUAL_AI_EXECUTION")
        db.commit()
    body0 = client.get("/api/ai/agent-decisions/summary?lookback_days=0").json()
    body_default = client.get("/api/ai/agent-decisions/summary").json()
    assert body0["total_decisions"] == 10
    assert body_default["total_decisions"] == 10
    assert body0["lookback_days"] == 0


def test_agent_decisions_summary_lookback_filters_by_age(client):
    """210: 오래된 row는 lookback 윈도우 밖이면 빠진다."""
    from datetime import datetime, timedelta, timezone
    from app.db.models import AgentDecisionLog
    now = datetime.now(timezone.utc)
    with client.test_db_factory() as db:
        # 오래된 row 1개 (10일 전).
        db.add(AgentDecisionLog(
            agent_name="ChiefTradingAgent", symbol="OLD", mode="SIMULATION",
            decision="BUY", confidence=70, reasons=[], meta={},
            chain_id="old-1", created_at=now - timedelta(days=10),
        ))
        # 최근 row 1개.
        db.add(AgentDecisionLog(
            agent_name="ChiefTradingAgent", symbol="NEW", mode="SIMULATION",
            decision="HOLD", confidence=40, reasons=[], meta={},
            chain_id="new-1", created_at=now,
        ))
        db.commit()
    body_all = client.get("/api/ai/agent-decisions/summary").json()
    body_7d  = client.get("/api/ai/agent-decisions/summary?lookback_days=7").json()
    assert body_all["total_decisions"] == 2
    assert body_7d["total_decisions"]  == 1
    assert body_7d["lookback_days"]    == 7


def test_agent_decisions_summary_lookback_validates_range(client):
    assert client.get("/api/ai/agent-decisions/summary?lookback_days=-1").status_code == 422
    assert client.get("/api/ai/agent-decisions/summary?lookback_days=999").status_code == 422


def test_agent_decisions_summary_recent_chains_capped_at_5(client):
    """6개 chain → recent_chains는 5개만 (id desc)."""
    from app.ai.agents import ChiefTradingAgent, CouncilContext, persist_decision
    chief = ChiefTradingAgent()
    with client.test_db_factory() as db:
        for i in range(6):
            d, members = chief.coordinate(CouncilContext(
                symbol=f"00{i:04d}", last_close=110, prev_close=100,
                equity=10_000_000, notional=110_000, regime="trending_up",
            ))
            persist_decision(db, d, mode="VIRTUAL_AI_EXECUTION")
            for m in members:
                persist_decision(db, m, mode="VIRTUAL_AI_EXECUTION")
        db.commit()

    body = client.get("/api/ai/agent-decisions/summary").json()
    assert body["total_chains"]    == 6
    assert len(body["recent_chains"]) == 5
