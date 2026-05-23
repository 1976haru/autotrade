/**
 * P-21: Decision Episode 카드 — Agent 탭, read-only.
 *
 * AI 판단→주문→체결→성과를 episode 단위로 연결한 학습용 기록을 최근 N개 표시.
 * **주문 신호가 아니며 실거래 권한이 아니다** — 표시 전용, 버튼/입력 0개.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

const _ACTION_COLOR = { BUY: "#16a34a", SELL: "#d97706", HOLD: "#64748b" };

export function DecisionEpisodeCard({
  apiClient = backendApi,
  testId = "decision-episode-card",
  limit = 5,
} = {}) {
  const [episodes, setEpisodes] = useState([]);
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (typeof apiClient.agentDecisionEpisodes !== "function") return;
    try {
      const r = await apiClient.agentDecisionEpisodes({ limit });
      setEpisodes(Array.isArray(r?.episodes) ? r.episodes : []);
      setSummary(r?.summary || null);
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
    }
  }, [apiClient, limit]);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <Card accentColor="#6366f133">
      <div data-testid={testId}>
        <SectionLabel>🧠 Decision Episode</SectionLabel>

        <div data-testid="episode-badges" style={{ marginBottom: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
          <span data-testid="episode-badge-learning"
                style={{ padding: "3px 8px", borderRadius: 4, fontSize: "var(--fs-xs)",
                         fontWeight: "var(--fw-bold)", background: "#6b7280", color: "#fff" }}>
            학습용 기록 · 주문 신호 아님
          </span>
        </div>

        <div data-testid="episode-intro"
             style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8 }}>
          AI 판단 → 주문 → 체결 → 성과를 episode 단위로 연결한 기록입니다.
        </div>

        {error && (
          <div data-testid="episode-error"
               style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6 }}>
            {error}
          </div>
        )}

        {summary && (
          <div data-testid="episode-summary"
               style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 8 }}>
            총 {summary.total ?? 0}건 · 제출 {summary.submitted_count ?? 0}건
          </div>
        )}

        {episodes.length === 0 ? (
          <div data-testid="episode-empty"
               style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            아직 기록된 Decision Episode가 없습니다.
          </div>
        ) : (
          <div data-testid="episode-list">
            {episodes.map((ep) => {
              const action = ep.final_action || "—";
              const strategies = Array.isArray(ep.selected_strategies)
                ? ep.selected_strategies.join(", ") : "";
              const hasOrder = !!ep.broker_order_no;
              const outcome = ep.outcome && ep.outcome.label ? ep.outcome.label : "미정";
              const ms = ep.market_summary || {};
              const dataStatus = ms.data_status || "—";
              // P-23: 4전략 vote (ORB/MOMENTUM/GAP/VWAP) — canonical 순서로 정렬.
              const _ORDER = ["ORB", "MOMENTUM", "GAP", "VWAP"];
              const votes = Array.isArray(ep.votes) ? ep.votes : [];
              const votesByStrat = {};
              for (const v of votes) {
                if (v && v.strategy) votesByStrat[v.strategy] = v;
              }
              const council = ep.council || {};
              return (
                <div
                  key={ep.episode_id}
                  data-testid={`episode-row-${ep.episode_id}`}
                  style={{ padding: "6px 0", borderTop: "1px solid var(--c-border)",
                           fontSize: "var(--fs-xs)" }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    <span style={{ fontWeight: "var(--fw-bold)" }}>{ep.symbol || "—"}</span>
                    <span style={{ fontWeight: "var(--fw-bold)",
                                   color: _ACTION_COLOR[action] || "var(--c-text)" }}>
                      {action}
                    </span>
                    {ep.confidence != null && (
                      <span style={{ color: "var(--c-text-3)" }}>conf {ep.confidence}</span>
                    )}
                    {ep.quality_score != null && (
                      <span style={{ color: "var(--c-text-3)" }}>q {ep.quality_score}</span>
                    )}
                    {ep.reason_code && (
                      <span style={{ color: "var(--c-text-3)" }}>· {ep.reason_code}</span>
                    )}
                  </div>
                  {/* P-22: 시장 스냅샷 요약 */}
                  <div data-testid={`episode-market-${ep.episode_id}`}
                       style={{ color: "var(--c-text-3)", marginTop: 1 }}>
                    {dataStatus === "NO_MARKET_DATA"
                      ? "시장 데이터 없음"
                      : dataStatus === "PRICE_STALE"
                        ? "현재가가 오래되어 PRICE_STALE"
                        : (
                          <>
                            {ms.price != null && <span>현재가 {Number(ms.price).toLocaleString("ko-KR")}원</span>}
                            {ms.market_regime && <span> · {ms.market_regime}</span>}
                            {ms.vwap != null && <span> · VWAP {Number(ms.vwap).toLocaleString("ko-KR")}</span>}
                            {ms.rsi != null && <span> · RSI {ms.rsi}</span>}
                            {ms.gap_pct != null && (
                              <span> · Gap {ms.gap_pct >= 0 ? "+" : ""}{ms.gap_pct}%</span>
                            )}
                            {ms.price_age_seconds != null && (
                              <span> · age {Math.round(ms.price_age_seconds)}s</span>
                            )}
                          </>
                        )}
                  </div>
                  {/* P-23: 4전략 vote 요약 (ORB/MOMENTUM/GAP/VWAP) */}
                  {votes.length > 0 && (
                    <div data-testid={`episode-votes-${ep.episode_id}`}
                         style={{ color: "var(--c-text-3)", marginTop: 1 }}>
                      {_ORDER.map((s) => {
                        const v = votesByStrat[s];
                        if (!v) return null;
                        return (
                          <span key={s} data-testid={`episode-vote-${ep.episode_id}-${s}`}
                                style={{ marginRight: 8 }}>
                            {s}: {v.signal}/{v.score}
                          </span>
                        );
                      })}
                      {(council.buy_score != null) && (
                        <span data-testid={`episode-scores-${ep.episode_id}`}>
                          (buy {council.buy_score} / sell {council.sell_score} / hold {council.hold_score})
                        </span>
                      )}
                    </div>
                  )}
                  <div style={{ color: "var(--c-text-3)", marginTop: 1 }}>
                    {strategies && <span>선택: {strategies} · </span>}
                    <span data-testid={`episode-order-${ep.episode_id}`}>
                      주문: {hasOrder ? `있음(${ep.broker_order_no})` : "없음"}
                    </span>
                    {" · 성과: "}{outcome}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <div data-testid="episode-footer"
             style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          학습용 기록입니다. 주문 신호가 아니며 실거래 권한이 아닙니다.
        </div>
      </div>
    </Card>
  );
}

export default DecisionEpisodeCard;
