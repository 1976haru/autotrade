"""FINAL-UI-API-01 — 체크리스트 기능 UI/API 통합 검증 (read-only).

지금까지 만든 체크리스트 카드들이 *실제 화면(탭)에 mount* 되고, *API client method
와 backend GET route 에 연결* 되어 있는지를 **정적(manifest) 으로 검증** 한다. 추가로
http mode 에서는 실행 중인 backend 의 *read-only GET endpoint 만* 호출해 응답 상태와
secret 노출 여부를 확인한다.

CLAUDE.md 절대 원칙 (본 모듈):
- read-only. broker / OrderExecutor / route_order / KIS 실제 API / 외부 HTTP 주문 호출 0건.
- 안전 flag / `.env` 변경 0건. secret / 계좌 원문 출력 0건.
- 새 기능(주문/실전/승인 버튼) 추가 아님 — 기존 화면/연결을 *검증* 만 한다.
- http mode 는 GET(read-only) endpoint 만 호출 — POST/주문 endpoint 0건.

`UiApiChecklistReport.is_live_authorization` / `broker_order_sent` / `contains_secret`
는 항상 False (dataclass `__post_init__` 가드).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# backend/app/system/ui_api_checklist.py -> repo root (parents[3])
_REPO_ROOT = Path(__file__).resolve().parents[3]

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass(frozen=True)
class ChecklistItem:
    """카드 ↔ 탭 ↔ API client method ↔ backend GET route 1:1 매니페스트."""

    name: str
    card: str
    tab: str            # 사람이 보는 탭 이름 (Dashboard / AISignal / Settings)
    tab_file: str       # repo-relative 탭 파일
    client_method: str  # client.js 의 method 이름
    client_url: str     # client.js 에 등장해야 하는 URL path substring
    route_file: str     # repo-relative backend route 파일
    route_path: str     # route decorator path substring
    http_method: str    # GET / POST
    http_path: str      # 전체 /api/... path
    read_only_http: bool  # http smoke 에서 호출해도 안전한 GET 여부


# 점검 대상 매니페스트 (FINAL-UI-API-01 §1, §2).
CHECKLIST_ITEMS: tuple[ChecklistItem, ...] = (
    # ---------- Dashboard ----------
    ChecklistItem(
        "portfolio_source", "PortfolioSourceCard", "Dashboard",
        "frontend/src/components/tabs/Dashboard.jsx",
        "portfolioSource", "/api/auto-paper/portfolio-source",
        "backend/app/api/routes_auto_paper.py", '"/portfolio-source"',
        "GET", "/api/auto-paper/portfolio-source", True),
    ChecklistItem(
        "auto_paper_loop", "AutoPaperLoopCard", "Dashboard",
        "frontend/src/components/tabs/Dashboard.jsx",
        "autoPaperStatus", "/api/auto-paper/status",
        "backend/app/api/routes_auto_paper.py", '"/status"',
        "GET", "/api/auto-paper/status", True),
    ChecklistItem(
        "agent_council_vote", "AgentCouncilVoteCard", "Dashboard",
        "frontend/src/components/tabs/Dashboard.jsx",
        "agentDecisionEpisodes", "/api/agents/decision-episodes",
        "backend/app/api/routes_agents.py", '"/decision-episodes"',
        "GET", "/api/agents/decision-episodes", True),
    ChecklistItem(
        "kis_paper_oneclick", "KisPaperOneClickTestCard", "Dashboard",
        "frontend/src/components/tabs/Dashboard.jsx",
        "kisPaperReadiness", "/api/kis-paper/readiness",
        "backend/app/api/routes_kis_paper.py", '"/readiness"',
        "GET", "/api/kis-paper/readiness", True),
    # ---------- Settings ----------
    ChecklistItem(
        "kis_paper_env", "KisPaperEnvStatusCard", "Settings",
        "frontend/src/components/tabs/Settings.jsx",
        "kisPaperReadiness", "/api/kis-paper/readiness",
        "backend/app/api/routes_kis_paper.py", '"/readiness"',
        "GET", "/api/kis-paper/readiness", True),
    ChecklistItem(
        "backend_sidecar", "BackendSidecarStatusCard", "Settings",
        "frontend/src/components/tabs/Settings.jsx",
        "exeStatus", "/api/system/exe-status",
        "backend/app/api/routes_system.py", '"/system/exe-status"',
        "GET", "/api/system/exe-status", True),
    ChecklistItem(
        "app_version", "AppVersionCard", "Settings",
        "frontend/src/components/tabs/Settings.jsx",
        "buildInfo", "/api/system/build-info",
        "backend/app/api/routes_system.py", '"/system/build-info"',
        "GET", "/api/system/build-info", True),
    ChecklistItem(
        "preflight_smoke", "PreflightSmokeCard", "Settings",
        "frontend/src/components/tabs/Settings.jsx",
        "preflight", "/api/system/preflight",
        "backend/app/api/routes_system.py", '"/system/preflight"',
        "GET", "/api/system/preflight", True),
    ChecklistItem(
        "runtime_log", "RuntimeEventLogViewer", "Settings",
        "frontend/src/components/tabs/Settings.jsx",
        "systemLogs", "/api/system/logs",
        "backend/app/api/routes_system.py", '"/system/logs"',
        "GET", "/api/system/logs", True),
    ChecklistItem(
        "universe_status", "UniverseStatusCard", "Settings",
        "frontend/src/components/tabs/Settings.jsx",
        "universeStatus", "/api/auto-paper/universe-status",
        "backend/app/api/routes_auto_paper.py", '"/universe-status"',
        "GET", "/api/auto-paper/universe-status", True),
    ChecklistItem(
        "live_safety", "LiveSafetyStatusCard", "Settings",
        "frontend/src/components/tabs/Settings.jsx",
        "liveSafetyStatus", "/api/status/live-safety",
        "backend/app/api/routes_status.py", '"/live-safety"',
        "GET", "/api/status/live-safety", True),
    ChecklistItem(
        "program_integrity", "ProgramIntegrityGateCard", "Settings",
        "frontend/src/components/tabs/Settings.jsx",
        "programIntegrity", "/api/system/program-integrity",
        "backend/app/api/routes_system.py", '"/system/program-integrity"',
        "GET", "/api/system/program-integrity", True),
    ChecklistItem(
        "premarket_readiness", "PremarketReadinessGateCard", "Settings",
        "frontend/src/components/tabs/Settings.jsx",
        "premarketReadiness", "/api/system/premarket-readiness",
        "backend/app/api/routes_system.py", '"/system/premarket-readiness"',
        "GET", "/api/system/premarket-readiness", True),
    ChecklistItem(
        "kis_paper_autotrade_audit", "KisPaperAiAutotradeAuditCard", "Settings",
        "frontend/src/components/tabs/Settings.jsx",
        "kisPaperAutotradeAudit", "/api/system/kis-paper-autotrade-audit",
        "backend/app/api/routes_system.py", '"/system/kis-paper-autotrade-audit"',
        "GET", "/api/system/kis-paper-autotrade-audit", True),
    # ---------- AISignal ----------
    ChecklistItem(
        "decision_explanation", "AgentDecisionExplanationCard", "AISignal",
        "frontend/src/components/tabs/AISignal.jsx",
        "agentDecisionExplanation", "/api/agents/decision-explanation",
        "backend/app/api/routes_agents.py", '"/decision-explanation"',
        "POST", "/api/agents/decision-explanation", False),
    ChecklistItem(
        "performance_metrics", "PerformanceMetricsSummary", "AISignal",
        "frontend/src/components/tabs/AISignal.jsx",
        "agentOrderQualityMetrics", "/api/agents/order-quality-metrics",
        "backend/app/api/routes_agents.py", '"/order-quality-metrics"',
        "GET", "/api/agents/order-quality-metrics", True),
    ChecklistItem(
        "decision_quality", "DecisionQualityScoreCard", "AISignal",
        "frontend/src/components/tabs/AISignal.jsx",
        "agentDecisionQuality", "/api/agents/decision-quality",
        "backend/app/api/routes_agents.py", '"/decision-quality"',
        "POST", "/api/agents/decision-quality", False),
    ChecklistItem(
        "post_trade_feedback", "PostTradeFeedbackCard", "AISignal",
        "frontend/src/components/tabs/AISignal.jsx",
        "agentFeedbackLoop", "/api/agents/feedback-loop",
        "backend/app/api/routes_agents.py", '"/feedback-loop"',
        "GET", "/api/agents/feedback-loop", True),
    ChecklistItem(
        "paper_gate_report", "PaperGateReportCard", "AISignal",
        "frontend/src/components/tabs/AISignal.jsx",
        "agentPaperGateReport", "/api/agents/paper-gate-report",
        "backend/app/api/routes_agents.py", '"/paper-gate-report"',
        "GET", "/api/agents/paper-gate-report", True),
)


@dataclass(frozen=True)
class ItemResult:
    name: str
    card: str
    tab: str
    mounted: bool          # 탭에 import + render
    client_wired: bool     # client.js method + url
    route_present: bool    # backend route decorator
    has_card_test: bool    # 카드 단위 vitest 존재 (invariant lock)
    verdict: str
    detail: str


@dataclass(frozen=True)
class HttpResult:
    name: str
    http_path: str
    status: int
    ok: bool
    secret_found: bool
    verdict: str
    detail: str


@dataclass(frozen=True)
class UiApiChecklistReport:
    mode: str                      # "manifest" | "http"
    items: tuple[ItemResult, ...] = ()
    http_results: tuple[HttpResult, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)
    overall_verdict: str = WARN
    ui_api_ready: bool = False
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    contains_secret: bool = False

    def __post_init__(self) -> None:
        if self.is_live_authorization:
            raise ValueError("UiApiChecklistReport.is_live_authorization must be False")
        if self.broker_order_sent:
            raise ValueError("UiApiChecklistReport.broker_order_sent must be False")
        if self.contains_secret:
            raise ValueError("UiApiChecklistReport.contains_secret must be False")


def _read(repo_root: Path, rel: str) -> str | None:
    p = repo_root / rel
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _card_test_exists(repo_root: Path, card: str) -> bool:
    """카드 단위 vitest(`<Card>.test.jsx`) 가 어딘가에 존재하는지 (invariant lock)."""
    for base in ("frontend/src/components/tabs", "frontend/src/components/common"):
        if (repo_root / base / f"{card}.test.jsx").exists():
            return True
    return False


def run_manifest_checks(repo_root: Path | None = None) -> UiApiChecklistReport:
    """정적(offline) 매니페스트 검증 — 서버/DB/app import 0건."""
    root = repo_root or _REPO_ROOT
    client_src = _read(root, "frontend/src/services/backend/client.js") or ""

    items: list[ItemResult] = []
    for it in CHECKLIST_ITEMS:
        tab_src = _read(root, it.tab_file) or ""
        route_src = _read(root, it.route_file) or ""

        mounted = bool(
            re.search(rf"\b{re.escape(it.card)}\b", tab_src)
            and re.search(rf"<\s*{re.escape(it.card)}\b", tab_src)
        )
        client_wired = (it.client_method in client_src) and (it.client_url in client_src)
        route_present = it.route_path in route_src
        has_test = _card_test_exists(root, it.card)

        ok = mounted and client_wired and route_present
        verdict = PASS if ok else FAIL
        missing = []
        if not mounted:
            missing.append("not-mounted")
        if not client_wired:
            missing.append("client-unwired")
        if not route_present:
            missing.append("route-missing")
        detail = "OK" if ok else ", ".join(missing)
        if ok and not has_test:
            verdict = WARN
            detail = "wired but no card-level test"
        items.append(ItemResult(
            it.name, it.card, it.tab, mounted, client_wired,
            route_present, has_test, verdict, detail))

    counts = {
        PASS: sum(1 for i in items if i.verdict == PASS),
        WARN: sum(1 for i in items if i.verdict == WARN),
        FAIL: sum(1 for i in items if i.verdict == FAIL),
    }
    overall = FAIL if counts[FAIL] else (WARN if counts[WARN] else PASS)
    return UiApiChecklistReport(
        mode="manifest",
        items=tuple(items),
        counts=counts,
        overall_verdict=overall,
        ui_api_ready=(counts[FAIL] == 0),
    )


def http_targets() -> tuple[ChecklistItem, ...]:
    """http smoke 에서 호출 가능한 read-only GET item 만 반환 (POST/주문 제외)."""
    return tuple(it for it in CHECKLIST_ITEMS if it.read_only_http and it.http_method == "GET")


def evaluate_http_results(raw: dict[str, tuple[int, bool]]) -> UiApiChecklistReport:
    """http mode 결과 평가.

    raw: {http_path: (status_code, secret_found)} — CLI 가 GET 호출 후 채운다.
    MARKET_CLOSED / 자격 미설정 등으로 4xx(503 등) 가 나도 FAIL 로 보지 않고 WARN
    (장 닫힘/자격 미설정은 정상). 200 + secret 없음만 PASS. 5xx 또는 secret 발견은 FAIL.
    """
    results: list[HttpResult] = []
    for it in http_targets():
        status, secret_found = raw.get(it.http_path, (0, False))
        if secret_found:
            verdict, detail = FAIL, f"secret pattern in response (status {status})"
        elif status == 200:
            verdict, detail = PASS, "200 OK"
        elif status == 0:
            verdict, detail = WARN, "not called / unreachable"
        elif 400 <= status < 500 or status in (503,):
            verdict, detail = WARN, f"non-200 ({status}) — 자격/장상태 가능"
        else:
            verdict, detail = FAIL, f"server error ({status})"
        results.append(HttpResult(
            it.name, it.http_path, status, status == 200, secret_found, verdict, detail))

    counts = {
        PASS: sum(1 for r in results if r.verdict == PASS),
        WARN: sum(1 for r in results if r.verdict == WARN),
        FAIL: sum(1 for r in results if r.verdict == FAIL),
    }
    overall = FAIL if counts[FAIL] else (WARN if counts[WARN] else PASS)
    return UiApiChecklistReport(
        mode="http",
        http_results=tuple(results),
        counts=counts,
        overall_verdict=overall,
        ui_api_ready=(counts[FAIL] == 0),
    )


def render_markdown(report: UiApiChecklistReport) -> str:
    c = report.counts
    lines = [
        "# FINAL-UI-API-01 — 체크리스트 UI/API 통합 검증 리포트",
        "",
        "> read-only 검증. 실제 KIS API 0건, 주문 0건, 실전 승인 아님, 수익 보장 아님.",
        "",
        f"- mode: **{report.mode}**",
        f"- overall: **{report.overall_verdict}** (PASS={c.get(PASS,0)} / "
        f"WARN={c.get(WARN,0)} / FAIL={c.get(FAIL,0)})",
        f"- ui_api_ready: **{report.ui_api_ready}**",
        f"- is_live_authorization={report.is_live_authorization} / "
        f"broker_order_sent={report.broker_order_sent} / "
        f"contains_secret={report.contains_secret}",
        "",
    ]
    if report.items:
        lines += [
            "## 매니페스트 (카드 ↔ 탭 ↔ client ↔ route)",
            "",
            "| verdict | card | tab | mounted | client | route | test | detail |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for i in report.items:
            lines.append(
                f"| {i.verdict} | `{i.card}` | {i.tab} | "
                f"{'✅' if i.mounted else '❌'} | {'✅' if i.client_wired else '❌'} | "
                f"{'✅' if i.route_present else '❌'} | {'✅' if i.has_card_test else '—'} | "
                f"{i.detail} |")
        lines.append("")
    if report.http_results:
        lines += [
            "## http smoke (read-only GET only)",
            "",
            "| verdict | endpoint | status | secret | detail |",
            "|---|---|---|---|---|",
        ]
        for r in report.http_results:
            lines.append(
                f"| {r.verdict} | `{r.http_path}` | {r.status} | "
                f"{'⚠️FOUND' if r.secret_found else 'none'} | {r.detail} |")
        lines.append("")
    lines += [
        "## 비고",
        "- POST(read-only) endpoint `/api/agents/decision-explanation`, `/decision-quality` "
        "는 http smoke 에서 제외(GET only). 매니페스트/backend 테스트에서 검증.",
        "- 카드별 금지 버튼(매수/매도/실전/승인/Place Order) 부재 + secret 미표시는 "
        "frontend integration/vitest 에서 런타임 검증.",
    ]
    return "\n".join(lines) + "\n"
