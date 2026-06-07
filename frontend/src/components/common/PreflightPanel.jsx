import { useCallback, useEffect, useState } from "react";

import { backendApi } from "../../services/backend/client";

// V8: 출발 전 점검 — 시작 버튼 근처 컴팩트 표시. 전부 OK면 "출발 준비 완료 ✓",
//   하나라도 FAIL/WARN이면 해당 항목 한국어 안내. 로드 시 + 수동 새로고침(폴링 0).
const _dot = (status) => (status === "ok" ? "#15a05f" : status === "warn" ? "#f5a623" : "#c0392b");

export function PreflightPanel({ api = backendApi }) {
  const [data, setData] = useState(null);   // null=로딩
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      setData(await api.preflight());
    } catch {
      setFailed(true);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const allOk = data?.all_ok;
  const problems = (data?.items || []).filter((i) => i.status !== "ok");

  return (
    <div data-testid="preflight-panel" style={{
      background: "rgba(255,255,255,.5)", borderRadius: 12, padding: "10px 14px", marginBottom: 10,
      border: `1px solid ${allOk ? "rgba(21,160,95,.3)" : "rgba(40,20,24,.14)"}`,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <button type="button" data-testid="preflight-toggle" onClick={() => setOpen((o) => !o)}
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 13.5, fontWeight: 800, color: "#2a1418" }}>
          {failed ? (
            <span data-testid="preflight-fail">출발 전 점검 — 불러오기 실패</span>
          ) : !data ? (
            <span data-testid="preflight-loading">출발 전 점검 — 확인 중…</span>
          ) : allOk ? (
            <span data-testid="preflight-ready" style={{ color: "#0f5132" }}>✅ 출발 준비 완료</span>
          ) : (
            <span data-testid="preflight-issues" style={{ color: "#7a4a00" }}>⚠ 확인이 필요한 항목 {problems.length}개 {open ? "▴" : "▾"}</span>
          )}
        </button>
        <button type="button" data-testid="preflight-refresh" onClick={load} disabled={loading}
          style={{ fontSize: 12, border: "none", background: "transparent", cursor: "pointer", color: "rgba(40,20,24,.6)" }}>↻</button>
      </div>

      {data && (open || !allOk) && (
        <div style={{ marginTop: 8 }}>
          {(data.items || []).map((i) => (
            <div key={i.key} data-testid={`preflight-item-${i.key}`} style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0", fontSize: 12.5 }}>
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: _dot(i.status), flex: "none" }} />
              <span style={{ width: 96, fontWeight: 700, color: "#2a1418" }}>{i.label}</span>
              <span style={{ color: "rgba(40,20,24,.7)" }}>{i.detail}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
