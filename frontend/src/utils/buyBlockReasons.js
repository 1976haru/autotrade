/**
 * P-17: 매수 불가 사유 formatter — "왜 안 샀는지" 를 사람이 이해하기 쉬운
 * 한국어로 변환한다.
 *
 * 본 모듈은 *표시 전용* 이다. 실제 매수 로직을 열지 않으며, 실거래 활성화
 * 문구/버튼/토글을 만들지 않는다 (테스트로 lock).
 *
 * 입력 예:
 *   { reason_code: "INSUFFICIENT_PAPER_CASH",
 *     reason_message: "남은 Paper 현금이 부족하여 매수 차단",
 *     details: { required_amount: 500000, remaining_cash: 300000 } }
 * 출력 예:
 *   { code: "INSUFFICIENT_PAPER_CASH",
 *     title: "남은 Paper 현금이 부족하여 매수 차단",
 *     detail: "필요 금액 500,000원 > 남은 Paper 현금 300,000원",
 *     severity: "warning", category: "capital" }
 */

// code → 기본 한국어 제목 (backend BUY_BLOCK_REASON_TITLES_KO 와 정합).
export const BUY_BLOCK_REASON_TITLES = Object.freeze({
  MIN_LOT_NOT_AFFORDABLE:        "1주 가격이 투자한도 초과로 제외",
  INSUFFICIENT_PAPER_CASH:       "남은 Paper 현금이 부족하여 매수 차단",
  DAILY_BUY_LIMIT_EXCEEDED:      "일일 최대 매수금액을 초과하여 매수 차단",
  SYMBOL_WEIGHT_LIMIT_EXCEEDED:  "종목별 최대 비중을 초과하여 매수 차단",
  MAX_POSITIONS_REACHED:         "최대 보유 종목 수에 도달하여 매수 차단",
  DUPLICATE_POSITION_BUY_BLOCKED: "이미 보유 중인 종목이라 추가 매수 차단",
  PRICE_STALE:                   "현재가가 오래되어 매수 차단",
  INVALID_PRICE:                 "현재가가 비정상이라 매수 차단",
  ABNORMAL_PRICE_MOVE:           "가격 급등락이 감지되어 매수 차단",
  PAPER_EXECUTION_DISABLED:      "Paper 가상 실행이 비활성화되어 매수 보류",
  BLOCKED_BY_PERMISSION_GATE:    "PermissionGate에서 매수 흐름이 차단되었습니다",
  BLOCKED_BY_RISK_MANAGER:       "RiskManager에서 매수 흐름이 차단되었습니다",
  EMERGENCY_STOP:                "긴급정지 상태라 매수하지 않았습니다",
  AI_EXECUTION_DISABLED_SAFE:    "AI 자동 실행이 안전상 비활성화되어 매수하지 않았습니다",
  LIVE_DISABLED_SAFE:            "실거래가 안전상 비활성화되어 있습니다",
  MARKET_CLOSED:                 "장 시간이 아니어서 매수하지 않았습니다",
  NO_MARKET_DATA:                "시장 데이터가 없어 매수하지 않았습니다",
  NO_STRATEGY_SIGNAL:            "전략 매수 신호가 없어 매수하지 않았습니다",
  NO_CANDIDATE:                  "매수 후보가 생성되지 않았습니다",
  NO_UNIVERSE:                   "매매 대상 종목군이 비어 매수하지 않았습니다",
  USING_FALLBACK_UNIVERSE:       "임시 종목군을 사용 중이라 매수를 보류했습니다",
  STRATEGY_ENGINE_NOT_CONNECTED: "전략 엔진이 연결되지 않아 매수하지 않았습니다",
  AUTO_BOT_NOT_RUNNING:          "자동매매가 실행 중이 아니어서 매수하지 않았습니다",
  UNKNOWN:                       "알 수 없는 사유로 매수하지 않았습니다",
});

export const BUY_BLOCK_REASON_CATEGORY = Object.freeze({
  MIN_LOT_NOT_AFFORDABLE:        "capital",
  INSUFFICIENT_PAPER_CASH:       "capital",
  DAILY_BUY_LIMIT_EXCEEDED:      "capital",
  SYMBOL_WEIGHT_LIMIT_EXCEEDED:  "capital",
  MAX_POSITIONS_REACHED:         "capital",
  DUPLICATE_POSITION_BUY_BLOCKED: "capital",
  PRICE_STALE:                   "price",
  INVALID_PRICE:                 "price",
  ABNORMAL_PRICE_MOVE:           "price",
  PAPER_EXECUTION_DISABLED:      "permission",
  BLOCKED_BY_PERMISSION_GATE:    "permission",
  BLOCKED_BY_RISK_MANAGER:       "risk",
  EMERGENCY_STOP:                "risk",
  AI_EXECUTION_DISABLED_SAFE:    "permission",
  LIVE_DISABLED_SAFE:            "permission",
  MARKET_CLOSED:                 "market",
  NO_MARKET_DATA:                "market",
  NO_STRATEGY_SIGNAL:            "strategy",
  NO_CANDIDATE:                  "strategy",
  NO_UNIVERSE:                   "strategy",
  USING_FALLBACK_UNIVERSE:       "strategy",
  STRATEGY_ENGINE_NOT_CONNECTED: "system",
  AUTO_BOT_NOT_RUNNING:          "system",
  UNKNOWN:                       "unknown",
});

export const BUY_BLOCK_REASON_SEVERITY = Object.freeze({
  MIN_LOT_NOT_AFFORDABLE:        "info",
  INSUFFICIENT_PAPER_CASH:       "warning",
  DAILY_BUY_LIMIT_EXCEEDED:      "warning",
  SYMBOL_WEIGHT_LIMIT_EXCEEDED:  "warning",
  MAX_POSITIONS_REACHED:         "info",
  DUPLICATE_POSITION_BUY_BLOCKED: "info",
  PRICE_STALE:                   "warning",
  INVALID_PRICE:                 "danger",
  ABNORMAL_PRICE_MOVE:           "danger",
  PAPER_EXECUTION_DISABLED:      "blocked",
  BLOCKED_BY_PERMISSION_GATE:    "blocked",
  BLOCKED_BY_RISK_MANAGER:       "blocked",
  EMERGENCY_STOP:                "danger",
  AI_EXECUTION_DISABLED_SAFE:    "info",
  LIVE_DISABLED_SAFE:            "info",
  MARKET_CLOSED:                 "info",
  NO_MARKET_DATA:                "warning",
  NO_STRATEGY_SIGNAL:            "info",
  NO_CANDIDATE:                  "info",
  NO_UNIVERSE:                   "warning",
  USING_FALLBACK_UNIVERSE:       "info",
  STRATEGY_ENGINE_NOT_CONNECTED: "warning",
  AUTO_BOT_NOT_RUNNING:          "info",
  UNKNOWN:                       "info",
});

// 기존 backend raw code 별칭 → 정규 code.
const _CODE_ALIASES = Object.freeze({
  PRICE_MISSING:           "NO_MARKET_DATA",
  STALE_DATA:              "PRICE_STALE",
  STALE_PRICE:             "PRICE_STALE",
  BELOW_MIN_LOT:           "MIN_LOT_NOT_AFFORDABLE",
  PRICE_OVER_CAP:          "MIN_LOT_NOT_AFFORDABLE",
  INSUFFICIENT_CASH:       "INSUFFICIENT_PAPER_CASH",
  BLOCKED_MAX_POSITIONS:   "MAX_POSITIONS_REACHED",
  PAPER_GUARD_DUPLICATE:   "DUPLICATE_POSITION_BUY_BLOCKED",
  PAPER_GUARD_DAILY_LIMIT: "DAILY_BUY_LIMIT_EXCEEDED",
  PAPER_GUARD_SYMBOL_WEIGHT: "SYMBOL_WEIGHT_LIMIT_EXCEEDED",
  PAPER_GUARD_RISK_MANAGER: "BLOCKED_BY_RISK_MANAGER",
  UNKNOWN_ERROR:           "UNKNOWN",
});


/** code 문자열을 정규 어휘로 변환. null/미지원이면 "UNKNOWN". */
export function normalizeReasonCode(raw) {
  if (raw == null) return "UNKNOWN";
  const code = String(raw).trim().toUpperCase();
  if (!code) return "UNKNOWN";
  const aliased = _CODE_ALIASES[code] || code;
  if (BUY_BLOCK_REASON_TITLES[aliased]) return aliased;
  return "UNKNOWN";
}


function _fmtKrw(v) {
  if (v == null || !Number.isFinite(Number(v))) return null;
  return `${Math.floor(Number(v)).toLocaleString("ko-KR")}원`;
}

function _fmtPct(v) {
  if (v == null || !Number.isFinite(Number(v))) return null;
  const n = Number(v);
  // 0~1 비율이면 % 로, 이미 % 스케일(>1)이면 그대로.
  const pct = n <= 1 ? n * 100 : n;
  return `${Math.round(pct * 10) / 10}%`;
}


/** details 객체 → 사람이 읽을 수 있는 상세 설명 문자열 (없으면 ""). */
export function buildBuyBlockDetail(code, details) {
  if (!details || typeof details !== "object") return "";
  const d = details;
  switch (code) {
    case "INSUFFICIENT_PAPER_CASH": {
      const req = _fmtKrw(d.required_amount);
      const rem = _fmtKrw(d.remaining_cash);
      if (req && rem) return `필요 금액 ${req} > 남은 Paper 현금 ${rem}`;
      if (rem) return `남은 Paper 현금 ${rem}`;
      return "";
    }
    case "MIN_LOT_NOT_AFFORDABLE": {
      const price = _fmtKrw(d.price);
      const cap = _fmtKrw(d.cap_krw);
      if (price && cap) return `1주 ${price} > 종목당 투자한도 ${cap}`;
      if (price) return `1주 가격 ${price}`;
      return "";
    }
    case "DAILY_BUY_LIMIT_EXCEEDED": {
      const used = _fmtKrw(d.daily_buy_used);
      const lim = _fmtKrw(d.daily_buy_limit);
      if (used && lim) return `오늘 매수 ${used} / 한도 ${lim}`;
      if (lim) return `일일 한도 ${lim}`;
      return "";
    }
    case "SYMBOL_WEIGHT_LIMIT_EXCEEDED": {
      const w = _fmtPct(d.weight_pct);
      const mx = _fmtPct(d.max_symbol_weight_pct);
      if (w && mx) return `예상 비중 ${w} > 최대 ${mx}`;
      if (mx) return `종목별 최대 비중 ${mx}`;
      return "";
    }
    case "MAX_POSITIONS_REACHED": {
      const cur = d.current_positions;
      const mx = d.max_positions;
      if (cur != null && mx != null) return `현재 보유 ${cur}종목 / 최대 ${mx}종목`;
      return "";
    }
    case "PRICE_STALE": {
      const age = d.age_seconds;
      if (age != null) return `현재가 갱신 후 ${Math.round(Number(age))}초 경과`;
      return "";
    }
    case "ABNORMAL_PRICE_MOVE": {
      const ch = _fmtPct(d.change_pct);
      if (ch) return `직전 대비 변동 ${ch}`;
      return "";
    }
    default:
      return "";
  }
}


/**
 * 매수 불가 reason 객체를 표시용 구조로 변환.
 *
 * 입력은 {reason_code, reason_message, details} 또는 문자열 code 또는 null.
 * @returns {{code, title, detail, severity, category, message}}
 */
export function formatBuyBlockReason(reason) {
  // null / undefined 안전 처리.
  if (reason == null) {
    const code = "UNKNOWN";
    return {
      code,
      title: BUY_BLOCK_REASON_TITLES[code],
      detail: "",
      severity: BUY_BLOCK_REASON_SEVERITY[code],
      category: BUY_BLOCK_REASON_CATEGORY[code],
      message: "",
    };
  }

  // 문자열만 들어온 경우 = code.
  const obj = typeof reason === "string" ? { reason_code: reason } : reason;
  const rawCode = obj.reason_code ?? obj.code ?? obj.reasonCode ?? null;
  const code = normalizeReasonCode(rawCode);
  const details = obj.details ?? obj.detail ?? null;
  // backend 가 이미 한국어 title 을 보냈으면 우선, 없으면 code 기본 title.
  const backendTitle =
    typeof obj.title === "string" && obj.title.trim() ? obj.title.trim() : null;
  const title = backendTitle || BUY_BLOCK_REASON_TITLES[code];

  // detail: details 객체 있으면 구조화, 없고 reason_message 가 title 과
  // 다르면 message 를 detail 로 노출.
  let detail = "";
  if (details && typeof details === "object") {
    detail = buildBuyBlockDetail(code, details);
  }
  const message =
    typeof obj.reason_message === "string" ? obj.reason_message : "";
  if (!detail && message && message !== title) detail = message;

  return {
    code,
    title,
    detail,
    severity: BUY_BLOCK_REASON_SEVERITY[code] || "info",
    category: BUY_BLOCK_REASON_CATEGORY[code] || "unknown",
    message,
  };
}


/** severity → 표시 색상 (UI 공통). */
export function buyBlockSeverityColor(severity) {
  switch (severity) {
    case "danger":  return "#ef4444";
    case "blocked": return "#b91c1c";
    case "warning": return "#f59e0b";
    case "info":    return "#3b82f6";
    default:        return "#64748b";
  }
}
