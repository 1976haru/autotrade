/**
 * 37: 3-Level Kill Switch read-only panel.
 *
 * 현재 level + 후보 카운트 + 위험 경고 문구를 한 카드로 표시. 운영자가
 * StrategyRisk / Settings / Dashboard 어디든 본 컴포넌트를 재사용할 수 있도록
 * common에 둔다.
 *
 * **자동 청산 / 자동 취소 버튼은 절대 만들지 않는다** — read-only candidate
 * count만 표시. 실제 취소 / 청산은 별도 수동 승인 흐름 (#37 절대 원칙 5/6/7).
 *
 * 정책:
 * - LEVEL 1 신규 매수 중단
 * - LEVEL 2 미체결 취소 후보 표시
 * - LEVEL 3 청산 후보 표시
 *
 * 본 컴포넌트는 토글 버튼을 내장하지 않는다 — 실제 토글 UI는 StrategyRisk
 * 탭의 기존 EmergencyStopConfirmModal이 담당. 본 컴포넌트는 *상태 표시*만.
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "./index";
import { EmptyState, ErrorState, LoadingState, StatusBadge } from "./primitives";
import { backendApi } from "../../services/backend/client";


const LEVEL_META = {
  OFF:     { label: "OFF",     status: "success", title: "정상 운영" },
  LEVEL_1: { label: "LEVEL 1", status: "warning", title: "신규 매수 중단" },
  LEVEL_2: { label: "LEVEL 2", status: "danger",  title: "+ 미체결 취소 후보" },
  LEVEL_3: { label: "LEVEL 3", status: "danger",  title: "+ 청산 후보 표시" },
};

const LEVEL_ROWS = [
  { id: "LEVEL_1", title: "LEVEL 1", desc: "신규 매수 즉시 중단" },
  { id: "LEVEL_2", title: "LEVEL 2", desc: "미체결 취소 후보 표시 (자동 취소 X)" },
  { id: "LEVEL_3", title: "LEVEL 3", desc: "청산 후보 표시 (자동 전량청산 X)" },
];


function friendlyError(err) {
  if (!err) return "알 수 없는 오류가 발생했습니다.";
  const msg = String(err.message || "").toLowerCase();
  if (msg.includes("failed to fetch") || msg.includes("networkerror")) {
    return "백엔드 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
  }
  return "Kill Switch 상태를 불러오는 중 오류가 발생했습니다.";
}


function LevelRow({ row, isActive }) {
  return (
    <div
      data-testid={`killswitch-level-row-${row.id}`}
      data-active={isActive ? "true" : "false"}
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "6px 8px", borderRadius: 4,
        background: isActive ? "rgba(239, 68, 68, 0.10)" : "transparent",
        border: `1px solid ${isActive ? "var(--c-danger)" : "var(--c-border)"}`,
      }}
    >
      <span style={{
        fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
        background: isActive ? "var(--c-danger)" : "var(--c-surface-2, #f1f5f9)",
        color: isActive ? "#fff" : "var(--c-text-3)",
      }}>
        {row.title}
      </span>
      <span style={{ fontSize: "var(--fs-sm)", color: "var(--c-text-2)" }}>
        {row.desc}
      </span>
    </div>
  );
}


export function KillSwitchPanel({ testId = "killswitch-panel", autoLoad = true }) {
  const [state, setState] = useState({ requestKey: 0, error: null, status: null });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!autoLoad) return;
    let cancelled = false;
    backendApi.emergencyStopStatus()
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
        <LoadingState testId={`${testId}-loading`} title="Kill Switch 상태 확인 중..." />
      </Card>
    );
  }

  if (state.error) {
    return (
      <Card>
        <ErrorState
          testId={`${testId}-error`}
          title="Kill Switch 상태 조회 실패"
          hint={friendlyError(state.error)}
          retryLabel="다시 시도"
          onRetry={() => setTick((t) => t + 1)}
        />
      </Card>
    );
  }

  const { status } = state;
  if (!status) {
    return (
      <Card>
        <EmptyState testId={`${testId}-empty`} title="Kill Switch 상태 없음" />
      </Card>
    );
  }

  const meta = LEVEL_META[status.level] || LEVEL_META.OFF;

  return (
    <Card>
      <div data-testid={testId} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "baseline",
          gap: 8, flexWrap: "wrap",
        }}>
          <SectionLabel>3단계 Kill Switch</SectionLabel>
          <StatusBadge status={meta.status} testId={`${testId}-level-badge`}>
            {meta.label}
          </StatusBadge>
        </div>

        <div style={{ fontSize: "var(--fs-sm)", color: "var(--c-text-2)" }}>
          {meta.title}
          {status.reason_code && (
            <span style={{ marginLeft: 6, color: "var(--c-text-3)", fontSize: "var(--fs-xs)" }}>
              · 사유: {status.reason_code}
            </span>
          )}
          {status.decided_by && (
            <span style={{ marginLeft: 6, color: "var(--c-text-3)", fontSize: "var(--fs-xs)" }}>
              · {status.decided_by}
            </span>
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {LEVEL_ROWS.map((r) => (
            <LevelRow
              key={r.id}
              row={r}
              isActive={
                (r.id === "LEVEL_1" && (status.level === "LEVEL_1" || status.level === "LEVEL_2" || status.level === "LEVEL_3")) ||
                (r.id === "LEVEL_2" && (status.level === "LEVEL_2" || status.level === "LEVEL_3")) ||
                (r.id === "LEVEL_3" && status.level === "LEVEL_3")
              }
            />
          ))}
        </div>

        <div style={{
          display: "flex", gap: 12, fontSize: "var(--fs-xs)",
          color: "var(--c-text-3)", flexWrap: "wrap",
        }}>
          <span data-testid={`${testId}-cancel-count`}>
            미체결 취소 후보: <b style={{ color: "var(--c-text-1)" }}>
              {status.cancel_candidate_count ?? 0}
            </b>
          </span>
          <span data-testid={`${testId}-liquidation-count`}>
            청산 후보: <b style={{ color: "var(--c-text-1)" }}>
              {status.liquidation_candidate_count ?? 0}
            </b>
          </span>
        </div>

        <div
          data-testid={`${testId}-warning`}
          style={{
            padding: 8, borderRadius: 4,
            background: "rgba(245, 158, 11, 0.08)",
            border: "1px solid rgba(245, 158, 11, 0.25)",
            fontSize: "var(--fs-xs)", color: "var(--c-warning)",
          }}
        >
          ⚠ 자동 청산은 비활성화되어 있습니다. 청산은 운영자가 후보를 확인한 뒤 수동 승인으로 진행해야 합니다.
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


export default KillSwitchPanel;
