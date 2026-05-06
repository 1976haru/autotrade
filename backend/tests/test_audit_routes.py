from app.db.models import AiAnalysisLog, BacktestRun, OrderAuditLog


# ---------- /api/audit/orders ----------

def test_list_order_audits_empty(client):
    res = client.get("/api/audit/orders")
    assert res.status_code == 200
    assert res.json() == []


def _seed_orders(client, count: int) -> None:
    with client.test_db_factory() as db:
        for i in range(count):
            db.add(OrderAuditLog(
                mode="SIMULATION",
                symbol=f"00593{i}",
                side="BUY",
                quantity=1,
                order_type="MARKET",
                latest_price=75_000,
                decision="APPROVED",
                reasons=[],
            ))
        db.commit()


def test_list_order_audits_returns_recent_first(client):
    _seed_orders(client, 3)
    res = client.get("/api/audit/orders")
    rows = res.json()
    assert len(rows) == 3
    # descending by id → most recent symbol first
    assert rows[0]["symbol"] == "005932"
    assert rows[2]["symbol"] == "005930"


def test_list_order_audits_respects_limit_and_offset(client):
    _seed_orders(client, 5)
    rows = client.get("/api/audit/orders", params={"limit": 2}).json()
    assert len(rows) == 2
    assert rows[0]["symbol"] == "005934"
    rows = client.get("/api/audit/orders", params={"limit": 2, "offset": 2}).json()
    assert len(rows) == 2
    assert rows[0]["symbol"] == "005932"


def test_list_order_audits_invalid_limit_returns_422(client):
    assert client.get("/api/audit/orders", params={"limit": 0}).status_code == 422
    assert client.get("/api/audit/orders", params={"limit": 999}).status_code == 422
    assert client.get("/api/audit/orders", params={"offset": -1}).status_code == 422


def test_order_audit_normalizes_naive_created_at_to_utc(client):
    _seed_orders(client, 1)
    row = client.get("/api/audit/orders").json()[0]
    assert row["created_at"].endswith("+00:00") or row["created_at"].endswith("Z")


# 134: trade_reason column propagated through the audit response.
def test_order_audit_persists_explicit_trade_reason(client):
    """OrderRequest에 trade_reason을 명시하면 audit row + 응답에 surface."""
    res = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
        "trade_reason": "stop_loss",
    })
    assert res.status_code == 200
    row = client.get("/api/audit/orders").json()[0]
    assert row["trade_reason"] == "stop_loss"


def test_order_audit_trade_reason_is_null_when_omitted(client):
    """OrderRequest에 trade_reason 미명시 — audit row에 NULL 그대로."""
    res = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
    })
    assert res.status_code == 200
    row = client.get("/api/audit/orders").json()[0]
    assert row["trade_reason"] is None


# 138: strategy column propagated through the audit response.
def test_order_audit_persists_explicit_strategy(client):
    """OrderRequest.strategy → audit row + 응답에 surface."""
    res = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
        "strategy": "sma_crossover",
    })
    assert res.status_code == 200
    row = client.get("/api/audit/orders").json()[0]
    assert row["strategy"] == "sma_crossover"


def test_order_audit_strategy_is_null_for_manual_orders(client):
    """수동 주문(strategy 미명시) → audit row의 strategy=NULL."""
    res = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
    })
    assert res.status_code == 200
    row = client.get("/api/audit/orders").json()[0]
    assert row["strategy"] is None


# 139: signal_strength + signal_confidence persisted via the audit row.
def test_order_audit_persists_explicit_signal_quality(client):
    res = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
        "signal_strength": 85, "signal_confidence": 70,
    })
    assert res.status_code == 200, res.text
    row = client.get("/api/audit/orders").json()[0]
    assert row["signal_strength"]   == 85
    assert row["signal_confidence"] == 70


def test_order_audit_signal_quality_null_for_manual_orders(client):
    res = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
    })
    assert res.status_code == 200
    row = client.get("/api/audit/orders").json()[0]
    assert row["signal_strength"]   is None
    assert row["signal_confidence"] is None


def test_order_audit_signal_quality_clamped_at_pydantic_layer(client):
    """OrderRequest의 ge=0 le=100 제약 — out-of-range는 422."""
    res = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
        "signal_strength": 999,
    })
    assert res.status_code == 422


# ---------- /api/audit/ai ----------

def test_list_ai_audits_empty(client):
    assert client.get("/api/audit/ai").json() == []


def test_list_ai_audits_returns_logged_call(client):
    with client.test_db_factory() as db:
        db.add(AiAnalysisLog(
            ticker="005930",
            extra="실적",
            active_strats=["ORB"],
            risk_params={"maxDailyLoss": 300_000},
            text="진입 권장",
            model="claude-sonnet-4-6",
            input_tokens=42,
            output_tokens=17,
            score={"total": 71},
        ))
        db.commit()
    rows = client.get("/api/audit/ai").json()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "005930"
    assert rows[0]["score"] == {"total": 71}
    assert rows[0]["model"] == "claude-sonnet-4-6"


def test_list_ai_audits_includes_error_field_when_set(client):
    with client.test_db_factory() as db:
        db.add(AiAnalysisLog(
            ticker="005930", extra="", active_strats=[], risk_params={},
            error="ANTHROPIC_API_KEY is not set",
        ))
        db.commit()
    row = client.get("/api/audit/ai").json()[0]
    assert row["text"] is None
    assert row["error"] == "ANTHROPIC_API_KEY is not set"
    assert row["score"] is None


# 123: mode column propagated through the audit response.
def test_list_ai_audits_includes_mode_when_set(client):
    with client.test_db_factory() as db:
        db.add(AiAnalysisLog(
            ticker="005930", extra="", active_strats=[], risk_params={},
            mode="LIVE_AI_ASSIST",
        ))
        db.commit()
    row = client.get("/api/audit/ai").json()[0]
    assert row["mode"] == "LIVE_AI_ASSIST"


def test_list_ai_audits_mode_is_null_for_pre_0004_rows(client):
    """0004 마이그레이션 이전에 만들어진 row는 mode를 모른다 — NULL이 그대로
    응답에 surface되어 FE의 ModeBadge가 미렌더 결정을 내린다."""
    with client.test_db_factory() as db:
        db.add(AiAnalysisLog(
            ticker="005930", extra="", active_strats=[], risk_params={},
            # mode= 명시 안 함 → NULL
        ))
        db.commit()
    row = client.get("/api/audit/ai").json()[0]
    assert row["mode"] is None


# ---------- /api/audit/backtests ----------

def test_list_backtest_runs_empty(client):
    assert client.get("/api/audit/backtests").json() == []


def test_list_backtest_runs_returns_summary_without_trades_payload(client):
    with client.test_db_factory() as db:
        db.add(BacktestRun(
            strategy="sma_crossover",
            params={"short": 5, "long": 20},
            initial_cash=1_000_000,
            quantity=10,
            bars_processed=30,
            final_cash=1_005_000,
            total_pnl=5_000,
            win_count=2,
            loss_count=1,
            max_drawdown=2_000,
            data_source="market",
            data_symbol="005930",
            data_interval="1d",
            trades_json=[{"symbol": "005930", "pnl": 5000}],
        ))
        db.commit()
    rows = client.get("/api/audit/backtests").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["strategy"]    == "sma_crossover"
    assert row["data_source"] == "market"
    assert row["data_symbol"] == "005930"
    assert row["total_pnl"]   == 5_000
    # Summary route deliberately omits trades_json / data_start / data_end
    assert "trades_json" not in row
    assert "data_start" not in row
