/**
 * 39: AI Permission Gate read-only status card.
 *
 * 현재 mode + flags 기반 AI 권한 level을 표시. **권한 행사 / 토글 버튼은
 * 절대 만들지 않는다** — 본 카드는 *상태 표시*만 한다 (CLAUDE.md 절대 원칙
 * 7: AI Permission Gate는 권한 판정만 한다).
 *
 * 표시:
 * - 현재 mode + level 배지
 * - 허용된 행동 / 차단된 행동
 * - 사람 승인 필요 여부
 * - virtual only 여부
 * - LIVE 실행 비활성 표시
 * - "AI API Key는 주문 권한이 아닙니다" 안내
 *
 * 본 카드는 read-only — fetch 외 어떤 mutation도 하지 않는다.
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "./index";
import { ErrorState, LoadingState, StatusBadge } from "./primitives";
import { backendApi } from "../../services/backend/client";


const LEVEL_META = {
  FULL_STOP:              { label: "FULL_STOP",         status: "danger",  desc: "AI 완전 중지" },
  RECOMMEND_ONLY:         { label: "추천만",            status: "info",    desc: "신호만 생성, 주문 흐름 진입 X" },
  APPROVAL_REQUIRED:      { label: "승인 요청",         status: "warning", desc: "AI 제안, 운영자 승인 필수" },
  VIRTUAL_EXECUTION:      { label: "가상 실행",         status: "info",    desc: "가상 broker만 자동 실행" },
  LIMITED_LIVE_EXECUTION: { label: "제한적 실행",       status: "warning", desc: "실거래 가능 (모든 가드 통과 시)" },
};

const ACTION_LABEL = {
  RECOMMEND:            "추천",
  SUBMIT_FOR_APPROVAL:  "승인 요청 제출",
  VIRTUAL_EXECUTE:      "가상 실행",
  LIVE_EXECUTE:         "실거래 실행",
  FUTURES_LIVE_EXECUTE: "선물 실거래",
};


function friendlyError(err) {
  if (!err) return "알 수 없는 오류가 발생했습니다.";
  const msg = String(err.message || "").toLowerCase();
  if (msg.includes("failed to fetch") || msg.includes("networkerror")) {
    return "백엔드 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
  }
  return "AI 권한 상태를 불러오는 중 오류가 발생했습니다.";
}


export function AiPermissionCard({ testId = "ai-permission-card", autoLoad = true }) {
  const [state, setState] = useState({ requestKey: 0, error: null, status: null });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!autoLoad) return;
    let cancelled = false;
    backendApi.aiPermissionStatus()
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
        <LoadingState testId={`${testId}-loading`} title="AI 권한 상태 확인 중..." />
      </Card>
    );
  }
  if (state.error) {
    return (
      <Card>
        <ErrorState
          testId={`${testId}-error`}
          title="AI 권한 상태 조회 실패"
          hint={friendlyError(state.error)}
          retryLabel="다시 시도"
          onRetry={() => setTick((t) => t + 1)}
        />
      </Card>
    );
  }

  const { status } = state;
  const meta = LEVEL_META[status.level] || LEVEL_META.FULL_STOP;
  const allowed = status.allowed_actions || [];
  const blocked = status.blocked_actions || [];

  return (
    <Card>
      <div data-testid={testId} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "baseline",
          gap: 8, flexWrap: "wrap",
        }}>
          <SectionLabel>AI 권한 게이트</SectionLabel>
          <StatusBadge status={meta.status} testId={`${testId}-level-badge`}>
            {meta.label}
          </StatusBadge>
        </div>

        <div style={{ fontSize: "var(--fs-sm)", color: "var(--c-text-2)" }}>
          {meta.desc}
          {status.mode && (
            <span style={{ marginLeft: 6, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
              · 운용모드: <b>{status.mode}</b>
            </span>
          )}
        </div>

        {allowed.length > 0 && (
          <div data-testid={`${testId}-allowed`} style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            <span style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginRight: 4 }}>
              허용:
            </span>
            {allowed.map((a) => (
              <span key={a} style={{
                fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
                background: "rgba(34, 197, 94, 0.10)", color: "var(--c-success)",
                border: "1px solid rgba(34, 197, 94, 0.25)",
              }}>
                {ACTION_LABEL[a] || a}
              </span>
            ))}
          </div>
        )}

        {blocked.length > 0 && (
          <div data-testid={`${testId}-blocked`} style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            <span style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginRight: 4 }}>
              차단:
            </span>
            {blocked.map((a) => (
              <span key={a} style={{
                fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
                background: "rgba(239, 68, 68, 0.08)", color: "var(--c-danger)",
                border: "1px solid rgba(239, 68, 68, 0.20)",
              }}>
                {ACTION_LABEL[a] || a}
              </span>
            ))}
          </div>
        )}

        <div style={{
          display: "flex", gap: 12, fontSize: "var(--fs-xs)",
          color: "var(--c-text-3)", flexWrap: "wrap",
        }}>
          {status.requires_human_approval && (
            <span data-testid={`${testId}-needs-approval`}>👤 사람 승인 필요</span>
          )}
          {status.virtual_only && (
            <span data-testid={`${testId}-virtual-only`}>🧪 가상 실행 전용</span>
          )}
          {status.live_execution_disabled && (
            <span data-testid={`${testId}-live-disabled`}>⛔ LIVE 실행 비활성</span>
          )}
          {status.futures_live_disabled && (
            <span data-testid={`${testId}-futures-disabled`}>⛔ 선물 LIVE 비활성</span>
          )}
        </div>

        <div
          data-testid={`${testId}-notice`}
          style={{
            padding: 8, borderRadius: 4,
            background: "rgba(125, 211, 252, 0.08)",
            border: "1px solid rgba(125, 211, 252, 0.25)",
            fontSize: "var(--fs-xs)", color: "var(--c-info)",
          }}
        >
          🔒 AI API Key는 주문 권한이 아닙니다. 권한은 운용모드 + 안전 flag + 운영자 승인으로만 결정됩니다.
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


export default AiPermissionCard;
