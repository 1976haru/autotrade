"""3-level Kill Switch 테스트 (#37).

체크리스트 #37: emergency_stop을 OFF / LEVEL_1 / LEVEL_2 / LEVEL_3 단계로 분리.
- LEVEL_1: 신규 BUY 차단.
- LEVEL_2: + 미체결 취소 후보 표시.
- LEVEL_3: + 보유 포지션 청산 후보 표시.

본 테스트가 가드:
1. POST /risk/emergency-stop이 level 인자 수용.
2. 기존 enabled-only API 호환성 유지.
3. status / cancel-candidates / liquidation-candidates endpoint read-only 동작.
4. 자동 cancel_order / place_order / 자동 청산 호출 0건 (코드 단 invariant).
"""

from app.brokers.base import Position
from app.db.models import EmergencyStopEvent, OrderAuditLog, PendingApproval
from app.risk.emergency_stop import (
    KillSwitchLevel,
    apply_kill_switch_to_risk,
    build_status,
    compute_cancel_candidates,
    compute_liquidation_candidates,
    normalize_legacy_level,
    normalize_level,
)


# ====================================================================
# Module — pure helpers
# ====================================================================


class TestKillSwitchLevelEnum:
    def test_values(self):
        assert KillSwitchLevel.OFF.value == "OFF"
        assert KillSwitchLevel.LEVEL_1.value == "LEVEL_1"
        assert KillSwitchLevel.LEVEL_2.value == "LEVEL_2"
        assert KillSwitchLevel.LEVEL_3.value == "LEVEL_3"


class TestNormalizeLevel:
    def test_none_or_empty_yields_off(self):
        assert normalize_level(None) == KillSwitchLevel.OFF
        assert normalize_level("") == KillSwitchLevel.OFF

    def test_valid_strings_round_trip(self):
        assert normalize_level("LEVEL_1") == KillSwitchLevel.LEVEL_1
        assert normalize_level("LEVEL_3") == KillSwitchLevel.LEVEL_3

    def test_invalid_string_falls_back_off(self):
        assert normalize_level("MYSTERY") == KillSwitchLevel.OFF


class TestNormalizeLegacyLevel:
    def test_legacy_enabled_true_yields_level_1(self):
        """level NULL + enabled=True (legacy row) → LEVEL_1."""
        assert normalize_legacy_level(None, enabled=True) == KillSwitchLevel.LEVEL_1

    def test_legacy_enabled_false_yields_off(self):
        assert normalize_legacy_level(None, enabled=False) == KillSwitchLevel.OFF

    def test_explicit_level_overrides_enabled(self):
        # explicit level이 우선
        assert normalize_legacy_level("LEVEL_2", enabled=True) == KillSwitchLevel.LEVEL_2
        assert normalize_legacy_level("LEVEL_3", enabled=True) == KillSwitchLevel.LEVEL_3


class TestApplyKillSwitchToRisk:
    def test_off_clears_emergency_stop(self):
        class _Risk:
            emergency_stop = True
            kill_switch_level = KillSwitchLevel.LEVEL_2
        r = _Risk()
        apply_kill_switch_to_risk(r, KillSwitchLevel.OFF)
        assert r.emergency_stop is False
        assert r.kill_switch_level == KillSwitchLevel.OFF

    def test_level_1_sets_emergency_stop_true(self):
        class _Risk:
            emergency_stop = False
            kill_switch_level = KillSwitchLevel.OFF
        r = _Risk()
        apply_kill_switch_to_risk(r, KillSwitchLevel.LEVEL_1)
        assert r.emergency_stop is True
        assert r.kill_switch_level == KillSwitchLevel.LEVEL_1

    def test_level_3_also_sets_emergency_stop_true(self):
        class _Risk:
            emergency_stop = False
            kill_switch_level = KillSwitchLevel.OFF
        r = _Risk()
        apply_kill_switch_to_risk(r, KillSwitchLevel.LEVEL_3)
        assert r.emergency_stop is True
        assert r.kill_switch_level == KillSwitchLevel.LEVEL_3


# ====================================================================
# Candidates — DB / position helpers
# ====================================================================


class TestComputeCancelCandidates:
    def test_empty_db_returns_empty(self, client):
        with client.test_db_factory() as db:
            assert compute_cancel_candidates(db) == []

    def test_pending_approval_surfaced(self, client):
        with client.test_db_factory() as db:
            audit = OrderAuditLog(
                mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
                order_type="MARKET", latest_price=75_000,
                decision="NEEDS_APPROVAL", reasons=["manual"],
            )
            db.add(audit)
            db.commit()
            db.refresh(audit)
            pa = PendingApproval(
                audit_id=audit.id, symbol="005930", side="BUY", quantity=1,
                order_type="MARKET", limit_price=None, mode="SIMULATION",
                status="PENDING",
            )
            db.add(pa)
            db.commit()
            cands = compute_cancel_candidates(db)
        assert len(cands) == 1
        assert cands[0].source == "pending_approval"
        assert cands[0].symbol == "005930"
        assert cands[0].decision == "NEEDS_APPROVAL"

    def test_orphan_audit_needs_approval_surfaced(self, client):
        """PendingApproval이 없는데 OrderAuditLog NEEDS_APPROVAL row가 있는
        drift 케이스도 후보에 포함."""
        with client.test_db_factory() as db:
            db.add(OrderAuditLog(
                mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
                order_type="MARKET", latest_price=75_000,
                decision="NEEDS_APPROVAL", reasons=[],
            ))
            db.commit()
            cands = compute_cancel_candidates(db)
        assert len(cands) == 1
        assert cands[0].source == "audit_needs_approval"
        assert cands[0].approval_id is None

    def test_decided_pending_excluded(self, client):
        """status != PENDING (APPROVED/REJECTED/EXPIRED)은 후보 X."""
        with client.test_db_factory() as db:
            audit = OrderAuditLog(
                mode="SIMULATION", symbol="X", side="BUY", quantity=1,
                order_type="MARKET", latest_price=1_000,
                decision="APPROVED", reasons=[],
            )
            db.add(audit)
            db.commit()
            db.refresh(audit)
            db.add(PendingApproval(
                audit_id=audit.id, symbol="X", side="BUY", quantity=1,
                order_type="MARKET", limit_price=None, mode="SIMULATION",
                status="APPROVED",
            ))
            db.commit()
            cands = compute_cancel_candidates(db)
        assert cands == []


class TestComputeLiquidationCandidates:
    def test_empty_positions(self):
        assert compute_liquidation_candidates([]) == []

    def test_zero_quantity_excluded(self):
        positions = [Position(symbol="X", quantity=0, avg_price=100, market_price=110)]
        assert compute_liquidation_candidates(positions) == []

    def test_unrealized_pnl_calculation(self):
        positions = [
            Position(symbol="X", quantity=10, avg_price=100, market_price=120),
            Position(symbol="Y", quantity=5,  avg_price=200, market_price=180),
        ]
        cands = compute_liquidation_candidates(positions)
        assert len(cands) == 2
        x = next(c for c in cands if c.symbol == "X")
        y = next(c for c in cands if c.symbol == "Y")
        assert x.unrealized_pnl == 200   # (120 - 100) * 10
        assert y.unrealized_pnl == -100  # (180 - 200) * 5

    def test_to_dict(self):
        positions = [Position(symbol="X", quantity=1, avg_price=100, market_price=110)]
        d = compute_liquidation_candidates(positions)[0].to_dict()
        assert d["symbol"] == "X"
        assert d["unrealized_pnl"] == 10


# ====================================================================
# build_status
# ====================================================================


class TestBuildStatus:
    def test_off_when_risk_not_active(self, client):
        from app.api.deps import get_risk_manager
        risk = get_risk_manager()
        # ensure off
        apply_kill_switch_to_risk(risk, KillSwitchLevel.OFF)
        with client.test_db_factory() as db:
            s = build_status(risk=risk, db=db)
        assert s.level == KillSwitchLevel.OFF
        assert s.emergency_stop is False

    def test_level_synced_with_emergency_stop(self, client):
        from app.api.deps import get_risk_manager
        risk = get_risk_manager()
        apply_kill_switch_to_risk(risk, KillSwitchLevel.LEVEL_3)
        with client.test_db_factory() as db:
            s = build_status(risk=risk, db=db)
        assert s.level == KillSwitchLevel.LEVEL_3
        assert s.emergency_stop is True
        # cleanup so other tests start clean
        apply_kill_switch_to_risk(risk, KillSwitchLevel.OFF)


# ====================================================================
# Routes — POST /emergency-stop with level
# ====================================================================


class TestEmergencyStopRouteWithLevel:
    def test_existing_enabled_only_request_still_works(self, client):
        """기존 호출자(enabled만 보내는)도 LEVEL_1로 자동 매핑."""
        res = client.post("/api/risk/emergency-stop", json={"enabled": True})
        assert res.status_code == 200
        body = res.json()
        assert body["emergency_stop"] is True
        assert body["level"] == "LEVEL_1"
        # cleanup
        client.post("/api/risk/emergency-stop", json={"enabled": False})

    def test_explicit_level_2(self, client):
        res = client.post("/api/risk/emergency-stop", json={
            "enabled": True, "level": "LEVEL_2",
            "reason_code": "data_stale", "decided_by": "ops1",
        })
        assert res.status_code == 200
        body = res.json()
        assert body["emergency_stop"] is True
        assert body["level"] == "LEVEL_2"
        client.post("/api/risk/emergency-stop", json={"enabled": False})

    def test_explicit_level_3(self, client):
        res = client.post("/api/risk/emergency-stop", json={
            "enabled": True, "level": "LEVEL_3",
            "reason_code": "broker_error",
        })
        assert res.status_code == 200
        assert res.json()["level"] == "LEVEL_3"
        client.post("/api/risk/emergency-stop", json={"enabled": False})

    def test_invalid_level_rejected(self, client):
        res = client.post("/api/risk/emergency-stop", json={
            "enabled": True, "level": "MYSTERY_LEVEL",
        })
        assert res.status_code in (400, 422)

    def test_disable_returns_off(self, client):
        client.post("/api/risk/emergency-stop", json={"enabled": True, "level": "LEVEL_3"})
        res = client.post("/api/risk/emergency-stop", json={"enabled": False})
        assert res.status_code == 200
        assert res.json()["emergency_stop"] is False
        assert res.json()["level"] == "OFF"

    def test_history_includes_level(self, client):
        client.post("/api/risk/emergency-stop", json={
            "enabled": True, "level": "LEVEL_2", "decided_by": "ops",
            "reason_code": "broker_error",
        })
        res = client.get("/api/risk/emergency-stop/history")
        assert res.status_code == 200
        rows = res.json()
        assert len(rows) >= 1
        assert rows[0]["level"] == "LEVEL_2"
        assert rows[0]["enabled"] is True
        assert rows[0]["reason_code"] == "broker_error"
        client.post("/api/risk/emergency-stop", json={"enabled": False})

    def test_legacy_history_row_normalized_to_level_1(self, client):
        """level 컬럼 NULL + enabled=True인 legacy row는 LEVEL_1로 정규화."""
        with client.test_db_factory() as db:
            db.add(EmergencyStopEvent(
                enabled=True, decided_by="legacy_op", reason_code=None,
                # level 미지정 — NULL
            ))
            db.commit()
        res = client.get("/api/risk/emergency-stop/history")
        rows = res.json()
        # 가장 최근 row는 위에서 추가한 legacy row.
        legacy_row = next(r for r in rows if r["decided_by"] == "legacy_op")
        assert legacy_row["level"] == "LEVEL_1"


# ====================================================================
# /status endpoint
# ====================================================================


class TestStatusEndpoint:
    def test_status_off_initially(self, client):
        res = client.get("/api/risk/emergency-stop/status")
        assert res.status_code == 200
        body = res.json()
        assert body["level"] in ("OFF", "LEVEL_1", "LEVEL_2", "LEVEL_3")
        assert isinstance(body["cancel_candidate_count"], int)
        assert body["liquidation_candidate_count"] == 0  # broker 호출 회피

    def test_status_after_level_2_toggle(self, client):
        client.post("/api/risk/emergency-stop", json={
            "enabled": True, "level": "LEVEL_2",
            "decided_by": "ops", "reason_code": "data_stale",
        })
        body = client.get("/api/risk/emergency-stop/status").json()
        assert body["level"] == "LEVEL_2"
        assert body["emergency_stop"] is True
        assert body["reason_code"] == "data_stale"
        assert body["decided_by"] == "ops"
        client.post("/api/risk/emergency-stop", json={"enabled": False})


# ====================================================================
# /cancel-candidates endpoint
# ====================================================================


class TestCancelCandidatesEndpoint:
    def test_empty_returns_empty_list(self, client):
        res = client.get("/api/risk/emergency-stop/cancel-candidates")
        assert res.status_code == 200
        body = res.json()
        assert body["candidates"] == []
        assert body["count"] == 0
        assert "수동 승인" in body["note"]

    def test_pending_approval_surfaced(self, client):
        with client.test_db_factory() as db:
            audit = OrderAuditLog(
                mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
                order_type="MARKET", latest_price=75_000,
                decision="NEEDS_APPROVAL", reasons=[],
            )
            db.add(audit)
            db.commit()
            db.refresh(audit)
            db.add(PendingApproval(
                audit_id=audit.id, symbol="005930", side="BUY", quantity=1,
                order_type="MARKET", limit_price=None, mode="SIMULATION",
                status="PENDING",
            ))
            db.commit()
        body = client.get("/api/risk/emergency-stop/cancel-candidates").json()
        assert body["count"] == 1
        cand = body["candidates"][0]
        assert cand["symbol"] == "005930"
        assert cand["source"] == "pending_approval"


# ====================================================================
# /liquidation-candidates endpoint
# ====================================================================


class TestLiquidationCandidatesEndpoint:
    def test_endpoint_responds_with_candidates_shape(self, client):
        # MockBroker는 default position이 없을 수도 있음 — shape만 검증.
        res = client.get("/api/risk/emergency-stop/liquidation-candidates")
        assert res.status_code == 200
        body = res.json()
        assert "candidates" in body
        assert "count" in body
        assert "total_unrealized_pnl" in body
        assert "자동 청산은 비활성화" in body["note"]
        assert isinstance(body["candidates"], list)


# ====================================================================
# Safety — 자동 cancel/place/청산 호출 0건 (코드 단 invariant)
# ====================================================================


class TestSafety:
    def test_module_does_not_call_cancel_or_place_order(self):
        """app/risk/emergency_stop.py는 broker.cancel_order / broker.place_order
        / route_order를 호출하지 않는다 — 자동 취소 / 자동 청산을 절대 만들지
        않기 위함."""
        import inspect

        from app.risk import emergency_stop as mod
        src = inspect.getsource(mod)
        forbidden = (
            "broker.cancel_order(", "broker.place_order(",
            ".cancel_order(", ".place_order(",
            "route_order(",
        )
        for f in forbidden:
            assert f not in src, f"forbidden symbol in emergency_stop module: {f}"

    def test_routes_does_not_call_cancel_or_place_order_directly(self):
        """routes_risk.py도 emergency-stop endpoint 흐름에서 cancel/place 직접
        호출 0건. broker.get_positions만 read-only로 사용 가능."""
        import inspect

        from app.api import routes_risk as mod
        src = inspect.getsource(mod)
        forbidden = (
            "broker.cancel_order(", "broker.place_order(",
            "route_order(",
        )
        for f in forbidden:
            assert f not in src, f"forbidden symbol in routes_risk: {f}"

    def test_existing_emergency_stop_hard_reject_still_works(self, client):
        """LEVEL_1 활성화 후 RiskManager.evaluate_order이 여전히 모든 주문 거부 —
        기존 hard-stop 동작 보존."""
        from app.api.deps import get_risk_manager
        from app.brokers.base import Balance, OrderRequest, OrderSide
        from app.core.modes import OperationMode
        risk = get_risk_manager()
        apply_kill_switch_to_risk(risk, KillSwitchLevel.LEVEL_1)
        try:
            result = risk.evaluate_order(
                order=OrderRequest(symbol="X", side=OrderSide.BUY, quantity=1),
                mode=OperationMode.SIMULATION,
                balance=Balance(cash=10_000_000, equity=10_000_000, buying_power=10_000_000),
                positions=[], latest_price=1_000,
            )
            assert result.decision.value == "REJECTED"
            assert "emergency stop is enabled" in result.reasons
        finally:
            apply_kill_switch_to_risk(risk, KillSwitchLevel.OFF)
