/**
 * P-28: 전략별 성과 대시보드 — Agent 탭, read-only.
 *
 * decision episode(P-21~P-27) 기록의 *추정 수익률* 로 ORB/Momentum/Gap/VWAP/
 * Agent Council 전략별 성과(승률/평균/PF/MDD/주문·체결/시간대·국면·risk_profile)를
 * 표시한다. **실제 계좌 잔고가 아니며 주문 신호가 아니다** — 버튼/입력 0개.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

const _STRAT_LABEL = {
  ORB: "ORB 돌파", MOMENTUM: "모멘텀", GAP: "갭", VWAP: "VWAP",
  AGENT_COUNCIL: "Agent Council",
};

const _pct = (v) => (v != null ? `${v >= 0 ? "+" : ""}${(v).toFixed(2)}%` : "—");
const _ratio = (v) => (v != null ? Number(v).toFixed(2) : "—");
const _rate = (v) => (v != null ? `${(v * 100).toFixed(1)}%` : "—");

export function PerformanceDashboard({
  apiClient = backendApi,
  testId = "performance-dashboard",
  limit = 500,
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (typeof apiClient.agentStrategyPerformance !== "function") return;
    try {
      const r = await apiClient.agentStrategyPerformance({ limit });
      setReport(r || null);
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
    }
  }, [apiClient, limit]);

  useEffect(() => { refresh(); }, [refresh]);

  const strategies = Array.isArray(report?.strategies) ? report.strategies : [];
  const cmp = report?.agent_vs_single || {};
  const insufficient = report?.status === "INSUFFICIENT_DATA";

  const _bucketRows = (obj) =>
    Object.entries(obj || {}).map(([k, v]) => (
      <tr key={k} data-testid={`perf-bucket-${k}`}>
        <td style={{ paddingRight: 10 }}>{k}</td>
        <td>{v.decision_count ?? 0}</td>
        <td>{_rate(v.win_rate)}</td>
        <td>{_pct(v.average_return)}</td>
        <td>{_ratio(v.profit_factor)}</td>
      </tr>
    ));

  return (
    <Card accentColor="#0ea5e933">
      <div data-testid={testId}>
        <SectionLabel>📊 전략별 성과 대시보드</SectionLabel>

        <div data-testid="perf-badges"
             style={{ marginBottom: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
          <span style={{ padding: "3px 8px", borderRadius: 4, fontSize: "var(--fs-xs)",
                         fontWeight: "var(--fw-bold)", background: "#6b7280", color: "#fff" }}>
            분석용 · 주문 신호 아님
          </span>
        </div>

        <div data-testid="perf-disclaimer"
             style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8 }}>
          성과 분석은 Paper/episode 기록 기준이며 실제 계좌 잔고가 아닙니다.
          실전 전환 판단은 별도 Paper Gate 리포트가 필요합니다.
          본 화면은 분석용이며 주문 신호가 아닙니다.
        </div>

        {error && (
          <div data-testid="perf-error"
               style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6 }}>
            {error}
          </div>
        )}

        {insufficient && (
          <div data-testid="perf-insufficient"
               style={{ fontSize: "var(--fs-xs)", color: "#b45309", marginBottom: 8 }}>
            성과 데이터가 부족합니다 — 사후 성과 라벨링(P-25) 누적 후 다시 확인하세요.
            (평가 {report?.evaluated_episodes ?? 0} / 전체 {report?.total_episodes ?? 0})
          </div>
        )}

        {/* 전략별 성과 표 */}
        <div data-testid="perf-strategy-table" style={{ overflowX: "auto", marginBottom: 10 }}>
          <table style={{ width: "100%", fontSize: "var(--fs-xs)", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ color: "var(--c-text-3)", textAlign: "left" }}>
                <th>전략</th><th>판단</th><th>선정</th><th>주문</th><th>체결</th>
                <th>승률</th><th>평균</th><th>손익비</th><th>PF</th><th>최대손실</th><th>MDD</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((b) => (
                <tr key={b.strategy} data-testid={`perf-strategy-${b.strategy}`}
                    style={{ borderTop: "1px solid var(--c-border)",
                             fontWeight: b.strategy === "AGENT_COUNCIL" ? "var(--fw-bold)" : "normal" }}>
                  <td>{_STRAT_LABEL[b.strategy] || b.strategy}
                    <span style={{ color: "var(--c-text-3)" }}> ({b.strategy})</span>
                  </td>
                  <td>{b.decision_count ?? 0}</td>
                  <td>{b.selected_count ?? 0}</td>
                  <td>{b.order_count ?? 0}</td>
                  <td>{b.filled_count ?? 0}</td>
                  <td>{_rate(b.win_rate)}</td>
                  <td>{_pct(b.average_return)}</td>
                  <td>{_ratio(b.payoff_ratio)}</td>
                  <td>{_ratio(b.profit_factor)}</td>
                  <td>{_pct(b.max_loss)}</td>
                  <td>{_rate(b.max_drawdown)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Agent Council vs 단일 전략 */}
        <div data-testid="perf-agent-vs-single"
             style={{ fontSize: "var(--fs-xs)", marginBottom: 10,
                      color: cmp.warning ? "#b45309" : "var(--c-text-2)" }}>
          <strong>Agent Council vs 단일 전략:</strong>{" "}
          {cmp.agent_outperforms_best_single == null
            ? "비교 데이터 부족"
            : cmp.agent_outperforms_best_single
              ? `Agent Council(PF ${_ratio(cmp.agent_profit_factor)})이 최고 단일 전략 `
                + `${cmp.best_single_strategy}(PF ${_ratio(cmp.best_single_profit_factor)})보다 우수 `
                + `(edge ${_ratio(cmp.agent_edge)})`
              : (cmp.warning || "Agent Council 이 단일 전략보다 낮습니다.")}
        </div>

        {/* risk_profile / market_regime / 시간대 */}
        <details data-testid="perf-breakdowns" style={{ fontSize: "var(--fs-xs)" }}>
          <summary style={{ cursor: "pointer", color: "var(--c-text-3)" }}>
            risk_profile · market_regime · 시간대별 성과
          </summary>
          <div style={{ marginTop: 6 }}>
            <div data-testid="perf-by-risk" style={{ marginBottom: 6 }}>
              <div style={{ fontWeight: "var(--fw-bold)" }}>risk_profile별</div>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <tbody>{_bucketRows(report?.by_risk_profile)}</tbody>
              </table>
            </div>
            <div data-testid="perf-by-regime" style={{ marginBottom: 6 }}>
              <div style={{ fontWeight: "var(--fw-bold)" }}>market_regime별</div>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <tbody>{_bucketRows(report?.by_market_regime)}</tbody>
              </table>
            </div>
            <div data-testid="perf-by-phase">
              <div style={{ fontWeight: "var(--fw-bold)" }}>시간대별</div>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <tbody>{_bucketRows(report?.by_time_phase)}</tbody>
              </table>
            </div>
          </div>
        </details>

        <div data-testid="perf-footer"
             style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          분석용 기록입니다. 주문 신호가 아니며 실거래 권한이 아닙니다.
        </div>
      </div>
    </Card>
  );
}

export default PerformanceDashboard;
