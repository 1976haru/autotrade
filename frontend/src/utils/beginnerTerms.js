/**
 * PART4-3: 초보자 용어 사전 — 운영 상태/오류 코드를 *일상어* 로 변환한다.
 *
 * 2026-06-01 첫 실전 모의 후, 화면에 dry_run / WINDOW_CLOSED / EGW00201 같은
 * 전문용어가 그대로 노출돼 운영자(주식 초보)가 정상/오류를 구분하기 어려웠다.
 * 본 모듈은 그런 코드를 "점검만 / 장 시간 아님 / 조회 과다·자동대기" 처럼
 * 쉬운 말로 바꿔준다.
 *
 * 기존 noTradeReasons.js / buyBlockReasons.js 와 같은 *표시 전용* 매퍼다 —
 * 실제 매수/매도 로직을 열지 않으며, 실거래 활성화 문구/버튼/토글을 만들지
 * 않는다 (테스트로 lock).
 */

// 운영 상태/모드 코드 → 일상어 라벨.
export const BEGINNER_TERMS = Object.freeze({
  // 실행 모드
  dry_run:                "점검만 (실제 주문 없음)",
  DRY_RUN:                "점검만 (실제 주문 없음)",
  VIRTUAL_ONLY:           "가상 점검만 (실시세·실주문 없음)",
  KIS_REALTIME_DRYRUN:    "실시세 점검만 (주문은 안 보냄)",
  KIS_REALTIME_PAPER_AUTO: "모의 자동매매 (가상 자금)",
  KIS_REALTIME_SMOKE_TEST: "모의 자동매매 점검 (1종목·1주)",

  // 시장/시간
  WINDOW_CLOSED:          "지금은 자동매매 시간이 아님",
  KIS_PAPER_ORDER_WINDOW_CLOSED: "지금은 자동매매 시간이 아님",
  MARKET_CLOSED:          "장 시간이 아님 (정상)",
  WAITING_FOR_MARKET_OPEN: "장 열리기를 기다리는 중",

  // 시세/조회
  EGW00201:               "조회가 너무 잦아 잠시 자동 대기 중 (정상 보호)",
  KIS_PRICE_STALE:        "시세가 잠깐 오래됨 — 판단 보류",
  KIS_MARKET_DATA_UNAVAILABLE: "시세를 못 받아 이번엔 쉬는 중",
  KIS_PRICE_INVALID:      "시세가 비정상이라 이번엔 쉬는 중",
  PRICE_STALE:            "시세가 오래되어 판단 보류",

  // 주문 결과
  RECEIVED:               "주문 접수됨 (체결 확인 대기)",
  FILLED:                 "체결 완료",
  PARTIALLY_FILLED:       "일부만 체결됨",
  REJECTED:               "주문 거부됨",
  SELL_NO_HELD_POSITION:  "보유하지 않은 종목이라 매도 안 함",
  DUPLICATE_POSITION_BLOCKED: "이미 보유 중이라 추가 매수 안 함",
  MAX_CONCURRENT_POSITIONS_REACHED: "보유 종목이 한도까지 차서 신규 매수 안 함",
  DAILY_BUY_LIMIT_REACHED: "오늘 매수 한도를 다 써서 매수 안 함",
  MAX_NEW_POSITIONS_PER_TICK_REACHED: "이번 회차 신규 매수 한도 도달",
  HOLD_NO_SIGNAL:         "마땅한 신호가 없어 관망",
  INSUFFICIENT_PAPER_CASH: "모의 현금이 부족해 매수 안 함",

  // 데이터 소스
  mock:                   "가짜(테스트) 시세",
  yfinance:               "지연 시세 (참고용)",
  kis:                    "한투 실시간 시세",
  PAPER_SIMULATED:        "내부 모의 계좌",
  KIS_PAPER_ACCOUNT:      "한투 모의 계좌",
  UNAVAILABLE:            "조회 실패 (0원이 아니라 '확인 불가')",
});

/**
 * 코드/상태값을 초보자 라벨로 변환. 매핑이 없으면 원본을 그대로 반환
 * (정보 손실 0 — 모르는 코드를 숨기지 않는다).
 *
 * @param {string} code 운영 상태/오류 코드
 * @returns {string} 일상어 라벨 또는 원본
 */
export function toBeginnerTerm(code) {
  if (code === null || code === undefined) return "";
  const key = String(code).trim();
  if (key === "") return "";
  return BEGINNER_TERMS[key] || key;
}

/**
 * boolean dry_run 값을 라벨로. true → "점검만", false → "실제 모의주문 전송".
 * (실거래가 아니라 *모의* 주문임에 유의 — 실거래 활성화 아님.)
 */
export function dryRunLabel(isDryRun) {
  return isDryRun
    ? "점검만 (주문 안 보냄)"
    : "모의주문 전송 (가상 자금)";
}

export default toBeginnerTerm;
