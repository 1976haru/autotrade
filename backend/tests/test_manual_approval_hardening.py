"""Manual Approval 보강 (#41) — TTL / 재검증 표시 / source 분류 테스트.

체크리스트 #41은 기존 PendingApproval / PermissionGate / approve-reject-cancel
구조를 *유지*하면서 다음을 보강:
- approval TTL 정책 (settings.approval_ttl_seconds)
- ApprovalOut에 expires_at / seconds_until_expiry / is_expired
- attempts 요약 (count / last_at / last_reasons)
- request_source 분류 (AI / STRATEGY / MANUAL / LIQUIDATION / RISK_OVERRIDE)
"""

from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.db.models import OrderAuditLog, PendingApproval


def _seed_pending(client, *, audit_id=None, symbol="005930", side="BUY",
                   created_seconds_ago=0, attempts=None, audit_extras=None,
                   audit_decision="NEEDS_APPROVAL"):
    """audit + pending row 시드. 테스트마다 db state 분리."""
    audit_extras = audit_extras or {}
    with client.test_db_factory() as db:
        if audit_id is None:
            audit = OrderAuditLog(
                mode="SIMULATION", symbol=symbol, side=side, quantity=1,
                order_type="MARKET", latest_price=100,
                decision=audit_decision, reasons=["manual approval required"],
                **audit_extras,
            )
            db.add(audit)
            db.commit()
            db.refresh(audit)
            audit_id = audit.id
        ts = datetime.now(timezone.utc) - timedelta(seconds=created_seconds_ago)
        approval = PendingApproval(
            audit_id=audit_id, symbol=symbol, side=side, quantity=1,
            order_type="MARKET", limit_price=None, mode="SIMULATION",
            status="PENDING", created_at=ts, attempts=attempts or [],
        )
        db.add(approval)
        db.commit()
        db.refresh(approval)
        return approval.id


# ====================================================================
# TTL behavior
# ====================================================================


class TestApprovalTtl:
    def test_ttl_disabled_yields_no_expires_at(self, client):
        """settings.approval_ttl_seconds=0(default) → expires_at=None, is_expired=False."""
        _seed_pending(client)
        body = client.get("/api/approvals").json()
        assert len(body) == 1
        assert body[0]["expires_at"] is None
        assert body[0]["seconds_until_expiry"] is None
        assert body[0]["is_expired"] is False

    def test_ttl_enabled_populates_fields(self, client, monkeypatch):
        """settings.approval_ttl_seconds=600 → expires_at + seconds_until_expiry 채워짐."""
        s = get_settings()
        monkeypatch.setattr(s, "approval_ttl_seconds", 600)
        _seed_pending(client, created_seconds_ago=10)
        body = client.get("/api/approvals").json()
        assert len(body) == 1
        assert body[0]["expires_at"] is not None
        assert body[0]["seconds_until_expiry"] is not None
        assert body[0]["seconds_until_expiry"] > 0
        assert body[0]["seconds_until_expiry"] <= 600
        assert body[0]["is_expired"] is False

    def test_ttl_lazy_expires_stale_pending(self, client, monkeypatch):
        """ttl 초과 row는 list_pending에서 제외 + status=EXPIRED로 전환."""
        s = get_settings()
        monkeypatch.setattr(s, "approval_ttl_seconds", 60)  # 60초 ttl
        _seed_pending(client, created_seconds_ago=120)  # 2분 경과 → 만료
        body = client.get("/api/approvals").json()
        # pending에서 제외 (lazy expired)
        assert body == []
        # history?status=EXPIRED에서 surface
        history = client.get("/api/approvals/history?status=EXPIRED").json()
        assert len(history) == 1
        assert history[0]["status"] == "EXPIRED"

    def test_ttl_zero_keeps_old_approvals_pending(self, client, monkeypatch):
        s = get_settings()
        monkeypatch.setattr(s, "approval_ttl_seconds", 0)
        _seed_pending(client, created_seconds_ago=999_999)  # 매우 오래됨
        body = client.get("/api/approvals").json()
        # ttl=0이면 stale도 PENDING 그대로
        assert len(body) == 1
        assert body[0]["status"] == "PENDING"
        assert body[0]["expires_at"] is None


# ====================================================================
# Attempts summary surfacing
# ====================================================================


class TestAttemptsSummary:
    def test_no_attempts_yields_zero_count(self, client):
        _seed_pending(client)
        body = client.get("/api/approvals").json()
        assert body[0]["attempt_count"] == 0
        assert body[0]["last_attempt_at"] is None
        assert body[0]["last_attempt_reasons"] == []

    def test_attempts_array_summarized(self, client):
        ts1 = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        ts2 = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        _seed_pending(client, attempts=[
            {"at": ts1, "decided_by": "ops1", "reasons": ["stale price"]},
            {"at": ts2, "decided_by": "ops1",
             "reasons": ["max_order_notional 초과", "긴급정지"]},
        ])
        body = client.get("/api/approvals").json()
        assert body[0]["attempt_count"] == 2
        assert body[0]["last_attempt_reasons"] == \
               ["max_order_notional 초과", "긴급정지"]
        assert body[0]["last_attempt_at"] is not None


# ====================================================================
# request_source 분류
# ====================================================================


class TestRequestSource:
    def test_source_from_audit_source_column(self, client):
        """OrderAuditLog.source(#40)가 채워져 있으면 그대로 매핑."""
        _seed_pending(client, audit_extras={
            "source": "AI", "requested_by_ai": True,
            "ai_decision_meta": {"reasons": ["AI agreed"], "confidence": 80},
        })
        body = client.get("/api/approvals").json()
        assert body[0]["request_source"] == "AI"
        assert body[0]["request_source_label"] == "AI 제안"
        assert body[0]["requested_by_ai"] is True

    def test_strategy_yields_strategy_source(self, client):
        _seed_pending(client, audit_extras={
            "source": "STRATEGY", "strategy": "vwap",
        })
        body = client.get("/api/approvals").json()
        assert body[0]["request_source"] == "STRATEGY"
        assert body[0]["request_source_label"] == "전략 신호"
        assert body[0]["strategy"] == "vwap"

    def test_manual_default(self, client):
        _seed_pending(client)  # no source/strategy/ai
        body = client.get("/api/approvals").json()
        assert body[0]["request_source"] == "MANUAL"
        assert body[0]["request_source_label"] == "수동 주문"

    def test_operator_override_maps_to_risk_override(self, client):
        _seed_pending(client, audit_extras={"source": "OPERATOR_OVERRIDE"})
        body = client.get("/api/approvals").json()
        assert body[0]["request_source"] == "RISK_OVERRIDE"
        assert body[0]["request_source_label"] == "리스크 예외 요청"

    def test_liquidation_inferred_from_trade_reason(self, client):
        """source 컬럼 NULL + trade_reason에 'liquidation' → LIQUIDATION 분류."""
        _seed_pending(client, audit_extras={
            "trade_reason": "liquidation", "source": None,
        })
        body = client.get("/api/approvals").json()
        assert body[0]["request_source"] == "LIQUIDATION"
        assert body[0]["request_source_label"] == "청산 후보"

    def test_legacy_unknown_source_falls_back(self, client):
        """audit row에 source NULL이고 다른 단서도 없으면 MANUAL."""
        _seed_pending(client, audit_extras={"source": None})
        body = client.get("/api/approvals").json()
        # 없으면 MANUAL fallback (source/strategy/ai/trade_reason 모두 None)
        assert body[0]["request_source"] in ("MANUAL", "UNKNOWN")


# ====================================================================
# Backwards compat — 기존 ApprovalOut 필드 그대로
# ====================================================================


class TestBackwardsCompat:
    def test_existing_fields_still_present(self, client):
        """체크리스트 #41 변경이 기존 필드를 깨지 않음 — strict shape 확인."""
        _seed_pending(client)
        body = client.get("/api/approvals").json()
        keys = set(body[0].keys())
        # 기존 필드 (190 / 076 / 167 등)
        for required in [
            "id", "created_at", "audit_id", "symbol", "side", "quantity",
            "order_type", "limit_price", "mode", "status", "decided_at",
            "decided_by", "note", "reasons", "attempts",
            "requested_by_ai", "strategy", "signal_strength",
            "signal_confidence", "ai_decision_meta",
        ]:
            assert required in keys, f"missing field: {required}"
        # 신규 필드 (#41)
        for new in [
            "expires_at", "seconds_until_expiry", "is_expired",
            "attempt_count", "last_attempt_at", "last_attempt_reasons",
            "request_source", "request_source_label",
        ]:
            assert new in keys, f"missing new field: {new}"


# ====================================================================
# History — EXPIRED 포함
# ====================================================================


class TestHistoryFilter:
    def test_history_includes_expired_rows(self, client, monkeypatch):
        s = get_settings()
        monkeypatch.setattr(s, "approval_ttl_seconds", 60)
        _seed_pending(client, created_seconds_ago=200)  # stale → 만료될 row
        # list_pending lazy expire
        client.get("/api/approvals")
        # status=EXPIRED 필터로 history 조회
        body = client.get("/api/approvals/history?status=EXPIRED").json()
        assert len(body) == 1
        assert body[0]["status"] == "EXPIRED"

    def test_history_status_filter_validates(self, client):
        # PENDING은 history에서 거부
        res = client.get("/api/approvals/history?status=PENDING")
        # FastAPI Literal 검증으로 422.
        assert res.status_code in (400, 422)


# ====================================================================
# Single approval endpoint
# ====================================================================


class TestSingleApproval:
    def test_get_returns_extended_fields(self, client, monkeypatch):
        s = get_settings()
        monkeypatch.setattr(s, "approval_ttl_seconds", 600)
        approval_id = _seed_pending(client, audit_extras={
            "source": "AI", "requested_by_ai": True,
        })
        body = client.get(f"/api/approvals/{approval_id}").json()
        assert body["id"] == approval_id
        assert body["expires_at"] is not None
        assert body["request_source"] == "AI"

    def test_get_404_for_unknown(self, client):
        res = client.get("/api/approvals/99999")
        assert res.status_code == 404
