// ReferenceHome(새 홈 — reference.jpg 스타일) 전용 순수 함수.
// DOM/시간/네트워크 부작용 0 — 입력→값만. 일상 한국어, 추정치 금지(실체결 기준).
import { isKstToday } from "./tradingStatus";

// ── 계좌번호 마스킹 (절대원칙 #4: 평문 노출 금지) ──────────────────────────────
/** "50191162-01" → "5019****-01" 식. 앞 4 + 끝 2만 노출, 가운데 별표.
 *  값이 없거나 너무 짧으면 null (호출자가 "모의계좌"로 표시). */
export function maskAccountNo(raw) {
  if (!raw) return null;
  const digits = String(raw).replace(/[^0-9]/g, "");
  if (digits.length < 6) return null;
  const head = digits.slice(0, 4);
  const tail = digits.slice(-2);
  return `${head}${"*".repeat(Math.max(2, digits.length - 6))}${tail}`;
}

// ── 매매기법 칩 (ORB·모멘텀·VWAP·갭) ──────────────────────────────────────────
// strategy-performance 응답의 strategies[] 블록(decision_count/buy/sell/hold 등,
// 모두 *실제 카운트*)에서 칩 데이터를 뽑는다. 승률·손익(추정치)은 쓰지 않는다.
const STRATEGY_LABELS = Object.freeze({
  ORB: "ORB",
  MOMENTUM: "모멘텀",
  VWAP: "VWAP",
  GAP: "갭",
});
const CHIP_ORDER = ["ORB", "MOMENTUM", "VWAP", "GAP"];

/** 최근 우세 신호를 일상어로. 추정 아님 — 실제 vote 카운트 비교. */
function prevailingVerdict(block) {
  const buy = block?.buy_vote_count || 0;
  const sell = block?.sell_vote_count || 0;
  const hold = block?.hold_vote_count || 0;
  if (buy === 0 && sell === 0 && hold === 0) return "거래 시작 전";
  if (buy >= sell && buy > 0) return "매수 우세";
  if (sell > 0 && sell > buy) return "매도 우세";
  return "관망";
}

/**
 * @param {{strategies?: Array}|null} report  agentStrategyPerformance() 응답
 * @returns {Array<{key, label, signals, verdict}>}  항상 4개(ORB/모멘텀/VWAP/갭)
 */
export function strategyChips(report) {
  const blocks = Array.isArray(report?.strategies) ? report.strategies : [];
  const byName = new Map();
  for (const b of blocks) {
    if (b && b.strategy) byName.set(String(b.strategy).toUpperCase(), b);
  }
  return CHIP_ORDER.map((key) => {
    const b = byName.get(key);
    // U5: "신호 수" = 실제 BUY/SELL 신호(buy+sell vote). decision_count 는 *평가 횟수*
    //   (매 틱 4기법 전부 평가 → 4개가 항상 동일값 = 공유 카운터처럼 보였다). 기법별로
    //   다른 실제 신호 수를 보여 정직화한다.
    const signals = (b?.buy_vote_count || 0) + (b?.sell_vote_count || 0);
    return {
      key,
      label: STRATEGY_LABELS[key],
      signals,
      verdict: b ? prevailingVerdict(b) : "거래 시작 전",
    };
  });
}

// ── 미체결 카운트 ─────────────────────────────────────────────────────────────
/** 제출됐지만 아직 체결/거부되지 않은 주문 수 (실체결 기준 — 추정 아님). */
export function openOrderCount(orders) {
  return (orders || []).filter((r) => {
    if (!r) return false;
    const bs = String(r.broker_status || "").toUpperCase();
    const dec = String(r.decision || "").toUpperCase();
    if (bs === "REJECTED" || dec === "REJECTED") return false;
    if ((r.filled_quantity || 0) > 0 || bs === "FILLED") return false;
    return true;
  }).length;
}

/**
 * D2: 홈 "미체결" 칩 — *오늘(KST)* 기준 미체결 수.
 *   = 오늘 주문 − 오늘 체결 − 오늘 거부 (음수면 0).
 * 전체 목록(openOrderCount)이 과거일 잔여/미동기 주문까지 세어 부풀던 문제(06-05
 * 화면 "3건" vs 실제 1건)를 today 요약 기준으로 교정한다.
 * @param {{orderCount, filledCount, rejectedCount}} today  summarizeTodayOrders 결과
 */
export function todayOpenOrderCount(today) {
  const oc = today?.orderCount ?? 0;
  const fc = today?.filledCount ?? 0;
  const rc = today?.rejectedCount ?? 0;
  return Math.max(0, oc - fc - rc);
}

// ── 종목코드 → 한글명 (전 화면 코드 노출 방지) ───────────────────────────────
import { KR_STOCK_NAMES, MOCK_STOCKS } from "../config/constants";

const _MOCK_NAME = Object.fromEntries((MOCK_STOCKS || []).map((s) => [s.code, s.name]));

/** 코드 → 한글명. 모르면 코드 그대로(최후 fallback). extra=런타임 보강 맵(포지션 등). */
export function resolveSymbolName(symbol, extra = null) {
  if (!symbol) return symbol;
  const code = String(symbol);
  if (extra && extra[code] && extra[code] !== code) return extra[code];
  return KR_STOCK_NAMES[code] || _MOCK_NAME[code] || code;
}

// ── KST 날짜 라벨 ("" 오늘 / "어제" / "MM/DD") ────────────────────────────────
function _kstYmd(ms) {
  return new Date(ms + 9 * 3600 * 1000).toISOString().slice(0, 10);
}
// ── KST 시:분 ("HH:MM") — backend 가 emit 하는 naive(타임존 없는) UTC 타임스탬프를
//    UTC 로 간주(+Z)해 KST(+9)로 변환한다. 030fa98 와 같은 취지(naive=UTC 가정).
//    이 변환이 없으면 slice(11,16) 가 UTC 시각(예 02:56)을 그대로 노출했다. ─────
function _kstHm(timestamp) {
  if (!timestamp) return "—";
  const s = /[zZ]|[+-]\d\d:?\d\d$/.test(timestamp) ? timestamp : `${timestamp}Z`;
  const t = new Date(s).getTime();
  if (Number.isNaN(t)) return "—";
  return new Date(t + 9 * 3600 * 1000).toISOString().slice(11, 16);
}
/** entry.timestamp(UTC naive 가정)가 오늘이면 "", 어제면 "어제", 그 외 "MM/DD". */
export function kstDayLabel(timestamp, now = new Date()) {
  if (!timestamp) return "";
  const s = /[zZ]|[+-]\d\d:?\d\d$/.test(timestamp) ? timestamp : `${timestamp}Z`;
  const t = new Date(s).getTime();
  if (Number.isNaN(t)) return "";
  const day = _kstYmd(t);
  const todayKey = _kstYmd(now.getTime());
  const yKey = _kstYmd(now.getTime() - 24 * 3600 * 1000);
  if (day === todayKey) return "";
  if (day === yKey) return "어제";
  return `${day.slice(5, 7)}/${day.slice(8, 10)}`;
}

// ── 실시간 현황판 "지금 AI가 하는 일" — decision-log entry → 일상어 한 줄 ──────
const STRAT_KO = Object.freeze({
  ORB: "ORB", MOMENTUM: "모멘텀", VWAP: "VWAP", GAP: "갭",
});

// ── 오늘 기준 매매기법 칩 (누적치 대신 *오늘* 신호 수 + 최근 판단) ───────────────
const _CHIP_ORDER2 = ["ORB", "MOMENTUM", "VWAP", "GAP"];
const _CHIP_LABEL2 = { ORB: "ORB", MOMENTUM: "모멘텀", VWAP: "VWAP", GAP: "갭" };
const _ACT_VERDICT = { BUY: "매수", SELL: "매도", HOLD: "관망", EXIT: "청산", NO_OP: "관망" };

/**
 * decision-log entries(오늘 KST만) → [{key,label,signals,verdict}].
 * signals = 오늘 해당 기법 신호 수, verdict = 오늘 가장 최근 판단(매수/관망/…).
 * 오늘 신호 없으면 "오늘 신호 없음".
 */
export function todayStrategyChips(entries, now = new Date()) {
  const todayKey = _kstYmd(now.getTime());
  const counts = {}; const lastAct = {};
  for (const e of (entries || [])) {
    if (!e || !e.timestamp) continue;
    const s = /[zZ]|[+-]\d\d:?\d\d$/.test(e.timestamp) ? e.timestamp : `${e.timestamp}Z`;
    const t = new Date(s).getTime();
    if (Number.isNaN(t) || _kstYmd(t) !== todayKey) continue;
    const strat = String(e.strategy || "").toUpperCase();
    if (!_CHIP_LABEL2[strat]) continue;
    counts[strat] = (counts[strat] || 0) + 1;
    if (!(strat in lastAct)) lastAct[strat] = String(e.decision_action || "").toUpperCase();
  }
  return _CHIP_ORDER2.map((key) => ({
    key,
    label: _CHIP_LABEL2[key],
    signals: counts[key] || 0,
    verdict: counts[key] ? (_ACT_VERDICT[lastAct[key]] || "관망") : "오늘 신호 없음",
  }));
}
const ACTION_KO = Object.freeze({ BUY: "매수", SELL: "매도", HOLD: "보류", EXIT: "청산", NO_OP: "관망" });

// U7: 주문 보류/차단 사유 코드 → 일상 한국어. 데이터에 reason_code 가 있을 때만
//   사용(지어내기 금지). 없으면 "사유 확인 중".
const REASON_KO = Object.freeze({
  KIS_PAPER_ORDER_LIMIT_EXCEEDED:   "일일 주문 한도",
  KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED:"1회 주문 금액 한도",
  KIS_PAPER_ORDER_WINDOW_CLOSED:    "주문 시간대 아님",
  KIS_REALTIME_PRICE_REQUIRED:      "실시간 시세 대기",
  KIS_PRICE_STALE:                  "시세 지연",
  LOW_CONFIDENCE:                   "신호 확신 부족",
  LOW_QUALITY_SCORE:                "신호 품질 부족",
  MISSING_EXIT_PLAN:                "청산 계획 없음",
  MAX_CONCURRENT_POSITIONS_REACHED: "동시 보유 한도",
  DAILY_BUY_LIMIT_REACHED:          "일일 매수금액 한도",
  DUPLICATE_POSITION_BLOCKED:       "이미 보유 중",
  KIS_PAPER_ERROR:                  "시세 조회 제한",
  MARKET_CLOSED:                    "장 마감",
  NO_STRATEGY_SIGNAL:               "신호 없음",
});

/** 보류 사유(한국어) — reason_code 가 매핑되면 그 사유, 아니면 null. */
function _reasonKo(entry) {
  const code = String(entry?.reason_code || "").toUpperCase();
  return REASON_KO[code] || null;
}

// ⑤ 조사 로/으로 — 마지막 글자 받침에 따라. 받침 없음·ㄹ받침 → "로", 그 외 → "으로".
//   예전엔 항상 "로" 를 붙여 "주문 시간대 아님로"(틀림) 처럼 깨졌다. "한도로"(✓)는 유지.
function _ro(word) {
  const ch = String(word || "").slice(-1);
  const c = ch.charCodeAt(0);
  if (Number.isNaN(c) || c < 0xac00 || c > 0xd7a3) return "로";
  const jong = (c - 0xac00) % 28;
  return jong === 0 || jong === 8 ? "로" : "으로";
}

/** 장 마감/대기 안내 (현황판 비었을 때). */
export function marketClosedLine() {
  return "장이 닫혀 있어요 — 다음 장에서 다시 움직여요";
}

/**
 * decision-log entry 1건 → { time, text }. 모두 실제 기록 기반(추정 아님).
 * 예: "삼성전자 — 모멘텀이 매수 신호 → 주문 보냈어요"
 *     "NAVER — 신호가 약해서 보류했어요"
 */
export function livePanelLine(entry, nameOf = resolveSymbolName) {
  if (!entry) return null;
  const time = _kstHm(entry.timestamp);   // ★UTC→KST 변환 (옛: raw slice = UTC 노출)
  // R2/M2: 운영자 이벤트(설정 변경 · 수동 매도 등) — reason 문구를 그대로 표시.
  if (["CONFIG_CHANGE", "MANUAL_SELL"].includes(String(entry.decision_action || "").toUpperCase())
      || String(entry.reason_code || "").toUpperCase().startsWith("OPERATOR_")) {
    const msg = entry.reason || (Array.isArray(entry.reasons) ? entry.reasons[0] : "") || "운영자가 설정을 바꿨어요";
    // V6: 점검/진단 중 실기동에 찍힌 변경은 '(점검)'으로 구분(이력 삭제 0, 표시 계층).
    const src = String(entry.event_source || "operator").toLowerCase();
    return { time, text: src && src !== "operator" ? `${msg} (점검)` : msg };
  }
  const name = nameOf(entry.symbol) || entry.symbol || "어떤 종목";
  const strat = STRAT_KO[String(entry.strategy || "").toUpperCase()] || null;
  const action = String(entry.decision_action || "").toUpperCase();
  const fill = String(entry.paper_fill_status || "").toUpperCase();
  const blocked = !!entry.risk_veto || fill === "PAPER_REJECTED";
  // ★실제 주문 전송 여부 — paper_order_id 가 있거나 체결/대기 상태일 때만 "주문 나감".
  //   결정만 나고 게이트(품질/한도 등)에서 막힌 건은 '주문 보냈어요'로 오보고하던 버그 수정.
  const submitted = !!entry.paper_order_id
    || fill === "PAPER_FILLED" || fill === "PAPER_PENDING";

  const reasonKo = _reasonKo(entry);
  let text;
  if (blocked) {
    // U7: 리스크 차단도 구체 사유가 있으면 함께.
    text = reasonKo
      ? `${name} — ${reasonKo}로 주문을 막았어요`
      : `${name} — 리스크 판단이 주문을 막았어요`;
  } else if (action === "HOLD" || action === "NO_OP" || action === "") {
    text = `${name} — 신호가 약해서 보류했어요`;
  } else {
    const actKo = ACTION_KO[action] || "거래";
    const sig = strat ? `${strat}이 ${actKo} 신호` : `${actKo} 신호`;
    if (submitted) {
      const done = fill === "PAPER_FILLED"
        ? (action === "SELL" ? "팔았어요" : action === "BUY" ? "샀어요" : "주문 보냈어요")
        : "주문 보냈어요";
      text = `${name} — ${sig} → ${done}`;
    } else {
      // U7: 결정은 났지만 주문 미전송 — *실제 사유* 를 표시(reason_code 매핑).
      //   사유를 모를 때만 "사유 확인 중"(지어내기 금지).
      const why = reasonKo ? `${reasonKo}${_ro(reasonKo)} 보류했어요` : "주문은 안 나갔어요 (사유 확인 중)";
      text = `${name} — ${sig}였지만 ${why}`;
    }
  }
  return { time, text };
}

/**
 * U7: 현황판 도배 방지 — *연속* 동일 텍스트(동일 종목+동일 사유) 이벤트를 묶는다.
 *   원본 이벤트 기록은 백엔드에 보존되며, 화면만 압축한다. count>1 이면 "×N회".
 *   time 은 묶음의 *첫(가장 이른) 표시 시각*을 유지.
 * @param {Array<{time, text, day?}>} lines  livePanelLine 결과 배열
 */
export function groupLiveLines(lines) {
  const out = [];
  for (const l of (lines || [])) {
    if (!l || !l.text) continue;
    const prev = out[out.length - 1];
    if (prev && prev.text === l.text) {
      prev.count += 1;
    } else {
      out.push({ ...l, count: 1 });
    }
  }
  return out;
}

// ── 미니 KPI (오늘 실현손익 | 승률 | 체결률) — 실체결 기준만 ──────────────────
/**
 * @param {{cashState, today, perf}} args  cashState=cash-state, today=summarizeTodayOrders,
 *   perf=/api/performance(daily) FIFO 청산 라운드트립 성과(win_count/loss_count/win_rate).
 * @returns {{realizedText, realizedRaw, winRateText, fillRateText}}
 */
export function miniKpis({ cashState, today, perf } = {}) {
  const rawRealized = cashState ? (cashState.realized_pnl_krw ?? null) : null;
  const oc = today?.orderCount ?? 0;
  const fc = today?.filledCount ?? 0;
  const hasFills = fc > 0;
  // ★체결이 있으면 '거래 시작 전'이 아니다 — 실현손익(청산손익)을 모르면 0원으로 표기.
  const realizedRaw = rawRealized != null ? rawRealized : (hasFills ? 0 : null);
  // W1: 승률은 *PerformanceCard 와 동일한* FIFO 청산 라운드트립(/api/performance)에서.
  //   청산이 1건이라도 있으면 "11% (1승 8패)"(성과카드와 동일 포맷·소스), 0건이면
  //   '청산 거래 없음'(체결은 있으나 청산 라운드트립 0), 체결 0이면 '거래 시작 전'.
  const wins = Number(perf?.win_count ?? 0);
  const losses = Number(perf?.loss_count ?? 0);
  const closed = wins + losses;
  let winRateText;
  if (perf && perf.win_rate != null && closed > 0) {
    winRateText = `${Math.round(perf.win_rate * 100)}% (${wins}승 ${losses}패)`;
  } else {
    winRateText = hasFills ? "청산 거래 없음" : "거래 시작 전";
  }
  return {
    realizedRaw,
    realizedText: realizedRaw == null
      ? "거래 시작 전"
      : `${realizedRaw > 0 ? "+" : ""}${realizedRaw.toLocaleString("ko-KR")}원`,
    winRateText,
    fillRateText: oc > 0 ? `${Math.round((fc / oc) * 100)}%` : "거래 시작 전",
  };
}

// ── 오늘 진행률 (일일 매수금액 사용량 게이지) ─────────────────────────────────
/**
 * 오늘(KST 아님 — 전체 목록 기준 BUY notional) 매수 사용금액 vs 일일 한도.
 * @returns {{orderCount, buyUsedKrw, buyMaxKrw, buyPct}}
 */
export function dailyProgress({ orders, today, buyMaxKrw = 3_000_000, now = new Date() } = {}) {
  let buyUsed = 0;
  for (const r of (orders || [])) {
    if (!r) continue;
    // V1: 매수 사용금액은 *오늘(KST) 체결*만(D2/D5). 예전엔 전체 누적을 더해
    //   휴장일에도 "매수 1148만"이 떴다 — 오늘 주문 0이면 0만이 정답.
    if (!isKstToday(r.created_at, now)) continue;
    if (String(r.side || "").toUpperCase() !== "BUY") continue;
    const bs = String(r.broker_status || "").toUpperCase();
    const dec = String(r.decision || "").toUpperCase();
    if (bs === "REJECTED" || dec === "REJECTED") continue;
    const qty = r.filled_quantity || r.quantity || 0;
    // U6: order-audit row 에는 `price` 필드가 없다(avg_fill_price / limit_price /
    //   latest_price). 기존 `r.price` 는 항상 undefined → 매수금액이 0 으로 집계됐다
    //   (06-05: 체결 14건인데 "매수 0만"). 실체결가 우선(D4/D6 동일 원칙).
    const px = r.avg_fill_price || r.limit_price || r.latest_price || 0;
    buyUsed += px * qty;
  }
  const max = buyMaxKrw > 0 ? buyMaxKrw : 3_000_000;
  return {
    orderCount: today?.orderCount ?? 0,
    buyUsedKrw: buyUsed,
    buyMaxKrw: max,
    buyPct: Math.min(100, Math.round((buyUsed / max) * 100)),
  };
}

// ── 주요 기능 바로가기 (기존 탭으로 이동) ─────────────────────────────────────
export const FEATURE_SHORTCUTS = Object.freeze([
  { tab: "approve",  label: "승인",   icon: "📝" },
  { tab: "strat",    label: "리스크", icon: "🛡️" },
  { tab: "chart",    label: "차트",   icon: "📈" },
  { tab: "backtest", label: "백테스트", icon: "🧪" },
  { tab: "audit",    label: "로그",   icon: "📜" },
  { tab: "engine",   label: "엔진",   icon: "⚙️" },
]);

// ── 계좌정보 행 색 포인트 (reference 항목별 보라/노랑/빨강 계열) ───────────────
export const ACCOUNT_BULLET = Object.freeze({
  estimatedAsset: "#b794f6", // 추정자산 — 보라
  deposit:        "#f6e05e", // 예수금 — 노랑
  stockValue:     "#b794f6", // 주식평가금액 — 보라
  realized:       "#fc8181", // 실현손익 — 빨강 계열
  returnPct:      "#fc8181", // 손익률 — 빨강 계열
});
