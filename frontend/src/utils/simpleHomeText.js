// SimpleHome(새 홈) 전용 — 모든 화면 문구를 *일상 한국어*로 바꾸는 순수 함수 모음.
// 50대·비전문가 운영자가 스마트폰에서 한눈에 이해하도록: 영문 상태코드/사유코드/
// cycle·tick·broker 같은 전문용어를 절대 노출하지 않는다. DOM/시간 부작용 0 —
// 테스트하기 쉽도록 입력→문자열만.

// ── 🔴 멈춤 사유 → 일상어 (modification #2) ──────────────────────────────────
// computeTradingStatus가 주는 reason(라벨/코드)을 "왜 멈췄고 뭘 하면 되는지"
// 한 줄로 풀어쓴다. 코드/영문이 그대로 새어나가지 않게 default도 일상어.
const STOP_REASON_KO = Object.freeze({
  "긴급정지": "긴급 정지를 눌러서 멈춰 있어요 — 다시 시작하려면 아래 🛑 긴급 정지를 한 번 더 눌러 풀어주세요",
  EMERGENCY_STOP: "긴급 정지를 눌러서 멈춰 있어요 — 다시 시작하려면 아래 🛑 긴급 정지를 한 번 더 눌러 풀어주세요",
  "일일한도 도달": "오늘 주문 한도를 다 썼어요 — 내일 다시 시작해요",
  DAILY_ORDER_LIMIT_EXCEEDED: "오늘 주문 한도를 다 썼어요 — 내일 다시 시작해요",
  DAILY_BUY_LIMIT_EXCEEDED: "오늘 살 수 있는 금액을 다 썼어요 — 내일 다시 시작해요",
  DAILY_LOSS_LIMIT: "오늘 손실 한도에 닿아서 안전하게 멈췄어요",
  DAILY_LOSS_LIMIT_EXCEEDED: "오늘 손실 한도에 닿아서 안전하게 멈췄어요",
  "계좌 주문불가": "계좌가 아직 주문을 받지 못하는 상태예요 — 설정에서 계좌를 확인해 주세요",
  ACCOUNT_NOT_ORDERABLE: "계좌가 아직 주문을 받지 못하는 상태예요 — 설정에서 계좌를 확인해 주세요",
  KIS_PRICE_STALE: "시세가 잠시 끊겨서 안전하게 기다리는 중이에요",
  KIS_MARKET_DATA_UNAVAILABLE: "시세를 받아오지 못해서 잠시 멈춰 있어요",
});

/** 🔴 멈춤 사유를 일상어 한 줄로. 모르는 코드는 코드 노출 없이 안전 문구. */
export function translateStopReason(reason) {
  if (!reason) return "잠시 멈춰 있어요";
  const key = String(reason).trim();
  if (STOP_REASON_KO[key]) return STOP_REASON_KO[key];
  // 영문 코드(_가 섞인)는 그대로 보여주지 않는다.
  if (/^[A-Z0-9_]+$/.test(key)) return "잠시 멈춰 있어요 — 사유를 확인하고 있어요";
  return key; // 이미 사람이 읽는 한국어 라벨이면 그대로
}

// ── 상태 히어로 ──────────────────────────────────────────────────────────────
// computeTradingStatus 결과({state, reason})를 큰 이모지 + 큰 글씨 + 작은 안내로.
const HERO_BG = Object.freeze({
  TRADING: "#f1fbf6",  // 연초록
  WAITING: "#fffdf3",  // 연노랑
  STOPPED: "#f6f7f9",  // 연회색
  BLOCKED: "#fff5f4",  // 연빨강
});

/**
 * @param {{state, reason}} status  computeTradingStatus()의 반환
 * @returns {{emoji, big, sub, bg}}  모두 일상 한국어
 */
export function heroFromStatus(status) {
  const state = status?.state ?? "STOPPED";
  const reason = status?.reason ?? "";
  switch (state) {
    case "TRADING":
      return { emoji: "🟢", big: "거래 중이에요",
        sub: "조건에 맞는 종목을 자동으로 사고팔고 있어요", bg: HERO_BG.TRADING };
    case "WAITING": {
      const preOpen = reason === "장 시작 전";
      return { emoji: "🟡",
        big: preOpen ? "곧 장이 열려요" : "장이 끝났어요",
        sub: preOpen
          ? "오전 9시에 자동으로 거래를 시작해요"
          : "다음 영업일 오전 9시에 자동으로 다시 시작해요",
        bg: HERO_BG.WAITING };
    }
    case "BLOCKED":
      return { emoji: "🔴", big: "멈췄어요",
        sub: translateStopReason(reason), bg: HERO_BG.BLOCKED };
    case "STOPPED":
    default:
      return { emoji: "⏸️", big: "멈춰 있어요",
        sub: "아래 ▶ 버튼을 누르면 자동매매를 시작해요", bg: HERO_BG.STOPPED };
  }
}

// ── 내 돈: 잔고 조회 실패 안내 (modification #1) ──────────────────────────────
/** 잔고를 못 불러왔을 때 — 죽은 숫자 대신 이 문구를 보여준다. */
export function moneyFailureLine(lastOkHm) {
  return lastOkHm
    ? `잔고를 불러오지 못했어요 (마지막 확인: ${lastOkHm})`
    : "잔고를 불러오지 못했어요 (아직 한 번도 못 불러왔어요)";
}

// ── AI 한마디 ────────────────────────────────────────────────────────────────
const AI_ACTION_KO = Object.freeze({
  BUY: "사는 게 좋아 보인대요",
  SELL: "파는 게 좋아 보인대요",
  HOLD: "지금은 그대로 지켜보재요",
});

/**
 * 최근 Agent 최종 판단을 일상어 한 줄로.
 * @param {{decision, confidence, symbol}|null} d
 */
export function aiOneLiner(d) {
  if (!d || !d.decision) return "🤖 아직 판단 전이에요 — 장이 열리면 살펴봐요";
  const act = AI_ACTION_KO[String(d.decision).toUpperCase()] ?? "지금은 그대로 지켜보재요";
  const conf = (d.confidence != null && !Number.isNaN(Number(d.confidence)))
    ? ` (확신 ${Math.round(Number(d.confidence))}%)` : "";
  return `🤖 ${act}${conf}`;
}

// ── 오늘 한 일: 주문 한 줄 → 일상어 문장 ──────────────────────────────────────
const SIDE_KO = { BUY: "사려고", SELL: "팔려고" };

/**
 * 주문 audit row 하나를 "OO 2주 사려고 주문을 넣었어요" 식 문장으로.
 * 거부/체결 결과까지 자연스러운 한 줄로. 종목명은 호출자가 넘긴 nameOf로 한글화.
 * @returns {string}
 */
export function timelineSentence(row, nameOf = (s) => s) {
  if (!row) return "";
  const name = nameOf(row.symbol) || row.symbol || "어떤 종목";
  const side = SIDE_KO[String(row.side || "").toUpperCase()] || "거래하려고";
  const qty = row.quantity ? `${row.quantity}주 ` : "";
  const status = String(row.broker_status || "").toUpperCase();
  const decision = String(row.decision || "").toUpperCase();

  if (decision === "REJECTED" || status === "REJECTED")
    return `${name} 주문이 받아들여지지 않았어요 (확인이 필요해요)`;
  if ((row.filled_quantity || 0) > 0 || status === "FILLED")
    return `${name} ${qty}${row.side === "SELL" ? "팔았어요" : "샀어요"}`;
  if (decision === "NEEDS_APPROVAL")
    return `${name} ${qty}${side} 승인 대기 중이에요`;
  return `${name} ${qty}${side} 주문을 넣었어요`;
}

/** "오늘 주문 15건 · 체결 0건 · 거부 4건" — 거래 없으면 안내 문구. */
export function todaySummaryLine({ orderCount = 0, filledCount = 0, rejectedCount = 0 } = {}) {
  if (orderCount === 0) return "오늘은 아직 주문을 낸 적이 없어요";
  return `오늘 주문 ${orderCount}건 · 체결 ${filledCount}건 · 거부 ${rejectedCount}건`;
}
