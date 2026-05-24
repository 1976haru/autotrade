"""INTRADAY-DATA-02 — KIS 분봉 collector 안전 placeholder 테스트.

핵심: 공식 endpoint 미확인 시 NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION, 실제 요청 0건,
order/route_order/httpx import 0건, secret 원문 0건.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.market_data.kis_intraday_collector import (
    BLOCKED_NOT_READ_ONLY,
    DISABLED,
    NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION,
    READY_READ_ONLY,
    KisIntradayCollectorStatus,
    collect_intraday_via_kis,
    evaluate_kis_intraday_collector,
    to_dict,
)

_REPO = Path(__file__).resolve().parents[2]


def test_default_needs_official_endpoint_confirmation():
    st = evaluate_kis_intraday_collector({})
    assert st.status == NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION
    assert st.can_collect is False
    assert st.endpoint_present is False and st.tr_id_present is False


def test_enabled_without_endpoint_still_needs_confirmation():
    st = evaluate_kis_intraday_collector({"KIS_INTRADAY_ENABLED": "true"})
    assert st.status == NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION
    assert st.can_collect is False


def test_endpoint_present_but_not_read_only_blocked():
    st = evaluate_kis_intraday_collector({
        "KIS_INTRADAY_ENABLED": "true", "KIS_INTRADAY_ENDPOINT": "/x",
        "KIS_INTRADAY_TR_ID": "FHK", "KIS_INTRADAY_READ_ONLY": "false"})
    assert st.status == BLOCKED_NOT_READ_ONLY
    assert st.can_collect is False


def test_endpoint_present_read_only_disabled():
    st = evaluate_kis_intraday_collector({
        "KIS_INTRADAY_ENDPOINT": "/x", "KIS_INTRADAY_TR_ID": "FHK",
        "KIS_INTRADAY_READ_ONLY": "true", "KIS_INTRADAY_ENABLED": "false"})
    assert st.status == DISABLED
    assert st.can_collect is False


def test_ready_read_only_when_all_set():
    st = evaluate_kis_intraday_collector({
        "KIS_INTRADAY_ENABLED": "true", "KIS_INTRADAY_ENDPOINT": "/quotations/inquire",
        "KIS_INTRADAY_TR_ID": "FHKXXXXX", "KIS_INTRADAY_READ_ONLY": "true"})
    assert st.status == READY_READ_ONLY
    assert st.can_collect is True
    assert st.endpoint_present is True and st.tr_id_present is True


def test_collect_never_sends_request_default():
    r = collect_intraday_via_kis("005930", env={})
    assert r.status == NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION
    assert r.requested is False
    assert r.bars == ()


def test_collect_never_sends_request_even_when_ready():
    """READY_READ_ONLY 여도 본 PR 은 실제 fetch 미구현 — requested False."""
    env = {"KIS_INTRADAY_ENABLED": "true", "KIS_INTRADAY_ENDPOINT": "/q",
           "KIS_INTRADAY_TR_ID": "FHK", "KIS_INTRADAY_READ_ONLY": "true"}
    r = collect_intraday_via_kis("005930", env=env)
    assert r.requested is False
    assert r.bars == ()


def test_status_invariants_and_to_dict():
    st = evaluate_kis_intraday_collector({})
    d = to_dict(st)
    assert d["is_order_endpoint"] is False
    assert d["contains_secret"] is False
    # present-only — 원문 endpoint/tr_id 값은 dict 에 없다.
    assert "endpoint" not in d and "tr_id" not in d
    assert "endpoint_present" in d and "tr_id_present" in d


def test_guard_rejects_order_endpoint():
    with pytest.raises(ValueError):
        KisIntradayCollectorStatus(
            NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION, False, True, False, False,
            can_collect=False, is_order_endpoint=True)


def test_guard_rejects_can_collect_without_ready():
    with pytest.raises(ValueError):
        KisIntradayCollectorStatus(
            DISABLED, False, True, True, True, can_collect=True)


def test_module_no_order_or_http_imports():
    """실제 import 문 기준 — broker/order/route_order/httpx/requests import 0건."""
    src = (_REPO / "backend" / "app" / "market_data" / "kis_intraday_collector.py").read_text(
        encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("httpx", "requests", "urllib", "app.brokers", "app.execution",
                        "order_router", "OrderExecutor"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for call in ("route_order(", ".place_order(", "OrderExecutor(", "requests.get",
                 "httpx.get", "urlopen("):
        assert call not in src, f"forbidden call: {call}"
