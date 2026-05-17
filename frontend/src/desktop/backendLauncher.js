// Agent Trader v1 — frontend backend launcher / health checker (#90).
//
// 본 모듈은 *EXE 데스크톱 모드* 에서 backend sidecar 의 상태를 polling 한다.
// 실제 sidecar spawn 은 Rust (`src-tauri/src/main.rs`) 가 담당하며, 본 JS 는
// HTTP 상태만 관찰해 사용자에게 "백엔드 연결 중 / 완료 / 실패" 를 표시한다.
//
// 절대 원칙 (CLAUDE.md):
//   - 본 모듈은 broker / OrderExecutor / route_order 를 호출하지 *않는다*.
//   - 매수 / 매도 / 실거래 트리거 0건 — `/api/status` 와 `/api/kis-paper/
//     readiness` 만 read-only 로 호출.
//   - Secret / API Key / 계좌번호를 응답에서 *기대하지 않으며* (backend 도
//     보내지 않음), 만약 들어와도 본 모듈은 *저장 / 출력 / 렌더링 0건*.
//
// 상태 머신:
//   IDLE          — 초기, polling 시작 전
//   CONNECTING    — polling 중, 아직 응답 없음
//   DB_PREPARING  — backend 응답 OK 지만 db_ready=false (alembic migration 중)
//   READY         — backend healthy + DB ready + readiness 응답 OK
//   NEEDS_ENV     — backend healthy 지만 KIS .env 미설정 (KIS 모드 불가)
//   UNSAFE        — backend healthy 지만 ENABLE_LIVE_TRADING=true 등 위험 flag
//   FAILED        — 일정 시간 polling 실패

export const LAUNCHER_STATES = Object.freeze({
  IDLE:         "IDLE",
  CONNECTING:   "CONNECTING",
  DB_PREPARING: "DB_PREPARING",
  READY:        "READY",
  NEEDS_ENV:    "NEEDS_ENV",
  UNSAFE:       "UNSAFE",
  FAILED:       "FAILED",
});

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
// fix/desktop-backend-startup-readiness: 첫 실행 시 alembic migration 이 1~2분
// 소요될 수 있어 기존 30s timeout 으로는 "DB 준비 중" 인데 FAILED 로 단정되는
// 회귀가 발생. 90s 로 늘려 첫 실행 migration 을 허용 — 이후 실행은 보통 수초
// 이내 READY 로 진입. KIS rate limit 영향 없음 (로컬 polling).
const DEFAULT_TIMEOUT_MS  = 90_000;    // 90s 안에 살아나지 않으면 FAILED
const DEFAULT_INTERVAL_MS = 1_000;     // 1s 간격 polling — KIS rate limit 무관 (로컬)
// CONNECTING 상태에서 elapsed 가 이 임계를 넘으면 hint 가 "초기 DB 준비 중"
// 으로 격상되어 사용자가 첫 실행 migration 지연임을 인지한다.
const DB_PREP_HINT_THRESHOLD_MS = 5_000;

// fix/desktop-sidecar-port-fallback: 8000 이 stale 프로세스에 점유된 경우
// backend launcher 가 8001/8002 로 fallback bind 한다. frontend 도 동일 순서로
// 시도 — 8000 실패 시 8001, 8002 를 차례로 probe.
const DEFAULT_FALLBACK_PORTS = [8000, 8001, 8002];

function _baseUrlForPort(port) {
  return `http://127.0.0.1:${port}`;
}

// **Tauri 감지** — `window.__TAURI_INTERNALS__` 또는 `__TAURI__` 등을 통해
// 데스크톱 모드 여부 판단. 브라우저 dev 환경에선 false → backend 수동 실행.
export function isDesktopApp() {
  if (typeof window === "undefined") return false;
  // Tauri v2 는 `__TAURI_INTERNALS__` 를 노출.
  if (window.__TAURI_INTERNALS__ != null) return true;
  if (window.__TAURI__ != null) return true;
  if (window.__TAURI_METADATA__ != null) return true;
  return false;
}

/** classify the backend snapshot into a launcher state. */
export function classifyLauncherState({ statusOk, readiness, safety, status }) {
  if (!statusOk) return LAUNCHER_STATES.CONNECTING;

  // fix/desktop-nonblocking-migration-health: backend 가 살아있어도 alembic
  // migration 이 *진행 중* 이면 db_ready=false 로 carry. *backend offline 으로
  // 오인하지 않고* "초기 DB 준비 중" 상태로 표시. db_ready=true 가 되면 본
  // 분기를 빠져나가 정상 READY/NEEDS_ENV/UNSAFE 판단으로 자동 전환.
  if (status && status.db_ready === false) {
    return LAUNCHER_STATES.DB_PREPARING;
  }

  const flags = safety || readiness?.safety_flags || {};
  const enableLive   = flags.enable_live_trading === true;
  const enableAiExec = flags.enable_ai_execution === true;
  const enableFutLive = flags.enable_futures_live_trading === true;
  const kisIsPaper   = flags.kis_is_paper !== false;   // default true

  if (enableLive || enableAiExec || enableFutLive || !kisIsPaper) {
    return LAUNCHER_STATES.UNSAFE;
  }
  if (readiness && readiness.can_run_kis_paper === false
      && readiness.can_run_mock !== false) {
    // backend 살아있고 안전 flag OK, 다만 KIS key 미입력 — mock 만 사용 가능.
    return LAUNCHER_STATES.NEEDS_ENV;
  }
  return LAUNCHER_STATES.READY;
}

/** human-readable label for UI. */
export function launcherStateLabel(state) {
  switch (state) {
    case LAUNCHER_STATES.IDLE:         return "대기 중";
    case LAUNCHER_STATES.CONNECTING:   return "백엔드 연결 중";
    case LAUNCHER_STATES.DB_PREPARING: return "초기 DB 준비 중";
    case LAUNCHER_STATES.READY:        return "백엔드 연결 완료";
    case LAUNCHER_STATES.NEEDS_ENV:    return "한투 모의투자 API 설정 필요";
    case LAUNCHER_STATES.UNSAFE:       return "안전 flag 위반 — 모의 테스트 차단";
    case LAUNCHER_STATES.FAILED:       return "백엔드 실행 실패 — 재시작 또는 설정 확인 필요";
    default:                            return state;
  }
}

/** color hint for UI. */
export function launcherStateColor(state) {
  switch (state) {
    case LAUNCHER_STATES.READY:        return "#22c55e";
    case LAUNCHER_STATES.NEEDS_ENV:    return "#fbbf24";
    case LAUNCHER_STATES.UNSAFE:       return "#ef4444";
    case LAUNCHER_STATES.FAILED:       return "#ef4444";
    case LAUNCHER_STATES.CONNECTING:   return "#7dd3fc";
    case LAUNCHER_STATES.DB_PREPARING: return "#fbbf24";
    default:                            return "#94a3b8";
  }
}

// ====================================================================
// 진단용 연결 시도 로그 (in-memory ring buffer)
// ====================================================================
//
// EXE 모드에서 sidecar 가 살아나지 않을 때 사용자가 "로그 보기" 로 어떤
// 시도가 있었고 어떤 에러가 났는지 한눈에 볼 수 있게 한다. localStorage 나
// 디스크에 쓰지 않는다 — secret 노출 위험 회피.

const _CONNECTION_LOG_MAX = 50;
const _connectionLog = [];

function _appendLog(entry) {
  const ts = new Date().toISOString();
  _connectionLog.push({ ts, ...entry });
  while (_connectionLog.length > _CONNECTION_LOG_MAX) {
    _connectionLog.shift();
  }
}

export function getConnectionLog() {
  // 복사본 반환 — 외부 mutation 차단.
  return _connectionLog.slice();
}

export function clearConnectionLog() {
  _connectionLog.length = 0;
}


/** Single probe — `/api/status` + `/api/kis-paper/readiness`. */
export async function probeBackendOnce({
  baseUrl = DEFAULT_BACKEND_URL,
  fetchImpl = (typeof window !== "undefined" && window.fetch)
    ? window.fetch.bind(window)
    : globalThis.fetch,
} = {}) {
  if (typeof fetchImpl !== "function") {
    return { statusOk: false, error: "fetch not available" };
  }
  try {
    const statusRes = await fetchImpl(`${baseUrl}/api/status`);
    if (!statusRes || !statusRes.ok) {
      const err = `status http ${statusRes?.status}`;
      _appendLog({ kind: "probe_failed", url: `${baseUrl}/api/status`, error: err });
      // fix/desktop-sidecar-runtime-diagnostics: /api/status 실패 시 /health
      // fallback. backend 가 *살아있는지* 만이라도 확인. /health 가 응답하면
      // statusOk=true 로 표시하되 status payload 는 empty — UNSAFE / NEEDS_ENV
      // 분기는 classifyLauncherState 가 safety=null 로 READY 로 처리.
      try {
        const healthRes = await fetchImpl(`${baseUrl}/health`);
        if (healthRes && healthRes.ok) {
          _appendLog({ kind: "health_fallback_ok", url: `${baseUrl}/health` });
          return {
            statusOk: true,
            status: { __via_health_fallback: true },
            readiness: null,
            safety: null,
          };
        }
        _appendLog({
          kind: "health_fallback_failed",
          url: `${baseUrl}/health`,
          error: `http ${healthRes?.status}`,
        });
      } catch (he) {
        _appendLog({
          kind: "health_fallback_exception",
          url: `${baseUrl}/health`,
          error: he?.message || String(he),
        });
      }
      return { statusOk: false, error: err };
    }
    const status = await statusRes.json();
    _appendLog({ kind: "probe_ok", url: `${baseUrl}/api/status` });

    // readiness — *실패해도 launcher 는 살아있다*. backend healthy 인데
    // readiness 만 빠지면 NEEDS_ENV / UNSAFE 가 아닌 READY 로 보일 수
    // 있어 fallback 으로 status.safety_flags 를 사용.
    let readiness = null;
    try {
      const rdRes = await fetchImpl(`${baseUrl}/api/kis-paper/readiness`);
      if (rdRes && rdRes.ok) {
        readiness = await rdRes.json();
      }
    } catch (_e) {
      readiness = null;
    }

    return {
      statusOk: true,
      status,
      readiness,
      safety: status?.safety_flags || null,
    };
  } catch (err) {
    const msg = err?.message || String(err);
    _appendLog({ kind: "probe_exception", url: `${baseUrl}/api/status`, error: msg });
    return { statusOk: false, error: msg };
  }
}

/** Poll backend until READY/NEEDS_ENV/UNSAFE or timeout. Pure JS — caller decides UI.
 *
 * fix/desktop-sidecar-port-fallback: baseUrl 단일 probe 대신 ports 배열을
 * 받아 multi-port probing. 호환성: baseUrl 만 주어진 경우 그 포트 1개로 fallback.
 */
export function startBackendPoll({
  baseUrl = DEFAULT_BACKEND_URL,
  ports,
  intervalMs = DEFAULT_INTERVAL_MS,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  onUpdate,
  fetchImpl,
  setTimeoutImpl = setTimeout,
  clearTimeoutImpl = clearTimeout,
  nowImpl = () => Date.now(),
} = {}) {
  // ports 가 명시되지 않았고 baseUrl 이 default 면 fallback ports 사용.
  // baseUrl 이 명시되면 그 단일 포트만 시도 (backwards compat).
  let resolvedPorts;
  if (Array.isArray(ports) && ports.length > 0) {
    resolvedPorts = ports;
  } else if (baseUrl === DEFAULT_BACKEND_URL) {
    resolvedPorts = DEFAULT_FALLBACK_PORTS;
  } else {
    // baseUrl 에서 port 추출.
    const m = String(baseUrl).match(/:(\d+)/);
    resolvedPorts = m ? [parseInt(m[1], 10)] : DEFAULT_FALLBACK_PORTS;
  }

  const startedAt = nowImpl();
  let timer = null;
  let cancelled = false;

  const tick = async () => {
    if (cancelled) return;
    const elapsed = nowImpl() - startedAt;
    if (elapsed > timeoutMs) {
      onUpdate?.({
        state: LAUNCHER_STATES.FAILED,
        elapsedMs: elapsed,
        error: "backend did not come up within timeout",
      });
      return;
    }
    const probe = await probeBackendWithFallback({
      ports: resolvedPorts,
      fetchImpl,
    });
    if (cancelled) return;
    if (!probe.statusOk) {
      onUpdate?.({
        state: LAUNCHER_STATES.CONNECTING,
        elapsedMs: elapsed,
        error: probe.error,
      });
      timer = setTimeoutImpl(tick, intervalMs);
      return;
    }
    const newState = classifyLauncherState({
      statusOk: true,
      readiness: probe.readiness,
      safety: probe.safety,
      status: probe.status,
    });
    onUpdate?.({
      state: newState,
      elapsedMs: elapsed,
      readiness: probe.readiness,
      safety: probe.safety,
      status: probe.status,
      // 어느 포트에 성공했는지 carry — UI 가 "현재 backend port: 8001" 표시 가능.
      baseUrl: probe.baseUrl,
      port: probe.port,
    });
    // 도달 후에도 *interval polling 을 유지* — 사용자가 .env 를 수정하면
    // 새 상태로 자동 전환. 다만 종료 조건은 cancel().
    timer = setTimeoutImpl(tick, intervalMs);
  };

  // 첫 tick 은 즉시 — *동기적으로 promise 를 반환하지 않는다* (caller 가
  // onUpdate 로 받음).
  tick();

  return {
    cancel() {
      cancelled = true;
      if (timer != null) {
        clearTimeoutImpl(timer);
        timer = null;
      }
    },
  };
}

/**
 * Multi-port probe — 8000 실패 시 8001, 8002 순서 시도.
 *
 * 첫 번째 success 반환. 모두 실패면 마지막 error 반환. 매 시도가
 * connection log 에 기록되므로 사용자가 "로그 보기" 로 어느 포트가 살아있는지
 * 확인 가능.
 */
export async function probeBackendWithFallback({
  ports = DEFAULT_FALLBACK_PORTS,
  fetchImpl,
} = {}) {
  let lastError = "no ports tried";
  for (const port of ports) {
    const baseUrl = _baseUrlForPort(port);
    const probe = await probeBackendOnce({ baseUrl, fetchImpl });
    if (probe.statusOk) {
      _appendLog({ kind: "probe_success_port", url: baseUrl });
      return { ...probe, baseUrl, port };
    }
    lastError = probe.error || lastError;
  }
  _appendLog({ kind: "probe_all_ports_failed", error: lastError });
  return { statusOk: false, error: lastError };
}


/** UI-friendly summary for KisPaperOneClickTestCard 데스크톱 보강. */
export function summarizeForCard(snapshot) {
  if (!snapshot) {
    return {
      desktopMode: isDesktopApp(),
      state: LAUNCHER_STATES.IDLE,
      label: launcherStateLabel(LAUNCHER_STATES.IDLE),
      color: launcherStateColor(LAUNCHER_STATES.IDLE),
      canStartTest: false,
      hint: "백엔드 연결 대기",
    };
  }
  const { state, elapsedMs } = snapshot;
  const canStartTest =
    state === LAUNCHER_STATES.READY || state === LAUNCHER_STATES.NEEDS_ENV;
  let hint = "";
  switch (state) {
    case LAUNCHER_STATES.READY:
      hint = "한투 모의 빠른 점검 시작 버튼을 누를 수 있습니다.";
      break;
    case LAUNCHER_STATES.NEEDS_ENV:
      hint = "KIS 모의투자 키가 비어 있어 mock 모드만 가능합니다. " +
             "%APPDATA%\\Autotrade\\.env 에 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO 를 채우면 KIS 모드 활성.";
      break;
    case LAUNCHER_STATES.UNSAFE:
      hint = "ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING " +
             "중 하나가 켜져 있습니다. 모의 테스트는 차단됩니다 — .env 에서 false 로 변경.";
      break;
    case LAUNCHER_STATES.FAILED:
      // fix/desktop-backend-startup-readiness: 90s timeout 후에도 응답이
      // 없다면 단순 "재빌드" 안내보다 *로그 파일 위치* 를 명확히 제시한다.
      // %APPDATA%\Autotrade\logs\backend-YYYYMMDD.log 가 단일 진실.
      hint = "백엔드가 90초 안에 응답하지 않았습니다. " +
             "%APPDATA%\\Autotrade\\logs\\backend-YYYYMMDD.log 를 열어 " +
             "alembic migration 실패 / uvicorn 부팅 오류 / 포트 충돌 메시지를 확인하세요. " +
             "필요 시 앱을 재시작하거나 scripts/build_backend_sidecar.ps1 로 sidecar 를 재빌드.";
      break;
    case LAUNCHER_STATES.CONNECTING:
      // 첫 실행 alembic migration 은 1~2분 걸릴 수 있어 단순 "기다려주세요" 보다
      // 사용자에게 명시적 안내. elapsed 가 짧으면 일반 안내, 길어지면 DB 안내.
      if (typeof elapsedMs === "number" && elapsedMs >= DB_PREP_HINT_THRESHOLD_MS) {
        hint = "초기 DB 준비 중입니다. 최대 1~2분 걸릴 수 있습니다. " +
               "%APPDATA%\\Autotrade\\logs 의 backend-*.log 에서 진행 상황을 확인할 수 있습니다.";
      } else {
        hint = "백엔드가 시작될 때까지 잠시만 기다려주세요... " +
               "(첫 실행 시 DB 초기화로 최대 1~2분 소요될 수 있습니다.)";
      }
      break;
    case LAUNCHER_STATES.DB_PREPARING:
      // fix/desktop-nonblocking-migration-health: backend 가 응답하지만 alembic
      // migration 이 백그라운드에서 진행 중인 상태. *offline 아님* — db_ready
      // 가 true 로 바뀌면 다음 polling 에서 자동 READY 전환.
      hint = "초기 DB 준비 중입니다. 최대 1~2분 걸릴 수 있습니다. " +
             "백엔드는 정상 응답 중이며 DB 마이그레이션 완료 후 자동으로 연결 완료로 전환됩니다.";
      break;
    default:
      hint = "";
  }
  return {
    desktopMode: isDesktopApp(),
    state,
    label: launcherStateLabel(state),
    color: launcherStateColor(state),
    canStartTest,
    hint,
  };
}
