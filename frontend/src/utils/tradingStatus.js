// UI-revamp STEP 1 (2026-06-04): "지금 거래되는지" 단일 판정.
//
// 문제: 봇 핀은 RUNNING인데 자동매매 루프는 멈춰 있는 등, 여러 카드가 서로 다른
// 상태를 보여줘 운영자가 "지금 진짜 거래되는지" 헷갈렸다. 본 helper는 *하나의*
// 우선순위 규칙으로 단일 상태를 만든다. 순수 함수 — DOM/시간 부작용 0.

export const TRADING_STATE = Object.freeze({
  TRADING: "TRADING",   // 🟢 실제로 신규 주문이 나갈 수 있는 상태 (장중 + 가동 + 차단 없음)
  WAITING: "WAITING",   // 🟡 가동했으나 장이 아직/이미 닫힘 — 정상 대기
  STOPPED: "STOPPED",   // ⏸ 자동매매 루프가 꺼져 있음 (시작 버튼 필요)
  BLOCKED: "BLOCKED",   // 🔴 가동 중이어도 차단 (긴급정지/일일한도/계좌주문불가)
});

const _DISPLAY = {
  TRADING: { icon: "🟢", color: "#22c55e" },
  WAITING: { icon: "🟡", color: "#f59e0b" },
  STOPPED: { icon: "⏸", color: "#94a3b8" },
  BLOCKED: { icon: "🔴", color: "#ef4444" },
};

/**
 * 단일 거래 상태 판정. 우선순위(위험/명확성 순):
 *   1) 긴급정지 ON           → BLOCKED "긴급정지"
 *   2) 루프 정지(!running)    → STOPPED "시작 버튼 필요"
 *   3) 차단 사유 있음          → BLOCKED (계좌 주문불가 / 일일한도 등)
 *   4) 장이 닫힘(!marketOpen) → WAITING (장 시작 전 / 장 마감)
 *   5) 그 외                   → TRADING (cycle N)
 *
 * @returns {{state, icon, color, label, reason}}
 */
export function computeTradingStatus({
  running = false,
  emergencyStop = false,
  marketOpen = false,
  marketPhase = null,        // "PRE_OPEN" | "OPEN" | "CLOSED" | "WEEKEND" (옵션)
  blockedReason = null,      // 예: "계좌 주문불가", "일일한도 도달" (옵션)
  cycle = null,
} = {}) {
  const wrap = (state, label, reason = "") => ({
    state, label, reason,
    icon: _DISPLAY[state].icon, color: _DISPLAY[state].color,
  });

  if (emergencyStop) return wrap(TRADING_STATE.BLOCKED, "거래 불가", "긴급정지");
  if (!running)      return wrap(TRADING_STATE.STOPPED, "정지", "시작 버튼 필요");
  if (blockedReason) return wrap(TRADING_STATE.BLOCKED, "거래 불가", String(blockedReason));
  if (!marketOpen) {
    const closedLabel = marketPhase === "PRE_OPEN" ? "장 시작 전" : "장 마감";
    return wrap(TRADING_STATE.WAITING, "대기", closedLabel);
  }
  const cyc = (cycle != null && cycle !== "") ? ` (cycle ${cycle})` : "";
  return wrap(TRADING_STATE.TRADING, `거래 중${cyc}`, "");
}

/** 사람이 읽는 한 줄 — "🟢 거래 중 (cycle 12)" / "🔴 거래 불가 (긴급정지)" */
export function tradingStatusHeadline(status) {
  if (!status) return "";
  const r = status.reason ? ` (${status.reason})` : "";
  return `${status.icon} ${status.label}${r}`.trim();
}

// ── 오늘 핵심 숫자 ────────────────────────────────────────────────────────
// order audit row(created_at UTC 저장)를 *KST 날짜* 기준으로 집계.

function _kstDateKey(iso) {
  // naive(타임존 없음) ISO는 UTC로 간주(.env created_at은 UTC 저장).
  const s = (typeof iso === "string" && !/[zZ]|[+-]\d\d:?\d\d$/.test(iso)) ? iso + "Z" : iso;
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return null;
  // KST = UTC+9
  const kst = new Date(d.getTime() + 9 * 60 * 60 * 1000);
  return kst.toISOString().slice(0, 10);
}

// V1: created_at(UTC ISO)이 *오늘(KST)*인지. dailyProgress 등 당일 집계의 단일 기준.
export function isKstToday(iso, now = new Date()) {
  const todayKey = new Date(now.getTime() + (now.getTimezoneOffset() + 540) * 60000)
    .toISOString().slice(0, 10);
  return _kstDateKey(iso) === todayKey;
}

// ── 보유 종목 출처 분류 ────────────────────────────────────────────────────
// "봇이 매수" = 봇 주문이 *실제 체결된* 종목만(filled_quantity>0). 주문만 내고
// 미체결이면 봇이 보유를 만든 게 아니므로 "기존 보유"로 본다 → 미체결뿐인 현재
// 라이브 포지션은 전부 "기존 보유"로 정확히 표시된다.
export function botFilledSymbolSet(orders) {
  const s = new Set();
  for (const r of (orders || [])) {
    if (r && r.symbol && (r.filled_quantity || 0) > 0) s.add(r.symbol);
  }
  return s;
}

/** 종목 → "BOT"(봇이 매수) | "EXISTING"(기존 보유). */
export function classifyPositionSource(symbol, botFilledSymbols) {
  const set = botFilledSymbols instanceof Set
    ? botFilledSymbols : new Set(botFilledSymbols || []);
  return set.has(symbol) ? "BOT" : "EXISTING";
}

export const POSITION_SOURCE_BADGE = Object.freeze({
  BOT:      { label: "봇이 매수", color: "#22c55e" },
  EXISTING: { label: "기존 보유", color: "#94a3b8" },
});

/**
 * 오늘(KST) 주문/체결 집계. realizedPnl은 주문 row에 손익이 없으므로 계산하지
 * 않고(null) 호출자가 별도 소스로 주입 — 추정/오기재 방지.
 */
export function summarizeTodayOrders(orders, now = new Date()) {
  // now(로컬 시계)를 KST 날짜 키로 환산. UTC+9 = getTimezoneOffset(+분) + 540분.
  const key = new Date(now.getTime() + (now.getTimezoneOffset() + 540) * 60000)
    .toISOString().slice(0, 10);
  const rows = (orders || []).filter((r) => _kstDateKey(r.created_at) === key);
  const filled = rows.filter(
    (r) => (r.filled_quantity || 0) > 0 || r.broker_status === "FILLED",
  ).length;
  const rejected = rows.filter(
    (r) => r.decision === "REJECTED" || r.broker_status === "REJECTED",
  ).length;
  return { orderCount: rows.length, filledCount: filled, rejectedCount: rejected };
}
