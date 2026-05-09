import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

// 54: Risk Auditor card — 장중 리스크 감사 advisory.
//
// **주문 신호가 아님 / 안전 리포트**. BUY/SELL/HOLD 표시 X. 매수/매도 버튼 X.
// 본 카드의 어떤 버튼도 emergency_stop을 *직접 토글하지 않는다* — 운영자는
// 기존 Kill Switch UI에서 수동 토글 (본 카드는 권고만 표시).

const _LEVEL_PALETTE = {
  GREEN:  { color: "#22c55e", label: "정상",   bg: "#0c2035" },
  YELLOW: { color: "#fbbf24", label: "경고",   bg: "#3b2a05" },
  ORANGE: { color: "#fb923c", label: "주의",   bg: "#3b1f25" },
  RED:    { color: "#ef4444", label: "긴급",   bg: "#3b1f25" },
};

const _SEVERITY_COLOR = {
  INFO:     "#94a3b8",
  WARN:     "#fbbf24",
  HIGH:     "#fb923c",
  CRITICAL: "#ef4444",
};


function _Field({ label, value }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between",
                   padding: "5px 0", borderBottom: "1px solid #1e3a5c33",
                   fontSize: 11 }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span style={{ color: "#e2e8f0", fontWeight: 700 }}>{value}</span>
    </div>
  );
}


function _EventRow({ event }) {
  const color = _SEVERITY_COLOR[event.severity] || "#94a3b8";
  return (
    <div data-testid={`risk-event-${event.type}`}
         style={{ padding: "6px 8px", marginBottom: 4,
                   background: "#0c2035", borderRadius: 3,
                   borderLeft: `3px solid ${color}`,
                   fontSize: 11 }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                     marginBottom: 2 }}>
        <span style={{ fontWeight: 700, color: "#e2e8f0" }}>
          {event.type}
        </span>
        <span style={{ fontSize: 9, fontWeight: 700, color }}>
          {event.severity}
        </span>
      </div>
      <div style={{ color: "#94a3b8", lineHeight: 1.5 }}>
        {event.summary}
      </div>
      {event.recommended_action && (
        <div style={{ marginTop: 3, fontSize: 9, color: "#7dd3fc" }}>
          → {event.recommended_action}
        </div>
      )}
    </div>
  );
}


export function RiskAuditorCard({ report, loading, error, onRefresh }) {
  if (loading && !report) {
    return (
      <Card>
        <SectionLabel>🛡 리스크 감사</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>🛡 리스크 감사</SectionLabel>
        <div data-testid="risk-auditor-error"
             style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.6 }}>
          리스크 감사 데이터를 아직 불러오지 못했습니다.
          {onRefresh && (
            <div style={{ marginTop: 8 }}>
              <button onClick={onRefresh} style={{
                fontSize: 10, padding: "3px 8px", background: "#0c2035",
                border: "1px solid #1e3a5c", borderRadius: 3,
                cursor: "pointer", color: "#7dd3fc",
              }}>↻ 다시 시도</button>
            </div>
          )}
        </div>
      </Card>
    );
  }
  if (!report) return null;

  const palette = _LEVEL_PALETTE[report.audit_level] || _LEVEL_PALETTE.GREEN;
  const events = report.events || [];

  return (
    <Card data-testid="risk-auditor-card" accentColor={`${palette.color}55`}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>🛡 리스크 감사</SectionLabel>
        <span data-testid="risk-auditor-not-order-badge" style={{
          fontSize: 9, fontWeight: 700, color: "#94a3b8",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #94a3b855", background: "#94a3b815",
        }}>
          주문 신호 아님 · 안전 리포트
        </span>
      </div>

      {/* 운영자 요약 */}
      <div style={{ marginBottom: 8, padding: "8px 10px",
                     background: palette.bg,
                     border: `1px solid ${palette.color}33`,
                     borderRadius: 4 }}>
        {(report.summary_lines || []).map((line, i) => (
          <div key={i} data-testid={`risk-auditor-line-${i}`}
               style={{ fontSize: 11, color: "#e2e8f0",
                         lineHeight: 1.6 }}>
            {line}
          </div>
        ))}
      </div>

      {/* 핵심 상태 */}
      <_Field
        label="감사 단계"
        value={
          <span data-testid="risk-auditor-level"
                style={{ color: palette.color }}>
            {palette.label} ({report.audit_level})
          </span>
        }
      />
      <_Field
        label="위험 점수"
        value={
          <span data-testid="risk-auditor-score">
            {report.risk_score} / 100
          </span>
        }
      />
      <_Field
        label="감지 이벤트"
        value={`${events.length}건`}
      />
      <_Field
        label="감사 row"
        value={`${report.total_audit_rows_inspected}건`}
      />

      {/* PAUSE / EMERGENCY_STOP 권고 (advisory only) */}
      {report.emergency_stop_recommended && (
        <div data-testid="risk-auditor-stop-recommendation"
             style={{ marginTop: 8, padding: "8px 10px",
                       background: "#3b1f25",
                       border: "1px solid #ef444466",
                       borderRadius: 4, fontSize: 11,
                       color: "#fca5a5", lineHeight: 1.6 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>
            ⚠ EMERGENCY_STOP_RECOMMENDED
          </div>
          <div style={{ fontSize: 10 }}>
            본 카드는 *권고*만 합니다. 실제 긴급정지 토글은{" "}
            <strong>전략·리스크 탭</strong>의 Kill Switch UI에서 운영자가
            수동으로 수행합니다.
            {report.recommended_stop_reason && (
              <div style={{ marginTop: 3, fontFamily: "monospace" }}>
                권고 reason 코드: {report.recommended_stop_reason}
              </div>
            )}
          </div>
        </div>
      )}
      {!report.emergency_stop_recommended && report.pause_trading_recommended && (
        <div data-testid="risk-auditor-pause-recommendation"
             style={{ marginTop: 8, padding: "8px 10px",
                       background: "#3b2a05",
                       border: "1px solid #fbbf2466",
                       borderRadius: 4, fontSize: 11,
                       color: "#fbbf24", lineHeight: 1.6 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>
            ⓘ PAUSE_TRADING_RECOMMENDED
          </div>
          <div style={{ fontSize: 10, color: "#94a3b8" }}>
            거래 일시 중단을 권고합니다 — 신규 진입 회피, 보유 포지션 모니터링.
            본 카드는 *권고*만 — 실제 거래 중단은 운영자가 수행.
          </div>
        </div>
      )}

      {/* 이벤트 목록 */}
      {events.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 10, color: "#475569",
                         marginBottom: 4 }}>위험 이벤트</div>
          <div data-testid="risk-auditor-events">
            {events.map((e, i) => (
              <_EventRow key={`${e.type}-${i}`} event={e} />
            ))}
          </div>
        </div>
      )}

      <div style={{ marginTop: 8, fontSize: 9, color: "#64748b",
                     lineHeight: 1.5 }}>
        ※ 본 리포트는 *주문 신호가 아닙니다*. 안전 감독 advisory 전용 — 실제
        거래 / 긴급정지 / 포지션 청산은 운영자가 기존 UI에서 수동 수행합니다.
      </div>

      {onRefresh && (
        <div style={{ marginTop: 6, textAlign: "right" }}>
          <button onClick={onRefresh} style={{
            fontSize: 10, padding: "3px 8px", background: "#0c2035",
            border: "1px solid #1e3a5c", borderRadius: 3,
            cursor: "pointer", color: "#7dd3fc",
          }}>↻ 새로고침</button>
        </div>
      )}
    </Card>
  );
}


// 54: hook for /api/agents/risk-auditor/report.
export function useRiskAuditorReport(params = {}) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = async () => {
    setLoading(true); setError("");
    try {
      const data = await backendApi.riskAuditorReport(params);
      setReport(data);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setError("");
      try {
        const data = await backendApi.riskAuditorReport(params);
        if (!cancelled) setReport(data);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(params)]);

  return { report, loading, error, refresh };
}
