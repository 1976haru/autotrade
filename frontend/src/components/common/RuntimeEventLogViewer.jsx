/**
 * #56 / 7-04 — 통합 오류/이벤트 로그 뷰어 (Settings 탭, read-only).
 *
 * RuntimeEvent / AgentDecision(AI 판단) / KIS 주문 이벤트를 한 화면에서 최근
 * 100건 확인. source / severity 필터 + keyword 검색 + 복사. backend 가 free-text
 * 를 마스킹하므로 secret/계좌 원문은 표시되지 않으며, 복사 직전 client 측에서도
 * 2차 secret 스캔(containsSecretDeep)으로 방어한다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수 / 매도 / 실거래 / Place Order / 주문 재시도 / 재전송 버튼 0개.
 *  - 주문을 발생시키는 API 호출 0건 (read-only systemLogs GET 만).
 *  - Secret / API key / 계좌번호 / access_token 원문 표시 0건.
 *  - 입력 form 은 검색/필터 외 주문 관련 0개.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";
import { containsSecretDeep } from "./OperatorDiagnosticsCard";

const SOURCES = ["ALL", "RUNTIME_EVENT", "AGENT_DECISION", "KIS_ORDER"];
const SEVERITIES = ["ALL", "INFO", "WARN", "ERROR", "CRITICAL"];

const _SEV_TONE = {
  DEBUG: { fg: "#475569", bg: "#f1f5f9" },
  INFO: { fg: "#1e3a8a", bg: "#eff6ff" },
  WARN: { fg: "#92400e", bg: "#fef3c7" },
  ERROR: { fg: "#b91c1c", bg: "#fef2f2" },
  CRITICAL: { fg: "#7f1d1d", bg: "#fee2e2" },
};

function _fmtLine(e) {
  // 복사용 한 줄 — sanitize 된 필드만 (backend 마스킹 + whitelist).
  const parts = [
    e.timestamp ? `[${e.timestamp}]` : "",
    e.source, e.severity,
    e.symbol || "", e.action || "", e.reason_code || "",
    e.broker_order_no ? `order=${e.broker_order_no}` : "",
    e.episode_id ? `ep=${e.episode_id}` : "",
    "-", e.message || "",
  ];
  return parts.filter(Boolean).join(" ");
}

export function RuntimeEventLogViewer({
  apiClient = backendApi,
  testId = "runtime-event-log-viewer",
  clipboard = (typeof navigator !== "undefined" && navigator.clipboard) || null,
} = {}) {
  const [logs, setLogs] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [source, setSource] = useState("ALL");
  const [severity, setSeverity] = useState("ALL");
  const [q, setQ] = useState("");
  const [copyState, setCopyState] = useState("idle");

  const refresh = useCallback(async () => {
    if (typeof apiClient.systemLogs !== "function") { setLoading(false); return; }
    setLoading(true);
    try {
      const r = await apiClient.systemLogs({ source, severity, q: q || null, limit: 100 });
      setLogs(Array.isArray(r?.logs) ? r.logs : []);
      setError(null);
    } catch (err) {
      setLogs(null);
      setError(err?.message || String(err));
    } finally {
      setLoading(false);
    }
  }, [apiClient, source, severity, q]);

  useEffect(() => { refresh(); }, [refresh]);

  const rows = useMemo(() => (Array.isArray(logs) ? logs : []), [logs]);

  const onCopy = useCallback(async () => {
    const payload = {
      generated_at: new Date().toISOString(),
      note: "운영 로그 — 민감정보는 자동으로 가려집니다. 조회 전용입니다.",
      logs: rows.map(_fmtLine),
    };
    // 복사 직전 2차 secret 스캔 — 적중 시 클립보드에 *쓰지 않는다*.
    if (containsSecretDeep(payload)) {
      setCopyState("blocked");
      return;
    }
    try {
      if (clipboard && typeof clipboard.writeText === "function") {
        await clipboard.writeText(payload.logs.join("\n"));
        setCopyState("copied");
      } else {
        setCopyState("nocopy");
      }
    } catch {
      setCopyState("error");
    }
  }, [rows, clipboard]);

  return (
    <div data-testid={testId}>
      <Card>
        <SectionLabel>📜 최근 이벤트 로그</SectionLabel>

        <div data-testid="log-viewer-note" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          최근 100건 · 민감정보는 자동으로 가려집니다. 조회 전용입니다.
        </div>

        {/* source 필터 */}
        <div data-testid="log-viewer-source-filters" style={{
          display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6,
        }}>
          {SOURCES.map((s) => (
            <button key={s} type="button"
              data-testid={`log-source-${s}`} data-active={String(source === s)}
              onClick={() => setSource(s)}
              style={{
                padding: "2px 8px", borderRadius: 4, fontSize: 11,
                border: "1px solid var(--c-border)",
                background: source === s ? "#1e3a8a" : "var(--c-surface-2)",
                color: source === s ? "#fff" : "var(--c-text-2)",
                cursor: "pointer",
              }}>{s}</button>
          ))}
        </div>

        {/* severity 필터 */}
        <div data-testid="log-viewer-severity-filters" style={{
          display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6,
        }}>
          {SEVERITIES.map((s) => (
            <button key={s} type="button"
              data-testid={`log-severity-${s}`} data-active={String(severity === s)}
              onClick={() => setSeverity(s)}
              style={{
                padding: "2px 8px", borderRadius: 4, fontSize: 11,
                border: "1px solid var(--c-border)",
                background: severity === s ? "#92400e" : "var(--c-surface-2)",
                color: severity === s ? "#fff" : "var(--c-text-2)",
                cursor: "pointer",
              }}>{s}</button>
          ))}
        </div>

        {/* 검색 + 새로고침 + 복사 */}
        <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
          <input
            data-testid="log-viewer-search"
            type="text" value={q} placeholder="사유 코드 / 메시지 검색"
            onChange={(e) => setQ(e.target.value)}
            style={{
              flex: 1, padding: "3px 8px", fontSize: 12, borderRadius: 4,
              border: "1px solid var(--c-border)", background: "var(--c-surface)",
              color: "var(--c-text-1)",
            }}
          />
          <button type="button" data-testid="log-viewer-refresh-btn"
            onClick={refresh} disabled={loading}
            style={{
              padding: "3px 10px", borderRadius: 4, fontSize: 12,
              border: "1px solid var(--c-border)", cursor: loading ? "wait" : "pointer",
              background: "var(--c-surface-2)", color: "var(--c-text-1)",
            }}>새로고침</button>
          <button type="button" data-testid="log-viewer-copy-btn"
            onClick={onCopy}
            style={{
              padding: "3px 10px", borderRadius: 4, fontSize: 12,
              border: "1px solid var(--c-border)", cursor: "pointer",
              background: "var(--c-surface-2)", color: "var(--c-text-1)",
            }}>복사</button>
        </div>

        {copyState === "copied" ? (
          <div data-testid="log-viewer-copy-ok" style={{
            fontSize: "var(--fs-xs)", color: "#166534", marginBottom: 6,
          }}>복사되었습니다 (민감정보 제외).</div>
        ) : null}
        {copyState === "blocked" ? (
          <div data-testid="log-viewer-copy-blocked" style={{
            fontSize: "var(--fs-xs)", color: "#b91c1c", marginBottom: 6,
          }}>민감정보가 감지되어 복사를 차단했습니다.</div>
        ) : null}

        {loading && logs == null && !error ? (
          <div data-testid="log-viewer-loading" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          }}>로그 불러오는 중…</div>
        ) : null}

        {error ? (
          <div data-testid="log-viewer-error" style={{
            padding: "6px 10px", borderRadius: 6, background: "#fef2f2",
            color: "#b91c1c", fontSize: "var(--fs-xs)",
          }}>로그를 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {logs != null && !error && rows.length === 0 ? (
          <div data-testid="log-viewer-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)", padding: "8px 0",
          }}>표시할 로그가 없습니다.</div>
        ) : null}

        {rows.length > 0 ? (
          <div data-testid="log-viewer-rows" style={{
            maxHeight: 360, overflowY: "auto",
          }}>
            {rows.map((e, i) => {
              const tone = _SEV_TONE[e.severity] || _SEV_TONE.INFO;
              return (
                <div key={i} data-testid={`log-row-${i}`}
                  data-source={e.source} data-severity={e.severity}
                  style={{
                    padding: "5px 8px", borderRadius: 4, marginBottom: 3,
                    background: tone.bg, fontSize: "var(--fs-xs)",
                  }}>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                    <span style={{
                      fontWeight: 700, color: tone.fg, fontSize: 10,
                    }}>{e.severity}</span>
                    <span style={{ color: "var(--c-text-3)", fontSize: 10 }}>{e.source}</span>
                    {e.symbol ? <span style={{ color: "var(--c-text-2)" }}>{e.symbol}</span> : null}
                    {e.action ? <span style={{ color: "#1e3a8a" }}>{e.action}</span> : null}
                    {e.reason_code ? (
                      <span data-testid={`log-reason-${i}`} style={{
                        color: "#475569", fontFamily: "var(--font-mono, monospace)",
                      }}>{e.reason_code}</span>
                    ) : null}
                    {e.broker_order_no ? (
                      <span data-testid={`log-order-no-${i}`} style={{ color: "#475569" }}>
                        주문번호 {e.broker_order_no}
                      </span>
                    ) : null}
                  </div>
                  <div style={{ color: "var(--c-text-2)", marginTop: 2 }}>{e.message}</div>
                  {e.timestamp ? (
                    <div style={{ color: "var(--c-text-3)", fontSize: 10, marginTop: 1 }}>
                      {e.timestamp}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : null}

        <div data-testid="log-viewer-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 화면은 read-only 운영 로그입니다. 조회 전용이며 어떤 거래도 실행하지
          않습니다. API key / Secret / 계좌번호는 표시되지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default RuntimeEventLogViewer;
