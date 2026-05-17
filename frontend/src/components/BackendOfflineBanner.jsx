import { useCallback, useState } from "react";

import { useBackendStatus } from "../store/useBackendStatus";
import {
  clearConnectionLog,
  getConnectionLog,
  isDesktopApp,
} from "../desktop/backendLauncher";
import {
  isBackendLogAvailable,
  readBackendLog,
} from "../desktop/backendLogReader";

// 214: GitHub Pages 배포 시엔 실제 백엔드가 없다 (FastAPI는 Pages에서 실행되지
// 않음). VITE_DEMO_MODE=true 빌드 플래그가 켜진 상태에서 backend가 unreachable
// 이면 "백엔드 켜라"는 운영자 메시지가 아니라 "🧪 Demo Mode" 배너를 띄워
// 데모 사용자가 mock 데이터임을 즉시 인지하도록 한다.
//
// 213: 로컬 dev에서는 VITE_DEMO_MODE 가 비어 있어 기존 메시지(uvicorn 실행
// 안내)가 그대로 노출된다. 두 모드는 export 한 헬퍼 isDemoBuild로만 분기.
//
// fix/desktop-backend-sidecar-autostart: Tauri 데스크톱(EXE) 모드 — sidecar 가
// 자동 spawn 됨. 사용자가 개발자 아니므로 "uvicorn 실행하세요" 메시지는 *오히려
// 혼란*. EXE 모드 분기에서는 친절한 "백엔드 자동 실행 중" + 재시도 / 로그 보기
// 버튼만 노출.
export function isDemoBuild() {
  if (typeof import.meta === "undefined") return false;
  const v = import.meta.env?.VITE_DEMO_MODE;
  return v === "true" || v === true;
}

function _DesktopBanner({ readBackendLogImpl = readBackendLog } = {}) {
  const [showLog, setShowLog] = useState(false);
  const [logSnapshot, setLogSnapshot] = useState([]);
  const [backendLog, setBackendLog] = useState(null); // null = not loaded
  const [reloadKey, setReloadKey] = useState(0);

  const onShowLog = useCallback(async () => {
    setLogSnapshot(getConnectionLog());
    setShowLog(true);
    // backend log 도 함께 로드 (Tauri 환경에서만 실제 content; else null).
    try {
      const txt = await readBackendLogImpl();
      setBackendLog(txt);
    } catch (err) {
      setBackendLog(`(read error: ${err?.message || err})`);
    }
  }, [readBackendLogImpl]);

  const onHideLog = useCallback(() => setShowLog(false), []);

  const onRetry = useCallback(() => {
    // 단순 reload — backendLauncher 폴링은 이미 background 에서 동작 중이지만
    // 사용자에게 "내가 한 번 더 시도했다" 피드백 제공.
    clearConnectionLog();
    setReloadKey((k) => k + 1);
    if (typeof window !== "undefined" && typeof window.location?.reload === "function") {
      window.location.reload();
    }
  }, []);

  return (
    <div
      data-testid="desktop-backend-launching-banner"
      data-reload-key={reloadKey}
      style={{
        padding: "12px 16px",
        margin: "10px 12px",
        background: "#eff6ff",
        border: "1px solid #bfdbfe",
        borderRadius: "var(--r-lg)",
        color: "#1e3a8a",
        fontSize: "var(--fs-sm)",
        lineHeight: "var(--lh-loose)",
        boxShadow: "var(--sh-1)",
      }}
    >
      <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4, fontSize: "var(--fs-md)" }}>
        🔄 백엔드 자동 실행 중입니다
      </div>
      <div style={{ color: "var(--c-text-2)", marginBottom: 8 }}>
        앱이 backend sidecar 를 자동으로 시작하고 있습니다.
        <b> 첫 실행 시 DB 초기화(alembic migration)로 최대 1~2분이 걸릴 수 있습니다.</b>
        {" "}최대 90초간 자동 재시도하며, 그래도 안 되면 아래 "재시도" 또는 "로그 보기" 를
        눌러 backend-YYYYMMDD.log 의 startup 진행 상황을 확인하세요.
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
        <button
          data-testid="btn-retry-connection"
          onClick={onRetry}
          style={{
            padding: "6px 14px",
            borderRadius: "var(--r-md)",
            background: "#2563eb",
            color: "#fff",
            border: "none",
            cursor: "pointer",
            fontWeight: "var(--fw-bold)",
            fontSize: "var(--fs-xs)",
          }}
        >
          재시도
        </button>
        <button
          data-testid="btn-show-connection-log"
          onClick={showLog ? onHideLog : onShowLog}
          style={{
            padding: "6px 14px",
            borderRadius: "var(--r-md)",
            background: "#fff",
            color: "#1e3a8a",
            border: "1px solid #bfdbfe",
            cursor: "pointer",
            fontSize: "var(--fs-xs)",
          }}
        >
          {showLog ? "로그 닫기" : "로그 보기"}
        </button>
      </div>
      {showLog && (
        <>
          <div
            data-testid="connection-log-panel"
            style={{
              background: "#fff",
              border: "1px solid #bfdbfe",
              borderRadius: "var(--r-md)",
              padding: "6px 10px",
              marginTop: 6,
              fontSize: "var(--fs-xs)",
              color: "var(--c-text)",
              maxHeight: 180,
              overflowY: "auto",
            }}
          >
            <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              연결 시도 (frontend)
            </div>
            {logSnapshot.length === 0 ? (
              <div data-testid="connection-log-empty">
                아직 연결 시도 기록이 없습니다 (배너가 처음 뜬 직후 일 수 있음).
              </div>
            ) : (
              logSnapshot.map((e, i) => (
                <div key={i} data-testid={`connection-log-entry-${i}`}>
                  <code style={{ color: "var(--c-text-3)" }}>{e.ts}</code>{" "}
                  <b>{e.kind}</b>
                  {e.url ? ` ${e.url}` : ""}
                  {e.error ? ` — ${e.error}` : ""}
                </div>
              ))
            )}
          </div>
          {/* fix/desktop-sidecar-runtime-diagnostics: Tauri 단에서 기록한 sidecar
              stdout/stderr/exit 로그. 비-Tauri 환경에서는 null 반환 → "데스크톱
              모드에서만" 안내. Secret 패턴은 backendLogReader 가 [REDACTED] 마스킹. */}
          <div
            data-testid="backend-log-panel"
            style={{
              background: "#0f172a",
              border: "1px solid #334155",
              borderRadius: "var(--r-md)",
              padding: "6px 10px",
              marginTop: 6,
              fontSize: "var(--fs-xs)",
              color: "#e2e8f0",
              maxHeight: 240,
              overflowY: "auto",
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
            }}
          >
            <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4, color: "#7dd3fc" }}>
              백엔드 sidecar 로그 ({"%APPDATA%\\Autotrade\\logs\\desktop-backend.log"})
            </div>
            {backendLog === null ? (
              <div data-testid="backend-log-na">
                {isBackendLogAvailable()
                  ? "로딩 중..."
                  : "데스크톱(EXE) 모드에서만 사용 가능합니다."}
              </div>
            ) : backendLog.trim() === "" ? (
              <div data-testid="backend-log-empty">
                (로그 파일이 비어 있습니다 — sidecar 가 아직 출력하지 않음)
              </div>
            ) : (
              <pre
                data-testid="backend-log-content"
                style={{
                  margin: 0,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  fontFamily: "inherit",
                }}
              >
                {backendLog}
              </pre>
            )}
          </div>
        </>
      )}
      <div
        data-testid="desktop-mode-badge"
        style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-4)", marginTop: 6 }}
      >
        데스크톱 모드 · KIS 모의 · 실거래 OFF
      </div>
    </div>
  );
}

// fix/desktop-nonblocking-migration-health: backend 가 응답하지만 alembic
// migration 이 진행 중 (db_ready=false) 일 때 표시되는 배너. *backend offline
// 배너와 별개* — 사용자가 "백엔드 죽었다" 로 오인하지 않게 노란 안내 톤.
function _DbPreparingBanner({ status }) {
  const migStatus = status?.migration_status || "running";
  const startedAt = status?.migration_started_at || null;
  return (
    <div
      data-testid="backend-db-preparing-banner"
      data-migration-status={migStatus}
      style={{
        padding: "10px 14px",
        margin: "10px 12px",
        background: "#fefce8",
        border: "1px solid #fde68a",
        borderRadius: "var(--r-lg)",
        color: "#854d0e",
        fontSize: "var(--fs-sm)",
        lineHeight: "var(--lh-loose)",
      }}
    >
      <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
        ⏳ 초기 DB 준비 중입니다
      </div>
      <div style={{ color: "var(--c-text-2)", marginBottom: 4 }}>
        백엔드는 정상 응답 중이며 데이터베이스 마이그레이션이 진행 중입니다.
        <b> 최대 1~2분이 걸릴 수 있으며</b>, 완료 후 자동으로 연결 완료 상태로 전환됩니다.
      </div>
      <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
        migration_status: <code>{migStatus}</code>
        {startedAt ? <> · started_at: <code>{startedAt}</code></> : null}
      </div>
    </div>
  );
}


export function BackendOfflineBanner() {
  const { status, error, loading, baseUrl, viaFallback } = useBackendStatus();
  if (loading) return null;

  // fix/desktop-nonblocking-migration-health: backend 가 응답해도 (no error)
  // db_ready=false 면 "백엔드 offline" 으로 오인하지 않고 *DB 준비 중* 배너
  // 노출. useBackendStatus 가 N초마다 재폴링하므로 db_ready=true 가 되면
  // 본 분기를 빠져나가 정상 연결 완료 UI 로 자동 전환.
  if (!error && status && status.db_ready === false) {
    return <_DbPreparingBanner status={status} />;
  }

  // fix/frontend-detects-fallback-backend-port: connected 상태에서 fallback
  // 포트면 작은 초록 배지 — "✅ Backend 연결 완료: 8001". 기본 포트 (8000)
  // 면 invisible (current behavior). 본 상태는 update fetch 실패와 *별개* —
  // UpdateBanner 자체가 별도 컴포넌트로 격리됨.
  if (!error) {
    if (viaFallback && baseUrl) {
      const portMatch = String(baseUrl).match(/:(\d+)/);
      const portLabel = portMatch ? portMatch[1] : baseUrl;
      return (
        <div
          data-testid="backend-connected-fallback-banner"
          data-port={portLabel}
          style={{
            padding: "6px 12px",
            margin: "6px 12px",
            background: "#f0fdf4",
            border: "1px solid #bbf7d0",
            borderRadius: "var(--r-md)",
            color: "#065f46",
            fontSize: "var(--fs-xs)",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <span>✅</span>
          <span>
            Backend 연결 완료: <b>:{portLabel}</b>
            <span style={{ marginLeft: 6, color: "var(--c-text-3)" }}>
              (fallback port — 기본 8000 사용 불가로 자동 전환됨)
            </span>
          </span>
        </div>
      );
    }
    return null;
  }

  // EXE/Tauri 데스크톱 모드 — sidecar 자동 spawn 흐름. uvicorn 안내 *0건*.
  if (isDesktopApp()) {
    return <_DesktopBanner />;
  }

  if (isDemoBuild()) {
    // 234 (UI-006): 새 .ui-demo-banner 토큰으로 시각 통일.
    return (
      <div data-testid="demo-mode-banner" style={{ margin: "10px 12px" }}>
        <div className="ui-demo-banner">
          <div className="ui-demo-banner__title">🧪 Demo Mode (GitHub Pages)</div>
          <div className="ui-demo-banner__body">
            이 화면은 <b>UI 데모</b>입니다. 실제 백엔드/브로커가 없어 모든 데이터는 mock·virtual 입니다.
            실거래·실주문은 발생하지 않으며, 일부 카드는 빈 상태로 표시됩니다.
          </div>
          <div className="ui-demo-banner__hint">
            전체 기능을 보려면 로컬에서 backend(uvicorn) + frontend(npm run dev)를 함께 실행하세요.
          </div>
          {/* 220 build tag — auto-update 회로 검증용. 작은 회색 글씨로 유지. */}
          <div
            data-testid="demo-build-tag"
            style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-5)" }}
          >
            build · auto-update-220
          </div>
        </div>
      </div>
    );
  }

  // 240 (Light-003): 로컬 dev — 개발자용 uvicorn 안내 유지.
  return (
    <div
      data-testid="backend-offline-banner"
      style={{
        padding: "12px 16px",
        margin: "10px 12px",
        background: "#fef2f2",
        border: "1px solid #fecaca",
        borderRadius: "var(--r-lg)",
        color: "#7f1d1d",
        fontSize: "var(--fs-sm)",
        lineHeight: "var(--lh-loose)",
        boxShadow: "var(--sh-1)",
      }}
    >
      <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4, fontSize: "var(--fs-md)" }}>
        ⚠ 백엔드 연결 대기 중입니다
      </div>
      <div style={{ color: "var(--c-text-2)", marginBottom: 8 }}>
        실데이터를 보려면 backend와 frontend를 함께 실행하세요.
      </div>
      <pre
        style={{
          background: "var(--c-surface-2)",
          border: "1px solid var(--c-border)",
          padding: "8px 10px",
          borderRadius: "var(--r-md)",
          fontSize: "var(--fs-xs)",
          color: "var(--c-text)",
          margin: 0,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
{`cd backend
uvicorn app.main:app --reload`}
      </pre>
      <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 6 }}>
        실행 후 페이지를 새로고침하세요.
      </div>
    </div>
  );
}
