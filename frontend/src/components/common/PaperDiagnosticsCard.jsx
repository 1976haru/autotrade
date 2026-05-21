/**
 * Paper Diagnostics Card — *"왜 오늘 주문이 0건인가"* 단일 진단 카드.
 *
 * 사용자가 정규장 시간에 자동매매를 실행했는데도 매수/매도가 0건일 때, 화면
 * 한 곳에서 모든 차단 사유를 한국어로 읽을 수 있게 한다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 본 카드는 broker / 실거래 API / OrderExecutor 호출 0건.
 *  - "지금 매수" / "Place Order" / "실거래 시작" / "ENABLE_LIVE_TRADING"
 *    라벨 button 0개 — 본 카드는 *진단 표시 전용*.
 *  - "Paper / SIMULATION 진단 · 실거래 권한 없음" 영구 배지 노출.
 *  - input / textarea / select 0개.
 *  - 자동 새로고침이 *PermissionGate 토글* / *strategy 시작* 등 mutation
 *    호출을 발생시키지 않는다.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";


// PaperBlockReason → 화면 색상 매핑.
const _REASON_TONE = {
  NONE:                          { color: "#15803d", bg: "#dcfce7" },
  NO_UNIVERSE:                   { color: "#b91c1c", bg: "#fee2e2" },
  USING_FALLBACK_UNIVERSE:       { color: "#b45309", bg: "#fef3c7" },
  NO_MARKET_DATA:                { color: "#b91c1c", bg: "#fee2e2" },
  MOCK_MARKET_DATA_ONLY:         { color: "#b45309", bg: "#fef3c7" },
  STRATEGY_ENGINE_NOT_CONNECTED: { color: "#b91c1c", bg: "#fee2e2" },
  AUTO_BOT_NOT_RUNNING:          { color: "#b91c1c", bg: "#fee2e2" },
  NO_CANDIDATE:                  { color: "#b45309", bg: "#fef3c7" },
  NO_STRATEGY_SIGNAL:            { color: "#b45309", bg: "#fef3c7" },
  BLOCKED_BY_RISK_MANAGER:       { color: "#b91c1c", bg: "#fee2e2" },
  BLOCKED_BY_PERMISSION_GATE:    { color: "#b91c1c", bg: "#fee2e2" },
  PAPER_EXECUTION_DISABLED:      { color: "#b91c1c", bg: "#fee2e2" },
  MARKET_CLOSED:                 { color: "#475569", bg: "#f1f5f9" },
  LIVE_DISABLED_SAFE:            { color: "#1e40af", bg: "#dbeafe" },
  AI_EXECUTION_DISABLED_SAFE:    { color: "#1e40af", bg: "#dbeafe" },
  MODE_MISMATCH:                 { color: "#b91c1c", bg: "#fee2e2" },
};


function _toneFor(reason) {
  return _REASON_TONE[reason] || { color: "#475569", bg: "#f1f5f9" };
}


export function PaperDiagnosticsCard({
  testId = "paper-diagnostics-card",
  frontendMode = null,
  autoLoad = true,
}) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const qs = new URLSearchParams();
      if (frontendMode) qs.set("frontend_mode", frontendMode);
      const r = await backendApi.paperDiagnosticsPreflight({
        frontendMode,
      });
      setReport(r);
      setError("");
    } catch (e) {
      setError(e?.message || "진단 리포트 조회 실패");
    } finally {
      setLoading(false);
    }
  }, [frontendMode]);

  useEffect(() => {
    if (autoLoad) refresh();
  }, [autoLoad, refresh]);

  const primaryTone = _toneFor(report?.primary_block_reason || "NONE");
  const has_blocking = report?.has_blocking === true;

  return (
    <Card>
      <div data-testid={testId} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <SectionLabel>🩺 오늘 주문 0건 원인</SectionLabel>

        {/* 영구 disclaimer — 본 카드는 진단 표시 전용. */}
        <div
          data-testid={`${testId}-disclaimer`}
          style={{
            padding: "6px 10px",
            background: "#1e3a8a15",
            border: "1px solid #1e3a8a55",
            borderRadius: 4,
            fontSize: 11,
            color: "#1e3a8a",
            lineHeight: 1.5,
          }}
        >
          본 카드는 PAPER / SIMULATION 자동매매가 *왜 주문 0건인지* 를 한 곳에서
          진단합니다. broker 호출 0건, mutation 0건 — 표시 전용입니다.
        </div>

        {loading && !report ? (
          <div data-testid={`${testId}-loading`}
               style={{ fontSize: 11, color: "var(--c-text-3)" }}>
            진단 리포트 조회 중…
          </div>
        ) : null}

        {error ? (
          <div data-testid={`${testId}-error`}
               style={{
                 padding: "6px 10px",
                 background: "#fef2f2",
                 border: "1px solid #fecaca",
                 borderRadius: 4,
                 fontSize: 11,
                 color: "#991b1b",
               }}>
            ⚠️ {error}
          </div>
        ) : null}

        {report && (
          <>
            {/* 1. 주요 차단 사유 헤드라인 */}
            <div
              data-testid={`${testId}-primary-reason`}
              style={{
                padding: "8px 10px",
                background: primaryTone.bg,
                border: `1px solid ${primaryTone.color}55`,
                borderRadius: 6,
                fontSize: 12,
                color: primaryTone.color,
                lineHeight: 1.5,
              }}
            >
              <div style={{ fontWeight: 700, marginBottom: 2 }}>
                {has_blocking ? "🚨" : "✅"}{" "}
                <span data-testid={`${testId}-primary-reason-code`}>
                  {report.primary_block_reason}
                </span>
              </div>
              <div data-testid={`${testId}-summary-ko`}>{report.summary_ko}</div>
            </div>

            {/* 2. Universe 정보 */}
            <div
              data-testid={`${testId}-universe-section`}
              style={{
                padding: "8px 10px",
                background: "var(--c-surface-2, #f8fafc)",
                border: "1px solid var(--c-border)",
                borderRadius: 6,
                fontSize: 11,
                lineHeight: 1.6,
              }}
            >
              <div style={{ fontWeight: 700, marginBottom: 4 }}>📌 Universe</div>
              <div>
                Source: <b data-testid={`${testId}-universe-source`}>
                  {report.universe_source}
                </b>
              </div>
              <div>
                Count: <b data-testid={`${testId}-universe-count`}>
                  {report.universe_count}개
                </b>
              </div>
              {report.universe_fallback_used && (
                <div
                  data-testid={`${testId}-universe-fallback-warning`}
                  style={{
                    marginTop: 4,
                    padding: "4px 8px",
                    background: "#fef3c7",
                    border: "1px solid #fcd34d",
                    borderRadius: 4,
                    color: "#92400e",
                  }}
                >
                  ⚠️ 관심종목이 없어 시가총액 상위 50개 기본 Universe 를 사용합니다.
                  {report.universe_warning_ko ? (
                    <div style={{ marginTop: 2, fontSize: 10 }}>
                      {report.universe_warning_ko}
                    </div>
                  ) : null}
                </div>
              )}
            </div>

            {/* 3. Market data + 자동봇 + permission 상태 */}
            <div
              data-testid={`${testId}-runtime-section`}
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 6,
                fontSize: 11,
              }}
            >
              <div
                data-testid={`${testId}-runtime-market-provider`}
                style={{ padding: "6px 8px",
                          background: "var(--c-surface-2, #f8fafc)",
                          border: "1px solid var(--c-border)",
                          borderRadius: 4 }}
              >
                Market data provider:{" "}
                <b>{report.market_data_provider || "—"}</b>
              </div>
              <div
                data-testid={`${testId}-runtime-mode`}
                style={{ padding: "6px 8px",
                          background: "var(--c-surface-2, #f8fafc)",
                          border: "1px solid var(--c-border)",
                          borderRadius: 4 }}
              >
                Backend 모드: <b>{report.default_mode}</b>
                {report.frontend_mode ? (
                  <span style={{ color: "var(--c-text-3)" }}>
                    {" / "}Frontend: <b>{report.frontend_mode}</b>
                  </span>
                ) : null}
              </div>
              <div
                data-testid={`${testId}-runtime-autobot`}
                style={{ padding: "6px 8px",
                          background: "var(--c-surface-2, #f8fafc)",
                          border: "1px solid var(--c-border)",
                          borderRadius: 4 }}
              >
                자동봇 loop: <b>{report.auto_bot_state}</b>
                {report.auto_bot_running ? " (RUNNING)" : " (정지)"}
              </div>
              <div
                data-testid={`${testId}-runtime-strategy-engine`}
                style={{ padding: "6px 8px",
                          background: "var(--c-surface-2, #f8fafc)",
                          border: "1px solid var(--c-border)",
                          borderRadius: 4 }}
              >
                전략 엔진 연동:{" "}
                <b style={{
                  color: report.strategy_engine_connected ? "#15803d" : "#b91c1c",
                }}>
                  {report.strategy_engine_connected ? "연결됨" : "미연동"}
                </b>
              </div>
              <div
                data-testid={`${testId}-runtime-permission`}
                style={{ padding: "6px 8px",
                          background: "var(--c-surface-2, #f8fafc)",
                          border: "1px solid var(--c-border)",
                          borderRadius: 4 }}
              >
                PermissionGate (PAPER 가상 실행):{" "}
                <b style={{
                  color: report.paper_virtual_execution_allowed
                    ? "#15803d" : "#b91c1c",
                }}>
                  {report.paper_virtual_execution_allowed ? "허용" : "차단"}
                </b>
              </div>
              <div
                data-testid={`${testId}-runtime-last-cycle`}
                style={{ padding: "6px 8px",
                          background: "var(--c-surface-2, #f8fafc)",
                          border: "1px solid var(--c-border)",
                          borderRadius: 4 }}
              >
                최근 cycle: 결정 {report.last_decision_count}건 / 원장{" "}
                {report.last_ledger_events}건
              </div>
            </div>

            {/* 4. PAPER 가상 실행 차단 안내 (블로킹일 때만) */}
            {!report.paper_virtual_execution_allowed && (
              <div
                data-testid={`${testId}-paper-blocked-banner`}
                style={{
                  padding: "6px 10px",
                  background: "#fee2e2",
                  border: "1px solid #fca5a5",
                  borderRadius: 4,
                  fontSize: 11,
                  color: "#991b1b",
                }}
              >
                현재 PAPER 모드이나 가상 실행이 차단되어 주문이 생성되지 않았습니다.
              </div>
            )}

            {/* 5. 자동봇 미연동 안내 (블로킹일 때만) */}
            {!report.strategy_engine_connected && (
              <div
                data-testid={`${testId}-autobot-not-connected-banner`}
                style={{
                  padding: "6px 10px",
                  background: "#fee2e2",
                  border: "1px solid #fca5a5",
                  borderRadius: 4,
                  fontSize: 11,
                  color: "#991b1b",
                }}
              >
                자동봇이 backend strategy loop 와 연결되지 않았습니다.
              </div>
            )}

            {/* 6. blocking_messages_ko 리스트 */}
            {report.blocking_messages_ko?.length > 0 && (
              <div
                data-testid={`${testId}-blocking-messages`}
                style={{
                  display: "flex", flexDirection: "column", gap: 4,
                  padding: "8px 10px",
                  background: "#fef2f2",
                  border: "1px solid #fecaca",
                  borderRadius: 6,
                }}
              >
                <div style={{ fontSize: 11, fontWeight: 700, color: "#991b1b" }}>
                  ❌ 차단 사유 ({report.blocking_messages_ko.length}건)
                </div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11,
                              color: "#991b1b", lineHeight: 1.6 }}>
                  {report.blocking_messages_ko.map((m, i) => (
                    <li key={`blk-${i}`}
                        data-testid={`${testId}-blocking-message-${i}`}>
                      {m}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* 7. warning_messages_ko 리스트 */}
            {report.warning_messages_ko?.length > 0 && (
              <div
                data-testid={`${testId}-warning-messages`}
                style={{
                  display: "flex", flexDirection: "column", gap: 4,
                  padding: "8px 10px",
                  background: "#fffbeb",
                  border: "1px solid #fde68a",
                  borderRadius: 6,
                }}
              >
                <div style={{ fontSize: 11, fontWeight: 700, color: "#92400e" }}>
                  ⚠️ 경고 ({report.warning_messages_ko.length}건)
                </div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11,
                              color: "#92400e", lineHeight: 1.6 }}>
                  {report.warning_messages_ko.map((m, i) => (
                    <li key={`warn-${i}`}
                        data-testid={`${testId}-warning-message-${i}`}>
                      {m}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* 8. next_actions_ko — 운영자에게 권장 다음 단계 */}
            {report.next_actions_ko?.length > 0 && (
              <div
                data-testid={`${testId}-next-actions`}
                style={{
                  display: "flex", flexDirection: "column", gap: 4,
                  padding: "8px 10px",
                  background: "#eff6ff",
                  border: "1px solid #bfdbfe",
                  borderRadius: 6,
                }}
              >
                <div style={{ fontSize: 11, fontWeight: 700, color: "#1e40af" }}>
                  💡 다음 단계 권장
                </div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11,
                              color: "#1e40af", lineHeight: 1.6 }}>
                  {report.next_actions_ko.map((m, i) => (
                    <li key={`act-${i}`}
                        data-testid={`${testId}-next-action-${i}`}>
                      {m}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}

        {/* 절대 invariant 배지 */}
        <div
          data-testid={`${testId}-invariant-badges`}
          style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
        >
          {[
            "Paper / SIMULATION 진단",
            "broker 호출 0건",
            "실거래 권한 없음",
          ].map((label) => (
            <span
              key={label}
              style={{
                fontSize: 9,
                fontWeight: 700,
                padding: "1px 6px",
                borderRadius: 3,
                background: "#94a3b820",
                border: "1px solid #94a3b855",
                color: "#475569",
              }}
            >
              {label}
            </span>
          ))}
        </div>
      </div>
    </Card>
  );
}

export default PaperDiagnosticsCard;
