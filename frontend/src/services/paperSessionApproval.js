function kstDate(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now).reduce((out, part) => {
    if (part.type !== "literal") out[part.type] = part.value;
    return out;
  }, {});
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export const ORDER_PATH_NOT_READY = "ORDER_PATH_NOT_READY";

// P1-A: 승인해도 KIS 모의주문이 나갈 수 없는 사유 → 한국어 안내 (backend 사유 코드와 1:1).
export const ORDER_BLOCKER_MESSAGES_KO = {
  MARKET_DATA_PROVIDER_NOT_KIS:
    "시세가 mock(가상)으로 설정돼 있어 KIS 모의주문이 나가지 않아요. %APPDATA%\\Autotrade\\.env 의 MARKET_DATA_PROVIDER=kis 로 바꾼 뒤 프로그램을 다시 실행하세요.",
  BACKGROUND_TICK_DISABLED:
    "백그라운드 자동 실행이 꺼져 있어요 (ENABLE_AI_PAPER_BACKGROUND_TICK=true 필요).",
  KIS_CREDENTIALS_MISSING: "KIS 모의투자 앱키/시크릿/계좌번호가 비어 있어요.",
  FILL_POLLER_DISABLED: "체결 확인 기능이 꺼져 있어요 (KIS_PAPER_FILL_POLLING=true 필요).",
  KIS_PAPER_SMOKE_MODE_ON: "스모크 테스트 모드가 켜져 있어요 (KIS_PAPER_SMOKE_MODE=false 필요).",
};

export function orderBlockerMessage(code) {
  return ORDER_BLOCKER_MESSAGES_KO[code] || `주문 경로 준비 안 됨 (${code})`;
}

/** Tauri invoke 오류(`HTTP 409: {"detail":"ORDER_PATH_NOT_READY:A,B"}`)에서 사유 코드 추출. */
export function parseOrderPathBlockers(err) {
  const text = typeof err === "string" ? err : String(err?.message ?? err ?? "");
  const m = text.match(/ORDER_PATH_NOT_READY:([A-Z0-9_,]+)/);
  return m ? m[1].split(",").filter(Boolean) : null;
}

const WEEKDAY_KO = ["일", "월", "화", "수", "목", "금", "토"];

/** "2026-11-30" → "11월 30일(월)" (날짜 자체로 요일 계산 — 시간대 무관). */
export function formatKoreanDate(isoDate) {
  const [y, m, d] = String(isoDate || "").split("-").map(Number);
  if (!y || !m || !d) return String(isoDate || "");
  const wd = WEEKDAY_KO[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return `${m}월 ${d}일(${wd})`;
}

async function resolveTargetDate(api) {
  const today = kstDate();
  if (typeof api.autoPaperNextTradingDay !== "function") {
    return { targetDate: today, isNextSession: false };
  }
  try {
    const r = await api.autoPaperNextTradingDay();
    const target = typeof r?.target_date === "string" ? r.target_date : today;
    return { targetDate: target, isNextSession: target !== (r?.today || today) };
  } catch {
    return { targetDate: today, isNextSession: false };
  }
}

async function preflightBlockers(api) {
  if (typeof api.autoPaperRunReadiness !== "function") return [];
  try {
    const r = await api.autoPaperRunReadiness();
    return Array.isArray(r?.order_blockers) ? r.order_blockers : [];
  } catch {
    return [];   // 조회 실패 — 서버 승인 단계(409)가 최종 차단한다.
  }
}

/** Desktop-only, explicit KIS PAPER order authorization for one KRX trading day. */
export async function ensurePaperSessionApproval({ api, capitalSettings, confirmFn = window.confirm }) {
  const invoke = window?.__TAURI_INTERNALS__?.invoke;
  if (typeof invoke !== "function") {
    return { ok: false, reason: "DESKTOP_SESSION_AUTH_REQUIRED" };
  }

  // P1-A: 주문이 나갈 수 없는 설정이면 승인 팝업 자체를 띄우지 않는다.
  const blockers = await preflightBlockers(api);
  if (blockers.length > 0) {
    return { ok: false, reason: ORDER_PATH_NOT_READY, blockers };
  }

  // P2: 대상 날짜 = 백엔드 KRX 달력 기준 다음 거래일 (장 마감 전이면 오늘).
  const { targetDate, isNextSession } = await resolveTargetDate(api);
  const current = typeof api.paperSessionApprovalGet === "function"
    ? await api.paperSessionApprovalGet()
    : null;
  const reusable = current?.target_date === targetDate
    && ["SCHEDULED", "ACTIVE"].includes(current?.status);
  if (reusable) return { ok: true, reused: true, targetDate, isNextSession };

  const perSymbol = Number(capitalSettings?.perSymbolAllocation || capitalSettings?.per_symbol_krw || 0);
  const maxPositions = Number(capitalSettings?.maxPositions || capitalSettings?.max_positions || 0);
  const daily = Number(capitalSettings?.maxDailyBuyAmount || capitalSettings?.daily_buy_limit_krw || 0);
  const dayLabel = isNextSession ? `${targetDate} (다음 거래일)` : targetDate;
  const when = isNextSession ? `${formatKoreanDate(targetDate)} (다음 거래일) 장중` : "오늘 장중";
  const approved = confirmFn(
    `KIS 모의계좌 자동매매 승인 (${dayLabel})\n\n`
    + `${when} 실제 KIS PAPER 매수·매도 주문이 자동 전송될 수 있습니다.\n`
    + `종목당 목표 ${perSymbol.toLocaleString()}원, 최대 ${maxPositions}종목, `
    + `일일 최대 ${daily.toLocaleString()}원\n\n`
    + "LIVE 계좌 주문은 허용되지 않습니다. 승인하시겠습니까?",
  );
  if (!approved) return { ok: false, cancelled: true, reason: "PAPER_APPROVAL_CANCELLED" };

  try {
    await invoke("approve_paper_session", { targetDate, explicitAck: true });
  } catch (err) {
    const codes = parseOrderPathBlockers(err);
    if (codes) return { ok: false, reason: ORDER_PATH_NOT_READY, blockers: codes };
    throw err;
  }
  return { ok: true, reused: false, targetDate, isNextSession };
}

export { kstDate };
