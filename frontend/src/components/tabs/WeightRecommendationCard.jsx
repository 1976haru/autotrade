/**
 * P-29: Agent 전략 가중치 개선 *후보* 카드 — Agent 탭, read-only.
 *
 * P-28 전략별 성과 기반으로 ORB/Momentum/Gap/VWAP 가중치 조정 후보를 표시한다.
 * **자동 적용 금지 / 운영자 승인 전 변경 금지 / 주문 신호 아님** — 적용·실전·매수·
 * 매도 버튼 0개, 입력 0개. 추천 결과는 분석용일 뿐 Agent 설정을 바꾸지 않는다.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

const _ACTION_LABEL = {
  INCREASE: "상향 ▲", DECREASE: "하향 ▼", KEEP: "유지", NEEDS_MORE_DATA: "데이터 부족",
};
const _ACTION_COLOR = {
  INCREASE: "#16a34a", DECREASE: "#dc2626", KEEP: "#64748b", NEEDS_MORE_DATA: "#b45309",
};
const _delta = (d) => (d == null ? "" : d > 0 ? `+${d}` : `${d}`);

export function WeightRecommendationCard({
  apiClient = backendApi,
  testId = "weight-recommendation-card",
  lookbackCount = 100,
} = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (typeof apiClient.agentWeightRecommendation !== "function") return;
    try {
      const r = await apiClient.agentWeightRecommendation({ lookbackCount });
      setData(r || null);
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
    }
  }, [apiClient, lookbackCount]);

  useEffect(() => { refresh(); }, [refresh]);

  const rec = data?.recommendation || null;
  const actions = Array.isArray(rec?.strategy_actions) ? rec.strategy_actions : [];
  const needsMore = rec?.status === "NEEDS_MORE_DATA";

  return (
    <Card accentColor="#a855f733">
      <div data-testid={testId}>
        <SectionLabel>⚖️ 전략 가중치 개선 후보</SectionLabel>

        <div data-testid="wr-badges"
             style={{ marginBottom: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
          <span style={{ padding: "3px 8px", borderRadius: 4, fontSize: "var(--fs-xs)",
                         fontWeight: "var(--fw-bold)", background: "#6b7280", color: "#fff" }}>
            추천 후보 · 자동 적용 안 됨
          </span>
        </div>

        <div data-testid="wr-disclaimer"
             style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8 }}>
          이 추천은 분석용이며 자동 적용되지 않습니다.
          운영자 승인 전에는 Agent 가중치가 변경되지 않습니다.
          본 추천은 주문 신호가 아니며 실제 계좌정보를 사용하지 않습니다.
        </div>

        {error && (
          <div data-testid="wr-error"
               style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6 }}>
            {error}
          </div>
        )}

        {rec && (
          <div data-testid="wr-meta"
               style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 6 }}>
            표본 lookback {rec.lookback_count}건 · risk_profile {rec.risk_profile}
            {" · market_regime "}{rec.market_regime}
          </div>
        )}

        {needsMore && (
          <div data-testid="wr-insufficient"
               style={{ fontSize: "var(--fs-xs)", color: "#b45309", marginBottom: 8 }}>
            표본이 부족하여 가중치 변경을 권하지 않습니다 — 사후 성과(P-25) 누적 후 다시 확인하세요.
          </div>
        )}

        {/* 전략별 추천 표 */}
        {rec && (
          <div data-testid="wr-table" style={{ overflowX: "auto", marginBottom: 8 }}>
            <table style={{ width: "100%", fontSize: "var(--fs-xs)", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ color: "var(--c-text-3)", textAlign: "left" }}>
                  <th>전략</th><th>현재</th><th>추천</th><th>변화</th><th>액션</th><th>표본</th>
                </tr>
              </thead>
              <tbody>
                {actions.map((a) => (
                  <tr key={a.strategy} data-testid={`wr-strategy-${a.strategy}`}
                      style={{ borderTop: "1px solid var(--c-border)" }}>
                    <td>{a.strategy}</td>
                    <td>{a.current_weight}</td>
                    <td style={{ fontWeight: "var(--fw-bold)" }}>{a.recommended_weight}</td>
                    <td style={{ color: _ACTION_COLOR[a.action] || "var(--c-text)" }}>
                      {_delta(a.delta)}
                    </td>
                    <td style={{ color: _ACTION_COLOR[a.action] || "var(--c-text)" }}>
                      {_ACTION_LABEL[a.action] || a.action}
                    </td>
                    <td>{a.sample_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 추천 사유 (전략별 reasons) */}
        {actions.length > 0 && (
          <details data-testid="wr-reasons" style={{ fontSize: "var(--fs-xs)", marginBottom: 6 }}>
            <summary style={{ cursor: "pointer", color: "var(--c-text-3)" }}>추천 사유</summary>
            <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
              {actions.map((a) => (
                <li key={a.strategy} data-testid={`wr-reason-${a.strategy}`}>
                  <strong>{a.strategy}</strong>: {(a.reasons || []).join(" · ") || "—"}
                </li>
              ))}
            </ul>
          </details>
        )}

        {rec?.expected_effect && (
          <div data-testid="wr-expected-effect"
               style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
            기대효과: {rec.expected_effect}
          </div>
        )}

        {rec?.warning && (
          <div data-testid="wr-warning"
               style={{ fontSize: "var(--fs-xs)", color: "#b45309", marginBottom: 4 }}>
            ⚠ {rec.warning}
          </div>
        )}

        <div data-testid="wr-footer"
             style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          운영자 승인 전 적용 금지 · 주문 신호 아님 · 실거래 권한 아님.
        </div>
      </div>
    </Card>
  );
}

export default WeightRecommendationCard;
