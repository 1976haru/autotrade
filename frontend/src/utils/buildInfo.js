/**
 * #57 / 7-05 — 앱 버전 / 빌드 commit 메타데이터 helper (pure, 테스트 가능).
 *
 * 사용자가 실행 중인 EXE 가 어느 main commit / 빌드 시각인지 확인할 수 있도록,
 * build-time 에 Vite 가 주입한 `import.meta.env.VITE_*` 값을 표준 모양으로
 * 정규화한다. backend `/api/system/build-info` 응답도 동일 normalize 로 표시.
 *
 * 절대 invariant (테스트로 lock):
 *  - secret / API key / 계좌번호를 표시·보관하지 않는다 (whitelist 필드만 copy).
 *  - is_live_authorization 은 항상 false, contains_secret 은 항상 false.
 *  - build metadata 가 없거나 'unknown' 이어도 crash 0건 — '확인 불가' 로 표시.
 */

export const BUILD_INFO_UNKNOWN = "unknown";

function _readEnv(key) {
  try {
    if (typeof import.meta !== "undefined" && import.meta.env) {
      const v = import.meta.env[key];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
  } catch {
    // import.meta 미지원 환경 — fallback.
  }
  return null;
}

function _str(v) {
  if (typeof v === "string" && v.trim()) return v.trim();
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return null;
}

/**
 * 임의 raw 객체(프론트 env 또는 backend 응답)를 표준 build info 로 정규화.
 * whitelist 필드만 copy → raw 에 secret-like 키가 있어도 결과에 노출 0건.
 */
export function normalizeBuildInfo(raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  const commitFull = _str(r.commit_full) || BUILD_INFO_UNKNOWN;
  const commit =
    _str(r.commit) ||
    (commitFull !== BUILD_INFO_UNKNOWN ? commitFull.slice(0, 7) : BUILD_INFO_UNKNOWN);
  let dirty = false;
  if (typeof r.is_dirty === "boolean") dirty = r.is_dirty;
  else if (typeof r.is_dirty === "string")
    dirty = r.is_dirty.trim().toLowerCase() === "true" || r.is_dirty.trim() === "1";
  return {
    version: _str(r.version) || BUILD_INFO_UNKNOWN,
    channel: _str(r.channel) || BUILD_INFO_UNKNOWN,
    commit,
    commit_full: commitFull,
    branch: _str(r.branch) || BUILD_INFO_UNKNOWN,
    build_time: _str(r.build_time) || BUILD_INFO_UNKNOWN,
    source: _str(r.source) || BUILD_INFO_UNKNOWN,
    is_dirty: dirty,
    // 안전 invariant — build info 는 실거래 권한과 무관, secret 미포함.
    is_live_authorization: false,
    contains_secret: false,
  };
}

/** Vite 가 build-time 에 주입한 프론트엔드 build metadata. */
export function getBuildInfo() {
  return normalizeBuildInfo({
    version: _readEnv("VITE_APP_VERSION"),
    channel: _readEnv("VITE_BUILD_CHANNEL"),
    commit: _readEnv("VITE_GIT_COMMIT"),
    commit_full: _readEnv("VITE_GIT_COMMIT_FULL"),
    branch: _readEnv("VITE_GIT_BRANCH"),
    build_time: _readEnv("VITE_BUILD_TIME"),
    source: _readEnv("VITE_BUILD_SOURCE"),
    is_dirty: _readEnv("VITE_GIT_DIRTY"),
  });
}

/** commit 을 7~12자리 short hash 로. unknown 이면 그대로. */
export function getCommitShort(value, length = 7) {
  const s = _str(value);
  if (!s || s === BUILD_INFO_UNKNOWN) return BUILD_INFO_UNKNOWN;
  return s.slice(0, Math.max(7, Math.min(12, length)));
}

/** ISO 문자열 → "YYYY-MM-DD HH:mm". 파싱 불가/unknown 이면 "확인 불가". */
export function formatBuildTime(value) {
  const s = _str(value);
  if (!s || s === BUILD_INFO_UNKNOWN) return "확인 불가";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return "확인 불가";
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

/** commit 또는 version 이 unknown 이면 true (빌드 식별 불가 안내용). */
export function isUnknownBuildInfo(info) {
  if (!info || typeof info !== "object") return true;
  const commitUnknown = !info.commit || info.commit === BUILD_INFO_UNKNOWN;
  const versionUnknown =
    !info.version ||
    info.version === BUILD_INFO_UNKNOWN ||
    info.version === "0.0.0-unknown";
  return commitUnknown || versionUnknown;
}

/** 두 build info 의 commit 이 같은지 (frontend ↔ backend 일치 확인용). */
export function commitsMatch(a, b) {
  const ca = getCommitShort(a?.commit);
  const cb = getCommitShort(b?.commit);
  if (ca === BUILD_INFO_UNKNOWN || cb === BUILD_INFO_UNKNOWN) return null; // 알 수 없음
  return ca === cb;
}
