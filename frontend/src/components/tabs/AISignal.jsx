import { useState } from "react";
import { Card, SectionLabel, Btn, Inp, ScoreBar } from "../common";
import { runAgentAnalysis } from "../../services/ai/claudeAgent";
import { STRATEGIES } from "../../config/strategies";
import { SIGNAL_COLOR, confluenceColor } from "../../utils/format";
import { fmtKRW } from "../../utils/format";
import { AgentCouncilCard } from "./AgentCouncilCard";
import { BuyBlockReasonsCard } from "./BuyBlockReasonsCard";
import { DecisionEpisodeCard } from "./DecisionEpisodeCard";
import { PerformanceDashboard } from "./PerformanceDashboard";
import { WeightRecommendationCard } from "./WeightRecommendationCard";
import { PaperGateReportCard } from "./PaperGateReportCard";
import { DecisionEpisodeExportButton } from "../common/DecisionEpisodeExportButton";
import { EventIntegrityDiagnosticsCard } from "../common/EventIntegrityDiagnosticsCard";
import { AgentDecisionExplanationCard } from "../common/AgentDecisionExplanationCard";
import { PerformanceMetricsSummary } from "../common/PerformanceMetricsSummary";
import { DecisionQualityScoreCard } from "../common/DecisionQualityScoreCard";
import { PostTradeFeedbackCard } from "../common/PostTradeFeedbackCard";
import { StrategyPotentialReportCard } from "../common/StrategyPotentialReportCard";
import { RealDataStrategyValidationCard } from "../common/RealDataStrategyValidationCard";
import { IntradayStrategyValidationCard } from "../common/IntradayStrategyValidationCard";
import { KisIntraday100ValidationCard } from "../common/KisIntraday100ValidationCard";
import { Wf6m50SymbolsCard } from "../common/Wf6m50SymbolsCard";
import { Wf6mRootCauseCard } from "../common/Wf6mRootCauseCard";
import { Wf6mRebuildExperimentsCard } from "../common/Wf6mRebuildExperimentsCard";
import { StrategyAgentDecompositionCard } from "../common/StrategyAgentDecompositionCard";
import { ForwardValidationCard } from "../common/ForwardValidationCard";
import { ForwardUniverseCard } from "../common/ForwardUniverseCard";
import { Locked60dWeeklyValidationCard } from "../common/Locked60dWeeklyValidationCard";
import { Locked60dWeeklyNewDataCard } from "../common/Locked60dWeeklyNewDataCard";
import { Intraday1YScaledValidationCard } from "../common/Intraday1YScaledValidationCard";
import { AgentDecisionSummaryCard } from "./AgentDecisionSummaryCard";
import { AgentStatsCard } from "./AgentStatsCard";
import { OperatingLoopCard } from "./OperatingLoopCard";
import { ThemeSignalsCard } from "./ThemeSignalsCard";
import { AiAssistProposalCard } from "./AiAssistProposalCard";
import { AiExecutionPolicyCard, useAiExecutionPolicy } from "./AiExecutionPolicyCard";

// 45: AI Execution policy card는 자체 hook으로 fetch — AISignal 본체에서
// 분리해 mount 비용 / re-render 영향을 격리.
function AiExecutionPolicyMount() {
  const { policy, loading, error } = useAiExecutionPolicy();
  return <AiExecutionPolicyCard policy={policy} loading={loading} error={error} />;
}


export function AISignal({ activeStratIds }) {
  const [ticker,  setTicker]  = useState("");
  const [extra,   setExtra]   = useState("");
  const [stream,  setStream]  = useState("");
  const [score,   setScore]   = useState(null);
  const [busy,    setBusy]    = useState(false);
  const [error,   setError]   = useState("");

  const activeNames = activeStratIds.map((id) => STRATEGIES[id]?.name).filter(Boolean);

  const run = async () => {
    if (!ticker.trim()) return;
    setBusy(true); setStream(""); setScore(null); setError("");
    try {
      await runAgentAnalysis({
        ticker, extra, activeStrats: activeNames,
        risk: { maxDailyLoss: 300000, maxPerTrade: 1000000, maxPositions: 5 },
        onChunk: setStream,
        onScore: setScore,
      });
    } catch (e) {
      setError("분석 오류: " + e.message);
    }
    setBusy(false);
  };

  // JSON 블록 제거한 설명 텍스트
  const explanation = stream.replace(/\{[\s\S]*?"total"[\s\S]*?\}/, "").trim();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <OperatingLoopCard />
      <ThemeSignalsCard />
      <AgentCouncilCard />
      {/* P-17: 오늘 매수 불가 사유 — Agent Council 결정 근처 표시 (표시 전용). */}
      <BuyBlockReasonsCard />
      {/* P-21: Decision Episode — 판단→주문→체결→성과 추적 (학습용 기록). */}
      <DecisionEpisodeCard />
      {/* #52 / 6-07: AI 판단 설명 — entry/counter/exit/risk/veto/sell 근거 표시
          (read-only, 주문 버튼 아님). */}
      <AgentDecisionExplanationCard />
      {/* #51 / 6-06: 판단 품질 점수 — 신호 일관성/데이터/리스크/장세/exit_plan +
          quality 낮으면 HOLD (read-only, 자동 적용 안 됨). */}
      <DecisionQualityScoreCard />
      {/* #50 / 6-05: 복기 피드백 루프 — 승패 요인/과잉진입/늦은 청산 + threshold
          추천 (read-only, 운영자 승인 필요, 자동 적용 안 됨). */}
      <PostTradeFeedbackCard />
      {/* P-28: 전략별 성과 대시보드 — episode 추정 수익률 기반 (실 계좌 미사용). */}
      <PerformanceDashboard />
      {/* #49 / 6-04: 성과 지표 요약 — expectancy + 체결 실패율/거절률/부분체결률 +
          차단 사유 TOP + Council vs best single (read-only, 표시 전용). */}
      <PerformanceMetricsSummary />
      {/* P-29: 전략 가중치 개선 후보 — 자동 적용 금지, 운영자 승인 전 변경 없음. */}
      <WeightRecommendationCard />
      {/* P-30: Paper Gate 성과 리포트 — 100건 미만 실전 전환 검토 불가, 자동 전환 없음. */}
      <PaperGateReportCard />
      {/* STRATEGY-VALIDATION-01: 전략 가능성 종합 평가 — backtest/WF/stress/paper 종합,
          자동 적용/실전 전환/주문 0건, sample fixture 는 STRONG 불가. */}
      <StrategyPotentialReportCard />
      {/* REAL-DATA-STRATEGY-01: 실제/준실제 데이터 기반 전략 검증 — CSV/yfinance,
          KIS historical 미구현, 자동 적용/실전 전환/주문 0건, sample fixture 경고. */}
      <RealDataStrategyValidationCard />
      {/* INTRADAY-DATA-01: 분봉 단타 전략 검증 — ORB/VWAP 등은 분봉 필요(일봉 0 trade),
          자동 적용/실전 전환/주문 0건. */}
      <IntradayStrategyValidationCard />
      {/* KIS-INTRADAY-100-VALIDATION-01: 실제 KIS 분봉 100종목 전략 최종 판정 —
          read-only 시세 수집, KIS 주문 API 0건, 자동 적용/실전 전환/주문 0건. */}
      <KisIntraday100ValidationCard />
      {/* WF-6M-50SYMBOLS-01: 6개월·50종목·1000만원 자금곡선 + 등급화 + 업그레이드 방향 —
          Paper/Backtest only, KIS 주문 0건, 자동 적용/실전 전환/주문 0건. */}
      <Wf6m50SymbolsCard />
      {/* WF-6M ROOT-CAUSE+REBUILD: 손실 원인분해 + 재설계 실험 — Paper/Backtest only,
          KIS 주문 0건, 자동 적용/실전 전환/주문 0건. */}
      <Wf6mRootCauseCard />
      <Wf6mRebuildExperimentsCard />
      {/* DECOMPOSITION-01: 매매기법 vs Agent 분해 — Paper/Backtest only, EXE 빌드 0건. */}
      <StrategyAgentDecompositionCard />
      {/* FORWARD-VALIDATION-01: 고정 룰 forward/OOS 검증 — Paper/Backtest only, EXE 빌드 0건. */}
      <ForwardValidationCard />
      {/* FORWARD-UNIVERSE-REBUILD-01: point-in-time 종목 선별 검증 — Paper/Backtest only, EXE 빌드 0건. */}
      <ForwardUniverseCard />
      {/* 60D-WEEKLY-FIXED-REVALIDATION-01: 고정 룰 holdout 재검증 — Paper/Backtest only, EXE 빌드 0건. */}
      <Locked60dWeeklyValidationCard />
      {/* 60D-WEEKLY-NEW-DATA: 고정 룰을 추가 기간 새 데이터로 재검증 — Paper/Backtest only, EXE 빌드 0건. */}
      <Locked60dWeeklyNewDataCard />
      {/* 1Y-SCALED-VALIDATION: 1년 데이터 10/25/50 확장 검증 — Paper/Backtest only, EXE 빌드 0건. */}
      <Intraday1YScaledValidationCard />
      {/* P-31: 분석용 데이터 내보내기 (CSV/JSONL) — secret 미포함, 주문 신호 아님. */}
      <DecisionEpisodeExportButton />
      {/* P-32: 이벤트 로그 품질 점검 — 진단 전용, 자동 주문 중단 아님. */}
      <EventIntegrityDiagnosticsCard />
      <AgentDecisionSummaryCard />
      <AgentStatsCard />
      {/* 44: AI Assist 제안 카드 — AI는 제안만, 사람 승인 후 주문. */}
      <AiAssistProposalCard defaultSymbol={ticker} />

      {/* 45: AI 자동 실행 정책 카드 — 기본 비활성, read-only 표시.
          토글 / 활성화 버튼은 의도적으로 추가하지 않는다. */}
      <AiExecutionPolicyMount />

      {/* 입력 */}
      <Card>
        <SectionLabel>🧠 AI 합류 신호 분석</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569", marginBottom: 10 }}>
          Google Trends + 네이버증권 + 수급 + 기술적지표 통합 분석
        </div>
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 5 }}>종목명 또는 코드</div>
          <Inp value={ticker} onChange={setTicker} placeholder="예: 삼성전자 / 005930" />
        </div>
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 5 }}>추가 컨텍스트 (선택)</div>
          <Inp value={extra} onChange={setExtra} placeholder="예: 오늘 실적 발표, 외국인 급매수" />
        </div>
        <Btn onClick={run} disabled={busy || !ticker.trim()} color="#7dd3fc" full>
          {busy
            ? "⟳ Google Trends · 네이버 · AI 분석 중..."
            : "🔍 합류 신호 분석 시작"}
        </Btn>
        {activeNames.length > 0 && (
          <div style={{ marginTop: 8, fontSize: 10, color: "#334155" }}>
            활성 전략: {activeNames.join(" · ")}
          </div>
        )}
      </Card>

      {error && (
        <Card accentColor="#ef444433">
          <div style={{ color: "#f87171", fontSize: 12 }}>{error}</div>
        </Card>
      )}

      {/* 합류점수 게이지 */}
      {score && (
        <Card accentColor={confluenceColor(score.total) + "55"}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>합류 점수 (Confluence)</div>
            <div style={{ fontSize: 30, fontWeight: 700, color: confluenceColor(score.total) }}>
              {score.total}
            </div>
          </div>

          <ScoreBar label="🔧 기술적 신호" value={score.tech}  color="#7dd3fc" />
          <ScoreBar label="📈 Google Trends" value={score.trend} color="#a78bfa" />
          <ScoreBar label="📰 네이버 뉴스"  value={score.news}  color="#f59e0b" />
          <ScoreBar label="💰 외인·기관 수급" value={score.flow}  color="#22c55e" />

          {/* 매매 신호 */}
          <div style={{ marginTop: 12, padding: "10px 14px", background: "#010a14", borderRadius: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span style={{ fontSize: 14, fontWeight: 700, color: SIGNAL_COLOR[score.signal] ?? "#94a3b8" }}>
                ● {score.signal}
              </span>
              <span style={{ fontSize: 11, color: "#64748b" }}>신뢰도 {score.conf}%</span>
            </div>

            {score.entry > 0 && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, fontSize: 12 }}>
                {[
                  ["진입가", score.entry, "#7dd3fc"],
                  ["목표가", score.target, "#22c55e"],
                  ["손절가", score.stop,   "#ef4444"],
                ].map(([label, val, color]) => (
                  <div key={label} style={{ textAlign: "center", padding: 8, background: "#0c2035", borderRadius: 4 }}>
                    <div style={{ fontSize: 10, color: "#475569" }}>{label}</div>
                    <div style={{ fontWeight: 700, color }}>{fmtKRW(val)}</div>
                  </div>
                ))}
              </div>
            )}

            {/* 진입 가이드 */}
            <div style={{ marginTop: 8, fontSize: 11 }}>
              {score.total >= 70
                ? <span style={{ color: "#22c55e" }}>✓ 합류점수 70+ → 진입 조건 충족</span>
                : score.total >= 50
                ? <span style={{ color: "#facc15" }}>⚠ 합류점수 {score.total} → 관망 권장</span>
                : <span style={{ color: "#ef4444" }}>✗ 합류점수 {score.total} → 진입 보류</span>
              }
            </div>
          </div>
        </Card>
      )}

      {/* 스트리밍 분석 텍스트 */}
      {explanation && (
        <Card>
          <SectionLabel>AI 분석 상세</SectionLabel>
          <div style={{
            maxHeight: 300, overflowY: "auto",
            fontSize: 12, lineHeight: 1.8, color: "#94a3b8", whiteSpace: "pre-wrap",
          }}>
            {explanation}
            {busy && (
              <span style={{
                display: "inline-block", width: 7, height: 13,
                background: "#7dd3fc", marginLeft: 2,
                animation: "blink .7s step-end infinite",
              }} />
            )}
          </div>
        </Card>
      )}

      <div style={{ fontSize: 10, color: "#1e3a5c", lineHeight: 1.6 }}>
        ⚠ AI 분석은 참고용입니다. 투자 손익의 책임은 투자자 본인에게 있으며 원금 손실 가능성이 있습니다.
      </div>
    </div>
  );
}
