// 108: 6 운용모드의 표시 상수 — short label + color. 093에서 Dashboard
// (24h 활동 byMode breakdown)와 092에서 Approvals (history mode filter)가
// 각자의 팔레트를 만들었지만, 실제 의미는 같은 mode → 같은 short label/color.
// AuditLog timeline에 mode badge가 추가되면서 세 번째 사용처가 생겨 공유
// utils로 추출. 표시 순서는 위험도 오름차순 — SIM부터 LIVE까지 자연스럽게 읽힘.

export const MODE_DISPLAY = [
  { id: "SIMULATION",           label: "SIM",     color: "#64748b" },
  { id: "PAPER",                label: "PAPER",   color: "#7dd3fc" },
  { id: "LIVE_SHADOW",          label: "SHADOW",  color: "#94a3b8" },
  { id: "LIVE_MANUAL_APPROVAL", label: "MANUAL",  color: "#22c55e" },
  { id: "LIVE_AI_ASSIST",       label: "AI 보조", color: "#a78bfa" },
  { id: "LIVE_AI_EXECUTION",    label: "AI 자동", color: "#f59e0b" },
];

const _MODE_INDEX = new Map(MODE_DISPLAY.map((m) => [m.id, m]));


// 알 수 없는 mode id에 대해선 fallback display(회색 + raw id)를 반환 — 미래에
// 새 mode가 추가되어 이 상수가 갱신되기 전까지 timeline이 깨지지 않게.
export function findModeDisplay(modeId) {
  if (!modeId) return null;
  const known = _MODE_INDEX.get(modeId);
  if (known) return known;
  return { id: modeId, label: modeId, color: "#475569" };
}
