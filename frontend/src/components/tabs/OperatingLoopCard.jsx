import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

// 223: 오늘 Agent 운용 흐름 — 사용자가 스마트폰 한 화면에서 현재 단계 +
// 직전 brief / intraday / review를 인지할 수 있도록. backend 라우트는 모두
// deterministic stub이라 AI Key 없이도 mock output을 안정적으로 받는다.

const STAGE_LABEL = {
  pre_market:        "장 시작 전",
  market_open_watch: "장 초반 감시",
  intraday:          "장중 판단",
  position_monitor:  "포지션 점검",
  post_market:       "장 마감 복기",
};

const READINESS_COLOR = {
  READY:   "#22c55e",
  CAUTION: "#fbbf24",
  BLOCKED: "#ef4444",
};


export function OperatingLoopCard() {
  const [status, setStatus] = useState(null);
  const [brief,  setBrief]  = useState(null);
  const [intra,  setIntra]  = useState(null);
  const [review, setReview] = useState(null);
  const [error,  setError]  = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, b, i, r] = await Promise.all([
          backendApi.operatingLoopStatus(),
          backendApi.preMarketBrief({}),
          backendApi.intradaySummary({}),
          backendApi.postMarketReview({}),
        ]);
        if (cancelled) return;
        setStatus(s);
        setBrief(b);
        setIntra(i);
        setReview(r);
        setError("");
      } catch (e) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <Card>
        <SectionLabel>🧭 오늘 Agent 운용 흐름</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>🧭 오늘 Agent 운용 흐름</SectionLabel>
        <div style={{ fontSize: 11, color: "#f87171" }}>조회 실패: {error}</div>
      </Card>
    );
  }

  const stageLabel = STAGE_LABEL[status?.stage] ?? status?.stage ?? "—";
  const readinessColor = READINESS_COLOR[brief?.readiness_label] ?? "#7dd3fc";
  const stages = Array.isArray(status?.stages) ? status.stages : [];

  return (
    <Card data-testid="operating-loop-card" accentColor={`${readinessColor}33`}>
      <SectionLabel>🧭 오늘 Agent 운용 흐름</SectionLabel>

      <div style={{ fontSize: 9, color: "#334155", marginBottom: 8, lineHeight: 1.5 }}>
        Agent가 하루를 5단계로 나눠 자체 운용. 모든 결정은 RiskManager + PermissionGate
        + Audit Log를 통과합니다 (가상 모드).
      </div>

      {/* 현재 단계 */}
      <div data-testid="operating-loop-stage" style={{
        background: "#0c2035", padding: 8, borderRadius: 4, marginBottom: 8,
      }}>
        <div style={{ fontSize: 9, color: "#475569" }}>현재 단계</div>
        <div style={{ fontSize: 14, fontWeight: 700, color: "#7dd3fc" }}>{stageLabel}</div>
        {stages.length > 0 && (
          <div style={{ fontSize: 9, color: "#334155", marginTop: 3 }}>
            {stages.map((s) => STAGE_LABEL[s] ?? s).join(" → ")}
          </div>
        )}
      </div>

      {/* readiness */}
      {brief && (
        <div data-testid="operating-loop-brief" style={{
          background: "#0c2035", padding: 8, borderRadius: 4, marginBottom: 8,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: readinessColor }}>
              {brief.readiness_label} · 점수 {brief.readiness_score}
            </div>
            <div style={{ fontSize: 9, color: "#475569" }}>
              위험도 {brief.market_risk_level}
            </div>
          </div>
          {Array.isArray(brief.operator_summary) && brief.operator_summary.map((line, idx) => (
            <div key={idx} style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>
              {line}
            </div>
          ))}
        </div>
      )}

      {/* intraday */}
      {intra && (
        <div data-testid="operating-loop-intraday" style={{
          background: "#0c2035", padding: 8, borderRadius: 4, marginBottom: 8,
        }}>
          <div style={{ fontSize: 10, color: "#94a3b8", marginBottom: 3 }}>장중 누적</div>
          <div style={{ display: "flex", gap: 10, fontSize: 10 }}>
            <span>후보 <b style={{ color: "#7dd3fc" }}>{intra.candidates_evaluated}</b></span>
            <span>가상 주문 <b style={{ color: "#22c55e" }}>{intra.virtual_orders_made}</b></span>
            <span>거절 <b style={{ color: "#ef4444" }}>{intra.rejected_signals}</b></span>
          </div>
        </div>
      )}

      {/* post-market score */}
      {review && (
        <div data-testid="operating-loop-review" style={{
          background: "#0c2035", padding: 8, borderRadius: 4,
        }}>
          <div style={{ fontSize: 10, color: "#94a3b8", marginBottom: 3 }}>장 마감 복기</div>
          <div style={{ fontSize: 10 }}>
            총 결정 <b style={{ color: "#7dd3fc" }}>{review.total_decisions}</b>
            {" · "}점수 변화 <b style={{
              color: review.agent_score_delta >= 0 ? "#22c55e" : "#ef4444",
            }}>{review.agent_score_delta >= 0 ? "+" : ""}{review.agent_score_delta}</b>
          </div>
        </div>
      )}
    </Card>
  );
}
