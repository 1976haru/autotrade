/**
 * REAL-DATA-STRATEGY-01 — 실제/준실제 데이터 전략 검증 카드 (AISignal 탭, read-only).
 *
 * "내 매매기법 + Agent 결합 전략이 *실제 데이터* 기준 가능성 있는가"를 표시한다.
 * data_source / real_data_used / sample_fixture_only / 표본 + 4 score + verdict +
 * 강한/위험 시장국면 + Agent 도움/방해 + 다음 단계.
 *
 * 절대 invariant (테스트로 lock):
 *  - 실전 전환 / 자동 적용 / 매수 / 매도 / 승인 버튼 0개 (새로고침/복사만).
 *  - secret/account 원문 표시 0건. input/textarea 0개.
 *  - "자동 적용 안 됨" / "실전 승인 아님" / "수익 보장 아님" 문구 노출.
 *  - sample fixture 면 경고 표시, real_data_used 표시.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = {
  STRONG_CANDIDATE: "#166534", CAUTIOUS_CANDIDATE: "#a16207",
  RESEARCH_ONLY: "#0369a1", NOT_READY: "#9a3412", BLOCKED: "#b91c1c",
};
const _V_BG = {
  STRONG_CANDIDATE: "#dcfce7", CAUTIOUS_CANDIDATE: "#fef9c3",
  RESEARCH_ONLY: "#e0f2fe", NOT_READY: "#ffedd5", BLOCKED: "#fee2e2",
};

function _fmt(x) {
  return x === null || x === undefined ? "평가불가" : Number(x).toFixed(1);
}

export function RealDataStrategyValidationCard({
  apiClient = backendApi,
  testId = "real-data-strategy-validation-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.realDataStrategyValidationLatest !== "function") return;
    try {
      setReport((await apiClient.realDataStrategyValidationLatest()) || null);
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
  const passSymbols = Array.isArray(report?.pass_symbols) ? report.pass_symbols : [];
  const blockedSymbols = Array.isArray(report?.blocked_symbols) ? report.blocked_symbols : [];
  const isDataset = passSymbols.length > 0 || blockedSymbols.length > 0
    || Array.isArray(report?.per_symbol);
  const favorable = Array.isArray(report?.favorable_conditions) ? report.favorable_conditions : [];
  const dangerous = Array.isArray(report?.dangerous_conditions) ? report.dangerous_conditions : [];
  const helped = Array.isArray(report?.agent_helped_where) ? report.agent_helped_where : [];
  const hurt = Array.isArray(report?.agent_hurt_where) ? report.agent_hurt_where : [];
  const nextSteps = Array.isArray(report?.next_steps) ? report.next_steps : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#0ea5e933">
        <SectionLabel>📈 실제 데이터 전략 가능성 검증 (REAL-DATA-STRATEGY-01)</SectionLabel>

        <div data-testid="real-data-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          내 매매기법 + Agent 결합 전략을 <b>실제/준실제 OHLCV</b> 로 검증합니다.
          <b> 자동 적용 안 됨 · 실전 승인 아님 · 수익 보장 아님.</b> KIS 과거 시세 API 는
          미구현이라 CSV / yfinance 를 사용하며, sample fixture 결과는 기능 확인용입니다.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="real-data-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="real-data-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {error ? (
          <div data-testid="real-data-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>실제 데이터 검증 결과를 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {report ? (
          <>
            <div data-testid="real-data-verdict" style={{
              padding: "6px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[verdict] || "#f1f5f9", fontWeight: "var(--fw-bold)",
              fontSize: "var(--fs-xs)", color: _V_COLOR[verdict] || "var(--c-text)",
            }}>
              종합 판정: {verdict} · 점수:{" "}
              <span data-testid="real-data-overall-score">{_fmt(report.overall_score)}</span>
            </div>

            <div data-testid="real-data-source" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              데이터 소스: {report.data_source} · real_data_used:{" "}
              <span data-testid="real-data-used">{report.real_data_used ? "예" : "아니오"}</span>
            </div>

            {report.sample_fixture_only ? (
              <div data-testid="real-data-sample-warning" style={{
                padding: "4px 8px", borderRadius: 4, background: "#fef9c3",
                color: "#a16207", fontSize: "var(--fs-xs)", marginBottom: 6,
              }}>⚠️ sample fixture (기능 확인용) — 실제 데이터가 아니므로 STRONG 판정 불가</div>
            ) : null}

            <div data-testid="real-data-sample" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              종목 {report.symbols_count ?? 0} · bar {report.bars_count ?? 0} ·
              거래일 {report.days_count ?? 0} · 거래수 {report.trades_count ?? 0} ·
              데이터품질 {report.quality?.status ?? "?"} · Paper {report.paper_sample_class}
            </div>

            <div data-testid="real-data-scores" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 6,
            }}>
              backtest {_fmt(report.backtest_score)} · walk-forward {_fmt(report.walk_forward_score)} ·
              stress {_fmt(report.stress_score)} · agent {_fmt(report.agent_value_score)}
              {" "}({report.agent_value_verdict})
            </div>

            {isDataset ? (
              <>
                <div data-testid="real-data-pass-symbols" style={{
                  fontSize: "var(--fs-xs)", color: "#166534", marginBottom: 4,
                }}>PASS 종목 ({passSymbols.length}): {passSymbols.join(", ") || "없음"}</div>
                <div data-testid="real-data-blocked-symbols" style={{
                  fontSize: "var(--fs-xs)", color: "#b91c1c", marginBottom: 4,
                }}>품질 BLOCKED 종목 ({blockedSymbols.length}): {blockedSymbols.join(", ") || "없음"}</div>
                <div data-testid="real-data-aggregate" style={{
                  fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
                }}>
                  total_trades {report.total_trades ?? 0} · median PF {_fmt(report.median_profit_factor)} ·
                  median WF {_fmt(report.median_walk_forward_score)} · Agent {report.agent_value_summary}
                </div>
              </>
            ) : null}

            {favorable.length > 0 ? (
              <div data-testid="real-data-favorable" style={{
                fontSize: "var(--fs-xs)", color: "#166534", marginBottom: 4,
              }}>강한 국면/시간대: {favorable.join(" · ")}</div>
            ) : null}
            {dangerous.length > 0 ? (
              <div data-testid="real-data-dangerous" style={{
                fontSize: "var(--fs-xs)", color: "#b91c1c", marginBottom: 4,
              }}>위험 국면/시간대: {dangerous.join(" · ")}</div>
            ) : null}
            {helped.length > 0 ? (
              <div data-testid="real-data-agent-helped" style={{
                fontSize: "var(--fs-xs)", color: "#166534", marginBottom: 4,
              }}>Agent 도움: {helped.join(" · ")}</div>
            ) : null}
            {hurt.length > 0 ? (
              <div data-testid="real-data-agent-hurt" style={{
                fontSize: "var(--fs-xs)", color: "#a16207", marginBottom: 4,
              }}>Agent 방해: {hurt.join(" · ")}</div>
            ) : null}
            {nextSteps.length > 0 ? (
              <div data-testid="real-data-next-steps" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>추천 다음 단계: {nextSteps.join(" · ")}</div>
            ) : null}
          </>
        ) : (
          <div data-testid="real-data-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          }}>표시할 검증 결과가 없습니다.</div>
        )}

        <div data-testid="real-data-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 검증은 실제 데이터 가능성 평가 전용입니다. <b>threshold 추천은 자동 적용되지
          않으며</b>, 실전 전환 승인과 무관하고, 수익을 보장하지 않습니다. 자격정보
          (계좌번호/secret)는 표시되지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default RealDataStrategyValidationCard;
