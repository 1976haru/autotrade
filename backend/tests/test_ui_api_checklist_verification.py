"""FINAL-UI-API-01 — 체크리스트 UI/API 통합 검증 테스트.

- 매니페스트(카드↔탭↔client↔route) 정적 검증.
- 읽기전용 GET endpoint 가 in-process TestClient 에서 실제 200 + secret 미노출로 응답.
- 리포트 불변값(is_live_authorization / broker_order_sent / contains_secret = False).
- 모듈이 broker / OrderExecutor / route_order 를 import 하지 않음 (read-only 가드).

실제 KIS API 0건, 주문 0건 — TestClient + MockBroker, dry-run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.system.ui_api_checklist import (
    CHECKLIST_ITEMS,
    FAIL,
    PASS,
    WARN,
    UiApiChecklistReport,
    evaluate_http_results,
    http_targets,
    render_markdown,
    run_manifest_checks,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"\b\d{8}-\d{2}\b"),
]


def _has_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


# --------------------------- manifest --------------------------------------

def test_manifest_all_items_pass():
    report = run_manifest_checks(_REPO_ROOT)
    assert report.mode == "manifest"
    assert report.overall_verdict == PASS, [
        (i.card, i.detail) for i in report.items if i.verdict != PASS
    ]
    assert report.ui_api_ready is True
    assert report.counts[FAIL] == 0
    assert len(report.items) == len(CHECKLIST_ITEMS) == 19


def test_manifest_each_card_mounted_wired_routed():
    report = run_manifest_checks(_REPO_ROOT)
    for item in report.items:
        assert item.mounted, f"{item.card} not mounted in {item.tab}"
        assert item.client_wired, f"{item.card} client method/url missing"
        assert item.route_present, f"{item.card} backend route missing"


def test_manifest_covers_three_tabs():
    tabs = {it.tab for it in CHECKLIST_ITEMS}
    assert {"Dashboard", "AISignal", "Settings"} <= tabs


def test_manifest_detects_missing_mount(tmp_path):
    """카드가 mount 안 돼 있으면 FAIL 로 잡는지 (가짜 repo 로 negative)."""
    # 빈 가짜 repo → 모든 파일 부재 → 전 항목 FAIL.
    report = run_manifest_checks(tmp_path)
    assert report.overall_verdict == FAIL
    assert report.ui_api_ready is False
    assert report.counts[FAIL] == len(CHECKLIST_ITEMS)


# --------------------------- report invariants -----------------------------

def test_report_invariants_default_false():
    report = run_manifest_checks(_REPO_ROOT)
    assert report.is_live_authorization is False
    assert report.broker_order_sent is False
    assert report.contains_secret is False


@pytest.mark.parametrize("kwargs", [
    {"is_live_authorization": True},
    {"broker_order_sent": True},
    {"contains_secret": True},
])
def test_report_guard_rejects_unsafe(kwargs):
    with pytest.raises(ValueError):
        UiApiChecklistReport(mode="manifest", **kwargs)


# --------------------------- http evaluator --------------------------------

def test_http_targets_exclude_post_and_orders():
    targets = http_targets()
    assert len(targets) == 17  # 19 - 2 POST(decision-explanation/quality)
    for t in targets:
        assert t.http_method == "GET"
        assert t.read_only_http is True
    paths = {t.http_path for t in targets}
    assert "/api/agents/decision-explanation" not in paths
    assert "/api/agents/decision-quality" not in paths


def test_http_evaluator_status_mapping():
    targets = http_targets()
    # 모두 200, secret 없음 → PASS.
    raw = {t.http_path: (200, False) for t in targets}
    rep = evaluate_http_results(raw)
    assert rep.overall_verdict == PASS
    assert rep.ui_api_ready is True

    # 하나가 503(자격/장상태) → WARN (FAIL 아님), ready 유지.
    raw[targets[0].http_path] = (503, False)
    rep = evaluate_http_results(raw)
    assert rep.overall_verdict == WARN
    assert rep.ui_api_ready is True

    # 하나가 500 → FAIL.
    raw[targets[0].http_path] = (500, False)
    rep = evaluate_http_results(raw)
    assert rep.overall_verdict == FAIL
    assert rep.ui_api_ready is False


def test_http_evaluator_secret_is_fail():
    targets = http_targets()
    raw = {t.http_path: (200, False) for t in targets}
    raw[targets[0].http_path] = (200, True)  # secret found
    rep = evaluate_http_results(raw)
    assert rep.overall_verdict == FAIL
    res = next(r for r in rep.http_results if r.http_path == targets[0].http_path)
    assert res.secret_found is True


def test_render_markdown_has_safety_disclaimers():
    md = render_markdown(run_manifest_checks(_REPO_ROOT))
    assert "실전 승인 아님" in md
    assert "주문 0건" in md
    # 금지(단언) 문구 부재.
    assert "수익 보장" not in md.replace("수익 보장 아님", "")


# --------------------------- read-only guard -------------------------------

def test_module_has_no_forbidden_imports():
    """실제 import 문 / 호출 패턴만 검사 (docstring 의 '… 0건' 설명 prose 는 허용)."""
    src = (_REPO_ROOT / "backend" / "app" / "system" / "ui_api_checklist.py").read_text(
        encoding="utf-8")
    # 실제 import 문 (줄 시작이 from/import).
    import_lines = [
        ln for ln in src.splitlines()
        if re.match(r"^\s*(from|import)\s", ln)
    ]
    forbidden_import_modules = (
        "app.brokers", "app.execution", "order_router", "paper_trader",
        "app.ai.assist", "app.ai.client", "anthropic", "openai", "httpx", "requests",
    )
    for ln in import_lines:
        for mod in forbidden_import_modules:
            assert mod not in ln, f"forbidden import: {ln.strip()}"
    # 실제 호출 패턴 (prose 와 달리 괄호 포함).
    for call in ("route_order(", ".place_order(", "OrderExecutor(", "submit_candidate("):
        assert call not in src, f"forbidden call pattern: {call}"


# --------------------------- in-process endpoint smoke ---------------------

# (path, query) — read-only GET endpoints. query 는 route 가 요구할 수 있는 최소값.
_READONLY_GET = [
    ("/api/status/live-safety", ""),
    ("/api/system/exe-status", ""),
    ("/api/system/build-info", ""),
    ("/api/system/program-integrity", ""),
    ("/api/system/premarket-readiness", ""),
    ("/api/system/kis-paper-autotrade-audit", ""),
    ("/api/system/logs", ""),
    ("/api/auto-paper/portfolio-source", ""),
    ("/api/auto-paper/universe-status", ""),
    ("/api/auto-paper/status", ""),
    ("/api/agents/order-quality-metrics", ""),
    ("/api/agents/feedback-loop", ""),
    ("/api/kis-paper/readiness", ""),
    ("/api/kis-paper/status", ""),
]


@pytest.mark.parametrize("path,query", _READONLY_GET)
def test_readonly_get_endpoint_responds(client, safe_default_flags, path, query):
    r = client.get(path + query)
    # 200 이 정상. 자격/장상태로 일부는 다른 코드일 수 있으나 5xx 는 허용 안 함.
    assert r.status_code < 500, f"{path} -> {r.status_code}: {r.text[:200]}"
    body = r.text
    assert not _has_secret(body), f"{path} response contains secret-like pattern"


@pytest.mark.parametrize("path,query", _READONLY_GET)
def test_readonly_get_no_live_authorization_in_body(client, safe_default_flags, path, query):
    r = client.get(path + query)
    if r.status_code != 200:
        pytest.skip(f"{path} returned {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        return
    text = str(data)
    # 응답이 실거래 권한/실주문을 주장하지 않는다.
    assert '"is_live_authorization": true' not in text.lower().replace(" ", " ")
    assert data.get("is_live_authorization", False) in (False, None)
    assert data.get("broker_order_sent", False) in (False, None)


def test_readonly_get_endpoints_are_truly_get_only(client, safe_default_flags):
    """대표 read-only endpoint 에 POST 하면 405 (write 동사 미허용)."""
    r = client.post("/api/system/program-integrity", json={})
    assert r.status_code in (404, 405)
