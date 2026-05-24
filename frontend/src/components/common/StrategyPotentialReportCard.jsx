/**
 * STRATEGY-VALIDATION-01 — 전략 가능성 종합 평가 카드 (AISignal 탭, read-only).
 *
 * 백테스트 / Walk-forward / Stress / Paper 결과를 종합한 advisory 평가를 표시한다.
 * overall_verdict(STRONG/CAUTIOUS/RESEARCH_ONLY/NOT_READY/BLOCKED) + 7개 sub-score +
 * 강점/약점/리스크/다음 단계 + Agent 결합 효과 판정.
 *
 * 절대 invariant (테스트로 lock):
 *  - 실전 전환 / 자동 적용 / 매수 / 매도 / 승인 버튼 0개 (새로고침/복사만 허용).
 *  - secret/account 원문 표시 0건. input/textarea 0개.
 *  - "자동 적용 안 됨" / "실전 승인 아님" / "수익 보장 아님" 문구 노출.
 *  - sample fixture 결과는 기능 확인용 — STRONG 판정 불가 안내.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = {
  STRONG_CANDIDATE: "#166534",
  CAUTIOUS_CANDIDATE: "#a16207",
  RESEARCH_ONLY: "#0369a1",
  NOT_READY: "#9a3412",
  BLOCKED: "#b91c1c",
};
const _V_BG = {
  STRONG_CANDIDATE: "#dcfce7",
  CAUTIOUS_CANDIDATE: "#fef9c3",
  RESEARCH_ONLY: "#e0f2fe",
  NOT_READY: "#ffedd5",
  BLOCKED: "#fee2e2",
};

const _SCORE_ROWS = [
  ["backtest_score", "백테스트"],
  ["walk_forward_score", "Walk-forward 안정성"],
  ["stress_resilience_score", "스트레스 내성"],
  ["paper_execution_score", "Paper 체결"],
  ["agent_value_score", "Agent 결합 효과"],
  ["risk_control_score", "리스크 관리"],
  ["data_sufficiency_score", "데이터 충분성"],
];

function _fmt(x) {
  return x === null || x === undefined ? "평가불가" : Number(x).toFixed(1);
}

export function StrategyPotentialReportCard({
  apiClient = backendApi,
  testId = "strategy-potential-report-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.strategyPotential !== "function") return;
    try {
      setReport((await apiClient.strategyPotential()) || null);
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
    }
  }, [apiClient]);

  useEffect(() => { refresh(); }, [refresh]);

  const copyReport = useCallback(() => {
    try {
      const text = JSON.stringify(report || {}, null, 2);
      if (navigator?.clipboard?.writeText) {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }
    } catch { /* clipboard 미가용 무시 */ }
  }, [report]);

  const verdict = report?.overall_verdict || null;
  const strengths = Array.isArray(report?.strengths) ? report.strengths : [];
  const weaknesses = Array.isArray(report?.weaknesses) ? report.weaknesses : [];
  const risks = Array.isArray(report?.risks) ? report.risks : [];
  const nextSteps = Array.isArray(report?.recommended_next_steps)
    ? report.recommended_next_steps : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#8b5cf633">
        <SectionLabel>🧭 전략 가능성 종합 평가 (STRATEGY-VALIDATION-01)</SectionLabel>

        <div data-testid="strategy-potential-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          백테스트 / Walk-forward / Stress / Paper 결과를 종합한 가능성 평가입니다.
          <b> 자동 적용 안 됨 · 실전 승인 아님 · 수익 보장 아님.</b> sample fixture 결과는
          기능 확인용이라 STRONG_CANDIDATE 판정이 불가하며, 실전 검토는 Paper 100건 +
          28거래일 이상 + 운영자 승인이 필요합니다.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="strategy-potential-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>리포트 새로고침</button>
          <button type="button" data-testid="strategy-potential-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "결과 복사"}</button>
        </div>

        {error ? (
          <div data-testid="strategy-potential-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>전략 가능성 평가를 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {report ? (
          <>
            <div data-testid="strategy-potential-verdict" style={{
              padding: "6px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[verdict] || "#f1f5f9", fontWeight: "var(--fw-bold)",
              fontSize: "var(--fs-xs)", color: _V_COLOR[verdict] || "var(--c-text)",
            }}>
              종합 판정: {verdict} · 점수:{" "}
              <span data-testid="strategy-potential-overall-score">
                {_fmt(report.overall_strategy_potential_score)}
              </span>
              {report.sample_fixture_only ? (
                <span data-testid="strategy-potential-fixture-badge" style={{ marginLeft: 6 }}>
                  · ⚠️ sample fixture (기능 확인용)
                </span>
              ) : null}
            </div>

            <div data-testid="strategy-potential-scores" style={{ marginBottom: 8 }}>
              {_SCORE_ROWS.map(([key, label]) => (
                <div key={key} data-testid={`strategy-potential-score-${key}`}
                     style={{ display: "flex", gap: 6, fontSize: "var(--fs-xs)",
                              padding: "1px 0" }}>
                  <span style={{ minWidth: 150, color: "var(--c-text-2)" }}>{label}</span>
                  <span style={{ fontWeight: "var(--fw-bold)" }}>{_fmt(report[key])}</span>
                </div>
              ))}
            </div>

            <div data-testid="strategy-potential-paper-sample" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              Paper 표본: {report.paper_sample_class} · Agent 결합: {report.agent_value_verdict}
            </div>

            {strengths.length > 0 ? (
              <div data-testid="strategy-potential-strengths" style={{
                fontSize: "var(--fs-xs)", color: "#166534", marginBottom: 4,
              }}>강점: {strengths.join(" · ")}</div>
            ) : null}
            {weaknesses.length > 0 ? (
              <div data-testid="strategy-potential-weaknesses" style={{
                fontSize: "var(--fs-xs)", color: "#a16207", marginBottom: 4,
              }}>약점: {weaknesses.join(" · ")}</div>
            ) : null}
            {risks.length > 0 ? (
              <div data-testid="strategy-potential-risks" style={{
                fontSize: "var(--fs-xs)", color: "#b91c1c", marginBottom: 4,
              }}>리스크: {risks.join(" · ")}</div>
            ) : null}
            {nextSteps.length > 0 ? (
              <div data-testid="strategy-potential-next-steps" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>추천 다음 단계: {nextSteps.join(" · ")}</div>
            ) : null}
          </>
        ) : (
          <div data-testid="strategy-potential-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          }}>표시할 평가 결과가 없습니다.</div>
        )}

        <div data-testid="strategy-potential-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 평가는 전략 가능성 검토 전용입니다. <b>threshold 추천은 자동 적용되지 않으며</b>,
          실전 전환 승인과 무관하고, 수익을 보장하지 않습니다. 자격정보(계좌번호/secret)는
          표시되지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default StrategyPotentialReportCard;
