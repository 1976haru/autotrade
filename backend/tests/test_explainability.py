"""Signal Explainability 단위 테스트 (#33).

SignalReason 모델 + helpers (compose / summarize / classify / require) +
extract_reasons_from_audit_row + /api/signals/{id}/explain endpoint.

본 모듈은 *주문을 만들지 않는다* — broker / RiskManager / PermissionGate /
OrderExecutor / route_order 어떤 함수도 호출하지 않는다 (테스트 가드).
"""

import pytest

from app.db.models import OrderAuditLog
from app.explainability import (
    ExplainStatus,
    MissingExplanationError,
    ReasonCategory,
    ReasonSeverity,
    ReasonStatus,
    SignalExplanation,
    SignalReason,
    classify_final_status,
    compose_signal_explanation,
    extract_reasons_from_audit_row,
    require_explanation_before_order,
    summarize_reasons,
)


# ====================================================================
# SignalReason / SignalExplanation
# ====================================================================


class TestSignalReason:
    def test_to_dict_round_trip(self):
        r = SignalReason(
            category=ReasonCategory.STRATEGY,
            status=ReasonStatus.PASS,
            severity=ReasonSeverity.MEDIUM,
            source="strategy:VolumeBreakout",
            code="VOLUME_OK",
            message="거래대금이 평균 대비 충분히 증가했습니다.",
            details={"volume_multiplier": 2.5},
        )
        d = r.to_dict()
        assert d["category"] == "STRATEGY"
        assert d["status"] == "PASS"
        assert d["severity"] == "MEDIUM"
        assert d["source"] == "strategy:VolumeBreakout"
        assert d["code"] == "VOLUME_OK"
        assert d["details"] == {"volume_multiplier": 2.5}

    def test_signal_reason_default_severity(self):
        r = SignalReason(
            category=ReasonCategory.OTHER, status=ReasonStatus.INFO, message="x",
        )
        assert r.severity == ReasonSeverity.MEDIUM


class TestSignalExplanationGrouping:
    def test_grouped_by_status(self):
        reasons = [
            SignalReason(category=ReasonCategory.STRATEGY, status=ReasonStatus.PASS,    message="a"),
            SignalReason(category=ReasonCategory.STRATEGY, status=ReasonStatus.WARN,    message="b"),
            SignalReason(category=ReasonCategory.RISK_MANAGER, status=ReasonStatus.FAIL, message="c"),
            SignalReason(category=ReasonCategory.PERMISSION_GATE, status=ReasonStatus.BLOCKED, message="d"),
            SignalReason(category=ReasonCategory.AGENT, status=ReasonStatus.INFO, message="e"),
        ]
        expl = SignalExplanation(reasons=reasons, final_status=ExplainStatus.REJECTED, summary="x")
        grouped = expl.grouped_by_status()
        assert len(grouped["PASS"]) == 1
        assert len(grouped["WARN"]) == 1
        assert len(grouped["FAIL"]) == 1
        assert len(grouped["BLOCKED"]) == 1
        assert len(grouped["INFO"]) == 1


# ====================================================================
# summarize_reasons
# ====================================================================


class TestSummarize:
    def test_empty_returns_placeholder(self):
        assert summarize_reasons([]) == "(설명 없음)"

    def test_orders_blocked_first_then_fail_warn_pass(self):
        rs = [
            SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.PASS, message="A"),
            SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.BLOCKED, message="B"),
            SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.WARN, message="C"),
        ]
        s = summarize_reasons(rs, max_items=3)
        # B (BLOCKED) 우선
        assert s.startswith("B")

    def test_severity_breaks_tie(self):
        rs = [
            SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.FAIL,
                          severity=ReasonSeverity.LOW, message="low"),
            SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.FAIL,
                          severity=ReasonSeverity.HIGH, message="high"),
        ]
        s = summarize_reasons(rs, max_items=2)
        # high가 앞에
        assert s.index("high") < s.index("low")


# ====================================================================
# classify_final_status
# ====================================================================


class TestClassifyFinalStatus:
    def test_empty_returns_unknown(self):
        assert classify_final_status([]) == ExplainStatus.UNKNOWN

    def test_blocked_yields_rejected(self):
        rs = [SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.BLOCKED, message="x")]
        assert classify_final_status(rs) == ExplainStatus.REJECTED

    def test_fail_yields_rejected(self):
        rs = [SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.FAIL, message="x")]
        assert classify_final_status(rs) == ExplainStatus.REJECTED

    def test_warn_only_yields_watch(self):
        rs = [SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.WARN, message="x")]
        assert classify_final_status(rs) == ExplainStatus.WATCH

    def test_pass_only_yields_approved(self):
        rs = [SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.PASS, message="x")]
        assert classify_final_status(rs) == ExplainStatus.APPROVED

    def test_pass_plus_warn_yields_watch(self):
        rs = [
            SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.PASS, message="x"),
            SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.WARN, message="y"),
        ]
        assert classify_final_status(rs) == ExplainStatus.WATCH

    def test_permission_status_overrides(self):
        rs = [SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.PASS, message="x")]
        # PermissionGate가 PENDING이면 explanation은 PENDING.
        class _PG:
            status = "PENDING"
        assert classify_final_status(rs, permission_result=_PG()) == ExplainStatus.PENDING

    def test_risk_reject_overrides_pass(self):
        rs = [SignalReason(category=ReasonCategory.OTHER, status=ReasonStatus.PASS, message="x")]
        class _RM:
            decision = "REJECT"
        assert classify_final_status(rs, risk_result=_RM()) == ExplainStatus.REJECTED


# ====================================================================
# require_explanation_before_order — '설명 없는 주문 금지' 정책
# ====================================================================


class TestRequireExplanation:
    def test_none_explanation_raises(self):
        with pytest.raises(MissingExplanationError):
            require_explanation_before_order(None)

    def test_empty_reasons_raises(self):
        expl = SignalExplanation(reasons=[], final_status=ExplainStatus.UNKNOWN, summary="")
        with pytest.raises(MissingExplanationError):
            require_explanation_before_order(expl)

    def test_with_reasons_returns_true(self):
        expl = SignalExplanation(
            reasons=[SignalReason(category=ReasonCategory.STRATEGY,
                                    status=ReasonStatus.PASS, message="x")],
            final_status=ExplainStatus.APPROVED, summary="x",
        )
        assert require_explanation_before_order(expl) is True

    def test_raise_on_empty_false_returns_bool(self):
        assert require_explanation_before_order(None, raise_on_empty=False) is False
        expl = SignalExplanation(reasons=[], final_status=ExplainStatus.UNKNOWN, summary="")
        assert require_explanation_before_order(expl, raise_on_empty=False) is False


# ====================================================================
# compose_signal_explanation
# ====================================================================


class TestComposeExplanation:
    def test_compose_from_strategy_signal_only(self):
        """StrategySignal-like 입력 — explanation.reasons / indicators 추출."""
        from app.strategies.base import (
            SignalAction,
            SignalExplanation as StrategySigExpl,
            StrategySignal,
        )
        sig = StrategySignal(
            action=SignalAction.BUY, symbol="X",
            explanation=StrategySigExpl(
                summary="strategy → BUY",
                reasons=["거래대금 증가", "VWAP 상단 정렬"],
                confidence=80,
                indicators={"volume_multiplier": 2.5},
            ),
        )
        expl = compose_signal_explanation(signal=sig, strategy="VolumeBreakout")
        assert expl.symbol == "X"
        assert expl.strategy == "VolumeBreakout"
        assert expl.action == "BUY"
        assert len(expl.reasons) == 2
        for r in expl.reasons:
            assert r.category == ReasonCategory.STRATEGY
        assert expl.indicators == {"volume_multiplier": 2.5}

    def test_compose_from_regime_decision_block(self):
        """MarketRegime BLOCK_NEW_BUY → BLOCKED status 자동 매핑."""
        from app.filters.market_regime import MarketRegimeFilter, MarketRegime

        f = MarketRegimeFilter()
        rg = f.evaluate([], regime_override=MarketRegime.RISK_OFF)
        expl = compose_signal_explanation(regime_decision=rg)
        assert any(r.category == ReasonCategory.MARKET_REGIME for r in expl.reasons)
        # BLOCK_NEW_BUY → BLOCKED → final REJECTED
        assert expl.final_status == ExplainStatus.REJECTED

    def test_compose_from_regime_decision_allow(self):
        from app.filters.market_regime import MarketRegimeFilter, MarketRegime

        f = MarketRegimeFilter()
        rg = f.evaluate([], regime_override=MarketRegime.TREND_UP)
        expl = compose_signal_explanation(regime_decision=rg)
        # ALLOW이면 PASS 또는 INFO (regime reasons는 단순 "classify_regime → trending..."
        # 형태일 수 있음). 핵심은 BLOCKED/FAIL이 없어야 한다.
        for r in expl.reasons:
            assert r.status not in (ReasonStatus.BLOCKED, ReasonStatus.FAIL)

    def test_compose_with_operator_note(self):
        expl = compose_signal_explanation(operator_note="운영자 명시 보류")
        assert expl.operator_note == "운영자 명시 보류"
        assert any(r.category == ReasonCategory.OPERATOR for r in expl.reasons)

    def test_compose_from_risk_result_reject(self):
        class _RM:
            decision = "REJECT"
            reasons = ["max_order_notional 초과", "max_positions 도달"]
        expl = compose_signal_explanation(risk_result=_RM())
        assert expl.final_status == ExplainStatus.REJECTED
        rm_reasons = [r for r in expl.reasons if r.category == ReasonCategory.RISK_MANAGER]
        assert len(rm_reasons) == 2
        # 모두 BLOCKED status (default for REJECT)
        assert all(r.status == ReasonStatus.BLOCKED for r in rm_reasons)

    def test_compose_from_permission_pending(self):
        class _PG:
            status = "PENDING"
            reasons = ["사용자 승인 필요"]
        expl = compose_signal_explanation(permission_result=_PG())
        assert expl.final_status == ExplainStatus.PENDING

    def test_compose_from_agent_decision(self):
        class _AG:
            decision = "REJECT"
            reasons = ["AI confidence 낮음"]
        expl = compose_signal_explanation(agent_decision=_AG())
        agent_reasons = [r for r in expl.reasons if r.category == ReasonCategory.AGENT]
        assert len(agent_reasons) >= 1
        assert agent_reasons[0].status == ReasonStatus.BLOCKED

    def test_compose_full_chain(self):
        """전 단계 입력 — 통합 합성."""
        from app.filters.market_regime import MarketRegimeFilter, MarketRegime
        from app.strategies.base import (
            SignalAction,
            SignalExplanation as StrategySigExpl,
            StrategySignal,
        )

        sig = StrategySignal(
            action=SignalAction.BUY, symbol="X",
            explanation=StrategySigExpl(
                summary="BUY", reasons=["volume OK"],
                indicators={"x": 1},
            ),
        )
        rg = MarketRegimeFilter().evaluate([], regime_override=MarketRegime.HIGH_VOLATILITY)

        class _RM:
            decision = "APPROVE"
            reasons  = ["all guards passed"]

        class _PG:
            status   = "APPROVED"
            reasons  = ["operator approved"]

        class _AG:
            decision = "APPROVE"
            reasons  = ["AI agreed"]

        expl = compose_signal_explanation(
            signal=sig, regime_decision=rg, risk_result=_RM(),
            permission_result=_PG(), agent_decision=_AG(),
            audit_trace_id=42, strategy="VolumeBreakout",
        )
        assert expl.audit_trace_id == 42
        assert expl.strategy == "VolumeBreakout"
        assert expl.action == "BUY"
        # 5 단계의 reasons 모두 포함
        cats = {r.category for r in expl.reasons}
        assert ReasonCategory.STRATEGY in cats
        assert ReasonCategory.MARKET_REGIME in cats
        assert ReasonCategory.RISK_MANAGER in cats
        assert ReasonCategory.PERMISSION_GATE in cats
        assert ReasonCategory.AGENT in cats
        # PermissionGate APPROVED → final_status APPROVED
        assert expl.final_status == ExplainStatus.APPROVED


# ====================================================================
# extract_reasons_from_audit_row
# ====================================================================


class TestExtractFromAuditRow:
    def _make_row(self, **overrides):
        defaults = dict(
            id=42, symbol="X", strategy="VolumeBreakout", side="BUY",
            decision="APPROVED", reasons=[], message="ok", ai_decision_meta=None,
        )
        defaults.update(overrides)

        class _Row:
            pass
        r = _Row()
        for k, v in defaults.items():
            setattr(r, k, v)
        return r

    def test_extract_approved_row(self):
        r = self._make_row(decision="APPROVED", reasons=["all guards passed"])
        expl = extract_reasons_from_audit_row(r)
        assert expl.audit_trace_id == 42
        assert expl.symbol == "X"
        assert expl.strategy == "VolumeBreakout"
        assert expl.action == "BUY"
        # decision 자체가 reason으로 추가됨 + reasons 리스트의 1건
        assert len(expl.reasons) >= 2
        assert any(r.code and r.code.startswith("DECISION_APPROVED") for r in expl.reasons)
        assert expl.final_status == ExplainStatus.APPROVED

    def test_extract_rejected_row(self):
        r = self._make_row(decision="REJECTED",
                           reasons=["max_order_notional 초과", "stale data"],
                           message="rejected")
        expl = extract_reasons_from_audit_row(r)
        assert expl.final_status == ExplainStatus.REJECTED
        assert any("DECISION_REJECTED" in (r.code or "") for r in expl.reasons)

    def test_extract_with_ai_decision_meta(self):
        r = self._make_row(
            decision="REJECTED",
            ai_decision_meta={
                "confidence": 35,
                "reasons": ["AI confidence too low"],
                "rejected_by_guard": True,
            },
        )
        expl = extract_reasons_from_audit_row(r)
        agent_reasons = [r for r in expl.reasons if r.category == ReasonCategory.AGENT]
        assert len(agent_reasons) >= 2  # AI reason + AI_CONFIDENCE + AI_REJECTED_BY_GUARD
        codes = {r.code for r in agent_reasons}
        assert "AI_CONFIDENCE" in codes
        assert "AI_REJECTED_BY_GUARD" in codes

    def test_extract_dict_row(self):
        """row가 dict 형태여도 동작."""
        d = {
            "id": 1, "symbol": "X", "strategy": "S", "side": "BUY",
            "decision": "APPROVED", "reasons": ["ok"], "message": "",
        }
        expl = extract_reasons_from_audit_row(d)
        assert expl.audit_trace_id == 1
        assert expl.action == "BUY"


# ====================================================================
# 직접 주문 invariant
# ====================================================================


class TestNoOrderImports:
    def test_module_does_not_import_broker_or_route(self):
        import inspect

        from app.explainability import reasons as mod
        src = inspect.getsource(mod)
        # route_order(/place_order는 함수 호출 형태만 금지 — docstring 언급은 허용.
        forbidden_imports = (
            "from app.brokers", "from app.risk", "from app.permission",
            "from app.execution",
        )
        forbidden_calls = ("route_order(", "place_order(")
        for f in forbidden_imports:
            assert f not in src, f"forbidden import in explainability: {f}"
        for f in forbidden_calls:
            assert f not in src, f"forbidden call in explainability: {f}"

    def test_routes_does_not_import_broker_or_route(self):
        import inspect

        from app.api import routes_explainability as mod
        src = inspect.getsource(mod)
        forbidden_imports = (
            "from app.brokers", "from app.risk.risk_manager",
            "from app.permission.gate", "from app.execution.order_router",
        )
        forbidden_calls = ("route_order(", "place_order(")
        for f in forbidden_imports:
            assert f not in src, f"forbidden import in routes: {f}"
        for f in forbidden_calls:
            assert f not in src, f"forbidden call in routes: {f}"


# ====================================================================
# /api/signals/{audit_id}/explain — endpoint
# ====================================================================


class TestExplainEndpoint:
    def _seed_audit(self, client, **overrides) -> int:
        defaults = dict(
            mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
            order_type="MARKET", latest_price=75_000, decision="APPROVED",
            reasons=["all guards passed"],
        )
        defaults.update(overrides)
        with client.test_db_factory() as db:
            row = OrderAuditLog(**defaults)
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id

    def test_404_when_id_not_found(self, client):
        res = client.get("/api/signals/999999/explain")
        assert res.status_code == 404

    def test_returns_explain_payload_for_approved_row(self, client):
        audit_id = self._seed_audit(client, decision="APPROVED",
                                      reasons=["거래대금 증가", "VWAP 상단"],
                                      strategy="VolumeBreakout")
        res = client.get(f"/api/signals/{audit_id}/explain")
        assert res.status_code == 200
        body = res.json()
        assert body["audit_trace_id"] == audit_id
        assert body["symbol"] == "005930"
        assert body["strategy"] == "VolumeBreakout"
        assert body["action"] == "BUY"
        assert body["final_status"] == "APPROVED"
        assert len(body["reasons"]) >= 3  # 2 reasons + decision row
        assert body["summary"]
        # grouped 키 5개 (PASS/WARN/FAIL/BLOCKED/INFO)
        assert set(body["grouped"].keys()) == {"PASS", "WARN", "FAIL", "BLOCKED", "INFO"}

    def test_returns_explain_payload_for_rejected_row(self, client):
        audit_id = self._seed_audit(client, decision="REJECTED",
                                      reasons=["max_order_notional 초과"],
                                      message="reject reason")
        res = client.get(f"/api/signals/{audit_id}/explain")
        assert res.status_code == 200
        body = res.json()
        assert body["final_status"] == "REJECTED"
        assert any("DECISION_REJECTED" in (r.get("code") or "") for r in body["reasons"])

    def test_includes_ai_meta_in_reasons(self, client):
        audit_id = self._seed_audit(
            client, decision="REJECTED",
            ai_decision_meta={
                "confidence": 30,
                "reasons": ["AI confidence too low"],
                "rejected_by_guard": True,
            },
        )
        res = client.get(f"/api/signals/{audit_id}/explain")
        assert res.status_code == 200
        body = res.json()
        codes = {r.get("code") for r in body["reasons"]}
        assert "AI_CONFIDENCE" in codes
        assert "AI_REJECTED_BY_GUARD" in codes
        cats = {r["category"] for r in body["reasons"]}
        assert "AGENT" in cats

    def test_grouped_buckets_sum_equals_reasons(self, client):
        audit_id = self._seed_audit(client, reasons=["a", "b", "c"])
        body = client.get(f"/api/signals/{audit_id}/explain").json()
        total = sum(len(v) for v in body["grouped"].values())
        assert total == len(body["reasons"])
