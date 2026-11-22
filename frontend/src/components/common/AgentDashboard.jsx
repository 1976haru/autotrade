import { useCallback, useEffect, useState } from "react";

import { backendApi } from "../../services/backend/client";
import { fmtKRW } from "../../utils/format";

// AG6: AI 에이전트 대시보드 — 깔때기/기법승률/보정/그림자추적/학습방향.
//   읽기 전용. 탭 진입/수동 새로고침만(폴링 0). CSS 막대(신규 차트 라이브러리 0).
const TABS = [
  { key: "daily", label: "오늘" },
  { key: "weekly", label: "주" },
  { key: "monthly", label: "월" },
];
const TECH_KO = { ORB: "ORB", MOMENTUM: "모멘텀", VWAP: "VWAP", GAP: "갭" };
const REASON_KO = {
  LOW_CONFIDENCE: "확신 부족", LOW_QUALITY_SCORE: "품질 부족",
  KIS_PAPER_ORDER_LIMIT_EXCEEDED: "일일 주문 한도", KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED: "1회 금액 한도",
  MAX_CONCURRENT_POSITIONS_REACHED: "동시 보유 한도", DAILY_BUY_LIMIT_REACHED: "일일 매수 한도",
  DUPLICATE_POSITION_BLOCKED: "이미 보유", KIS_PAPER_ERROR: "시세 제한",
  "사유 미기록": "사유 미기록",
};

const Bar = ({ pct, color }) => (
  <div style={{ flex: 1, height: 8, background: "rgba(40,20,24,.12)", borderRadius: 999, overflow: "hidden" }}>
    <div style={{ width: `${Math.max(0, Math.min(100, pct))}%`, height: "100%", background: color, borderRadius: 999 }} />
  </div>
);

export function AgentDashboard({ api = backendApi }) {
  const [period, setPeriod] = useState("daily");
  const [d, setD] = useState({});
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const [funnel, tech, cal, shadow, learning] = await Promise.allSettled([
      api.agentFunnel({ period }), api.performanceByTechnique({ period }),
      api.agentCalibration({ period }), api.agentShadow({ period }), api.agentLearning({ period }),
    ]);
    setD({
      funnel: funnel.status === "fulfilled" ? funnel.value : null,
      tech: tech.status === "fulfilled" ? tech.value : null,
      cal: cal.status === "fulfilled" ? cal.value : null,
      shadow: shadow.status === "fulfilled" ? shadow.value : null,
      learning: learning.status === "fulfilled" ? learning.value : null,
    });
    setLoading(false);
  }, [api, period]);

  useEffect(() => { load(); }, [load]); // 탭 진입/period 변경 시만(폴링 아님)

  const { funnel, tech, cal, shadow, learning } = d;
  const maxStage = Math.max(1, ...(funnel?.stages || []).map((s) => s.count));
  const maxTech = Math.max(1, ...((tech?.techniques || []).map((t) => Math.abs(t.net_contribution_krw || 0))));

  return (
    <div data-testid="agent-dashboard" style={{ marginTop: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: "#2a1418" }}>AI 에이전트</div>
        <div style={{ display: "flex", gap: 4 }}>
          {TABS.map((t) => (
            <button key={t.key} type="button" data-testid={`agent-tab-${t.key}`} onClick={() => setPeriod(t.key)}
              style={{ padding: "3px 9px", borderRadius: 7, fontFamily: "inherit", fontSize: 12, fontWeight: 800, cursor: "pointer",
                border: `1px solid ${period === t.key ? "#5b9dff" : "rgba(40,20,24,.2)"}`,
                background: period === t.key ? "rgba(91,157,255,.16)" : "transparent",
                color: period === t.key ? "#2563eb" : "rgba(40,20,24,.7)" }}>{t.label}</button>
          ))}
          <button type="button" data-testid="agent-refresh" onClick={load} disabled={loading}
            style={{ fontSize: 12, border: "none", background: "transparent", cursor: "pointer", color: "rgba(40,20,24,.6)" }}>↻</button>
        </div>
      </div>

      {/* 1. 결정 깔때기 */}
      <div data-testid="agent-funnel" style={sec}>
        <div style={secTitle}>결정 깔때기</div>
        {(!funnel || funnel.no_data) ? <Empty testid="agent-funnel-empty" /> : (funnel?.stages || []).map((s, i) => {
          const drop = (funnel?.drops || [])[i - 1];
          return (
            <div key={s.key}>
              {drop && drop.count > 0 && (
                <div style={{ fontSize: 11, color: "rgba(40,20,24,.55)", padding: "1px 0 3px 4px" }}>
                  ↓ −{drop.count}{drop.reasons?.[0] && ` (${REASON_KO[drop.reasons[0].reason_code] || drop.reasons[0].reason_code} ${drop.reasons[0].count})`}
                </div>
              )}
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0" }}>
                <span style={{ width: 76, fontSize: 12.5, color: "#2a1418", fontWeight: 700 }}>{s.label}</span>
                <Bar pct={(s.count / maxStage) * 100} color="#5b9dff" />
                <span style={{ width: 28, textAlign: "right", fontWeight: 800, fontSize: 13 }}>{s.count}</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* 2. 기법별 순손익 기여 (S4 재사용) */}
      <div data-testid="agent-technique" style={sec}>
        <div style={secTitle}>기법별 기여{tech?.small_sample && !tech?.no_data && <Badge />}</div>
        {(!tech || tech.no_data) ? <Empty testid="agent-technique-empty" /> : (tech?.techniques || []).map((t) => (
          <div key={t.technique} data-testid={`agent-tech-${t.technique}`} style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0", fontSize: 12.5 }}>
            <span style={{ width: 52, fontWeight: 700, color: "#2a1418" }}>{TECH_KO[t.technique] || t.technique}</span>
            <span style={{ width: 84, color: "rgba(40,20,24,.65)" }}>찬성 {t.trade_count}{t.win_rate != null && ` · ${Math.round(t.win_rate * 100)}%`}</span>
            <Bar pct={(Math.abs(t.net_contribution_krw || 0) / maxTech) * 100} color={t.net_contribution_krw >= 0 ? "#c0392b" : "#1f6feb"} />
            <span style={{ width: 80, textAlign: "right", fontWeight: 800, color: t.net_contribution_krw >= 0 ? "#c0392b" : "#1f6feb" }}>{t.net_contribution_krw > 0 ? "+" : ""}{fmtKRW(t.net_contribution_krw)}</span>
          </div>
        ))}
      </div>

      {/* 3. 확신도 보정 */}
      <div data-testid="agent-calibration" style={sec}>
        <div style={secTitle}>확신도 보정 (예측 vs 실제)</div>
        {(!cal || cal.no_data) ? <Empty testid="agent-cal-empty" /> : (cal?.buckets || []).map((b) => (
          <div key={b.bucket} data-testid={`agent-cal-${b.bucket}`} style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0", fontSize: 12.5 }}>
            <span style={{ width: 58, fontWeight: 700 }}>{b.bucket}</span>
            <Bar pct={b.win_rate * 100} color="#15a05f" />
            <span style={{ width: 96, textAlign: "right", color: "rgba(40,20,24,.7)" }}>실제 {Math.round(b.win_rate * 100)}% ({b.trade_count}건{b.small_sample ? "·표본적음" : ""})</span>
          </div>
        ))}
      </div>

      {/* 4. 그림자 추적 */}
      <div data-testid="agent-shadow" style={sec}>
        <div style={secTitle}>기각 신호 그림자 추적</div>
        {(!shadow || shadow.no_data) ? (
          <div data-testid="agent-shadow-empty" style={emptyStyle}>
            추적 완료 건이 쌓이면 표시돼요{shadow?.tracking_count > 0 && ` (추적 중 ${shadow.tracking_count}건)`}
          </div>
        ) : (
          <div style={{ fontSize: 12.5, color: "#2a1418", lineHeight: 1.6 }}>
            <span data-testid="agent-shadow-summary" style={{ fontWeight: 700 }}>
              기각 {shadow?.completed_count}건 중 {shadow?.correct_rejection_count}건은 안 사길 잘했어요
              {shadow?.correct_rate != null && ` (${Math.round(shadow.correct_rate * 100)}%)`}
            </span>
            <div style={{ color: "rgba(40,20,24,.7)" }}>
              회피 손실 −{fmtKRW(shadow?.avoided_loss_krw)}원 / 놓친 이익 +{fmtKRW(shadow?.missed_gain_krw)}원
              {shadow?.tracking_count > 0 && ` · 추적 중 ${shadow.tracking_count}건`}
            </div>
          </div>
        )}
      </div>

      {/* 5. 다음 학습 방향 */}
      <div data-testid="agent-learning" style={sec}>
        <div style={secTitle}>다음 학습 방향</div>
        {(learning?.observations || []).map((o, i) => (
          <div key={i} data-testid={`agent-obs-${o.code}`} style={{ fontSize: 12.5, color: "#2a1418", padding: "2px 0", lineHeight: 1.5 }}>· {o.text}</div>
        ))}
        {learning?.footer && (
          <div data-testid="agent-learning-footer" style={{ fontSize: 11, color: "rgba(40,20,24,.55)", marginTop: 6 }}>{learning.footer}</div>
        )}
      </div>
    </div>
  );
}

const sec = { marginTop: 10, borderTop: "1px solid rgba(40,20,24,.1)", paddingTop: 8 };
const secTitle = { fontSize: 12.5, fontWeight: 800, color: "#2a1418", marginBottom: 5 };
const emptyStyle = { fontSize: 12.5, color: "rgba(40,20,24,.6)", marginTop: 2 };
const Empty = ({ testid }) => <div data-testid={testid} style={emptyStyle}>— 쌓이는 중이에요</div>;
const Badge = () => <span style={{ fontSize: 11, fontWeight: 700, color: "#7a4a00", marginLeft: 5 }}>· 표본 적음 · 참고용</span>;
