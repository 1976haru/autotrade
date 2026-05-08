import { useState } from "react";
import { Card, SectionLabel, Btn, Inp, ScoreBar } from "../common";
import { runAgentAnalysis } from "../../services/ai/claudeAgent";
import { STRATEGIES } from "../../config/strategies";
import { SIGNAL_COLOR, confluenceColor } from "../../utils/format";
import { fmtKRW } from "../../utils/format";
import { AgentCouncilCard } from "./AgentCouncilCard";
import { AgentDecisionSummaryCard } from "./AgentDecisionSummaryCard";
import { AgentStatsCard } from "./AgentStatsCard";
import { OperatingLoopCard } from "./OperatingLoopCard";
import { ThemeSignalsCard } from "./ThemeSignalsCard";

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
      <AgentDecisionSummaryCard />
      <AgentStatsCard />

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
