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
  aiAgentDecisions: (limit = 50, chainId = null) => {
    const q = chainId ? `chain_id=${encodeURIComponent(chainId)}` : `limit=${limit}`;
    return backendFetch(`/api/ai/agent-decisions?${q}`);
  },
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
};
