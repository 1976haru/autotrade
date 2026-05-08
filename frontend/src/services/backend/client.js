const API_BASE_URL = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8000";

// Format the FastAPI HTTPException detail into something an operator can read
// without having to mentally parse JSON. Structured server errors get
// dedicated wording; everything else falls back to JSON stringify so we don't
// accidentally swallow useful diagnostic data.
export function formatBackendErrorDetail(detail) {
  if (detail == null) return null;
  if (typeof detail === "string") return detail;
  if (detail.error === "risk_check_failed_at_approve" && Array.isArray(detail.reasons)) {
    // 070: re-eval at approve time blocked execution. Keep the operator-facing
    // wording focused on the cause, not the internal error code.
    return `승인 시점 재평가에서 거부됨: ${detail.reasons.join(" / ")}`;
  }
  if (Array.isArray(detail.reasons)) {
    return detail.reasons.join(" / ");
  }
  return JSON.stringify(detail);
}

export async function backendFetch(path, options = {}) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let body = null;
    try { body = await res.json(); } catch { /* non-JSON error body */ }
    // FastAPI wraps errors in {detail: ...}. Fall back to the body itself if
    // a different layer (proxy, middleware) returned a flat object.
    const detail = body?.detail ?? body;
    const message = formatBackendErrorDetail(detail) || `Backend API error: ${res.status}`;
    const err = new Error(message);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

export const backendApi = {
  getStatus: () => backendFetch("/api/status"),
  getRiskPolicy: () => backendFetch("/api/risk/policy"),
  setEmergencyStop: (enabled, decision) => backendFetch("/api/risk/emergency-stop", {
    method: "POST",
    body: JSON.stringify({ enabled, ...(decision || {}) }),
  }),
  emergencyStopHistory: ({ limit = 50, offset = 0 } = {}) => {
    const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return backendFetch(`/api/risk/emergency-stop/history?${qs.toString()}`);
  },
  emergencyStopSummary: () => backendFetch("/api/risk/emergency-stop/summary"),
  // 37: 3-level Kill Switch — read-only status + candidate endpoints.
  emergencyStopStatus: () => backendFetch("/api/risk/emergency-stop/status"),
  emergencyStopCancelCandidates: () =>
    backendFetch("/api/risk/emergency-stop/cancel-candidates"),
  emergencyStopLiquidationCandidates: () =>
    backendFetch("/api/risk/emergency-stop/liquidation-candidates"),
  // 39: AI Permission Gate — read-only status surface.
  aiPermissionStatus: () => backendFetch("/api/risk/ai-permission/status"),
  // 42: Paper Trading status — read-only.
  paperStatus:        () => backendFetch("/api/paper/status"),
  brokerPrice:     (symbol) => backendFetch(`/api/broker/price/${symbol}`),
  brokerBalance:   () => backendFetch("/api/broker/balance"),
  brokerPositions: () => backendFetch("/api/broker/positions"),
  brokerOrder:     (order) => backendFetch("/api/broker/orders", {
    method: "POST",
    body: JSON.stringify(order),
  }),
  listApprovals: () => backendFetch("/api/approvals"),
  approveApproval: (id, decision) => backendFetch(`/api/approvals/${id}/approve`, {
    method: "POST",
    body: JSON.stringify(decision || {}),
  }),
  rejectApproval: (id, decision) => backendFetch(`/api/approvals/${id}/reject`, {
    method: "POST",
    body: JSON.stringify(decision || {}),
  }),
  cancelApproval: (id, decision) => backendFetch(`/api/approvals/${id}/cancel`, {
    method: "POST",
    body: JSON.stringify(decision || {}),
  }),
  listApprovalHistory: ({ limit = 50, offset = 0, status } = {}) => {
    const qs = new URLSearchParams();
    qs.set("limit",  String(limit));
    qs.set("offset", String(offset));
    if (status) qs.set("status", status);
    return backendFetch(`/api/approvals/history?${qs.toString()}`);
  },
  runBacktest: (req) => backendFetch("/api/backtest/run", {
    method: "POST",
    body: JSON.stringify(req),
  }),
  compareBacktests: (req) => backendFetch("/api/backtest/compare", {
    method: "POST",
    body: JSON.stringify(req),
  }),
  getBacktestRun: (id) => backendFetch(`/api/backtest/runs/${id}`),
  marketBars: (params) => {
    const qs = new URLSearchParams(params).toString();
    return backendFetch(`/api/market/bars?${qs}`);
  },
  listOrderAudits: ({ limit = 50, offset = 0, include_archived = false } = {}) => {
    const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (include_archived) qs.set("include_archived", "true");
    return backendFetch(`/api/audit/orders?${qs.toString()}`);
  },
  listAiAudits:    (limit = 50) => backendFetch(`/api/audit/ai?limit=${limit}`),
  listBacktestRuns:(limit = 50) => backendFetch(`/api/audit/backtests?limit=${limit}`),
  // 33: Signal Explainability — read-only audit row 분석. PASS/WARN/FAIL/BLOCKED/
  // INFO grouped reasons + summary + final_status를 반환.
  explainSignal: (auditId) => backendFetch(`/api/signals/${auditId}/explain`),
  engineRegistry:  () => backendFetch("/api/strategies/registry"),
  engineStatus:    () => backendFetch("/api/strategies/status"),
  engineScoreboard: () => backendFetch("/api/strategies/scoreboard"),
  engineConfigure: (req) => backendFetch("/api/strategies/configure", {
    method: "POST",
    body:   JSON.stringify(req),
  }),
  engineTick:      (req) => backendFetch("/api/strategies/tick", {
    method: "POST",
    body:   JSON.stringify(req),
  }),
  engineReset:     () => backendFetch("/api/strategies/reset", { method: "POST" }),
  engineReplay:    (req) => backendFetch("/api/strategies/replay", {
    method: "POST",
    body:   JSON.stringify(req),
  }),
  // 187: Agent Council surface.
  aiAgentStats:    (lookbackDays = 7) =>
    backendFetch(`/api/ai/agent-stats?lookback_days=${lookbackDays}`),
  aiAgentDecisions: (limit = 50, chainId = null, opts = {}) => {
    const qs = new URLSearchParams();
    if (chainId) qs.set("chain_id", chainId);
    else qs.set("limit", String(limit));
    if (opts.agent_name) qs.set("agent_name", opts.agent_name);
    if (opts.decision)   qs.set("decision",   opts.decision);
    return backendFetch(`/api/ai/agent-decisions?${qs.toString()}`);
  },
  aiAgentDecisionsSummary: (lookbackDays = 0) =>
    backendFetch(`/api/ai/agent-decisions/summary?lookback_days=${lookbackDays}`),
  // 193: Virtual order ledger surface.
  virtualOrders: ({ limit = 50, offset = 0, status = null, symbol = null } = {}) => {
    const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) qs.set("status", status);
    if (symbol) qs.set("symbol", symbol);
    return backendFetch(`/api/virtual/orders?${qs.toString()}`);
  },
  virtualOrdersSummary: () => backendFetch("/api/virtual/orders/summary"),
  virtualPositions: ({ lastPrices = null } = {}) => {
    const qs = new URLSearchParams();
    if (lastPrices) qs.set("last_prices", lastPrices);
    const tail = qs.toString();
    return backendFetch(`/api/virtual/positions${tail ? `?${tail}` : ""}`);
  },
  // 194: Futures order audit surface (read-only).
  futuresOrders: ({ limit = 50, offset = 0, contract = null,
                    decision = null, forced = null } = {}) => {
    const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (contract)        qs.set("contract", contract);
    if (decision)        qs.set("decision", decision);
    if (forced != null)  qs.set("forced",   String(forced));
    return backendFetch(`/api/futures/orders?${qs.toString()}`);
  },
  futuresOrdersSummary: () => backendFetch("/api/futures/orders/summary"),
  // 212: Position vs broker reconciliation status.
  reconciliationStatus: () => backendFetch("/api/reconciliation/status"),
  // 223: Agent Operating Loop — 모든 라우트가 deterministic stub이라 AI Key
  // 미설정 환경에서도 mock output 반환.
  operatingLoopStatus: () => backendFetch("/api/agents/operating-loop/status"),
  preMarketBrief: (req) => backendFetch("/api/agents/pre-market-brief", {
    method: "POST",
    body: JSON.stringify(req || {}),
  }),
  marketOpenWatch: (req) => backendFetch("/api/agents/market-open-watch", {
    method: "POST",
    body: JSON.stringify(req || {}),
  }),
  intradaySummary: (req) => backendFetch("/api/agents/intraday-summary", {
    method: "POST",
    body: JSON.stringify(req || {}),
  }),
  positionMonitor: (req) => backendFetch("/api/agents/position-monitor", {
    method: "POST",
    body: JSON.stringify(req || {}),
  }),
  postMarketReview: (req) => backendFetch("/api/agents/post-market-review", {
    method: "POST",
    body: JSON.stringify(req || {}),
  }),
  // 225: Market Regime Filter — 10가지 regime 분류 + 전략 허용/차단 + risk
  // 곱셈 계수. 입력은 정량 지표만, deterministic.
  marketRegime: (req) => backendFetch("/api/agents/market-regime", {
    method: "POST",
    body: JSON.stringify(req || {}),
  }),
  // 226: Signal Quality Gate — agent-aware scoring. 입력은 항목별 0-100 점수,
  // 출력은 quality_score / grade / approval_recommendation / breakdown.
  signalQuality: (req) => backendFetch("/api/agents/signal-quality", {
    method: "POST",
    body: JSON.stringify(req || {}),
  }),
  // 18: Watchlist — universe 후보군 (주문 신호 아님).
  listWatchlists:    () => backendFetch("/api/watchlists"),
  watchlistSummary:  () => backendFetch("/api/watchlists/summary"),
  getWatchlist:      (id) => backendFetch(`/api/watchlists/${id}`),
  createWatchlist:   (req) => backendFetch("/api/watchlists", {
    method: "POST",
    body: JSON.stringify(req),
  }),
  patchWatchlist:    (id, req) => backendFetch(`/api/watchlists/${id}`, {
    method: "PATCH",
    body: JSON.stringify(req),
  }),
  deleteWatchlist:   (id) => backendFetch(`/api/watchlists/${id}`, { method: "DELETE" }),
  addWatchlistItem:  (id, req) => backendFetch(`/api/watchlists/${id}/items`, {
    method: "POST",
    body: JSON.stringify(req),
  }),
  removeWatchlistItem: (id, itemId) =>
    backendFetch(`/api/watchlists/${id}/items/${itemId}`, { method: "DELETE" }),
  importWatchlistCsv: (id, csv) => backendFetch(`/api/watchlists/${id}/import-csv`, {
    method: "POST",
    body: JSON.stringify({ csv }),
  }),
  // 26: Monte Carlo risk simulation — read-only 분석.
  monteCarlo: (req) => backendFetch("/api/backtest/monte-carlo", {
    method: "POST",
    body: JSON.stringify(req),
  }),
  // 27: Strategy Promotion Gate — read-only 평가.
  evaluatePromotion: (req) => backendFetch("/api/governance/strategy-promotion/evaluate", {
    method: "POST",
    body: JSON.stringify(req),
  }),
  // 22: Theme signals — 후보 필터 전용 (주문 신호 아님).
  themeSignals: ({ limit = 50, grade = null, provider = null } = {}) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (grade)    qs.set("grade", grade);
    if (provider) qs.set("provider", provider);
    return backendFetch(`/api/themes/signals?${qs.toString()}`);
  },
  themesSummary: () => backendFetch("/api/themes/summary"),
  themesScan:    ({ universe = null, limit = 20 } = {}) =>
    backendFetch("/api/themes/scan", {
      method: "POST",
      body: JSON.stringify({ universe, limit }),
    }),
};
