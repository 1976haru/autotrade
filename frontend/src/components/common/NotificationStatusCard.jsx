/**
 * 체크리스트 #64: Notification 설정 상태 카드.
 *
 * 절대 원칙:
 *   1. 본 카드는 broker / 주문 / route_order 호출 0건.
 *   2. **Telegram Bot Token / chat_id 입력 UI를 노출하지 않는다** — 모든
 *      Secret은 backend/.env에만 존재. 본 카드는 *상태 표시 + 테스트 버튼*만.
 *   3. 응답에 secret이 들어올 수 없음(backend가 응답에서 제외) — 본 카드는
 *      추가 방어로 token / chat_id 키를 절대 표시하지 않는다.
 *   4. "테스트 알림 보내기" 버튼은 기존 `/api/notifications/test`만 호출.
 *
 * UI 구성:
 *   - 활성 여부 / 채널 / Telegram 구성 여부 / min severity / dedupe window
 *   - 알림 종류 안내 (긴급정지 / 손실한도 / 승인대기 / 데이터 지연 / API
 *     장애 / 선물 margin risk)
 *   - "Token은 backend .env에만 저장됩니다" 안내
 *   - "테스트 알림 보내기" 버튼 (notifications_enabled=true && telegram_configured=true 일 때만 활성)
 *   - 마지막 테스트 결과 표시
 */

import { useCallback, useEffect, useState } from "react";
import { Btn, Card, SectionLabel } from "./index";
import { friendlyErrorMessage } from "../../utils/errorMessage";
import { backendApi } from "../../services/backend/client";


const ALERT_TYPES = [
  { label: "긴급정지",          color: "#ef4444" },
  { label: "손실한도 접근",     color: "#f59e0b" },
  { label: "승인 대기",          color: "#fbbf24" },
  { label: "데이터 지연",        color: "#a78bfa" },
  { label: "API 장애",           color: "#ef4444" },
  { label: "주문 반복 거부",     color: "#fb7185" },
  { label: "선물 margin risk",   color: "#ef4444" },
  { label: "Risk Auditor 경고",  color: "#fbbf24" },
];


function _statusBadge(ok, okLabel, warnLabel) {
  const color = ok ? "#22c55e" : "#94a3b8";
  return (
    <span style={{
      padding: "2px 8px", borderRadius: 3, fontSize: 10, fontWeight: 700,
      color, background: `${color}15`, border: `1px solid ${color}55`,
    }}>
      {ok ? okLabel : warnLabel}
    </span>
  );
}


export function NotificationStatusCard({ testId = "notification-status-card" }) {
  const [status,     setStatus]     = useState(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState("");
  const [testBusy,   setTestBusy]   = useState(false);
  const [testResult, setTestResult] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const data = await backendApi.notificationsStatus();
      setStatus(data);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await refresh();
      if (cancelled) return;
    })();
    return () => { cancelled = true; };
  }, [refresh]);

  const sendTest = useCallback(async () => {
    setTestBusy(true); setTestResult(null);
    try {
      const r = await backendApi.notificationsTest();
      setTestResult(r);
    } catch (e) {
      setTestResult({ ok: false, error: e.message || String(e) });
    } finally {
      setTestBusy(false);
    }
  }, []);

  if (loading && !status) {
    return (
      <Card>
        <SectionLabel>🔔 알림 설정</SectionLabel>
        <div data-testid={`${testId}-loading`}
             style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }

  if (error && !status) {
    return (
      <Card>
        <SectionLabel>🔔 알림 설정</SectionLabel>
        <div data-testid={`${testId}-error`}
             style={{ fontSize: 11, color: "var(--c-danger)",
                      padding: "6px 8px", background: "#fef2f2",
                      border: "1px solid #fecaca", borderRadius: 4 }}>
          {friendlyErrorMessage(error) || "알림 상태를 불러올 수 없어요."}
        </div>
      </Card>
    );
  }

  const enabled = !!status?.enabled;
  const telegramCfg = !!status?.telegram_configured;
  const canSendTest = enabled && telegramCfg;

  return (
    <Card>
      <div data-testid={testId}
           style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{
          display: "flex", justifyContent: "space-between",
          alignItems: "baseline", gap: 6, flexWrap: "wrap",
        }}>
          <SectionLabel>🔔 알림 설정</SectionLabel>
          {_statusBadge(enabled, "활성", "비활성")}
        </div>

        <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.5 }}>
          장중 위험 이벤트를 즉시 알림으로 받습니다. <b>위험 알림 우선</b>
          으로 발송되며, 주문 성공 알림은 기본 비활성입니다.
        </div>

        {/* 채널 / Telegram 구성 / min severity / dedupe */}
        <div data-testid={`${testId}-fields`}
             style={{
               display: "grid", gridTemplateColumns: "1fr 1fr",
               gap: 6, fontSize: 11,
             }}>
          <_Field label="채널" value={status?.channel || "(미설정)"} />
          <_Field label="Telegram 구성" value={
            <span data-testid={`${testId}-telegram-cfg`}>
              {telegramCfg ? "✓ 구성됨" : "× 미구성"}
            </span>
          } />
          <_Field label="최소 심각도"
                  value={status?.min_severity_name || "INFO"} />
          <_Field label="중복 억제(초)"
                  value={String(status?.dedupe_window_seconds ?? 60)} />
        </div>

        {/* 보안 안내 */}
        <div data-testid={`${testId}-secret-notice`}
             style={{
               fontSize: 10, color: "#0e7490",
               padding: "6px 10px", background: "#ecfeff",
               border: "1px solid #67e8f9", borderRadius: 4, lineHeight: 1.5,
             }}>
          🔐 Telegram Bot Token / chat_id는 <b>backend/.env에만</b>
          저장됩니다. 본 화면 / git / 프론트엔드에는 어떤 Secret도 저장되지
          않습니다.
        </div>

        {/* 알림 종류 안내 */}
        <div>
          <div style={{ fontSize: 10, color: "#64748b", marginBottom: 4 }}>
            알림 종류 (위험 우선)
          </div>
          <div data-testid={`${testId}-alert-types`}
               style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {ALERT_TYPES.map((t) => (
              <span key={t.label}
                    style={{
                      padding: "2px 6px", fontSize: 9, fontWeight: 700,
                      borderRadius: 3, color: t.color,
                      background: `${t.color}15`,
                      border: `1px solid ${t.color}55`,
                    }}>
                {t.label}
              </span>
            ))}
          </div>
        </div>

        {/* 테스트 알림 + 결과 */}
        <div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            {/* 본 카드 외부의 테스트가 disabled 상태를 직접 검사하므로 native
                button을 사용 (Btn 컴포넌트는 data-testid 패스스루를 안 함). */}
            <button
              type="button"
              data-testid={`${testId}-test-btn`}
              onClick={sendTest}
              disabled={!canSendTest || testBusy}
              style={{
                padding: "8px 16px",
                borderRadius: "var(--r-md, 6px)",
                border: "none",
                cursor: (!canSendTest || testBusy) ? "not-allowed" : "pointer",
                background: (!canSendTest || testBusy)
                  ? "var(--c-surface-3, #cbd5e1)"
                  : "#3b82f6",
                color: (!canSendTest || testBusy)
                  ? "var(--c-text-4, #64748b)"
                  : "#fff",
                fontWeight: 700,
                fontSize: "var(--fs-sm, 13px)",
                fontFamily: "inherit",
              }}
            >
              {testBusy ? "전송 중…" : "🧪 테스트 알림 보내기"}
            </button>
            <Btn small color="#334155" onClick={refresh} disabled={loading}>
              ↻ 새로고침
            </Btn>
          </div>
          {!canSendTest && (
            <div data-testid={`${testId}-disabled-hint`}
                 style={{ fontSize: 10, color: "#94a3b8", marginTop: 4 }}>
              {!enabled
                ? "NOTIFICATIONS_ENABLED=false — backend/.env에서 활성화하세요."
                : "Telegram 미구성 — backend/.env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID를 설정하세요."}
            </div>
          )}
          {testResult && (
            <div data-testid={`${testId}-test-result`}
                 style={{
                   marginTop: 6, padding: "6px 8px",
                   borderRadius: 4, fontSize: 10,
                   color: testResult.ok ? "#15803d" : "#b91c1c",
                   background: testResult.ok ? "#dcfce7" : "#fee2e2",
                   border: `1px solid ${testResult.ok ? "#86efac" : "#fecaca"}`,
                   lineHeight: 1.5,
                 }}>
              {testResult.ok
                ? <>
                    ✓ 발송 성공 — 채널 <b>{testResult.channel}</b>
                    {testResult.skipped_reason && (
                      <> (skipped: <code>{testResult.skipped_reason}</code>)</>
                    )}
                  </>
                : <>
                    ✗ 발송 실패: {friendlyErrorMessage(testResult.error) || testResult.error}
                  </>}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}


function _Field({ label, value }) {
  return (
    <div style={{
      padding: "6px 8px", background: "#f1f5f9", borderRadius: 4,
    }}>
      <div style={{ fontSize: 9, color: "#64748b", marginBottom: 2 }}>
        {label}
      </div>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#0f172a" }}>
        {value}
      </div>
    </div>
  );
}


export { ALERT_TYPES };
export default NotificationStatusCard;
