/**
 * 3-09: 거래 없음(no-trade) 사유 formatter — "왜 거래가 없었는지" 를 사람이
 * 이해하기 쉬운 한국어로 변환한다.
 *
 * 본 모듈은 *표시 전용* 이다. 실제 매수/매도 로직을 열지 않으며, 실거래
 * 활성화 문구/버튼/토글/자동 재주문을 만들지 않는다 (테스트로 lock).
 * backend `app/auto_paper/no_trade_reasons.py::NO_TRADE_REASON_TITLES_KO` 와 정합.
 *
 * 거래 없음은 *오류가 아닐 수 있다* — 조건이 없어서 쉰 것인지, 차단된 것인지
 * 구분하기 위한 사유 표시이다.
 */

// code → 기본 한국어 제목 (backend NO_TRADE_REASON_TITLES_KO 와 정합).
export const NO_TRADE_REASON_TITLES = Object.freeze({
  NO_SIGNAL:                  "조건에 맞는 매수/매도 신호가 없어 거래하지 않음",
  NO_STRATEGY_SIGNAL:         "전략 신호가 없어 거래하지 않음",
  NO_CANDIDATE:               "매수 후보가 생성되지 않아 거래하지 않음",
  NO_MARKET_DATA:             "시장 데이터가 없어 거래하지 않음",
  MARKET_CLOSED:              "장 시간이 아니어서 거래하지 않음",
  PRICE_STALE:                "현재가가 오래되어 거래하지 않음",
  INVALID_PRICE:              "현재가가 비정상이라 거래하지 않음",
  ABNORMAL_PRICE_MOVE:        "가격 급등락이 감지되어 거래하지 않음",
  BLOCKED_BY_RISK_MANAGER:    "리스크 조건으로 거래 차단",
  BLOCKED_BY_PERMISSION_GATE: "주문 권한 조건으로 거래 차단",
  BLOCKED_BY_KIS_READINESS:   "KIS 모의투자 준비 상태가 충족되지 않아 거래하지 않음",
  PAPER_EXECUTION_DISABLED:   "Paper 가상 실행이 비활성화되어 거래하지 않음",
  EMERGENCY_STOP:             "긴급정지 상태라 거래하지 않음",
  EXIT_PLAN_MISSING:          "손절/익절 계획이 없어 매수 차단",
  EXIT_PLAN_INVALID:          "손절/익절 계획이 유효하지 않아 매수 차단",
  RISK_FLAGS_EXCEEDED:        "위험 플래그 초과로 HOLD",
  DUPLICATE_POSITION_BUY_BLOCKED: "이미 보유 중인 종목이라 추가 매수 차단",
  DAILY_BUY_LIMIT_EXCEEDED:   "일일 최대 매수금액을 초과하여 매수 차단",
  DAILY_ORDER_LIMIT_EXCEEDED: "일일 최대 주문 횟수를 초과하여 매수 차단",
  NOTIONAL_LIMIT_EXCEEDED:    "1회 주문금액 한도를 초과하여 매수 차단",
  SYMBOL_WEIGHT_LIMIT_EXCEEDED: "종목별 최대 비중을 초과하여 매수 차단",
  MAX_POSITIONS_REACHED:      "최대 보유 종목 수에 도달하여 매수 차단",
  INSUFFICIENT_PAPER_CASH:    "남은 Paper 현금이 부족하여 매수 차단",
  MIN_LOT_NOT_AFFORDABLE:     "1주 가격이 투자한도 초과로 제외",
  UNKNOWN:                    "알 수 없는 사유로 거래하지 않음",
});

export const NO_TRADE_REASON_CATEGORY = Object.freeze({
  NO_SIGNAL:                  "strategy",
  NO_STRATEGY_SIGNAL:         "strategy",
  NO_CANDIDATE:               "strategy",
  NO_MARKET_DATA:             "market",
  MARKET_CLOSED:              "market",
  PRICE_STALE:                "price",
  INVALID_PRICE:              "price",
  ABNORMAL_PRICE_MOVE:        "price",
  BLOCKED_BY_RISK_MANAGER:    "risk",
  BLOCKED_BY_PERMISSION_GATE: "permission",
  BLOCKED_BY_KIS_READINESS:   "permission",
  PAPER_EXECUTION_DISABLED:   "permission",
  EMERGENCY_STOP:             "risk",
  EXIT_PLAN_MISSING:          "risk",
  EXIT_PLAN_INVALID:          "risk",
  RISK_FLAGS_EXCEEDED:        "risk",
  DUPLICATE_POSITION_BUY_BLOCKED: "capital",
  DAILY_BUY_LIMIT_EXCEEDED:   "capital",
  DAILY_ORDER_LIMIT_EXCEEDED: "capital",
  NOTIONAL_LIMIT_EXCEEDED:    "capital",
  SYMBOL_WEIGHT_LIMIT_EXCEEDED: "capital",
  MAX_POSITIONS_REACHED:      "capital",
  INSUFFICIENT_PAPER_CASH:    "capital",
  MIN_LOT_NOT_AFFORDABLE:     "capital",
  UNKNOWN:                    "unknown",
});

export const NO_TRADE_REASON_SEVERITY = Object.freeze({
  NO_SIGNAL:                  "info",
  NO_STRATEGY_SIGNAL:         "info",
  NO_CANDIDATE:               "info",
  NO_MARKET_DATA:             "warning",
  MARKET_CLOSED:              "info",
  PRICE_STALE:                "warning",
  INVALID_PRICE:              "danger",
  ABNORMAL_PRICE_MOVE:        "danger",
  BLOCKED_BY_RISK_MANAGER:    "blocked",
  BLOCKED_BY_PERMISSION_GATE: "blocked",
  BLOCKED_BY_KIS_READINESS:   "warning",
  PAPER_EXECUTION_DISABLED:   "blocked",
  EMERGENCY_STOP:             "danger",
  EXIT_PLAN_MISSING:          "blocked",
  EXIT_PLAN_INVALID:          "blocked",
  RISK_FLAGS_EXCEEDED:        "warning",
  DUPLICATE_POSITION_BUY_BLOCKED: "info",
  DAILY_BUY_LIMIT_EXCEEDED:   "warning",
  DAILY_ORDER_LIMIT_EXCEEDED: "warning",
  NOTIONAL_LIMIT_EXCEEDED:    "warning",
  SYMBOL_WEIGHT_LIMIT_EXCEEDED: "warning",
  MAX_POSITIONS_REACHED:      "info",
  INSUFFICIENT_PAPER_CASH:    "warning",
  MIN_LOT_NOT_AFFORDABLE:     "info",
  UNKNOWN:                    "info",
});

// raw code 별칭 → 정규 code (backend _NO_TRADE_ALIASES 와 정합).
const _CODE_ALIASES = Object.freeze({
  HOLD:                "NO_SIGNAL",
  NO_OP:               "NO_SIGNAL",
  NO_TRADE:            "NO_SIGNAL",
  NO_DECISION:         "NO_SIGNAL",
  NO_ACTION:           "NO_SIGNAL",
  EXIT_PLAN_REQUIRED:  "EXIT_PLAN_MISSING",
  NO_EXIT_PLAN:        "EXIT_PLAN_MISSING",
  EXIT_PLAN_NOT_VALID: "EXIT_PLAN_INVALID",
  RISK_FLAGS_EXCEED:   "RISK_FLAGS_EXCEEDED",
  RISK_FLAG_EXCEEDED:  "RISK_FLAGS_EXCEEDED",
  RISK_VETO:           "RISK_FLAGS_EXCEEDED",
  KIS_READINESS_BLOCKED: "BLOCKED_BY_KIS_READINESS",
  KIS_NOT_READY:         "BLOCKED_BY_KIS_READINESS",
  // buy block raw aliases.
  STALE_DATA:          "PRICE_STALE",
  STALE_PRICE:         "PRICE_STALE",
  PRICE_MISSING:       "NO_MARKET_DATA",
  INSUFFICIENT_CASH:   "INSUFFICIENT_PAPER_CASH",
  PRICE_OVER_CAP:      "MIN_LOT_NOT_AFFORDABLE",
  BELOW_MIN_LOT:       "MIN_LOT_NOT_AFFORDABLE",
  PAPER_GUARD_DUPLICATE: "DUPLICATE_POSITION_BUY_BLOCKED",
  KIS_PAPER_ORDER_LIMIT_EXCEEDED:    "DAILY_ORDER_LIMIT_EXCEEDED",
  KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED: "NOTIONAL_LIMIT_EXCEEDED",
  KIS_PAPER_MAX_POSITIONS:           "MAX_POSITIONS_REACHED",
  UNKNOWN_ERROR:       "UNKNOWN",
});


/** code 문자열을 정규 어휘로 변환. null/미지원이면 "UNKNOWN". */
export function normalizeNoTradeReasonCode(raw) {
  if (raw == null) return "UNKNOWN";
  const code = String(raw).trim().toUpperCase();
  if (!code) return "UNKNOWN";
  const aliased = _CODE_ALIASES[code] || code;
  if (NO_TRADE_REASON_TITLES[aliased]) return aliased;
  return "UNKNOWN";
}


/**
 * 거래 없음 reason 객체를 표시용 구조로 변환.
 *
 * 입력은 {reason_code, reason_message, ...} 또는 문자열 code 또는 null.
 * @returns {{code, title, severity, category, message}}
 */
export function formatNoTradeReason(reason) {
  if (reason == null) {
    const code = "UNKNOWN";
    return {
      code,
      title: NO_TRADE_REASON_TITLES[code],
      severity: NO_TRADE_REASON_SEVERITY[code],
      category: NO_TRADE_REASON_CATEGORY[code],
      message: "",
    };
  }
  const obj = typeof reason === "string" ? { reason_code: reason } : reason;
  const rawCode = obj.reason_code ?? obj.code ?? obj.reasonCode ?? null;
  const code = normalizeNoTradeReasonCode(rawCode);
  const backendTitle =
    typeof obj.title === "string" && obj.title.trim() ? obj.title.trim() : null;
  const title = backendTitle || NO_TRADE_REASON_TITLES[code];
  const message =
    typeof obj.reason_message === "string" ? obj.reason_message : "";
  return {
    code,
    title,
    severity: NO_TRADE_REASON_SEVERITY[code] || "info",
    category: NO_TRADE_REASON_CATEGORY[code] || "unknown",
    message,
  };
}
