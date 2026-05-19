import { useCallback, useEffect, useRef, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";
import {
  LAUNCHER_STATES,
  isDesktopApp,
  launcherStateColor,
  startBackendPoll,
  summarizeForCard,
} from "../../desktop/backendLauncher";

// #89 한투 모의투자 AI 자동매매 one-click 테스트 카드.
// #90 데스크톱(EXE) 모드 보강 — backend sidecar 자동 실행 + 상태 표시.
//
// **주문이 아닙니다. 실제 돈이 나가지 않습니다.** 한투 모의투자 API 전용.
// 사용자는 "테스트 시작" 만 누르며, 매수/매도 *수동 버튼* 은 0개.
// AI 판단 결과로 표시되는 BUY/SELL 수는 *집계 카운터* 일 뿐 직접 주문 트리거 X.


const _STATE_COLOR = {
  IDLE:      "#94a3b8",
  CHECKING:  "#7dd3fc",
  READY:     "#22c55e",
  RUNNING:   "#7dd3fc",
  STOPPING:  "#fbbf24",
  COMPLETED: "#22c55e",
  BLOCKED:   "#ef4444",
  FAILED:    "#ef4444",
};

const _STATE_LABEL = {
  IDLE:      "대기",
  CHECKING:  "확인 중",
  READY:     "준비 완료",
  RUNNING:   "테스트 진행 중",
  STOPPING:  "중지 중",
  COMPLETED: "테스트 완료",
  BLOCKED:   "차단됨",
  FAILED:    "실패",
};

const _MODE_LABEL = {
  quick: "한투 모의 빠른 점검",
  slow:  "한투 모의 느린 스트레스",
  mock:  "내부 Mock 고속 스트레스",
};


function _Field({ label, value, testid, color }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between",
                   alignItems: "baseline", padding: "5px 0",
                   borderBottom: "1px solid var(--c-border)",
                   fontSize: "var(--fs-sm)" }}>
      <span style={{ color: "var(--c-text-3)" }}>{label}</span>
      <span data-testid={testid}
            style={{ color: color || "var(--c-text)", fontWeight: 700,
                      fontFamily: "monospace" }}>
        {value}
      </span>
    </div>
  );
}


function useDesktopLauncher() {
  // #90: backend sidecar 자동 실행 상태. 데스크톱 모드가 아니어도 *backend
  // 연결 polling* 은 유용 — 브라우저 dev 환경에서 backend 시작 여부를 같이 표시.
  const [snapshot, setSnapshot] = useState(null);
  const ctlRef = useRef(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    const ctl = startBackendPoll({
      intervalMs: 1500,
      timeoutMs:  30_000,
      onUpdate: setSnapshot,
    });
    ctlRef.current = ctl;
    return () => { try { ctl.cancel(); } catch { /* ignore */ } };
  }, []);

  return summarizeForCard(snapshot);
}


function useKisPaperTest() {
  const [readiness, setReadiness]   = useState(null);
  const [status, setStatus]         = useState(null);
  const [report, setReport]         = useState(null);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState("");
  const [pendingMode, setPendingMode] = useState(null);   // 확인 모달 trigger

  const refreshReadiness = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const r = await backendApi.kisPaperReadiness();
      setReadiness(r);
    } catch (e) {
      setError(e?.message || "readiness 조회 실패");
    }
    setLoading(false);
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const s = await backendApi.kisPaperStatus();
      setStatus(s);
    } catch (e) {
      // status 는 silent fail — UI 가 다른 신호로 표시.
      setError(e?.message || "");
    }
  }, []);

  const refreshReport = useCallback(async () => {
    try {
      const r = await backendApi.kisPaperReport();
      setReport(r);
    } catch (e) {
      setError(e?.message || "");
    }
  }, []);

  const requestStart = useCallback((mode) => {
    setPendingMode(mode);
  }, []);

  const cancelStart = useCallback(() => {
    setPendingMode(null);
  }, []);

  const confirmStart = useCallback(async () => {
    if (!pendingMode) return;
    setLoading(true); setError("");
    try {
      await backendApi.kisPaperStart({ mode: pendingMode, confirm: true });
      setPendingMode(null);
      await refreshStatus();
    } catch (e) {
      setError(e?.message || "테스트 시작 실패");
    }
    setLoading(false);
  }, [pendingMode, refreshStatus]);

  const stop = useCallback(async () => {
    setLoading(true); setError("");
    try {
      await backendApi.kisPaperStop();
      await refreshStatus();
    } catch (e) {
      setError(e?.message || "정지 실패");
    }
    setLoading(false);
  }, [refreshStatus]);

  useEffect(() => {
    // 초기 mount — async fetch 들이 setState 를 일으키지만, mount 시점 1회만이라
    // cascading render 가 발생하지 않는다.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshReadiness();
    refreshStatus();
    refreshReport();
  }, [refreshReadiness, refreshStatus, refreshReport]);

  return {
    readiness, status, report, loading, error, pendingMode,
    refreshReadiness, refreshStatus, refreshReport,
    requestStart, cancelStart, confirmStart, stop,
  };
}


export function KisPaperOneClickTestCard({
  preMarketCheckResult = null,
} = {}) {
  const {
    readiness, status, report, loading, error, pendingMode,
    refreshReadiness, refreshStatus, refreshReport,
    requestStart, cancelStart, confirmStart, stop,
  } = useKisPaperTest();

  // #90: 데스크톱(EXE) 모드 + backend sidecar 연결 상태.
  const launcher = useDesktopLauncher();

  const state = status?.state || "IDLE";
  const isRunning = state === "RUNNING";

  // #91 — Pre-market Checklist 결과를 받아서 시작 버튼을 추가로 게이트.
  // start_allowed=false (DO_NOT_START / FAIL) 면 어떤 mode 도 시작 불가.
  // result 가 없으면 기존 동작 유지 (backwards compat).
  const preMarketBlocked = (
    preMarketCheckResult != null
    && preMarketCheckResult.start_allowed === false
  );

  return (
    <Card>
     <div data-testid="kis-paper-one-click-card">
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>🧪 한투 모의투자 AI 자동매매 테스트</SectionLabel>
        <span data-testid="kis-paper-not-real-money-badge" style={{
          fontSize: 9, fontWeight: 700, color: "#fbbf24",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #fbbf2455", background: "#fbbf2415",
        }}>
          한투 모의투자 전용 · 실제 돈 안 나감
        </span>
      </div>

      <div data-testid="kis-paper-notice"
           style={{ marginBottom: 8, padding: "6px 8px",
                     background: "var(--c-surface-2, #f1f5f9)",
                     borderRadius: 4,
                     fontSize: "var(--fs-xs)",
                     color: "var(--c-text-3)", lineHeight: 1.6 }}>
        이 화면은 한투 모의투자 전용입니다. 실제 돈이 나가지 않습니다. 모든
        주문은 RiskManager + PermissionGate + OrderExecutor 경로를 지나가며,
        KIS_IS_PAPER=true / ENABLE_LIVE_TRADING=false 가 강제됩니다.
        실전 전환은 별도 승인 절차가 필요합니다.
      </div>

      {/* #90: 데스크톱(EXE) 모드 + backend sidecar 연결 상태. */}
      <div data-testid="kis-paper-desktop-launcher"
           style={{ marginBottom: 8, padding: "6px 8px",
                     background: "#0c2035", borderRadius: 4,
                     color: "#cbd5e1", fontSize: "var(--fs-xs)",
                     lineHeight: 1.5, border: "1px solid #1e3a5c" }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "baseline", marginBottom: 4 }}>
          <span style={{ fontWeight: 700, color: "#7dd3fc" }}>
            🖥 데스크톱 앱 상태
          </span>
          <span data-testid="kis-paper-desktop-mode-flag"
                style={{ fontSize: 10,
                          color: launcher.desktopMode ? "#22c55e" : "#94a3b8" }}>
            {launcher.desktopMode
              ? "EXE 데스크톱 모드 — 백엔드가 자동으로 실행됩니다"
              : "브라우저 모드 — 백엔드는 수동으로 실행"}
          </span>
        </div>
        <_Field label="백엔드 연결 상태"
                value={launcher.label}
                testid="kis-paper-launcher-state"
                color={launcherStateColor(launcher.state)} />
        {launcher.hint && (
          <div data-testid="kis-paper-launcher-hint"
               style={{ marginTop: 4, fontSize: 10, color: "#94a3b8" }}>
            {launcher.hint}
          </div>
        )}
      </div>

      {/* 준비 상태 */}
      {readiness && (
        <>
          <_Field label="현재 모드"
                  value={readiness.safety_flags?.default_mode || "—"}
                  testid="kis-paper-default-mode" />
          <_Field label="KIS_IS_PAPER"
                  value={String(readiness.safety_flags?.kis_is_paper)}
                  testid="kis-paper-flag-paper"
                  color={readiness.safety_flags?.kis_is_paper ? "#22c55e" : "#ef4444"} />
          <_Field label="실거래 차단"
                  value={readiness.safety_flags?.enable_live_trading
                    ? "❌ 켜져있음 (위험)" : "✓ 비활성 (안전)"}
                  testid="kis-paper-flag-live"
                  color={readiness.safety_flags?.enable_live_trading
                    ? "#ef4444" : "#22c55e"} />
          <_Field label="AI 자동 실행 차단"
                  value={readiness.safety_flags?.enable_ai_execution
                    ? "❌ 켜져있음 (위험)" : "✓ 비활성 (안전)"}
                  testid="kis-paper-flag-ai-exec"
                  color={readiness.safety_flags?.enable_ai_execution
                    ? "#ef4444" : "#22c55e"} />
          <_Field label="KIS Key 입력됨"
                  value={readiness.kis_key_present ? "✓ 입력됨" : "❌ 미입력"}
                  testid="kis-paper-key-present"
                  color={readiness.kis_key_present ? "#22c55e" : "#fbbf24"} />
          <_Field label="KIS Secret 입력됨"
                  value={readiness.kis_secret_present ? "✓ 입력됨" : "❌ 미입력"}
                  testid="kis-paper-secret-present"
                  color={readiness.kis_secret_present ? "#22c55e" : "#fbbf24"} />
          <_Field label="KIS 계좌번호 입력됨"
                  value={readiness.kis_account_present ? "✓ 입력됨" : "❌ 미입력"}
                  testid="kis-paper-account-present"
                  color={readiness.kis_account_present ? "#22c55e" : "#fbbf24"} />
          {/* fix/desktop-kis-env-readiness-load: 운영자가 *어떤* .env 가
              로드되었는지 진단할 수 있도록 경로 + 로드 성공 여부 노출.
              *Secret 원문 0건* — 경로만. */}
          <_Field label=".env 발견"
                  value={readiness.env_file_found ? "✓ 발견" : "❌ 없음"}
                  testid="kis-paper-env-found"
                  color={readiness.env_file_found ? "#22c55e" : "#fbbf24"} />
          <_Field label=".env 로드됨"
                  value={readiness.env_file_loaded ? "✓ 로드됨" : "❌ 로드 안 됨"}
                  testid="kis-paper-env-loaded"
                  color={readiness.env_file_loaded ? "#22c55e" : "#fbbf24"} />
          {readiness.env_loaded_path && (
            <_Field label=".env 경로"
                    value={readiness.env_loaded_path}
                    testid="kis-paper-env-path" />
          )}
          <_Field label="KIS Paper 모드 가능"
                  value={readiness.can_run_kis_paper
                    ? "✓ 가능" : "❌ 차단"}
                  testid="kis-paper-can-run-kis"
                  color={readiness.can_run_kis_paper ? "#22c55e" : "#fbbf24"} />
          <_Field label="Mock 모드 가능"
                  value={readiness.can_run_mock ? "✓ 가능" : "❌ 차단"}
                  testid="kis-paper-can-run-mock"
                  color={readiness.can_run_mock ? "#22c55e" : "#fbbf24"} />

          {(readiness.detail_messages || []).length > 0 && (
            <div data-testid="kis-paper-readiness-details"
                 style={{ marginTop: 8, padding: "6px 8px",
                           background: "#fef9c3", borderRadius: 4,
                           fontSize: 10, color: "#78350f", lineHeight: 1.5 }}>
              <div style={{ fontWeight: 700, marginBottom: 3 }}>안내</div>
              {readiness.detail_messages.map((m, i) => (
                <div key={i}>• {m}</div>
              ))}
            </div>
          )}
        </>
      )}

      {/* 현재 테스트 상태 */}
      <_Field label="테스트 상태"
              value={_STATE_LABEL[state] || state}
              testid="kis-paper-current-state"
              color={_STATE_COLOR[state] || "#94a3b8"} />
      {status?.mode && (
        <_Field label="실행 모드"
                value={_MODE_LABEL[status.mode] || status.mode}
                testid="kis-paper-current-mode" />
      )}

      {/* #91 — Pre-market check 가 차단 상태이면 시작 버튼이 disabled 됨을 안내. */}
      {preMarketBlocked ? (
        <div data-testid="kis-paper-premarket-blocked-banner"
             style={{ marginTop: 8, padding: "8px 10px",
                       background: "#fee2e2",
                       border: "1px solid #ef444455",
                       borderRadius: 4, fontSize: 11,
                       color: "#991b1b", fontWeight: 700,
                       lineHeight: 1.5 }}>
          🛑 Pre-market Checklist 가 시작 차단 상태입니다 (start_allowed=false).
          위 점검을 통과해야 모의투자 테스트를 시작할 수 있습니다.
        </div>
      ) : null}

      {/* 큰 버튼 5개 */}
      <div style={{ marginTop: 12, display: "flex", gap: 6, flexWrap: "wrap" }}>
        <button data-testid="kis-paper-btn-readiness"
                onClick={() => { refreshReadiness(); refreshStatus(); }}
                disabled={loading}
                style={{
                  padding: "6px 12px", fontSize: "var(--fs-sm)",
                  background: "#0c2035", color: "#7dd3fc",
                  border: "1px solid #1e3a5c", borderRadius: 4,
                  cursor: loading ? "not-allowed" : "pointer",
                  fontWeight: 700, fontFamily: "inherit",
                }}>
          1. 준비상태 확인
        </button>
        <button data-testid="kis-paper-btn-start-quick"
                onClick={() => requestStart("quick")}
                disabled={isRunning || !readiness?.can_run_kis_paper || preMarketBlocked}
                style={{
                  padding: "6px 12px", fontSize: "var(--fs-sm)",
                  background: (readiness?.can_run_kis_paper && !preMarketBlocked) ? "#22c55e" : "#475569",
                  color: (readiness?.can_run_kis_paper && !preMarketBlocked) ? "#000" : "#94a3b8",
                  border: "1px solid",
                  borderColor: (readiness?.can_run_kis_paper && !preMarketBlocked) ? "#22c55e" : "#475569",
                  borderRadius: 4,
                  cursor: (isRunning || !readiness?.can_run_kis_paper || preMarketBlocked)
                    ? "not-allowed" : "pointer",
                  fontWeight: 700, fontFamily: "inherit",
                }}>
          2. 한투 모의 빠른 점검 시작
        </button>
        <button data-testid="kis-paper-btn-start-slow"
                onClick={() => requestStart("slow")}
                disabled={isRunning || !readiness?.can_run_kis_paper || preMarketBlocked}
                style={{
                  padding: "6px 12px", fontSize: "var(--fs-sm)",
                  background: (readiness?.can_run_kis_paper && !preMarketBlocked) ? "#fbbf24" : "#475569",
                  color: (readiness?.can_run_kis_paper && !preMarketBlocked) ? "#000" : "#94a3b8",
                  border: "1px solid",
                  borderColor: (readiness?.can_run_kis_paper && !preMarketBlocked) ? "#fbbf24" : "#475569",
                  borderRadius: 4,
                  cursor: (isRunning || !readiness?.can_run_kis_paper || preMarketBlocked)
                    ? "not-allowed" : "pointer",
                  fontWeight: 700, fontFamily: "inherit",
                }}>
          3. 한투 모의 느린 스트레스 시작
        </button>
        <button data-testid="kis-paper-btn-start-mock"
                onClick={() => requestStart("mock")}
                disabled={isRunning || !readiness?.can_run_mock || preMarketBlocked}
                style={{
                  padding: "6px 12px", fontSize: "var(--fs-sm)",
                  background: (readiness?.can_run_mock && !preMarketBlocked) ? "#7dd3fc" : "#475569",
                  color: (readiness?.can_run_mock && !preMarketBlocked) ? "#000" : "#94a3b8",
                  border: "1px solid",
                  borderColor: (readiness?.can_run_mock && !preMarketBlocked) ? "#7dd3fc" : "#475569",
                  borderRadius: 4,
                  cursor: (isRunning || !readiness?.can_run_mock || preMarketBlocked)
                    ? "not-allowed" : "pointer",
                  fontWeight: 700, fontFamily: "inherit",
                }}>
          4. 내부 Mock 고속 스트레스 시작
        </button>
        <button data-testid="kis-paper-btn-stop"
                onClick={stop}
                disabled={!isRunning}
                style={{
                  padding: "6px 12px", fontSize: "var(--fs-sm)",
                  background: isRunning ? "#ef4444" : "#475569",
                  color: isRunning ? "#fff" : "#94a3b8",
                  border: "1px solid",
                  borderColor: isRunning ? "#ef4444" : "#475569",
                  borderRadius: 4,
                  cursor: isRunning ? "pointer" : "not-allowed",
                  fontWeight: 700, fontFamily: "inherit",
                }}>
          5. 테스트 정지
        </button>
      </div>

      {/* 확인 모달 */}
      {pendingMode && (
        <div data-testid="kis-paper-confirm-modal"
             style={{ marginTop: 10, padding: "10px 12px",
                       background: "#fef3c7",
                       border: "2px solid #fbbf24",
                       borderRadius: 4, fontSize: 12,
                       color: "#78350f", lineHeight: 1.5 }}>
          <div style={{ fontWeight: 800, marginBottom: 6 }}>
            ⚠ 모의투자 테스트 시작 확인
          </div>
          <div style={{ marginBottom: 8 }}>
            <strong>{_MODE_LABEL[pendingMode]}</strong> 를 시작합니다.
            <br />
            실제 돈은 *나가지 않습니다* — 한투 모의투자 또는 내부 mock 만.
            <br />
            AI 가 매수/매도 판단을 하고, 모의 주문이 발행될 수 있습니다.
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <button data-testid="kis-paper-confirm-yes"
                    onClick={confirmStart}
                    disabled={loading}
                    style={{
                      padding: "5px 12px", fontSize: 11,
                      background: "#22c55e", color: "#000",
                      border: "1px solid #22c55e", borderRadius: 4,
                      cursor: loading ? "not-allowed" : "pointer",
                      fontWeight: 700, fontFamily: "inherit",
                    }}>
              모의투자 주문 테스트 시작
            </button>
            <button data-testid="kis-paper-confirm-no"
                    onClick={cancelStart}
                    style={{
                      padding: "5px 12px", fontSize: 11,
                      background: "transparent", color: "#78350f",
                      border: "1px solid #78350f", borderRadius: 4,
                      cursor: "pointer", fontWeight: 700, fontFamily: "inherit",
                    }}>
              취소
            </button>
          </div>
        </div>
      )}

      {/* 결과판 — counters */}
      {status?.counters && (
        <div data-testid="kis-paper-counters"
             style={{ marginTop: 12 }}>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4,
                         fontWeight: 700 }}>
            결과판
          </div>
          <_Field label="AI 판단 횟수"
                  value={status.counters.ai_decisions ?? 0}
                  testid="kis-paper-counter-ai-decisions" />
          <_Field label="AI 매수 신호"
                  value={status.counters.ai_buy_signals ?? 0}
                  testid="kis-paper-counter-buy" />
          <_Field label="AI 매도 신호"
                  value={status.counters.ai_sell_signals ?? 0}
                  testid="kis-paper-counter-sell" />
          <_Field label="모의 주문 시도"
                  value={status.counters.orders_attempted ?? 0}
                  testid="kis-paper-counter-orders-attempted" />
          <_Field label="모의 주문 실행"
                  value={status.counters.orders_executed ?? 0}
                  testid="kis-paper-counter-orders-executed" />
          <_Field label="거절"
                  value={status.counters.orders_rejected ?? 0}
                  testid="kis-paper-counter-rejected" />
          <_Field label="체결"
                  value={status.counters.fills_observed ?? 0}
                  testid="kis-paper-counter-fills" />
          <_Field label="미체결"
                  value={status.counters.unfilled_count ?? 0}
                  testid="kis-paper-counter-unfilled" />
          <_Field label="리스크 차단"
                  value={status.counters.risk_blocks ?? 0}
                  testid="kis-paper-counter-risk-blocks" />
          <_Field label="오류"
                  value={status.counters.errors ?? 0}
                  testid="kis-paper-counter-errors"
                  color={(status.counters.errors || 0) > 0 ? "#ef4444" : undefined} />
        </div>
      )}

      {/* 점수판 */}
      {report?.score && (
        <div data-testid="kis-paper-score-card"
             style={{ marginTop: 12, padding: "8px 10px",
                       background: "#0c2035",
                       borderRadius: 4, color: "#e2e8f0" }}>
          <div style={{ fontSize: 14, fontWeight: 800,
                         color: "#7dd3fc", marginBottom: 4 }}>
            점수 <span data-testid="kis-paper-score-total">{report.score.total}</span> / 100
          </div>
          <div data-testid="kis-paper-score-grade"
               style={{ fontSize: 11, color: "#fbbf24" }}>
            {report.score.grade_label}
          </div>
          <div data-testid="kis-paper-score-one-liner"
               style={{ marginTop: 6, fontSize: 11, lineHeight: 1.5,
                         color: "#cbd5e1" }}>
            {report.score.one_liner}
          </div>
          {(report.score.attention_flags || []).length > 0 && (
            <div style={{ marginTop: 6, fontSize: 10, color: "#ef4444" }}>
              ⚠ 주의 flag: {report.score.attention_flags.join(", ")}
            </div>
          )}
        </div>
      )}

      {/* 실패 메시지 */}
      {status?.failures && status.failures.length > 0 && (
        <div data-testid="kis-paper-failures"
             style={{ marginTop: 10, padding: "6px 8px",
                       background: "#7f1d1d22",
                       border: "1px solid #ef444466",
                       borderRadius: 4, fontSize: 10,
                       color: "#fca5a5", lineHeight: 1.6 }}>
          <div style={{ fontWeight: 700, marginBottom: 3 }}>실패 / 차단</div>
          {status.failures.slice(0, 8).map((f, i) => (
            <div key={i}>• {f}</div>
          ))}
        </div>
      )}

      {error && (
        <div data-testid="kis-paper-error"
             style={{ marginTop: 10, padding: "6px 8px",
                       background: "#7f1d1d22",
                       border: "1px solid #ef444466",
                       borderRadius: 4, fontSize: 10,
                       color: "#fca5a5", lineHeight: 1.6 }}>
          ❌ {error}
        </div>
      )}

      <div style={{ marginTop: 10, textAlign: "right" }}>
        <button data-testid="kis-paper-refresh-report"
                onClick={() => { refreshStatus(); refreshReport(); }}
                style={{
                  padding: "3px 8px", fontSize: 10,
                  background: "#0c2035", border: "1px solid #1e3a5c",
                  borderRadius: 3, cursor: "pointer", color: "#7dd3fc",
                }}>
          ↻ 결과 새로고침
        </button>
      </div>
     </div>
    </Card>
  );
}
