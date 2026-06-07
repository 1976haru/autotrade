import { useCallback, useEffect, useState } from "react";

import { backendApi } from "../../services/backend/client";

// V8: 출발 전 점검 — 시작 버튼 근처 컴팩트 표시. 4상태(로딩/실패/OK/FAIL) 정직 표시.
//   로드 시 + 수동 새로고침(폴링 0).
const _dot = (status) => (status === "ok" ? "#15a05f" : status === "warn" ? "#f5a623" : "#c0392b");
const _hm = () => new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });

export function PreflightPanel({ api = backendApi }) {
  const [data, setData] = useState(null);   // null=로딩
  const [failed, setFailed] = useState(false);
  const [failedAt, setFailedAt] = useState(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      const d = await api.preflight();
      // ★응답 정합 검증 — items 배열 + all_ok boolean 이 아니면 *비정상 응답*
      //   (프록시 누락으로 SPA HTML/빈 객체가 올 때 등). "0개"로 둔갑 금지 → 실패 처리.
      if (!d || !Array.isArray(d.items) || typeof d.all_ok !== "boolean") {
        setFailed(true); setFailedAt(_hm()); setData(null);
      } else {
        setData(d);
      }
    } catch {
      setFailed(true);
      setFailedAt(_hm());
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  // 엄격 분기: all_ok===true 만 OK, all_ok===false 만 FAIL(항목≥1). 그 외(비정상)는 실패.
  const ready = data?.all_ok === true;
  const problems = ready ? [] : (data?.items || []).filter((i) => i.status !== "ok");

  return (
    <div data-testid="preflight-panel" style={{
      background: "rgba(255,255,255,.5)", borderRadius: 12, padding: "10px 14px", marginBottom: 10,
      border: `1px solid ${ready ? "rgba(21,160,95,.3)" : "rgba(40,20,24,.14)"}`,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <button type="button" data-testid="preflight-toggle" onClick={() => setOpen((o) => !o)}
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 13.5, fontWeight: 800, color: "#2a1418" }}>
          {failed ? (
            <span data-testid="preflight-fail" style={{ color: "#7a1d1d" }}>점검 정보를 불러오지 못했어요({failedAt}) — 재시도 ↻</span>
          ) : !data ? (
            <span data-testid="preflight-loading">출발 전 점검 — 확인 중…</span>
          ) : ready ? (
            <span data-testid="preflight-ready" style={{ color: "#0f5132" }}>✅ 출발 준비 완료</span>
          ) : (
            <span data-testid="preflight-issues" style={{ color: "#7a4a00" }}>⚠ 확인이 필요한 항목 {problems.length}개 {open ? "▴" : "▾"}</span>
          )}
        </button>
        <button type="button" data-testid="preflight-refresh" onClick={load} disabled={loading}
          style={{ fontSize: 12, border: "none", background: "transparent", cursor: "pointer", color: "rgba(40,20,24,.6)" }}>↻</button>
      </div>

      {data && (open || !ready) && (
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
