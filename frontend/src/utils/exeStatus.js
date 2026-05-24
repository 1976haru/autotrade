/**
 * #53 / 7-01 — EXE 운영 상태 정규화 + 라벨 helper (pure, 테스트 가능).
 *
 * 목적: 사용자가 "Backend 연결됨" 과 "연결 실패" 를 *동시에* 보지 않도록,
 * backend_api_reachable / sidecar_status / diagnostics_status / db_status /
 * kis_paper_readiness 를 분리된 단일 표준 상태로 정규화한다.
 *
 * 단일 진실 규칙 (테스트로 lock):
 *  - backend_api_reachable 는 *frontend 의 fetch 성공 여부* 가 결정한다
 *    (backend body 의 self-report 가 아니라). fetch 실패 시 reachable=false.
 *  - reachable=false 이면 diagnostics_status / db_status / kis_paper_readiness
 *    는 모두 UNKNOWN 으로 강등된다 (연결 실패인데 "정상" 표시 금지).
 *  - 따라서 "연결됨" 과 "연결 실패" 가 동시에 표시될 수 없다 (라벨은 단일
 *    boolean backend_api_reachable 에서만 파생).
 *  - 본 helper 는 broker / 주문 / 실거래 개념을 다루지 않으며 secret 원문을
 *    표시/보관하지 않는다.
 */

export const SIDECAR_STATUS = Object.freeze({
  RUNNING: "RUNNING",
  STARTING: "STARTING",
  STOPPED: "STOPPED",
  UNKNOWN: "UNKNOWN",
});

export const DIAGNOSTICS_STATUS = Object.freeze({
  OK: "OK",
  DEGRADED: "DEGRADED",
  FAIL: "FAIL",
  UNKNOWN: "UNKNOWN",
});

export const DB_STATUS = Object.freeze({
  OK: "OK",
  FAIL: "FAIL",
  UNKNOWN: "UNKNOWN",
});

export const KIS_PAPER_READINESS = Object.freeze({
  READY: "READY",
  BLOCKED: "BLOCKED",
  UNKNOWN: "UNKNOWN",
});

const _SIDECAR_SET = new Set(Object.values(SIDECAR_STATUS));
const _DIAGNOSTICS_SET = new Set(Object.values(DIAGNOSTICS_STATUS));
const _DB_SET = new Set(Object.values(DB_STATUS));
const _KIS_SET = new Set(Object.values(KIS_PAPER_READINESS));

function _oneOf(value, set, fallback) {
  return set.has(value) ? value : fallback;
}

/**
 * raw(backend /api/system/exe-status 응답 또는 null) + frontend 관측치를
 * 단일 표준 상태로 정규화.
 *
 * @param {object|null} raw — backend 응답 (fetch 실패 시 null 일 수 있음).
 * @param {object} [opts]
 * @param {boolean} opts.reachable — frontend fetch 성공 여부 (단일 진실).
 * @param {string}  [opts.sidecarStatus] — frontend 가 desktop 감지 등으로
 *   판정한 sidecar 상태 (있으면 raw 보다 우선).
 * @param {string}  [opts.errorMessage] — fetch 실패 시 오류 메시지.
 * @returns {{
 *   backend_api_reachable: boolean,
 *   sidecar_status: string,
 *   diagnostics_status: string,
 *   db_status: string,
 *   kis_paper_readiness: string,
 *   last_error_message: string|null,
 *   checked_at: string|null,
 *   is_live_authorization: boolean,
 *   contains_secret: boolean,
 * }}
 */
export function normalizeExeStatus(raw, opts = {}) {
  const reachable = opts.reachable === true;
  const r = raw && typeof raw === "object" ? raw : null;

  // sidecar 는 reachable 여부와 무관하게 frontend override → raw → UNKNOWN.
  const sidecar = _oneOf(
    opts.sidecarStatus ?? r?.sidecar_status,
    _SIDECAR_SET,
    SIDECAR_STATUS.UNKNOWN,
  );

  if (!reachable) {
    // 연결 실패 — 의존 상태는 모두 "확인 불가" 로 강등 (모순 표시 차단).
    return {
      backend_api_reachable: false,
      sidecar_status: sidecar,
      diagnostics_status: DIAGNOSTICS_STATUS.UNKNOWN,
      db_status: DB_STATUS.UNKNOWN,
      kis_paper_readiness: KIS_PAPER_READINESS.UNKNOWN,
      last_error_message:
        opts.errorMessage ||
        "Backend API에 연결할 수 없어 상태를 확인할 수 없습니다.",
      checked_at: r?.checked_at ?? null,
      is_live_authorization: false,
      contains_secret: false,
    };
  }

  return {
    backend_api_reachable: true,
    sidecar_status: sidecar,
    diagnostics_status: _oneOf(
      r?.diagnostics_status, _DIAGNOSTICS_SET, DIAGNOSTICS_STATUS.UNKNOWN,
    ),
    db_status: _oneOf(r?.db_status, _DB_SET, DB_STATUS.UNKNOWN),
    kis_paper_readiness: _oneOf(
      r?.kis_paper_readiness, _KIS_SET, KIS_PAPER_READINESS.UNKNOWN,
    ),
    last_error_message: r?.last_error_message ?? null,
    checked_at: r?.checked_at ?? null,
    // 안전 invariant — 응답이 무엇이든 true 로 표시하지 않는다.
    is_live_authorization: r?.is_live_authorization === true,
    contains_secret: r?.contains_secret === true,
  };
}

export function getBackendStatusLabel(status) {
  return status?.backend_api_reachable
    ? "Backend API 연결됨"
    : "Backend API 연결 실패";
}

export function getSidecarStatusLabel(status) {
  switch (status?.sidecar_status) {
    case SIDECAR_STATUS.RUNNING:
      return "Sidecar 실행 중";
    case SIDECAR_STATUS.STARTING:
      return "Sidecar 시작 중";
    case SIDECAR_STATUS.STOPPED:
      return "Sidecar 중지";
    default:
      return "Sidecar 확인 불가";
  }
}

export function getDiagnosticsStatusLabel(status) {
  switch (status?.diagnostics_status) {
    case DIAGNOSTICS_STATUS.OK:
      return "진단 상태 정상";
    case DIAGNOSTICS_STATUS.DEGRADED:
      return "진단 상태 일부 문제";
    case DIAGNOSTICS_STATUS.FAIL:
      return "진단 상태 실패";
    default:
      return "진단 상태 확인 불가";
  }
}

export function getDbStatusLabel(status) {
  switch (status?.db_status) {
    case DB_STATUS.OK:
      return "DB 정상";
    case DB_STATUS.FAIL:
      return "DB 실패";
    default:
      return "DB 확인 불가";
  }
}

export function getKisPaperReadinessLabel(status) {
  switch (status?.kis_paper_readiness) {
    case KIS_PAPER_READINESS.READY:
      return "KIS 모의투자 준비 상태: READY";
    case KIS_PAPER_READINESS.BLOCKED:
      return "KIS 모의투자 준비 상태: BLOCKED";
    default:
      return "KIS 모의투자 준비 상태: 확인 불가";
  }
}

/**
 * 정규화된 상태가 *자기모순* 인지 검사 (테스트/방어용).
 * reachable=false 인데 의존 상태가 정상/READY 로 표시되면 모순(true).
 * 정규화를 거친 객체라면 항상 false 여야 한다.
 */
export function isContradictoryStatus(status) {
  if (!status || typeof status !== "object") return true;
  if (typeof status.backend_api_reachable !== "boolean") return true;
  if (status.backend_api_reachable === false) {
    if (status.diagnostics_status === DIAGNOSTICS_STATUS.OK) return true;
    if (status.db_status === DB_STATUS.OK) return true;
    if (status.kis_paper_readiness === KIS_PAPER_READINESS.READY) return true;
  }
  return false;
}

/** ok(녹색) / warn(주황) / bad(적색) / unknown(회색) — UI 색상 힌트. */
export function backendTone(status) {
  return status?.backend_api_reachable ? "ok" : "bad";
}

export function diagnosticsTone(status) {
  switch (status?.diagnostics_status) {
    case DIAGNOSTICS_STATUS.OK:
      return "ok";
    case DIAGNOSTICS_STATUS.DEGRADED:
      return "warn";
    case DIAGNOSTICS_STATUS.FAIL:
      return "bad";
    default:
      return "unknown";
  }
}

export function sidecarTone(status) {
  switch (status?.sidecar_status) {
    case SIDECAR_STATUS.RUNNING:
      return "ok";
    case SIDECAR_STATUS.STARTING:
      return "warn";
    case SIDECAR_STATUS.STOPPED:
      return "bad";
    default:
      return "unknown";
  }
}

export function dbTone(status) {
  switch (status?.db_status) {
    case DB_STATUS.OK:
      return "ok";
    case DB_STATUS.FAIL:
      return "bad";
    default:
      return "unknown";
  }
}

export function kisPaperTone(status) {
  switch (status?.kis_paper_readiness) {
    case KIS_PAPER_READINESS.READY:
      return "ok";
    case KIS_PAPER_READINESS.BLOCKED:
      return "warn";
    default:
      return "unknown";
  }
}
