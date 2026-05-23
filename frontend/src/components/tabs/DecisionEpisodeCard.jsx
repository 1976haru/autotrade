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
              const oq = ep.order_quality_summary || {};
              const hasOq = oq.order_status != null;
              const os = ep.outcome_summary || {};
              const _pct = (v) => (v != null ? `${v >= 0 ? "+" : ""}${v}%` : null);
              // P-26: 매도 사유 (SELL episode 만).
              const sr = ep.sell_reason_summary || {};
              const isSell = String(action).toUpperCase() === "SELL";
              // P-27: 거래 복기 요약.
              const rev = ep.review_summary || {};
              const revSuggest = Array.isArray((ep.review || {}).improvement_suggestions)
                ? ep.review.improvement_suggestions : [];
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
                  {/* 2-08: RiskOfficer veto (위험 플래그 초과로 HOLD 강등 시) */}
                  {council.risk_veto_result && council.risk_veto_result.veto_applied && (
                    <div data-testid={`episode-risk-veto-${ep.episode_id}`}
                         style={{ color: "#dc2626", marginTop: 1, fontWeight: "var(--fw-bold)" }}>
                      RiskOfficer veto: 위험 플래그 {council.risk_veto_result.risk_flag_count}개 &gt;
                      허용 {council.risk_veto_result.max_risk_flags}개
                      ({council.risk_veto_result.risk_profile}) ·
                      {" "}{council.risk_veto_result.pre_veto_action} → HOLD 강등
                    </div>
                  )}
                  {/* 2-09: ExitPlan — valid BUY 면 손절/익절 표시, 검증 실패면 강등 사유 */}
                  {council.exit_plan && council.exit_plan.stop_loss != null && (
                    <div data-testid={`episode-exit-plan-${ep.episode_id}`}
                         style={{ color: "var(--c-text-3)", marginTop: 1 }}>
                      Exit plan: 손절 {Number(council.exit_plan.stop_loss).toLocaleString("ko-KR")}원
                      {" / 익절 "}{Number(council.exit_plan.take_profit).toLocaleString("ko-KR")}원
                      {council.exit_plan.risk_reward_ratio != null
                        && ` (RR ${council.exit_plan.risk_reward_ratio})`}
                    </div>
                  )}
                  {council.exit_plan_validation
                    && council.exit_plan_validation.valid === false && (
                    <div data-testid={`episode-exit-plan-veto-${ep.episode_id}`}
                         style={{ color: "#dc2626", marginTop: 1, fontWeight: "var(--fw-bold)" }}>
                      BUY 차단: exit_plan {council.exit_plan_validation.reason_code} → HOLD 강등
                    </div>
                  )}
                  {/* P-24: 주문·체결 품질 요약 */}
                  {hasOq && (
                    <div data-testid={`episode-quality-${ep.episode_id}`}
                         style={{ color: "var(--c-text-3)", marginTop: 1 }}>
                      주문 {oq.broker_order_no ? `#${oq.broker_order_no}` : ""} ·
                      {" "}{oq.order_status}
                      {oq.fill_status ? ` / ${oq.fill_status}` : ""}
                      {oq.latency_ms != null && <span> · 지연 {oq.latency_ms}ms</span>}
                      {oq.slippage_bps != null && <span> · 슬리피지 {oq.slippage_bps}bps</span>}
                      {oq.partial_fill ? <span> · 부분체결</span> : null}
                    </div>
                  )}
                  {/* P-25: 사후 성과 요약 */}
                  <div data-testid={`episode-outcome-${ep.episode_id}`}
                       style={{ color: "var(--c-text-3)", marginTop: 1 }}>
                    {os.status === "PENDING"
                      ? "성과 라벨 대기 중"
                      : os.status === "UNAVAILABLE"
                        ? "시장 데이터 부족으로 성과 계산 불가"
                        : (
                          <>
                            성과: {os.status || "—"}
                            {os.label ? ` · ${os.label}` : ""}
                            {os.return_5m != null && <span> · 5분 {_pct(os.return_5m)}</span>}
                            {os.return_30m != null && <span> · 30분 {_pct(os.return_30m)}</span>}
                            {os.return_close != null && <span> · 종가 {_pct(os.return_close)}</span>}
                            {os.max_favorable_excursion != null && (
                              <span> · MFE {_pct(os.max_favorable_excursion)}</span>
                            )}
                            {os.max_adverse_excursion != null && (
                              <span> / MAE {_pct(os.max_adverse_excursion)}</span>
                            )}
                          </>
                        )}
                  </div>
                  {/* P-26: 매도 사유 (SELL episode 만) */}
                  {isSell && sr.reason_code && (
                    <div data-testid={`episode-sell-reason-${ep.episode_id}`}
                         style={{ color: "#b45309", marginTop: 1, fontWeight: "var(--fw-bold)" }}>
                      매도 사유: {sr.reason_code}
                      {sr.message ? ` · ${sr.message}` : ""}
                    </div>
                  )}
                  {/* P-27: 거래 복기 (review_status 있을 때만) */}
                  {rev.review_status && (
                    <div data-testid={`episode-review-${ep.episode_id}`}
                         style={{ color: "#4338ca", marginTop: 1 }}>
                      {rev.review_status === "DATA_INSUFFICIENT"
                        ? "복기: 성과 데이터 부족 (DATA_INSUFFICIENT)"
                        : (
                          <>
                            복기: {rev.grade}
                            {rev.primary_tag ? ` · ${rev.primary_tag}` : ""}
                            {rev.summary ? ` · ${rev.summary}` : ""}
                            {revSuggest.length > 0 && (
                              <div data-testid={`episode-review-suggest-${ep.episode_id}`}
                                   style={{ color: "var(--c-text-3)", marginTop: 1 }}>
                                개선 제안: {revSuggest.slice(0, 2).join(" / ")}
                              </div>
                            )}
                          </>
                        )}
                    </div>
                  )}
                  <div style={{ color: "var(--c-text-3)", marginTop: 1 }}>
                    {strategies && <span>선택: {strategies} · </span>}
                    <span data-testid={`episode-order-${ep.episode_id}`}>
                      주문: {hasOrder ? `있음(${ep.broker_order_no})` : "없음"}
                    </span>
                    {" · 라벨: "}{outcome}
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
