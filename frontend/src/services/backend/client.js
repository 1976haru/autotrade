const API_BASE_URL = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8000";

export async function backendFetch(path, options = {}) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = null;
    try { detail = await res.json(); } catch { /* ignore */ }
    throw new Error(detail ? JSON.stringify(detail) : `Backend API error: ${res.status}`);
  }
  return res.json();
}

export const backendApi = {
  getStatus: () => backendFetch("/api/status"),
  getRiskPolicy: () => backendFetch("/api/risk/policy"),
  setEmergencyStop: (enabled) => backendFetch("/api/risk/emergency-stop", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  }),
  mockPrice: (symbol) => backendFetch(`/api/broker/mock/price/${symbol}`),
  mockBalance: () => backendFetch("/api/broker/mock/balance"),
  mockPositions: () => backendFetch("/api/broker/mock/positions"),
  mockOrder: (order) => backendFetch("/api/broker/mock/orders", {
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
  runBacktest: (req) => backendFetch("/api/backtest/run", {
    method: "POST",
    body: JSON.stringify(req),
  }),
  getBacktestRun: (id) => backendFetch(`/api/backtest/runs/${id}`),
  marketBars: (params) => {
    const qs = new URLSearchParams(params).toString();
    return backendFetch(`/api/market/bars?${qs}`);
  },
  listOrderAudits: (limit = 50) => backendFetch(`/api/audit/orders?limit=${limit}`),
  listAiAudits:    (limit = 50) => backendFetch(`/api/audit/ai?limit=${limit}`),
  listBacktestRuns:(limit = 50) => backendFetch(`/api/audit/backtests?limit=${limit}`),
};
