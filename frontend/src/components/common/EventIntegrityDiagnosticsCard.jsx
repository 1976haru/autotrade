/**
 * P-32: 이벤트 로그 품질 점검 카드 — Agent 탭, read-only.
 *
 * decision episode 의 판단→주문→체결→성과→복기→포트폴리오 연결 정합성을 진단해
 * 점수/이슈를 표시한다. **진단 전용 — 자동 주문 중단/재전송/실전/매수/매도 버튼이
 * 없으며**, 실제 계좌정보를 사용하지 않는다 (주문 신호 아님).
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index.jsx";
import { backendApi } from "../../services/backend/client";

const _SEV_COLOR = { CRITICAL: "#dc2626", HIGH: "#ea580c", WARN: "#b45309", INFO: "#64748b" };

export function EventIntegrityDiagnosticsCard({
  apiClient = backendApi,
  testId = "event-integrity-card",
  lookbackDays = 7,
} = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (typeof apiClient.eventIntegrityDiagnostics !== "function") return;
    try {
      const r = await apiClient.eventIntegrityDiagnostics({ lookbackDays });
      setData(r || null);
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
    }
  }, [apiClient, lookbackDays]);

  useEffect(() => { refresh(); }, [refresh]);

  const report = data?.report || null;
  const summary = data?.summary || {};
  const counts = summary.issue_counts || report?.issue_counts || {};
  const topIssues = report?.top_issues || [];
  const byCat = report?.by_category || {};
  const score = summary.integrity_score ?? report?.integrity_score;

  return (
    <Card accentColor="#f59e0b33">
      <div data-testid={testId}>
        <SectionLabel>🩺 이벤트 로그 품질 점검</SectionLabel>

        <div data-testid="ei-disclaimer"
             style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8 }}>
          이 진단은 이벤트 기록 정합성 확인용이며 주문 신호가 아닙니다.
          불일치가 있으면 성능 분석 신뢰도가 낮아질 수 있습니다.
          자동 주문 중단은 별도 정책으로만 처리됩니다. 실제 계좌정보를 사용하지 않습니다.
        </div>

        {error && (
          <div data-testid="ei-error"
               style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6 }}>
            {error}
          </div>
        )}

        {report && (
          <>
            <div data-testid="ei-score"
                 style={{ marginBottom: 8, fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)" }}>
              이벤트 정합성 점수: {score}
              <span data-testid="ei-safe-analysis"
                    style={{ marginLeft: 8, fontSize: "var(--fs-xs)",
                             color: summary.safe_for_analysis ? "#16a34a" : "#dc2626" }}>
                분석 적합: {summary.safe_for_analysis ? "예" : "아니오"}
              </span>
              <span data-testid="ei-safe-paper-gate"
                    style={{ marginLeft: 8, fontSize: "var(--fs-xs)",
                             color: summary.safe_for_paper_gate ? "#16a34a" : "#b45309" }}>
                Paper Gate 적합: {summary.safe_for_paper_gate ? "예" : "아니오"}
              </span>
            </div>

            <div data-testid="ei-issue-counts"
                 style={{ fontSize: "var(--fs-xs)", marginBottom: 8 }}>
              {["CRITICAL", "HIGH", "WARN", "INFO"].map((sev) => (
                <span key={sev} data-testid={`ei-count-${sev}`}
                      style={{ marginRight: 10, color: _SEV_COLOR[sev] }}>
                  {sev} {counts[sev] ?? 0}
                </span>
              ))}
            </div>

            {(counts.CRITICAL > 0 || counts.HIGH > 0) && (
              <div data-testid="ei-warn-banner"
                   style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d",
                            background: "#fee2e2", borderRadius: 4, padding: "4px 8px",
                            marginBottom: 8 }}>
                ⚠ 정합성 이슈가 있어 성능 분석 신뢰도가 낮아질 수 있습니다.
              </div>
            )}

            {topIssues.length > 0 && (
              <div data-testid="ei-top-issues" style={{ fontSize: "var(--fs-xs)", marginBottom: 6 }}>
                <strong>주요 이슈 TOP 5</strong>
                <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                  {topIssues.map((iss, i) => (
                    <li key={i} data-testid={`ei-issue-${iss.code}`}>
                      <span style={{ color: _SEV_COLOR[iss.severity], fontWeight: "var(--fw-bold)" }}>
                        [{iss.severity}]
                      </span>{" "}
                      {iss.code} · {iss.message}
                      {iss.suggested_action ? (
                        <span style={{ color: "var(--c-text-3)" }}> → {iss.suggested_action}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <details data-testid="ei-by-category" style={{ fontSize: "var(--fs-xs)" }}>
              <summary style={{ cursor: "pointer", color: "var(--c-text-3)" }}>
                category별 이슈
              </summary>
              <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                {Object.entries(byCat).map(([cat, n]) => (
                  <li key={cat} data-testid={`ei-cat-${cat}`}>{cat}: {n}건</li>
                ))}
              </ul>
            </details>
          </>
        )}

        <div data-testid="ei-footer"
             style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          진단 전용입니다. 자동 주문 중단이 아니며 실거래 권한이 아닙니다.
        </div>
      </div>
    </Card>
  );
}

export default EventIntegrityDiagnosticsCard;
