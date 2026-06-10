// FINAL-UI-API-01 — 체크리스트 카드 ↔ 탭 ↔ API client 정적 매니페스트 검증 (frontend).
//
// backend `app/system/ui_api_checklist.py` 매니페스트의 frontend 미러. 각 체크리스트
// 카드가 (1) 해당 탭에 import + render 되고, (2) client.js 에 API method + URL 이
// 연결되고, (3) 카드 단위 runtime vitest(`<Card>.test.jsx`) 가 존재(금지 버튼/fallback/
// secret 미표시 invariant lock) 하는지 정적으로 확인한다.
//
// 정적 검사라 렌더 타이밍 flake 가 없다. 금지 버튼 부재 / API 실패 fallback /
// secret 미표시의 *런타임* 검증은 각 카드의 개별 `.test.jsx` 가 담당한다.

import { existsSync, readFileSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";

import { describe, expect, it } from "vitest";

const _here = path.dirname(fileURLToPath(import.meta.url));
const TABS_DIR = _here; // frontend/src/components/tabs
const COMMON_DIR = path.resolve(_here, "../common");
const CLIENT_JS = path.resolve(_here, "../../services/backend/client.js");

// 카드 ↔ 탭파일 ↔ client method ↔ client URL (backend 매니페스트와 동일 집합).
const CHECKLIST = [
  // Dashboard
  ["PortfolioSourceCard", "Dashboard.jsx", "portfolioSource", "/api/auto-paper/portfolio-source"],
  ["AutoPaperLoopCard", "Dashboard.jsx", "autoPaperStatus", "/api/auto-paper/status"],
  ["AgentCouncilVoteCard", "Dashboard.jsx", "agentDecisionEpisodes", "/api/agents/decision-episodes"],
  // E2(2026-06-10): KisPaperOneClickTestCard 홈 개편으로 Dashboard 에서 제외 — 매니페스트에서도 제거(고아 매핑 정리).
  // Settings
  ["KisPaperEnvStatusCard", "Settings.jsx", "kisPaperReadiness", "/api/kis-paper/readiness"],
  ["BackendSidecarStatusCard", "Settings.jsx", "exeStatus", "/api/system/exe-status"],
  ["AppVersionCard", "Settings.jsx", "buildInfo", "/api/system/build-info"],
  ["PreflightSmokeCard", "Settings.jsx", "preflight", "/api/system/preflight"],
  ["RuntimeEventLogViewer", "Settings.jsx", "systemLogs", "/api/system/logs"],
  ["UniverseStatusCard", "Settings.jsx", "universeStatus", "/api/auto-paper/universe-status"],
  ["LiveSafetyStatusCard", "Settings.jsx", "liveSafetyStatus", "/api/status/live-safety"],
  ["ProgramIntegrityGateCard", "Settings.jsx", "programIntegrity", "/api/system/program-integrity"],
  ["PremarketReadinessGateCard", "Settings.jsx", "premarketReadiness", "/api/system/premarket-readiness"],
  ["KisPaperAiAutotradeAuditCard", "Settings.jsx", "kisPaperAutotradeAudit", "/api/system/kis-paper-autotrade-audit"],
  // AISignal
  ["AgentDecisionExplanationCard", "AISignal.jsx", "agentDecisionExplanation", "/api/agents/decision-explanation"],
  ["PerformanceMetricsSummary", "AISignal.jsx", "agentOrderQualityMetrics", "/api/agents/order-quality-metrics"],
  ["DecisionQualityScoreCard", "AISignal.jsx", "agentDecisionQuality", "/api/agents/decision-quality"],
  ["PostTradeFeedbackCard", "AISignal.jsx", "agentFeedbackLoop", "/api/agents/feedback-loop"],
  ["PaperGateReportCard", "AISignal.jsx", "agentPaperGateReport", "/api/agents/paper-gate-report"],
];

function readTab(tabFile) {
  return readFileSync(path.join(TABS_DIR, tabFile), "utf-8");
}

function cardTestExists(card) {
  return (
    existsSync(path.join(TABS_DIR, `${card}.test.jsx`)) ||
    existsSync(path.join(COMMON_DIR, `${card}.test.jsx`))
  );
}

describe("FINAL-UI-API-01: checklist card ↔ tab ↔ client manifest", () => {
  const clientSrc = readFileSync(CLIENT_JS, "utf-8");

  it("covers 18 checklist cards across Dashboard / AISignal / Settings", () => {
    // E2(2026-06-10): KisPaperOneClickTestCard 제거로 19→18.
    expect(CHECKLIST.length).toBe(18);
    const tabs = new Set(CHECKLIST.map((c) => c[1]));
    expect(tabs).toEqual(new Set(["Dashboard.jsx", "AISignal.jsx", "Settings.jsx"]));
  });

  it.each(CHECKLIST)(
    "%s is imported and rendered in its tab",
    (card, tabFile) => {
      const src = readTab(tabFile);
      // import 존재 (import { Card } ... 또는 import Card ...).
      expect(src.includes(card)).toBe(true);
      // JSX 로 렌더 (<Card ...).
      expect(new RegExp(`<\\s*${card}\\b`).test(src)).toBe(true);
    },
  );

  it.each(CHECKLIST)(
    "%s API is wired in client.js (method + url)",
    (card, _tabFile, method, url) => {
      expect(clientSrc.includes(method)).toBe(true);
      expect(clientSrc.includes(url)).toBe(true);
    },
  );

  it.each(CHECKLIST)(
    "%s has a card-level runtime test (invariant lock)",
    (card) => {
      expect(cardTestExists(card)).toBe(true);
    },
  );
});

describe("FINAL-UI-API-01: client.js read-only endpoints carry no order verbs", () => {
  const clientSrc = readFileSync(CLIENT_JS, "utf-8");

  it("checklist URLs are GET-style reads, not order endpoints", () => {
    // 체크리스트 endpoint URL 에 주문/실전 동사가 박혀 있지 않다.
    for (const [, , , url] of CHECKLIST) {
      expect(url).not.toMatch(/place-order|live-order|enable-live|\/orders\b/i);
    }
    // client.js 가 존재하고 backendFetch 래퍼를 쓴다 (단일 fetch wrapper).
    expect(clientSrc.includes("backendFetch")).toBe(true);
  });
});
