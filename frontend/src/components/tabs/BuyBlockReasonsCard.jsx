/**
 * P-17: 오늘 매수 불가 사유 카드 — Agent 탭에 노출.
 *
 * AI Paper / Agent / AutoPaperLoop 가 BUY 후보를 검토했지만 매수하지 *못한*
 * 이유를 사람이 이해하기 쉬운 한국어로 표시한다. **표시/진단 전용** —
 * 실제 주문 버튼 / 실거래 토글 / API key 입력 0개 (테스트로 lock).
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";
import { friendlyErrorMessage } from "../../utils/errorMessage";
import {
  buyBlockSeverityColor,
  formatBuyBlockReason,
} from "../../utils/buyBlockReasons";


function _SeverityChip({ severity }) {
  const color = buyBlockSeverityColor(severity);
  return (
    <span
      data-testid="buy-block-severity-chip"
      style={{
        display: "inline-block", width: 8, height: 8, borderRadius: 999,
        background: color, marginRight: 6, flexShrink: 0,
      }}
    />
  );
}


function _shortTime(ts) {
  if (!ts) return "";
  // ISO8601 → HH:MM (로컬). 파싱 실패 시 원본 일부.
  try {
    const d = new Date(ts);
    if (!Number.isNaN(d.getTime())) {
      return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
    }
  } catch { /* ignore */ }
  return String(ts).slice(11, 16);
}


export function BuyBlockReasonsCard({
  apiClient = backendApi,
  testId = "buy-block-reasons-card",
  limit = 10,
} = {}) {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (typeof apiClient.autoPaperBlockedReasonsToday !== "function") return;
    setLoading(true);
    try {
      const r = await apiClient.autoPaperBlockedReasonsToday({ limit });
      setSummary(r || null);
      setError("");
    } catch (e) {
      setError(friendlyErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [apiClient, limit]);

  useEffect(() => { refresh(); }, [refresh]);

  const total = summary?.total_blocked ?? 0;
  const byReason = summary?.by_reason ?? {};
  const recent = Array.isArray(summary?.recent) ? summary.recent : [];

  // by_reason 을 건수 내림차순으로.
  const byReasonRows = Object.entries(byReason)
    .map(([code, count]) => ({ ...formatBuyBlockReason(code), count }))
    .sort((a, b) => b.count - a.count);

  return (
    <Card accentColor="#ef444433">
      <div data-testid={testId}>
        <SectionLabel>🚫 오늘 매수 불가 사유</SectionLabel>

        <div
          data-testid="buy-block-reasons-badges"
          style={{ marginBottom: 10, display: "flex", flexWrap: "wrap", gap: 4 }}
        >
          <span
            data-testid="badge-display-only"
            style={{
              padding: "3px 8px", borderRadius: 4,
              fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
              background: "#6b7280", color: "#fff",
            }}
          >
            표시 전용 · 주문 기능 아님
          </span>
          <span
            data-testid="badge-paper-only"
            style={{
              padding: "3px 8px", borderRadius: 4,
              fontSize: "var(--fs-xs)",
              background: "#1e3a8a", color: "#fff",
            }}
          >
            Paper / AI Paper 기준
          </span>
        </div>

        <div
          data-testid="buy-block-intro"
          style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            marginBottom: 10, lineHeight: 1.6,
          }}
        >
          AI 가 매수 후보를 검토했지만 자금 / 가격 / 위험 / 시장 조건 때문에
          매수하지 *않은* 사유입니다.
        </div>

        {error && (
          <div
            data-testid="buy-block-error"
            style={{
              padding: "8px 10px", marginBottom: 8, borderRadius: 4,
              background: "#fef2f2", border: "1px solid #fecaca",
              color: "#7f1d1d", fontSize: "var(--fs-xs)",
            }}
          >
            {error}
          </div>
        )}

        {/* 총 차단 건수 */}
        <div
          data-testid="buy-block-total"
          style={{
            fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)",
            color: "var(--c-text)", marginBottom: 8,
          }}
        >
          오늘 매수 차단 {total}건
        </div>

        {total === 0 ? (
          <div
            data-testid="buy-block-empty"
            style={{
              padding: "10px", borderRadius: 4, background: "var(--c-surface-2)",
              color: "var(--c-text-3)", fontSize: "var(--fs-xs)",
            }}
          >
            {loading ? "불러오는 중…" : "아직 기록된 매수 불가 사유가 없습니다."}
          </div>
        ) : (
          <>
            {/* reason_code 별 요약 */}
            <div data-testid="buy-block-by-reason" style={{ marginBottom: 10 }}>
              {byReasonRows.map((row) => (
                <div
                  key={row.code}
                  data-testid={`buy-block-reason-${row.code}`}
                  style={{
                    display: "flex", alignItems: "center",
                    fontSize: "var(--fs-xs)", marginBottom: 3,
                  }}
                >
                  <_SeverityChip severity={row.severity} />
                  <span style={{ flex: 1, color: "var(--c-text)" }}>{row.title}</span>
                  <span style={{ color: "var(--c-text-3)", fontWeight: "var(--fw-bold)" }}>
                    {row.count}건
                  </span>
                </div>
              ))}
            </div>

            {/* 최근 차단 사유 */}
            <div
              style={{
                fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
                color: "var(--c-text-3)", marginBottom: 4,
              }}
            >
              최근 차단 내역
            </div>
            <div data-testid="buy-block-recent">
              {recent.map((rec, i) => {
                const f = formatBuyBlockReason(rec);
                const sym = rec.symbol || "—";
                const t = _shortTime(rec.timestamp);
                return (
                  <div
                    key={rec.event_id || `${sym}-${i}`}
                    data-testid="buy-block-recent-row"
                    style={{
                      display: "flex", alignItems: "flex-start",
                      padding: "6px 0", borderTop: i === 0 ? "none" : "1px solid var(--c-border)",
                      fontSize: "var(--fs-xs)",
                    }}
                  >
                    <_SeverityChip severity={f.severity} />
                    <div style={{ flex: 1 }}>
                      <div style={{ color: "var(--c-text)" }}>
                        <span style={{ fontWeight: "var(--fw-bold)" }}>{sym}</span>
                        {t ? <span style={{ color: "var(--c-text-3)" }}> · {t}</span> : null}
                        {" · "}{f.title}
                      </div>
                      {f.detail ? (
                        <div style={{ color: "var(--c-text-3)", marginTop: 1 }}>
                          {f.detail}
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}

        <div
          data-testid="buy-block-footer"
          style={{
            marginTop: 10, fontSize: "var(--fs-xs)",
            color: "var(--c-text-3)", lineHeight: 1.6,
          }}
        >
          본 카드는 매수하지 않은 이유를 설명하는 표시/진단용입니다. 실제 주문
          버튼을 제공하지 않으며 RiskManager / PermissionGate 는 계속 적용됩니다.
        </div>
      </div>
    </Card>
  );
}

export default BuyBlockReasonsCard;
