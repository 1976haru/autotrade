import { useEffect, useState } from "react";
import {
  PageHeader,
  StatusBadge,
  StatusPill,
} from "../common/primitives";
import { useBackendStatus } from "../../store/useBackendStatus";

// 230 (UI-002): Premium hero card. 사용자가 앱을 열자마자 핵심 5가지를 인지:
//   1) 앱 이름  2) 운용 모드  3) Demo/Backend 연결  4) 마지막 업데이트  5) 핵심 alert
// 인라인 스타일 대신 .ui-* 토큰 기반 클래스 + 인라인은 동적 색만.

const MODE_DISPLAY = {
  SIMULATION:           { color: "var(--c-info)",    label: "SIMULATION",    note: "가짜 데이터 + Mock Broker" },
  PAPER:                { color: "var(--c-info)",    label: "PAPER",         note: "실 시세 + 모의투자" },
  LIVE_SHADOW:          { color: "var(--c-warning)", label: "LIVE SHADOW",   note: "실 계좌 read-only" },
  LIVE_MANUAL_APPROVAL: { color: "var(--c-warning)", label: "LIVE MANUAL",   note: "운영자 승인 후 주문" },
  LIVE_AI_ASSIST:       { color: "var(--c-warning)", label: "LIVE AI ASSIST",note: "AI 후보 + 사용자 승인" },
  LIVE_AI_EXECUTION:    { color: "var(--c-danger)",  label: "LIVE AI EXEC",  note: "기본 비활성화" },
  VIRTUAL_AI_EXECUTION: { color: "var(--c-accent)",  label: "VIRTUAL AI",    note: "가상 자동 실행 (검증)" },
};


function _formatHHmm(date) {
  const h = String(date.getHours()).padStart(2, "0");
  const m = String(date.getMinutes()).padStart(2, "0");
  return `${h}:${m}`;
}


export function HeroSummaryCard({
  emergencyStop = false,
  pendingCount = 0,
  stalePendingCount = 0,
}) {
  const { status, error, loading } = useBackendStatus();
  // 마지막 업데이트 시각 — backend 응답(혹은 에러)이 settle된 시점. setState
  // 는 의도적으로 deps 변경 직후(useBackendStatus 가 비동기 fetch 종료 후
  // status/error를 갱신한 다음 tick) 발생 — react-hooks/set-state-in-effect
  // 가 경고하는 cascade가 아니라 단순 mirror.
  const [updatedAt, setUpdatedAt] = useState(() => new Date());
  useEffect(() => {
    if (status || error) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setUpdatedAt(new Date());
    }
  }, [status, error]);

  const mode = status?.default_mode ?? "SIMULATION";
  const modeMeta = MODE_DISPLAY[mode] ?? MODE_DISPLAY.SIMULATION;

  // 연결 상태 — 백엔드 unreachable이면 Demo Mode 가정.
  const connState = loading ? "loading" : (error ? "demo" : "connected");
  const connLabel = connState === "connected" ? "Backend 연결됨"
                  : connState === "demo"      ? "Demo Mode (Backend 미연결)"
                  : "연결 확인 중";
  const connStatus = connState === "connected" ? "success"
                  : connState === "demo"      ? "info"
                  : "neutral";

  return (
    <section data-testid="hero-summary"
             style={{
               background: "linear-gradient(180deg, var(--c-surface), rgba(2, 14, 28, 0.6))",
               border: "1px solid var(--c-border)",
               borderRadius: "var(--r-xl)",
               padding: "var(--s-5)",
               boxShadow: "var(--sh-2)",
             }}>
      <PageHeader
        title="AI 단타 자동매매"
        subtitle="지능형 Agent OS · 가상 자동운용 검증 단계"
        right={
          <StatusBadge status={connStatus} testId="hero-mode-badge">
            {modeMeta.label}
          </StatusBadge>
        }
      />

      <div style={{
        display: "flex", flexWrap: "wrap", gap: "var(--s-2)",
        marginTop: "var(--s-3)", alignItems: "center",
      }}>
        <StatusPill status={connStatus} testId="hero-conn-pill">
          {connLabel}
        </StatusPill>
        <StatusPill
          status={emergencyStop ? "danger" : "success"}
          testId="hero-emergency-pill"
        >
          {emergencyStop ? "🛑 긴급 정지 ON" : "✓ 긴급 정지 OFF"}
        </StatusPill>
        {pendingCount > 0 && (
          <StatusPill
            status={stalePendingCount > 0 ? "danger" : "warning"}
            testId="hero-pending-pill"
          >
            결재 대기 {pendingCount}건
            {stalePendingCount > 0 ? ` (${stalePendingCount} stale)` : ""}
          </StatusPill>
        )}
        <span style={{
          marginLeft: "auto", fontSize: "var(--fs-xs)",
          color: "var(--c-text-3)",
        }}>
          마지막 업데이트 {_formatHHmm(updatedAt)} · 실거래 미실행
        </span>
      </div>

      <div style={{
        marginTop: "var(--s-3)", padding: "var(--s-2) var(--s-3)",
        background: "var(--c-surface-2)", borderRadius: "var(--r-md)",
        fontSize: "var(--fs-sm)", color: "var(--c-text-2)",
        lineHeight: "var(--lh-base)",
      }} data-testid="hero-mode-note">
        <b style={{ color: "var(--c-text)" }}>운용 모드 안내:</b> {modeMeta.note}.
        모든 주문은 RiskManager + PermissionGate를 통과하며, AI Agent는 broker
        주문 API를 직접 호출하지 않습니다.
      </div>
    </section>
  );
}
