/**
 * #51 / 6-06 — 판단 품질 점수 카드 (read-only, advisory).
 *
 * 고도화된 quality_score(신호 일관성/데이터 신뢰도/리스크/장세 적합도/exit_plan)와
 * breakdown/penalties 를 표시하고, quality 가 낮으면 BUY 가 HOLD 로 강등됨을 안내한다.
 * `quality` prop(decision-quality 결과)을 주면 그대로 표시하고, 없으면 최신 episode
 * 의 council 로 backend 에 계산을 요청한다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 주문/실전/승인/Place Order 버튼 0개, 입력 form 0개.
 *  - "자동 적용 안 됨" / "quality 낮으면 HOLD" / "주문 버튼 아님" 문구 노출.
 *  - secret/account 원문 표시 0건.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _GRADE_COLOR = { A: "#16a34a", B: "#22c55e", C: "#a16207", D: "#ea580c", F: "#b91c1c" };
const _LABEL = {
  signal_consistency: "신호 일관성", data_reliability: "데이터 신뢰도",
  risk: "리스크", regime_fit: "장세 적합도", exit_plan: "exit_plan 품질",
};

export function DecisionQualityScoreCard({
  apiClient = backendApi,
  quality = null,
  testId = "decision-quality-score-card",
} = {}) {
  const [result, setResult] = useState(quality);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(Boolean(quality));

  const refresh = useCallback(async () => {
    if (quality) { setResult(quality); setLoaded(true); return; }
    if (typeof apiClient.agentDecisionQuality !== "function"
        || typeof apiClient.agentDecisionEpisodes !== "function") {
      setLoaded(true); return;
    }
    try {
      const r = await apiClient.agentDecisionEpisodes({ limit: 1 });
      const ep = Array.isArray(r?.episodes) && r.episodes.length ? r.episodes[0] : null;
      const council = ep && typeof ep.council === "object" ? ep.council : null;
      setResult(await apiClient.agentDecisionQuality({ council }));
      setError("");
    } catch (e) {
      setResult(null);
      setError(e?.message || String(e));
    } finally {
      setLoaded(true);
    }
  }, [apiClient, quality]);

  useEffect(() => { refresh(); }, [refresh]);

  const q = result;
  const breakdown = q?.breakdown || {};
  const penalties = q?.penalties || {};

  return (
    <div data-testid={testId}>
      <Card accentColor="#06b6d433">
        <SectionLabel>🎯 판단 품질 점수</SectionLabel>

        <div data-testid="quality-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          신호 일관성·데이터 신뢰도·리스크·장세 적합도·exit_plan 품질을 종합한 판단
          품질입니다. quality 가 낮으면 BUY 가 HOLD 로 강등됩니다. 분석/표시 전용 —
          주문 버튼이 아니며, 자동 적용되지 않고, 수익을 보장하지 않습니다.
        </div>

        {error ? (
          <div data-testid="quality-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>품질 점수를 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {loaded && q == null && !error ? (
          <div data-testid="quality-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          }}>표시할 판단 기록이 없습니다.</div>
        ) : null}

        {q ? (
          <>
            <div data-testid="quality-score" style={{
              padding: "5px 8px", borderRadius: 4, marginBottom: 8, background: "#ecfeff",
              fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
            }}>
              quality_score: <span data-testid="quality-grade"
                style={{ color: _GRADE_COLOR[q.quality_grade] || "var(--c-text)" }}>
                {q.enhanced_quality_score} ({q.quality_grade})
              </span>
              {" · "}임계 {q.min_quality}
            </div>

            {/* breakdown */}
            <div data-testid="quality-breakdown" style={{ marginBottom: 8 }}>
              {Object.entries(breakdown).map(([k, v]) => (
                <div key={k} data-testid={`quality-breakdown-${k}`}
                     style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                  {_LABEL[k] || k}: <strong>{v}</strong>
                </div>
              ))}
            </div>

            {/* penalties */}
            <div data-testid="quality-penalties" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 6,
            }}>
              penalties: feedback_penalty {penalties.feedback_penalty ?? 0}
            </div>

            {q.should_hold ? (
              <div data-testid="quality-hold" style={{
                padding: "5px 8px", borderRadius: 4, background: "#fef2f2",
                color: "#b91c1c", fontSize: "var(--fs-xs)", marginBottom: 6,
              }}>
                ⚠️ quality 낮음 → BUY 보류(HOLD). reason: {q.reason_code}
              </div>
            ) : (
              <div data-testid="quality-ok" style={{
                fontSize: "var(--fs-xs)", color: "#166534", marginBottom: 6,
              }}>품질 임계 충족 (강등 없음)</div>
            )}
          </>
        ) : null}

        <div data-testid="quality-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          quality_score 만으로 실전 전환을 허가하지 않습니다. 기존 RiskOfficer /
          exit_plan 정책을 우회하지 않으며, 자격정보(계좌번호/secret)는 표시되지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default DecisionQualityScoreCard;
