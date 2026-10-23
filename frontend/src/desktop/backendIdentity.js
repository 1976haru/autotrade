// WINDOWS-ONECLICK-EXE-V1 — 데스크톱(EXE)에서 *이번 실행본* 백엔드에만 연결.
//
// 문제: 8000→8001→8002 중 먼저 응답한 포트에 붙어서, 이전 버전/다른 실행본 백엔드나
// 다른 프로그램의 /health 를 정상 KIS Paper 서비스로 오인할 수 있었다.
// 해결: launcher 가 쓴 %APPDATA%/Autotrade/backend-port.json(Tauri `backend_port_info`)
// 의 port 를 먼저 시도하고, /health 의 app_id(고정) · instance_id(실행본별) 를 대조한다.
// 웹/개발 모드(Tauri 아님)에서는 종전 동작 유지 — 신원 검사는 데스크톱에서만 강제.
// Secret 0건 — port / uuid / commit 만 다룬다.

export const BACKEND_APP_ID = "agent-trader-backend";

export function isTauriRuntime() {
  return typeof window !== "undefined" && Boolean(window.__TAURI_INTERNALS__);
}

/** Tauri `backend_port_info` — 실패/웹 모드면 null. */
export async function readDesktopPortInfo({ invokeImpl } = {}) {
  const inv = invokeImpl
    || (isTauriRuntime() && typeof window.__TAURI_INTERNALS__.invoke === "function"
      ? (cmd) => window.__TAURI_INTERNALS__.invoke(cmd)
      : null);
  if (!inv) return null;
  try {
    const info = await inv("backend_port_info");
    if (!info || info.available === false) return null;
    const port = Number(info.port);
    if (!Number.isInteger(port) || port <= 0) return null;
    return { port, instanceId: info.instance_id || null, buildCommit: info.build_commit || null };
  } catch {
    return null;
  }
}

/** preferred port 를 맨 앞으로 (중복 제거). */
export function orderPorts(ports, preferred) {
  const list = Array.isArray(ports) ? ports.slice() : [];
  if (!preferred) return list;
  return [preferred, ...list.filter((p) => p !== preferred)];
}

/**
 * /health body 가 신뢰 가능한 Agent Trader 백엔드인지.
 * @returns {{ok: boolean, reason: string}}
 */
export function judgeBackendIdentity(health, { expectedInstanceId } = {}) {
  if (!health || typeof health !== "object") return { ok: false, reason: "NO_HEALTH" };
  if (health.app_id !== BACKEND_APP_ID) return { ok: false, reason: "OTHER_PROGRAM" };
  if (expectedInstanceId && health.instance_id && health.instance_id !== expectedInstanceId) {
    return { ok: false, reason: "OTHER_INSTANCE" };
  }
  if (health.live_trading_enabled === true || health.kis_is_paper === false) {
    return { ok: false, reason: "UNSAFE_LIVE_FLAGS" };
  }
  return { ok: true, reason: "VERIFIED" };
}

export const IDENTITY_REASON_KO = {
  NO_HEALTH: "백엔드 신원 응답이 없습니다.",
  OTHER_PROGRAM: "다른 프로그램이 포트를 사용 중이라 연결하지 않았습니다.",
  OTHER_INSTANCE: "이전에 실행된 다른 Agent Trader 백엔드라 연결하지 않았습니다.",
  UNSAFE_LIVE_FLAGS: "실거래 설정이 켜진 백엔드라 연결하지 않았습니다 (모의 전용).",
  VERIFIED: "이번 실행본 백엔드 확인 완료",
};

/** GET {baseUrl}/health → body | null. */
export async function fetchHealth(baseUrl, fetchImpl) {
  try {
    const res = await fetchImpl(`${baseUrl}/health`);
    if (!res || !res.ok || typeof res.json !== "function") return null;
    return await res.json();
  } catch {
    return null;
  }
}
