import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { ErrorState, LoadingState } from "../common/primitives";
import { friendlyErrorMessage } from "../../utils/errorMessage";
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
        <LoadingState title="Agent 흐름 조회 중" />
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>🧭 오늘 Agent 운용 흐름</SectionLabel>
        <ErrorState
          title="Agent 흐름 조회 실패"
          hint={friendlyErrorMessage(error)}
          testId="operating-loop-error"
        />
      </Card>
    );
  }

  const stageLabel = STAGE_LABEL[status?.stage] ?? status?.stage ?? "—";
  const readinessColor = READINESS_COLOR[brief?.readiness_label] ?? "#7dd3fc";
  const stages = Array.isArray(status?.stages) ? status.stages : [];

  return (
    // 242 (Light-005): light surface + 토큰 색.
    <div data-testid="operating-loop-card" style={{
      background: "var(--c-surface)",
      border: "1px solid var(--c-border)",
      borderRadius: "var(--r-xl)",
      padding: "var(--s-5)",
      boxShadow: "var(--sh-1)",
    }}>
      <div style={{
        fontSize: "var(--fs-md)", fontWeight: "var(--fw-bold)",
        color: "var(--c-text)", marginBottom: 4,
      }}>🧭 오늘 Agent 운용 흐름</div>

      <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
                     marginBottom: 12, lineHeight: "var(--lh-loose)" }}>
        Agent가 하루를 5단계로 나눠 자체 운용. 모든 결정은 RiskManager +
        PermissionGate + Audit Log를 통과합니다 (가상 모드).
      </div>

      {/* 현재 단계 */}
      <div data-testid="operating-loop-stage" style={_subStyle()}>
        <div style={_subLabel()}>현재 단계</div>
        <div style={{ fontSize: "var(--fs-lg)", fontWeight: "var(--fw-bold)",
                       color: "var(--c-info)", marginTop: 4 }}>{stageLabel}</div>
        {stages.length > 0 && (
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
                         marginTop: 6, lineHeight: "var(--lh-base)" }}>
            {stages.map((s) => STAGE_LABEL[s] ?? s).join(" → ")}
          </div>
        )}
      </div>

      {/* readiness */}
      {brief && (
        <div data-testid="operating-loop-brief" style={_subStyle()}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontSize: "var(--fs-md)", fontWeight: "var(--fw-bold)",
                           color: readinessColor }}>
              {brief.readiness_label} · 점수 {brief.readiness_score}
            </div>
            <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
              위험도 {brief.market_risk_level}
            </div>
          </div>
          {Array.isArray(brief.operator_summary) && brief.operator_summary.map((line, idx) => (
            <div key={idx} style={{ fontSize: "var(--fs-sm)",
                                     color: "var(--c-text-2)", marginTop: 4 }}>
              {line}
            </div>
          ))}
        </div>
      )}

      {/* intraday */}
      {intra && (
        <div data-testid="operating-loop-intraday" style={_subStyle()}>
          <div style={_subLabel()}>장중 누적</div>
          <div style={{ display: "flex", gap: 12, fontSize: "var(--fs-sm)",
                          marginTop: 4, color: "var(--c-text-2)" }}>
            <span>후보 <b style={{ color: "var(--c-info)" }}>{intra.candidates_evaluated}</b></span>
            <span>가상 주문 <b style={{ color: "var(--c-success)" }}>{intra.virtual_orders_made}</b></span>
            <span>거절 <b style={{ color: "var(--c-danger)" }}>{intra.rejected_signals}</b></span>
          </div>
        </div>
      )}

      {/* post-market score */}
      {review && (
        <div data-testid="operating-loop-review" style={{ ..._subStyle(), marginBottom: 0 }}>
          <div style={_subLabel()}>장 마감 복기</div>
          <div style={{ fontSize: "var(--fs-sm)", marginTop: 4,
                          color: "var(--c-text-2)" }}>
            총 결정 <b style={{ color: "var(--c-info)" }}>{review.total_decisions}</b>
            {" · "}점수 변화 <b style={{
              color: review.agent_score_delta >= 0 ? "var(--c-success)" : "var(--c-danger)",
            }}>{review.agent_score_delta >= 0 ? "+" : ""}{review.agent_score_delta}</b>
          </div>
        </div>
      )}
    </div>
  );
}


function _subStyle() {
  return {
    background: "var(--c-surface-2)",
    border: "1px solid var(--c-border)",
    padding: "12px 14px",
    borderRadius: "var(--r-md)",
    marginBottom: 8,
  };
}
function _subLabel() {
  return {
    fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
    textTransform: "uppercase", letterSpacing: "0.06em",
    fontWeight: "var(--fw-bold)",
  };
}
