/** 숫자·날짜 포맷 유틸 모음 */

/** 원화 포맷  123456 → "123,456" */
export const fmtKRW = (n) => (n ?? 0).toLocaleString("ko-KR");

/** 퍼센트 포맷  2.345 → "+2.3%" */
export const fmtPct = (n, decimals = 1) =>
  (n >= 0 ? "+" : "") + (n ?? 0).toFixed(decimals) + "%";

/** 손익 색상  양수=초록, 음수=빨강, 0=회색 */
export const pnlColor = (n) =>
  n > 0 ? "#22c55e" : n < 0 ? "#ef4444" : "#64748b";

/** 현재 시각 HH:MM:SS */
export const nowTime = () => {
  const d = new Date();
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((v) => String(v).padStart(2, "0"))
    .join(":");
};

/** 현재 날짜 YYYY-MM-DD */
export const nowDate = () => new Date().toLocaleDateString("ko-KR");

// UI-revamp (2026-06-04): 시각 표시 KST 통일. 백엔드가 UTC(naive 또는 'Z')로 주는
// 타임스탬프를 항상 한국시간으로 보여준다. naive(타임존 마커 없음)는 UTC로 간주.
const _hasTz = (s) => typeof s === "string" && /([zZ])|([+-]\d\d:?\d\d)$/.test(s);

/** ISO 타임스탬프 → KST "MM/DD HH:MM" (naive는 UTC로 간주). */
export function formatKst(iso) {
  if (!iso) return "";
  const d = new Date(_hasTz(iso) || typeof iso !== "string" ? iso : iso + "Z");
  if (Number.isNaN(d.getTime())) return String(iso);
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(d);
}

/** 현재 KST 시:분 "HH:MM" (상태 바 등). */
export function nowKstHm(d = new Date()) {
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(d);
}

/** 합류점수 색상 */
export const confluenceColor = (score) =>
  score >= 70 ? "#22c55e" : score >= 50 ? "#facc15" : "#ef4444";

/** 신호 색상 맵 */
export const SIGNAL_COLOR = {
  강력매수: "#00ff88",
  매수:     "#22c55e",
  관망:     "#facc15",
  매도:     "#f87171",
  강력매도: "#ef4444",
};


// 058 (PendingAgeBadge), 069 (EmergencyStopStuckBanner), 075→076 (failure
// badge), 077 (history relative time), 080 (24h activity attempts) all
// share the same "elapsed since this ISO timestamp, formatted in Korean"
// math. Initially scoped to PENDING approvals; moved here in 087 once
// Dashboard/App were importing across tabs to use it.

const _MIN  = 60_000;
const _HOUR = 60 * _MIN;
const _DAY  = 24 * _HOUR;

/** 상대 시간 한국어 표시: "방금" / "5분 전" / "3시간 전" / "2일 전".
 *  Negative deltas (clock skew) clamp to "방금" rather than producing
 *  negative numbers. */
export function formatPendingAge(createdAtIso, now = Date.now()) {
  const elapsed = Math.max(0, now - new Date(createdAtIso).getTime());
  if (elapsed < 30_000) return "방금";
  if (elapsed < _HOUR)  return `${Math.floor(elapsed / _MIN)}분 전`;
  if (elapsed < _DAY)   return `${Math.floor(elapsed / _HOUR)}시간 전`;
  return `${Math.floor(elapsed / _DAY)}일 전`;
}

/** PENDING 결재의 노후 임계값. 058에서 도입한 10분 — 062 status pin escalation,
 *  065 bulk stale cancel과 같은 값. */
export const PENDING_STALE_THRESHOLD_MS = 10 * 60 * 1000;

export function isPendingStale(createdAtIso, now = Date.now()) {
  return (now - new Date(createdAtIso).getTime()) >= PENDING_STALE_THRESHOLD_MS;
}
