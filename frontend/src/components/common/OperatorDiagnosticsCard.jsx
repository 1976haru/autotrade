/**
 * Operator Diagnostics Card — *"운영 진단 / 자가 점검"* 단일 카드.
 *
 * EXE 운영자가 한 곳에서:
 *  - 전체 상태 (정상 / 주의 / 오류 / 정지)
 *  - 현재 모드 + 안전 flag (live / AI / KIS paper / market provider)
 *  - 백엔드 / 시장 데이터 / Universe / 전략 엔진 / 자동봇 / PermissionGate /
 *    Paper 가상 실행 / 실거래 차단 상태
 *  - 오늘 카운터 + "오늘 주문 0건 원인" primary reason + pipeline stage
 *  - next_actions 권장 다음 단계
 *  - 최근 이벤트 로그 (필터: 오류만 / 경고 이상 / 카테고리)
 *  - 진단 리포트 복사 버튼 (clipboard JSON, secret 0건)
 *  - 로그 파일 위치 안내
 *
 * 절대 invariant (테스트로 lock):
 *  - 본 카드는 broker / 실거래 API / OrderExecutor 호출 0건.
 *  - "지금 매수" / "지금 매도" / "Place Order" / "실거래 시작" / "실거래 활성화"
 *    / "ENABLE_LIVE_TRADING" 라벨 button 0개.
 *  - "Paper / SIMULATION 진단 · 실거래 권한 없음" + "민감정보는 진단 리포트에
 *    포함되지 않습니다" 영구 배지.
 *  - input / textarea / select 0개.
 *  - clipboard copy 가 secret-like 패턴 포함 시 즉시 알림 (실제로는 backend
 *    가 fail-closed 로 막아 진입 자체가 차단).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";


const _STATUS_PALETTE = {
  HEALTHY: { label: "정상",   color: "#15803d", bg: "#dcfce7" },
  WARN:    { label: "주의",   color: "#92400e", bg: "#fef3c7" },
  ERROR:   { label: "오류",   color: "#991b1b", bg: "#fee2e2" },
  STOPPED: { label: "정지",   color: "#475569", bg: "#e2e8f0" },
};

const _LEVEL_TONE = {
  DEBUG:    { color: "#475569", bg: "#f1f5f9" },
  INFO:     { color: "#1e40af", bg: "#dbeafe" },
  WARN:     { color: "#92400e", bg: "#fef3c7" },
  ERROR:    { color: "#991b1b", bg: "#fee2e2" },
  CRITICAL: { color: "#7f1d1d", bg: "#fecaca" },
};


// 사용자 요청서 §화면 구성 E — 로그 파일 위치 안내.
const _LOG_PATH_HINT_KO = (
  "로그 파일 위치: 사용자 데이터 폴더 / logs (Windows 데스크톱 모드에서는 " +
  "%LOCALAPPDATA%\\AgentTrader\\logs 기준)."
);


// secret *값* 패턴 — 진단 리포트에 흘러들어온 token / key / 계좌번호 패턴.
// clipboard copy 전 *마지막* 방어선 (backend 가 이미 fail-closed 로 차단
// 하므로 정상 흐름에서 도달하지 않음). `\b` 경계 없는 form 도 함께 등록 —
// JSON quote/comma 등 비-word 경계가 누락된 경우에도 안전.
const _SECRET_VALUE_PATTERNS = [
  /sk-ant-[A-Za-z0-9\-_]{20,}/i,             // Anthropic
  /sk-[A-Za-z0-9]{16,}/,                     // OpenAI sk-... (16자 이상)
  /ghp_[A-Za-z0-9]{20,}/,                    // GitHub PAT
  /xox[bpaoist]-[A-Za-z0-9-]{10,}/i,         // Slack
  /\bBearer\s+[A-Za-z0-9.\-_]{20,}/i,        // Bearer token
  /eyJ[A-Za-z0-9\-_]{8,}\.[A-Za-z0-9\-_]{8,}\.[A-Za-z0-9\-_]{8,}/, // JWT
  /\b\d{8}-\d{2}\b/,                         // 한국 계좌번호 8-2 형식  // security-scan: ignore
  /\b\d{6}-\d{7}\b/,                         // 주민등록번호 패턴
  /\b\d{4}[\s-]\d{4}[\s-]\d{4}[\s-]\d{4}\b/, // 신용카드 번호 패턴
  /telegram[\s_-]*bot[\s_-]*token\s*[:=]\s*\S+/i,
  /PSAt[A-Za-z0-9]{20,}/,                    // KIS app_key 형식
];


// secret *key 이름* — 값과 무관하게 key 이름 자체가 의심스러우면 차단.
// 사용자 요청서 정책 B: "key 이름에 secret/token/password/api_key/account_no
// 등이 포함되면 값과 무관하게 차단해도 된다."
const _SECRET_KEY_NAMES = [
  "api_key", "apisecret", "api_secret",
  "app_key", "app_secret",
  "access_token", "refresh_token",
  "secret_token", "secret",
  "password", "passwd",
  "telegram_bot_token", "bot_token",
  "kis_app_key", "kis_app_secret", "kis_account_no",
  "anthropic_api_key", "openai_api_key",
  "private_key", "client_secret",
  "account_no", "account_number",
];


function _matchSecretValue(value) {
  if (typeof value !== "string" || !value) return null;
  for (const re of _SECRET_VALUE_PATTERNS) {
    if (re.test(value)) {
      return `value_pattern:${re.source.slice(0, 40)}`;
    }
  }
  return null;
}


function _isSuspiciousKeyName(key) {
  if (typeof key !== "string" || !key) return null;
  const norm = key.toLowerCase().replace(/[^a-z0-9]/g, "");
  for (const candidate of _SECRET_KEY_NAMES) {
    const candidateNorm = candidate.replace(/[^a-z0-9]/g, "");
    if (norm.includes(candidateNorm)) {
      return `key_name:${key}`;
    }
  }
  return null;
}


// 의심 key 이름이라도 *값이 boolean / null / 숫자* 면 secret 일 수 없다.
// 예: `contains_secret: false` 는 안전 flag 라벨이며, `password: ""` 는 빈
// 입력 — 둘 다 차단 대상이 *아니다*. 오로지 *비어있지 않은 string* 값일 때만
// key-name 조합으로 차단한다 (false positive 회피).
function _isSecretCandidateValue(v) {
  if (v == null) return false;
  if (typeof v === "boolean" || typeof v === "number") return false;
  if (typeof v === "string") return v.trim().length > 0;
  // object / array → 자식에서 다시 봄
  return false;
}


/**
 * structured walk — payload 의 모든 (key, value) 를 재귀로 점검.
 *
 * 차단 규칙:
 *  1. *값* 이 string 이고 regex 매칭 → 즉시 차단 ("value_pattern:...").
 *  2. key 이름이 의심스럽고 + 값이 *비어있지 않은 string* → 차단
 *     ("key_name:..."). boolean / 숫자 / null 값은 차단 대상 아님 (예:
 *     `contains_secret: false` 같은 안전 flag 라벨 false positive 방지).
 *  3. object / array → 재귀.
 *
 * @returns 첫 매칭 사유 문자열 또는 null.
 */
function _findSecret(node, depth) {
  if (depth > 8) return null;          // 안전: 무한 재귀 방지
  if (node == null) return null;
  if (typeof node === "string") {
    return _matchSecretValue(node);
  }
  if (typeof node !== "object") return null;
  if (Array.isArray(node)) {
    for (const item of node) {
      const found = _findSecret(item, depth + 1);
      if (found) return found;
    }
    return null;
  }
  // object → key 이름 + 값 모두 점검.
  for (const [k, v] of Object.entries(node)) {
    if (_isSecretCandidateValue(v)) {
      const keyHit = _isSuspiciousKeyName(k);
      if (keyHit) return keyHit;
    }
    const valHit = _findSecret(v, depth + 1);
    if (valHit) return valHit;
  }
  return null;
}


// 호환용 — text 기반 1차 빠른 검사 (regex 만). structured walk 보다 약하
// 지만 빠르고 추가 방어선.
function _containsSecret(text) {
  if (!text || typeof text !== "string") return false;
  return _SECRET_VALUE_PATTERNS.some((re) => re.test(text));
}


export const __test__ = {
  _SECRET_VALUE_PATTERNS,
  _SECRET_KEY_NAMES,
  _matchSecretValue,
  _isSuspiciousKeyName,
  _findSecret,
  _containsSecret,
};


export function OperatorDiagnosticsCard({
  testId = "operator-diagnostics-card",
  frontendMode = null,
  isDesktop = false,
  autoLoad = true,
  pollIntervalMs = 0,
  apiClient = backendApi,
  clipboard = (typeof navigator !== "undefined" && navigator.clipboard) || null,
}) {
  const [report, setReport] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copyState, setCopyState] = useState(null);  // null / "ok" / "fail" / "blocked"
  const [eventFilter, setEventFilter] = useState("ALL");  // ALL / WARN / ERROR

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const minLevel = eventFilter === "ALL" ? null : eventFilter;
      const [r, ev] = await Promise.all([
        apiClient.systemDiagnostics({ frontendMode, isDesktop }),
        apiClient.systemEventsRecent({ limit: 50, minLevel }),
      ]);
      setReport(r);
      setEvents(ev?.events || []);
      setError("");
    } catch (e) {
      setError(e?.message || "운영 진단 조회 실패");
    } finally {
      setLoading(false);
    }
  }, [apiClient, frontendMode, isDesktop, eventFilter]);

  useEffect(() => {
    if (autoLoad) refresh();
  }, [autoLoad, refresh]);

  useEffect(() => {
    if (pollIntervalMs > 0) {
      const t = setInterval(refresh, pollIntervalMs);
      return () => clearInterval(t);
    }
    return undefined;
  }, [refresh, pollIntervalMs]);

  const onCopyReport = useCallback(async () => {
    if (!report) {
      setCopyState("fail");
      return;
    }
    const payload = {
      diagnostics: report,
      recent_events: events.slice(-50),
      generated_at: new Date().toISOString(),
      note: "Paper / SIMULATION 진단 — 민감정보는 포함되지 않습니다.",
    };

    // fix/operator-diagnostics-copy-secret-guard: 2-layer 방어 — 먼저
    // *구조적* (key 이름 + 값 정규식) 점검 → JSON.stringify 결과 정규식
    // 보조 점검. 어느 한쪽이라도 매칭되면 clipboard.writeText 호출 *전*
    // 에 blocked 상태로 전환하고 즉시 반환. clipboard 는 절대 호출되지
    // 않는다 (사용자 요청서 정책 C 1번).
    const structuredHit = _findSecret(payload, 0);
    let text;
    try {
      text = JSON.stringify(payload, null, 2);
    } catch {
      // 직렬화 실패 — 안전을 위해 차단 처리.
      setCopyState("blocked");
      return;
    }
    if (structuredHit || _containsSecret(text)) {
      // *동기* 상태 업데이트 — async clipboard 호출 *전*. waitFor 가
      // 안정적으로 catch 할 수 있도록.
      setCopyState("blocked");
      return;
    }
    if (!clipboard || typeof clipboard.writeText !== "function") {
      setCopyState("fail");
      return;
    }
    try {
      await clipboard.writeText(text);
      setCopyState("ok");
    } catch {
      setCopyState("fail");
    }
  }, [report, events, clipboard]);

  const palette = report
    ? _STATUS_PALETTE[report.overall_status] || _STATUS_PALETTE.WARN
    : null;

  const filteredEvents = useMemo(() => {
    // backend 가 이미 min_level 필터를 처리 — 본 메모는 단지 정렬 보정.
    return events.slice().reverse();   // 최신이 위에
  }, [events]);

  return (
    <Card>
      <div data-testid={testId}
            style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "baseline", flexWrap: "wrap", gap: 6 }}>
          <SectionLabel>🚦 운영 진단</SectionLabel>
          <button
            type="button"
            data-testid={`${testId}-refresh-btn`}
            onClick={refresh}
            disabled={loading}
            style={{
              padding: "4px 10px", borderRadius: 4,
              border: "1px solid var(--c-border)",
              background: "var(--c-surface)",
              fontSize: 11, cursor: loading ? "wait" : "pointer",
            }}
          >
            새로고침
          </button>
        </div>

        {/* 영구 disclaimer */}
        <div
          data-testid={`${testId}-disclaimer`}
          style={{
            padding: "6px 10px", background: "#1e3a8a15",
            border: "1px solid #1e3a8a55", borderRadius: 4,
            fontSize: 11, color: "#1e3a8a", lineHeight: 1.5,
          }}
        >
          본 카드는 PAPER / SIMULATION 자동매매 운영 진단입니다. broker 호출
          0건, 실거래 권한 없음, 민감정보는 진단 리포트에 포함되지 않습니다.
        </div>

        {loading && !report ? (
          <div data-testid={`${testId}-loading`}
                style={{ fontSize: 11, color: "var(--c-text-3)" }}>
            운영 진단 조회 중…
          </div>
        ) : null}

        {error ? (
          <div data-testid={`${testId}-error`}
                style={{
                  padding: "6px 10px", background: "#fef2f2",
                  border: "1px solid #fecaca", borderRadius: 4,
                  fontSize: 11, color: "#991b1b",
                }}>
            ⚠️ {error}{" "}
            <span style={{ fontSize: 10, color: "#7f1d1d" }}>
              (백엔드가 응답하지 않습니다 — desktop launcher 가 backend 를
              띄웠는지 확인하세요.)
            </span>
          </div>
        ) : null}

        {report && palette && (
          <>
            {/* A. 상태 요약 카드 */}
            <div
              data-testid={`${testId}-status-summary`}
              style={{
                padding: "8px 10px", background: palette.bg,
                border: `1px solid ${palette.color}55`,
                borderRadius: 6, fontSize: 12, color: palette.color,
                lineHeight: 1.6,
              }}
            >
              <div style={{ fontWeight: 700, marginBottom: 4 }}>
                전체 상태:{" "}
                <span data-testid={`${testId}-overall-status`}>
                  {palette.label} ({report.overall_status})
                </span>
              </div>
              <div data-testid={`${testId}-conclusion-ko`}>
                {report.conclusion_ko}
              </div>
            </div>

            {/* 상세 grid */}
            <div
              data-testid={`${testId}-detail-grid`}
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                gap: 6, fontSize: 11,
              }}
            >
              <div data-testid={`${testId}-detail-mode`}
                    style={_cellStyle}>
                Backend 모드: <b>{report.default_mode}</b>
                {report.frontend_mode ? (
                  <span style={{ color: "var(--c-text-3)" }}>
                    {" / "}Frontend: <b>{report.frontend_mode}</b>
                  </span>
                ) : null}
              </div>
              <div data-testid={`${testId}-detail-backend`}
                    style={_cellStyle}>
                백엔드 연결:{" "}
                <b style={{ color: report.backend_ready ? "#15803d" : "#991b1b" }}>
                  {report.backend_ready ? "백엔드 연결 정상" : "백엔드 응답 없음"}
                </b>
              </div>
              <div data-testid={`${testId}-detail-market`}
                    style={_cellStyle}>
                시장 데이터 provider: <b>{report.market_data_provider}</b>
              </div>
              <div data-testid={`${testId}-detail-universe`}
                    style={_cellStyle}>
                Universe: <b>{report.universe_source}</b>{" "}
                ({report.universe_count}개)
                {report.universe_fallback_used ? (
                  <span style={{ color: "#92400e", marginLeft: 4 }}>
                    · fallback
                  </span>
                ) : null}
              </div>
              <div data-testid={`${testId}-detail-strategy`}
                    style={_cellStyle}>
                전략 엔진:{" "}
                <b style={{
                  color: report.strategy_engine_connected
                    ? "#15803d" : "#991b1b",
                }}>
                  {report.strategy_engine_connected
                    ? "연결됨" : "전략 엔진 미연동"}
                </b>
              </div>
              <div data-testid={`${testId}-detail-autobot`}
                    style={_cellStyle}>
                자동봇 loop: <b>{report.auto_bot_state}</b>
                {report.auto_bot_running ? " (RUNNING)" : ""}
              </div>
              <div data-testid={`${testId}-detail-permission`}
                    style={_cellStyle}>
                PermissionGate:{" "}
                <b style={{
                  color: report.paper_virtual_execution_allowed
                    ? "#15803d" : "#991b1b",
                }}>
                  {report.paper_virtual_execution_allowed
                    ? "Paper 가상 실행 허용" : "PAPER 가상 실행 차단"}
                </b>
              </div>
              <div data-testid={`${testId}-detail-live-blocked`}
                    style={_cellStyle}>
                실거래:{" "}
                <b style={{ color: "#1e40af" }}>
                  실거래는 비활성화되어 있습니다
                </b>
              </div>
            </div>

            {/* B. 오늘 주문 0건 원인 */}
            <div
              data-testid={`${testId}-zero-order-card`}
              style={{
                padding: "8px 10px", background: "var(--c-surface-2, #f8fafc)",
                border: "1px solid var(--c-border)", borderRadius: 6,
                fontSize: 11, lineHeight: 1.6,
              }}
            >
              <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 4 }}>
                🩺 오늘 주문 0건 원인
              </div>
              <div data-testid={`${testId}-zero-order-primary-code`}
                    style={{
                      fontFamily: "monospace", fontSize: 11,
                      color: report.has_orders_today ? "#15803d" : "#991b1b",
                      fontWeight: 700,
                    }}>
                {report.zero_order_primary_reason}
              </div>
              <div data-testid={`${testId}-zero-order-primary-message`}>
                {report.zero_order_primary_message}
              </div>
              <div data-testid={`${testId}-pipeline-stages`}
                    style={{
                      marginTop: 6, display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                      gap: 4,
                    }}>
                {(report.zero_order_pipeline_stages || []).map((s) => (
                  <div key={s.stage}
                        data-testid={`${testId}-pipeline-${s.stage}`}
                        style={{
                          padding: "3px 6px", borderRadius: 4,
                          fontSize: 10,
                          background: s.ok ? "#dcfce7" : "#fee2e2",
                          color: s.ok ? "#15803d" : "#991b1b",
                          border: `1px solid ${s.ok ? "#86efac" : "#fca5a5"}`,
                        }}>
                    {s.ok ? "✓" : "✗"} {s.stage}: {s.message}
                  </div>
                ))}
              </div>
              {(report.next_actions_ko || []).length > 0 ? (
                <div data-testid={`${testId}-next-actions`}
                      style={{ marginTop: 6 }}>
                  <div style={{ fontWeight: 700, color: "#1e40af",
                                  marginBottom: 2 }}>
                    💡 다음 단계
                  </div>
                  <ul style={{ margin: 0, paddingLeft: 16,
                                color: "#1e40af", lineHeight: 1.5 }}>
                    {report.next_actions_ko.map((a, i) => (
                      <li key={`act-${i}`}
                          data-testid={`${testId}-next-action-${i}`}>
                        {a}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          </>
        )}

        {/* C. 최근 이벤트 로그 */}
        <div
          data-testid={`${testId}-event-log-card`}
          style={{
            padding: "8px 10px", background: "var(--c-surface-2, #f8fafc)",
            border: "1px solid var(--c-border)", borderRadius: 6,
            fontSize: 11, lineHeight: 1.5,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between",
                          alignItems: "baseline", gap: 6, marginBottom: 4 }}>
            <div style={{ fontWeight: 700, fontSize: 12 }}>
              📜 최근 이벤트 로그 ({filteredEvents.length})
            </div>
            <div style={{ display: "flex", gap: 4 }}>
              {[
                { key: "ALL",   label: "전체" },
                { key: "WARN",  label: "경고 이상 보기" },
                { key: "ERROR", label: "오류만 보기" },
              ].map((opt) => (
                <button
                  key={opt.key}
                  type="button"
                  data-testid={`${testId}-event-filter-${opt.key}`}
                  data-selected={eventFilter === opt.key ? "true" : "false"}
                  onClick={() => setEventFilter(opt.key)}
                  style={{
                    padding: "3px 8px", borderRadius: 4,
                    border: `1px solid ${eventFilter === opt.key
                      ? "#2563eb" : "var(--c-border)"}`,
                    background: eventFilter === opt.key
                      ? "#eff6ff" : "var(--c-surface)",
                    color: eventFilter === opt.key ? "#1e3a8a" : "var(--c-text)",
                    fontSize: 10, cursor: "pointer",
                    fontWeight: eventFilter === opt.key ? 700 : 400,
                  }}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
          {filteredEvents.length === 0 ? (
            <div data-testid={`${testId}-event-log-empty`}
                  style={{ color: "var(--c-text-3)", fontSize: 11 }}>
              표시할 이벤트가 없습니다.
            </div>
          ) : (
            <div data-testid={`${testId}-event-log-list`}
                  style={{ maxHeight: 280, overflowY: "auto" }}>
              {filteredEvents.map((ev) => {
                const tone = _LEVEL_TONE[ev.level] || _LEVEL_TONE.INFO;
                return (
                  <div key={ev.id}
                        data-testid={`${testId}-event-row-${ev.id}`}
                        style={{
                          padding: "3px 6px", borderRadius: 3,
                          marginBottom: 2,
                          background: tone.bg,
                          color: tone.color,
                          fontSize: 10, lineHeight: 1.5,
                          fontFamily: "monospace",
                        }}>
                    [{ev.timestamp}] <b>{ev.level}</b> · {ev.category} ·{" "}
                    <b>{ev.code}</b> — {ev.message}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* D. 진단 리포트 복사 + E. 로그 파일 위치 안내 */}
        <div data-testid={`${testId}-actions-row`}
              style={{ display: "flex", gap: 6, alignItems: "center",
                        flexWrap: "wrap" }}>
          <button
            type="button"
            data-testid={`${testId}-copy-report-btn`}
            onClick={onCopyReport}
            disabled={!report || loading}
            style={{
              padding: "4px 10px", borderRadius: 4,
              border: "1px solid #2563eb",
              background: report ? "#eff6ff" : "var(--c-surface)",
              color: "#1e3a8a", fontSize: 11,
              fontWeight: 600,
              cursor: report ? "pointer" : "not-allowed",
            }}
          >
            진단 리포트 복사
          </button>
          {copyState === "ok" ? (
            <span data-testid={`${testId}-copy-ok`}
                  style={{ fontSize: 10, color: "#15803d" }}>
              ✓ 클립보드에 복사되었습니다 (민감정보 미포함).
            </span>
          ) : null}
          {copyState === "fail" ? (
            <span data-testid={`${testId}-copy-fail`}
                  style={{ fontSize: 10, color: "#991b1b" }}>
              ✗ 클립보드 접근 실패 — 수동으로 복사하세요.
            </span>
          ) : null}
          {copyState === "blocked" ? (
            <span data-testid={`${testId}-copy-blocked`}
                  style={{ fontSize: 10, color: "#991b1b" }}>
              ✗ 민감정보가 감지되어 복사가 차단되었습니다.
            </span>
          ) : null}
        </div>

        <div
          data-testid={`${testId}-log-path-hint`}
          style={{
            padding: "4px 8px", background: "var(--c-surface-2, #f8fafc)",
            border: "1px dashed var(--c-border)", borderRadius: 4,
            fontSize: 10, color: "var(--c-text-3)",
          }}
        >
          {_LOG_PATH_HINT_KO}
        </div>

        {/* 영구 invariant 배지 */}
        <div
          data-testid={`${testId}-invariant-badges`}
          style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
        >
          {[
            "Paper / SIMULATION 진단",
            "실거래 권한 없음",
            "민감정보는 진단 리포트에 포함되지 않습니다",
          ].map((label) => (
            <span
              key={label}
              style={{
                fontSize: 9, fontWeight: 700,
                padding: "1px 6px", borderRadius: 3,
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

const _cellStyle = {
  padding: "6px 8px",
  background: "var(--c-surface-2, #f8fafc)",
  border: "1px solid var(--c-border)",
  borderRadius: 4,
};


export default OperatorDiagnosticsCard;
