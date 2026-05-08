/**
 * 42: Paper Trading Mode read-only status card.
 *
 * 현재 paper mode + 사용 중인 broker + 안전 flag를 표시한다. 어떤 주문 /
 * test 버튼도 만들지 않는다 — 상태 표시 전용 (CLAUDE.md 절대 원칙: 실제
 * 주문 코드 작성 전 MockBroker / 테스트가 우선).
 *
 * 표시:
 * - 현재 모드 + paper 여부 배지
 * - paper_broker_kind (MOCK / KIS_PAPER)
 * - 4 안전 flag (live_trading / ai_execution / futures_live / kis_is_paper)
 * - 모의투자 체결 품질 주의 안내
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "./index";
import { ErrorState, LoadingState, StatusBadge } from "./primitives";
import { backendApi } from "../../services/backend/client";


const BROKER_LABEL = {
  MOCK:      { label: "MockBroker (즉시 가상 체결)", status: "info" },
  KIS_PAPER: { label: "KIS 모의투자",                 status: "warning" },
};


function friendlyError(err) {
  if (!err) return "알 수 없는 오류가 발생했습니다.";
  const msg = String(err.message || "").toLowerCase();
  if (msg.includes("failed to fetch") || msg.includes("networkerror")) {
    return "백엔드 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
  }
  return "Paper 모드 상태를 불러오는 중 오류가 발생했습니다.";
}


function FlagRow({ label, value, dangerWhenTrue = false, testId }) {
  // dangerWhenTrue=true이면 value=true일 때 위험 색 (예: enable_live_trading).
  // dangerWhenTrue=false면 value=true일 때 안전 색 (예: kis_is_paper).
  const isOk = dangerWhenTrue ? !value : value;
  const status = isOk ? "success" : "danger";
  return (
    <div data-testid={testId} style={{
      display: "flex", justifyContent: "space-between",
      fontSize: "var(--fs-xs)", lineHeight: 1.6,
    }}>
      <span style={{ color: "var(--c-text-3)" }}>{label}</span>
      <StatusBadge status={status}>
        {value ? "ON" : "OFF"}
      </StatusBadge>
    </div>
  );
}


export function PaperModeStatusCard({ testId = "paper-mode-status-card", autoLoad = true }) {
  const [state, setState] = useState({ requestKey: 0, error: null, status: null });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!autoLoad) return;
    let cancelled = false;
    backendApi.paperStatus()
      .then((s) => {
        if (!cancelled) setState({ requestKey: tick, error: null, status: s });
      })
      .catch((err) => {
        if (!cancelled) setState({ requestKey: tick, error: err, status: null });
      });
    return () => { cancelled = true; };
  }, [tick, autoLoad]);

  const isReady = state.requestKey === tick && (state.status || state.error);

  if (!isReady) {
    return (
      <Card>
        <LoadingState testId={`${testId}-loading`} title="Paper 모드 상태 확인 중..." />
      </Card>
    );
  }
  if (state.error) {
    return (
      <Card>
        <ErrorState
          testId={`${testId}-error`}
          title="Paper 모드 상태 조회 실패"
          hint={friendlyError(state.error)}
          retryLabel="다시 시도"
          onRetry={() => setTick((t) => t + 1)}
        />
      </Card>
    );
  }

  const s = state.status;
  const brokerMeta = BROKER_LABEL[s.paper_broker_kind] ||
    { label: s.paper_broker_kind, status: "neutral" };

  return (
    <Card>
      <div data-testid={testId} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "baseline",
          gap: 8, flexWrap: "wrap",
        }}>
          <SectionLabel>Paper Trading</SectionLabel>
          <StatusBadge
            status={s.is_paper_mode ? "success" : "neutral"}
            testId={`${testId}-mode-badge`}
          >
            {s.is_paper_mode ? `🧪 ${s.mode}` : s.mode}
          </StatusBadge>
        </div>

        <div data-testid={`${testId}-broker`} style={{
          display: "flex", justifyContent: "space-between", alignItems: "baseline",
          fontSize: "var(--fs-sm)",
        }}>
          <span style={{ color: "var(--c-text-3)" }}>Paper Broker</span>
          <StatusBadge status={brokerMeta.status}>
            {brokerMeta.label}
          </StatusBadge>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <FlagRow
            testId={`${testId}-flag-kis-is-paper`}
            label="KIS_IS_PAPER (모의투자 강제)"
            value={s.kis_is_paper}
          />
          <FlagRow
            testId={`${testId}-flag-live-trading`}
            label="ENABLE_LIVE_TRADING (실거래)"
            value={s.enable_live_trading}
            dangerWhenTrue
          />
          <FlagRow
            testId={`${testId}-flag-ai-execution`}
            label="ENABLE_AI_EXECUTION (AI 자동)"
            value={s.enable_ai_execution}
            dangerWhenTrue
          />
          <FlagRow
            testId={`${testId}-flag-futures-live`}
            label="ENABLE_FUTURES_LIVE_TRADING"
            value={s.enable_futures_live_trading}
            dangerWhenTrue
          />
          <FlagRow
            testId={`${testId}-flag-fill-polling`}
            label="ENABLE_FILL_POLLING"
            value={s.fill_polling_enabled}
          />
        </div>

        <div
          data-testid={`${testId}-notice`}
          style={{
            padding: 8, borderRadius: 4,
            background: "rgba(245, 158, 11, 0.08)",
            border: "1px solid rgba(245, 158, 11, 0.25)",
            fontSize: "var(--fs-xs)", color: "var(--c-warning)",
            lineHeight: 1.5,
          }}
        >
          ⚠ {s.notice}
        </div>

        <button
          type="button"
          data-testid={`${testId}-refresh`}
          onClick={() => setTick((t) => t + 1)}
          style={{
            alignSelf: "flex-end", fontSize: 11, fontWeight: 700,
            padding: "3px 10px", borderRadius: 3, cursor: "pointer",
            border: "1px solid var(--c-border)", background: "transparent",
            color: "var(--c-text-3)", fontFamily: "inherit",
          }}>
          새로고침
        </button>
      </div>
    </Card>
  );
}


export default PaperModeStatusCard;
