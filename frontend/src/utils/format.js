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
