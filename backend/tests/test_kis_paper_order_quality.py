"""P-24: KIS 모의 주문·체결 품질 로그 테스트.

검증:
 - build_order_quality_log: requested/submitted/filled 시각, request/avg_fill price,
   quantity/filled/unfilled, partial_fill, order_status/fill_status, KIS code/message
   allowlist, latency_ms, slippage_bps
 - dry-run/rejected/partial/unfilled 상태 매핑
 - fill polling placeholder
 - episode 연결: attach_order_quality + kis_order_result.order_quality nest
 - API 상세 order_quality 포함 + 목록 order_quality_summary + summary by_order_status
 - secret/account 미저장, is_live_authorization=False, broker import 0건
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.kis_paper.order_quality as oq_mod
from app.agents.decision_episode import (
    attach_order_quality,
    get_episode,
    new_episode_id,
    record_episode,
    summarize_episodes,
)
from app.db.base import Base
from app.db.session import get_db
from app.kis_paper.order_quality import (
    STATUS_DRY_RUN,
    STATUS_FILLED,
    STATUS_PARTIALLY_FILLED,
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    build_order_quality_log,
    placeholder_fill_polling,
)
from app.main import app

_MODULE = Path(oq_mod.__file__).resolve()
T0 = datetime(2026, 5, 27, 5, 0, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(milliseconds=333)


def _dec(side="BUY", quantity=13, price=75000):
    return SimpleNamespace(symbol="005930", side=side, quantity=quantity, price=price)


def _res(**kw):
    base = dict(reason_code="KIS_PAPER_SUBMITTED", reason_message="ok", dry_run=False,
                submitted=True, fill_status="FILLED", filled_quantity=13,
                avg_fill_price=75100, broker_order_no="PAPER-1", quantity=13)
    base.update(kw)
    return SimpleNamespace(**base)


# ─────────────────────────────────────────────────────────────────────────────
# build_order_quality_log — 필드
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildQuality:
    def test_filled_full_fields(self):
        q = build_order_quality_log(result=_res(), decision=_dec(),
                                    requested_at=T0, responded_at=T1).to_dict()
        assert q["order_status"] == STATUS_FILLED
        assert q["fill_status"] == "FILLED"
        assert q["requested_at"] is not None
        assert q["submitted_at"] is not None
        assert q["filled_at"] is not None
        assert q["request_price"] == 75000.0
        assert q["avg_fill_price"] == 75100.0
        assert q["quantity"] == 13
        assert q["filled_quantity"] == 13
        assert q["unfilled_quantity"] == 0
        assert q["partial_fill"] is False
        assert q["broker_order_no"] == "PAPER-1"

    def test_kis_code_message_allowlist(self):
        q = build_order_quality_log(result=_res(), decision=_dec()).to_dict()
        assert q["kis_response_code"] == "KIS_PAPER_SUBMITTED"
        assert q["kis_response_message"] == "ok"

    def test_latency_ms(self):
        q = build_order_quality_log(result=_res(), decision=_dec(),
                                    requested_at=T0, responded_at=T1).to_dict()
        assert q["latency_ms"] == 333

    def test_slippage_bps_buy(self):
        # (75100-75000)/75000*10000 = 13.33 (BUY 불리 = 양수)
        q = build_order_quality_log(result=_res(), decision=_dec()).to_dict()
        assert abs(q["slippage_bps"] - 13.33) < 0.05

    def test_slippage_bps_sell_sign(self):
        # SELL 체결가 < 요청가 가 불리 — 부호 정규화 확인.
        q = build_order_quality_log(
            result=_res(side="SELL", avg_fill_price=74900),
            decision=_dec(side="SELL")).to_dict()
        assert q["slippage_bps"] > 0  # 불리한 방향 양수

    def test_invariants(self):
        q = build_order_quality_log(result=_res(), decision=_dec()).to_dict()
        assert q["contains_secret"] is False
        assert q["is_live_authorization"] is False


class TestStatusMapping:
    def test_dry_run(self):
        q = build_order_quality_log(
            result=_res(reason_code="KIS_PAPER_DRY_RUN_OK", dry_run=True,
                        submitted=False, fill_status=None, filled_quantity=0,
                        avg_fill_price=None, broker_order_no=None),
            decision=_dec()).to_dict()
        assert q["order_status"] == STATUS_DRY_RUN

    def test_rejected(self):
        q = build_order_quality_log(
            result=_res(reason_code="BLOCKED_BY_RISK_MANAGER",
                        reason_message="notional too high", submitted=False,
                        fill_status=None, filled_quantity=0, avg_fill_price=None,
                        broker_order_no=None),
            decision=_dec()).to_dict()
        assert q["order_status"] == STATUS_REJECTED
        assert q["rejection_reason_message"] == "notional too high"
        assert q["rejection_reason_code"] == "BLOCKED_BY_RISK_MANAGER"

    def test_partially_filled(self):
        q = build_order_quality_log(
            result=_res(fill_status="PARTIALLY_FILLED", filled_quantity=5),
            decision=_dec()).to_dict()
        assert q["order_status"] == STATUS_PARTIALLY_FILLED
        assert q["unfilled_quantity"] == 8
        assert q["partial_fill"] is True

    def test_unfilled(self):
        q = build_order_quality_log(
            result=_res(submitted=False, fill_status=None, filled_quantity=0,
                        avg_fill_price=None),
            decision=_dec()).to_dict()
        assert q["order_status"] in ("UNFILLED",)

    def test_submitted_no_fill(self):
        q = build_order_quality_log(
            result=_res(fill_status=None, filled_quantity=0, avg_fill_price=None),
            decision=_dec()).to_dict()
        assert q["order_status"] == STATUS_SUBMITTED

    def test_fill_polling_placeholder(self):
        pp = placeholder_fill_polling(enabled=True)
        assert pp["enabled"] is True
        assert pp["poll_count"] == 0
        assert "final_status" in pp
        q = build_order_quality_log(result=_res(), decision=_dec()).to_dict()
        assert "fill_polling" in q


# ─────────────────────────────────────────────────────────────────────────────
# episode 연결
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


class TestEpisodeIntegration:
    def test_record_with_order_quality_nested(self, db):
        eid = new_episode_id()
        q = build_order_quality_log(result=_res(), decision=_dec(),
                                    requested_at=T0, responded_at=T1).to_dict()
        record_episode(db, episode_id=eid, final_action="BUY", symbol="005930",
                       kis_order_result={**_res().__dict__, "order_quality": q})
        db.commit()
        ep = get_episode(db, eid)
        assert ep["kis_order_result"]["order_quality"]["order_status"] == "FILLED"
        # 목록 요약.
        assert ep["order_quality_summary"]["order_status"] == "FILLED"
        assert ep["order_quality_summary"]["latency_ms"] == 333

    def test_attach_order_quality_helper(self, db):
        eid = new_episode_id()
        record_episode(db, episode_id=eid, final_action="BUY", symbol="005930",
                       kis_order_result={"reason_code": "KIS_PAPER_SUBMITTED"})
        db.commit()
        q = build_order_quality_log(result=_res(), decision=_dec()).to_dict()
        attach_order_quality(db, eid, q)
        db.commit()
        assert get_episode(db, eid)["kis_order_result"]["order_quality"]["order_status"] == "FILLED"

    def test_summary_aggregates(self, db):
        for fs, fq in [("FILLED", 13), ("PARTIALLY_FILLED", 5)]:
            eid = new_episode_id()
            q = build_order_quality_log(
                result=_res(fill_status=fs, filled_quantity=fq), decision=_dec(),
                requested_at=T0, responded_at=T1).to_dict()
            record_episode(db, episode_id=eid, final_action="BUY", symbol="X",
                           kis_order_result={"order_quality": q})
        eid = new_episode_id()
        qr = build_order_quality_log(
            result=_res(reason_code="BLOCKED_BY_RISK_MANAGER", submitted=False,
                        fill_status=None, filled_quantity=0, avg_fill_price=None),
            decision=_dec()).to_dict()
        record_episode(db, episode_id=eid, final_action="HOLD", symbol="Y",
                       kis_order_result={"order_quality": qr})
        db.commit()
        s = summarize_episodes(db)
        assert "by_order_status" in s and "by_fill_status" in s
        assert s["rejected_count"] == 1
        assert s["partial_fill_count"] == 1
        assert s["avg_latency_ms"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# API + 무누출
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db):
    def _odb():
        yield db
    eid = new_episode_id()
    q = build_order_quality_log(result=_res(), decision=_dec(),
                                requested_at=T0, responded_at=T1).to_dict()
    record_episode(db, episode_id="ep-oq-1", final_action="BUY", symbol="005930",
                   kis_order_result={**_res().__dict__, "order_quality": q})
    db.commit()
    app.dependency_overrides[get_db] = _odb
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


class TestApi:
    def test_detail_has_order_quality(self, client):
        b = client.get("/api/agents/decision-episodes/ep-oq-1").json()
        assert b["kis_order_result"]["order_quality"]["order_status"] == "FILLED"
        assert b["is_live_authorization"] is False

    def test_list_has_order_quality_summary(self, client):
        b = client.get("/api/agents/decision-episodes").json()
        ep = b["episodes"][0]
        assert "order_quality_summary" in ep
        assert "by_order_status" in b["summary"]

    def test_no_secret_or_account_in_response(self, client):
        raw = client.get("/api/agents/decision-episodes").text.lower()
        for banned in ("app_secret", "kis_app_secret", "account_no", "kis_account_no",
                       "access_token"):
            assert banned not in raw

    def test_no_secret_value_pattern(self, client):
        raw = client.get("/api/agents/decision-episodes").text
        assert not re.search(r"sk-[A-Za-z0-9]{16}|\b\d{6,}-\d{2,}\b", raw)


# ─────────────────────────────────────────────────────────────────────────────
# static guards
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_module_no_broker_route_order_import(self):
        text = _MODULE.read_text(encoding="utf-8")
        for pat in (r"^from app\.brokers", r"^import app\.brokers",
                    r"^from app\.execution", r"route_order\s*\(",
                    r"OrderExecutor\s*\(", r"\bbroker\.place_order\s*\(",
                    r"import httpx", r"import requests"):
            assert not re.search(pat, text, re.MULTILINE), f"order_quality 금지: /{pat}/"

    def test_output_dict_has_no_secret_keys(self):
        # OrderQualityLog 출력 dict 에 secret/account 키 0건 (allowlist 만).
        q = build_order_quality_log(result=_res(), decision=_dec()).to_dict()
        keys = " ".join(q.keys()).lower()
        for banned in ("app_secret", "app_key", "kis_account_no", "account_no",
                       "access_token", "token", "password", "secret_token"):
            assert banned not in keys
