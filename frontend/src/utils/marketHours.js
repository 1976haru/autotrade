/**
 * 한국 주식시장 시간 utility — frontend mirror of `backend/app/scheduler/market_clock.py`.
 *
 * fix/market-closed-state-distinction:
 * 사용자가 장 종료 후 desktop EXE 를 실행했을 때 카드들이 "조회 실패" 처럼
 * 보이는 문제를 해결하기 위한 client-side 시점 계산 helper. 본 모듈은 *순수
 * 함수* — fetch / DOM / global side-effect 0건. backend `/api/market/state`
 * 와 동일한 정책으로 phase 를 계산한다.
 *
 * 한국 시장 시간 (KST = UTC+9, DST 없음):
 *   평일 00:00 ~ 09:00 KST  → PRE_OPEN  (장 시작 대기)
 *   평일 09:00 ~ 15:30 KST  → OPEN      (정규장)
 *   평일 15:30 ~ 24:00 KST  → CLOSED    (장 종료 후)
 *   토 / 일                 → WEEKEND   (휴장)
 *
 * 공휴일은 후속 작업 — 본 PR 시점에는 평일/주말만 구분 (backend 와 동일).
 *
 * 절대 원칙:
 *  - 본 utility 는 *주문 신호* 가 아니다 — 단순 시점 helper.
 *  - "지금 매수" / "Place Order" / "BUY/SELL/HOLD" 같은 결정을 만들지 않는다.
 */

export const MarketPhase = Object.freeze({
  PRE_OPEN: "PRE_OPEN",
  OPEN:     "OPEN",
  CLOSED:   "CLOSED",
  WEEKEND:  "WEEKEND",
});

// KST = UTC + 9. DST 없음.
const KST_OFFSET_MIN = 9 * 60;

// 한국 정규장 시간 (장 시작 09:00, 장 종료 15:30 KST).
const MARKET_OPEN_HM  = { h: 9,  m: 0  };
const MARKET_CLOSE_HM = { h: 15, m: 30 };


/**
 * 주어진 시점을 KST naive {year, month, day, hour, minute, weekday} 로 변환.
 * Date 객체는 시스템 timezone 에 의존하므로, UTC 분(min) 단위로 +9h 를 더한
 * 뒤 다시 분해해 KST 표현을 만든다 — 운영자 PC 의 timezone 과 무관하게
 * 일관된 결과를 보장.
 */
export function toKstParts(now = new Date()) {
  const ms = now.getTime() + KST_OFFSET_MIN * 60_000;
  const kstDate = new Date(ms);
  // getUTC* 는 위에서 더한 KST offset 을 그대로 읽어준다.
  return {
    year:    kstDate.getUTCFullYear(),
    month:   kstDate.getUTCMonth() + 1,    // 1..12
    day:     kstDate.getUTCDate(),
    hour:    kstDate.getUTCHours(),
    minute:  kstDate.getUTCMinutes(),
    // getUTCDay: 0=Sun ~ 6=Sat. 한국식으로 0=월 ~ 6=일 로 정규화.
    weekday: (kstDate.getUTCDay() + 6) % 7,
  };
}


/** 현재 시점의 한국 시장 phase 를 반환한다. 공휴일은 미고려. */
export function currentMarketPhase(now = new Date()) {
  const k = toKstParts(now);
  if (k.weekday >= 5) return MarketPhase.WEEKEND;

  const minutesOfDay = k.hour * 60 + k.minute;
  const openMin  = MARKET_OPEN_HM.h  * 60 + MARKET_OPEN_HM.m;
  const closeMin = MARKET_CLOSE_HM.h * 60 + MARKET_CLOSE_HM.m;

  if (minutesOfDay < openMin)  return MarketPhase.PRE_OPEN;
  if (minutesOfDay < closeMin) return MarketPhase.OPEN;
  return MarketPhase.CLOSED;
}


/** phase 가 정규장 시간이면 true. */
export function isMarketOpen(now = new Date()) {
  return currentMarketPhase(now) === MarketPhase.OPEN;
}


/** phase 가 OPEN 이 아니면 true (PRE_OPEN / CLOSED / WEEKEND). */
export function isMarketClosed(now = new Date()) {
  return currentMarketPhase(now) !== MarketPhase.OPEN;
}


// ───────────────────────────────────────────────────────────────────────────
// 한국어 라벨 / banner 문구 — UI 카드들이 일관된 문구를 쓰도록 단일 진실.
// "MARKET_CLOSED — 장 종료로 신규 판단 없음" 형태를 유지.
// ───────────────────────────────────────────────────────────────────────────

const _LABEL_KR = {
  [MarketPhase.PRE_OPEN]: "장 시작 전",
  [MarketPhase.OPEN]:     "정규장 열림",
  [MarketPhase.CLOSED]:   "장 종료",
  [MarketPhase.WEEKEND]:  "주말 휴장",
};

const _REASON_KR = {
  [MarketPhase.PRE_OPEN]:
    "장 시작 전 — 09:00 KST 이전이라 신규 판단 없음",
  [MarketPhase.OPEN]:
    "정규장 열림 — 데이터 활성",
  [MarketPhase.CLOSED]:
    "장 종료 — 15:30 KST 이후라 신규 판단 없음",
  [MarketPhase.WEEKEND]:
    "주말 휴장 — 신규 판단 없음",
};


export function marketPhaseLabel(phase) {
  return _LABEL_KR[phase] || phase || "—";
}


export function marketPhaseReason(phase) {
  return _REASON_KR[phase] || "";
}


/**
 * 카드에서 노출할 헤드라인. "MARKET_CLOSED — 장 종료로 신규 판단 없음".
 * 사용자 메시지에 명시된 형태를 유지.
 */
export function marketClosedHeadline(phase) {
  if (phase === MarketPhase.OPEN) return "";
  const tag = phase || MarketPhase.CLOSED;
  if (phase === MarketPhase.PRE_OPEN) {
    return `${tag} — 장 시작 전이라 신규 판단 없음`;
  }
  if (phase === MarketPhase.WEEKEND) {
    return `${tag} — 주말 휴장으로 신규 판단 없음`;
  }
  return `${tag} — 장 종료로 신규 판단 없음`;
}
