/**
 * #49 / 6-04 — 성과 지표 요약 카드 (read-only, 분석 전용).
 *
 * 기존 PerformanceDashboard(P-28) 가 빠뜨린 expectancy + 체결 실패율 / 거절률 /
 * 부분체결률 + 차단 사유 TOP + Agent Council vs best single 을 한 카드에 요약한다.
 *  - 전략별: 승률 / 손익비 / profit_factor / MDD / expectancy
 *  - 주문 품질: order_failure_rate / rejected_rate / partial_fill_rate / fill_rate
 *  - 차단 사유 TOP / Agent Council vs best single
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수/매도/실전/승인/Place Order 버튼 0개, 입력 form 0개.
 *  - secret/account 원문 표시 0건. "분석/표시 전용" / "수익 보장 아님" 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _STRAT_LABEL = {
  ORB: "ORB 돌파", MOMENTUM: "모멘텀", GAP: "갭", VWAP: "VWAP",
  AGENT_COUNCIL: "Agent Council",
};

const _rate = (v) => (v != null ? `${(v * 100).toFixed(1)}%` : "—");
const _ratio = (v) => (v != null ? Number(v).toFixed(2) : "—");
const _exp = (v) => (v != null ? Number(v).toFixed(1) : "—");

export function PerformanceMetricsSummary({
  apiClient = backendApi,
  testId = "performance-metrics-summary",
  limit = 500,
} = {}) {
  const [perf, setPerf] = useState(null);
  const [oq, setOq] = useState(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      if (typeof apiClient.agentStrategyPerformance === "function") {
        setPerf((await apiClient.agentStrategyPerformance({ limit })) || null);
      }
      if (typeof apiClient.agentOrderQualityMetrics === "function") {
        setOq((await apiClient.agentOrderQualityMetrics({ limit })) || null);
      }
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
    }
  }, [apiClient, limit]);

  useEffect(() => { refresh(); }, [refresh]);

  const strategies = Array.isArray(perf?.strategies) ? perf.strategies : [];
  const cmp = perf?.agent_vs_single || {};
  const blocked = Array.isArray(oq?.blocked_reasons_top) ? oq.blocked_reasons_top : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#22c55e33">
        <SectionLabel>📈 성과 지표 요약 (기간별)</SectionLabel>

        <div data-testid="metrics-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          이 화면은 분석/표시 전용입니다. 주문 버튼이 아니며, 실전 전환 승인과
          무관하고, 수익을 보장하지 않습니다. episode 추정 기준 — 실제 계좌 잔고가
          아닙니다.
        </div>

        {error ? (
          <div data-testid="metrics-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>{error}</div>
        ) : null}

        {/* 전략별: 승률/손익비/PF/MDD/expectancy */}
        <div data-testid="metrics-strategy-table" style={{ overflowX: "auto", marginBottom: 10 }}>
          <table style={{ width: "100%", fontSize: "var(--fs-xs)", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ color: "var(--c-text-3)", textAlign: "left" }}>
                <th>전략</th><th>승률</th><th>손익비</th><th>PF</th><th>MDD</th><th>expectancy</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((b) => (
                <tr key={b.strategy} data-testid={`metrics-strategy-${b.strategy}`}
                    style={{ borderTop: "1px solid var(--c-border)",
                             fontWeight: b.strategy === "AGENT_COUNCIL" ? "var(--fw-bold)" : "normal" }}>
                  <td>{_STRAT_LABEL[b.strategy] || b.strategy}</td>
                  <td data-testid={`metrics-winrate-${b.strategy}`}>{_rate(b.win_rate)}</td>
                  <td data-testid={`metrics-payoff-${b.strategy}`}>{_ratio(b.payoff_ratio)}</td>
                  <td data-testid={`metrics-pf-${b.strategy}`}>{_ratio(b.profit_factor)}</td>
                  <td data-testid={`metrics-mdd-${b.strategy}`}>{_rate(b.max_drawdown)}</td>
                  <td data-testid={`metrics-expectancy-${b.strategy}`}>{_exp(b.expectancy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 주문 품질 비율 */}
        <div data-testid="metrics-order-quality" style={{
          display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
          gap: 6, marginBottom: 10,
        }}>
          <div data-testid="metrics-failure-rate" style={{ padding: "6px 8px", background: "#fef2f2", borderRadius: 4 }}>
            <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>체결 실패율</div>
            <div style={{ fontWeight: "var(--fw-bold)" }}>{_rate(oq?.order_failure_rate)}</div>
          </div>
          <div data-testid="metrics-rejected-rate" style={{ padding: "6px 8px", background: "#fff7ed", borderRadius: 4 }}>
            <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>주문 거절률</div>
            <div style={{ fontWeight: "var(--fw-bold)" }}>{_rate(oq?.rejected_rate)}</div>
          </div>
          <div data-testid="metrics-partial-rate" style={{ padding: "6px 8px", background: "#fefce8", borderRadius: 4 }}>
            <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>부분체결률</div>
            <div style={{ fontWeight: "var(--fw-bold)" }}>{_rate(oq?.partial_fill_rate)}</div>
          </div>
          <div data-testid="metrics-fill-rate" style={{ padding: "6px 8px", background: "#f0fdf4", borderRadius: 4 }}>
            <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>체결률</div>
            <div style={{ fontWeight: "var(--fw-bold)" }}>{_rate(oq?.fill_rate)}</div>
          </div>
        </div>

        {/* 차단 사유 TOP */}
        <div data-testid="metrics-blocked-reasons" style={{ marginBottom: 10 }}>
          <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
            차단 사유 TOP
          </div>
          {blocked.length === 0 ? (
            <div data-testid="metrics-blocked-empty" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            }}>차단 사유 기록 없음</div>
          ) : (
            <div>
              {blocked.map((b) => (
                <div key={b.reason} data-testid={`metrics-blocked-${b.reason}`}
                     style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                  • {b.reason} — {b.count}건
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Agent Council vs best single */}
        <div data-testid="metrics-agent-vs-single" style={{
          fontSize: "var(--fs-xs)", color: cmp.warning ? "#b45309" : "var(--c-text-2)",
        }}>
          <strong>Agent Council vs 단일 전략:</strong>{" "}
          {cmp.agent_outperforms_best_single == null
            ? "비교 데이터 부족"
            : cmp.agent_outperforms_best_single
              ? `Agent Council(PF ${_ratio(cmp.agent_profit_factor)})이 최고 단일 전략 `
                + `${cmp.best_single_strategy}(PF ${_ratio(cmp.best_single_profit_factor)})보다 우수`
              : (cmp.warning || "Agent Council 이 단일 전략보다 낮습니다.")}
        </div>

        <div data-testid="metrics-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
        }}>
          분석/표시 전용입니다. 주문 신호가 아니며 실거래 권한이 아닙니다.
          성과가 좋아 보여도 실전 전환은 별도 Paper Gate 검토가 필요합니다.
        </div>
      </Card>
    </div>
  );
}

export default PerformanceMetricsSummary;
