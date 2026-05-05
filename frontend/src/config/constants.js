/** 앱 전역 상수 */
export const APP_VERSION = "2.1.0";
export const APP_NAME    = "AI 단타 자동매매";

/** 모의투자용 종목 풀 */
export const MOCK_STOCKS = [
  { code: "005930", name: "삼성전자",   sector: "반도체" },
  { code: "000660", name: "SK하이닉스",sector: "반도체" },
  { code: "035420", name: "NAVER",      sector: "인터넷" },
  { code: "051910", name: "LG화학",    sector: "화학"   },
  { code: "035720", name: "카카오",    sector: "인터넷" },
  { code: "247540", name: "에코프로비엠",sector: "배터리"},
  { code: "006400", name: "삼성SDI",   sector: "배터리" },
  { code: "028260", name: "삼성물산",  sector: "건설"   },
];

/** 기본 포트폴리오 (시뮬레이션) */
export const DEFAULT_PORTFOLIO = {
  cash: 5_234_800,
  positions: [
    { code: "005930", name: "삼성전자",   qty: 10, avg: 76_000, cur: 78_200 },
    { code: "000660", name: "SK하이닉스",qty:  5, avg: 182_000,cur: 179_500 },
    { code: "035420", name: "NAVER",      qty:  3, avg: 194_000,cur: 197_800 },
  ],
};

/** 기본 리스크 설정 */
export const DEFAULT_RISK = {
  maxDailyLoss:  300_000,   // 일일 최대손실 (원)
  maxPerTrade:   1_000_000, // 종목당 최대 투자금 (원)
  maxPositions:  5,         // 최대 동시 보유 종목
  trailingStop:  true,      // 트레일링 스탑
  forceCloseAt:  "15:20",  // 강제청산 시간
  pauseOnStreak: 3,         // 연속 손실 시 일시정지
  maxDrawdown:   5,         // 최대 낙폭 % (서킷브레이커)
  circuitBreaker: true,
};

/** 합류점수 가중치 */
export const CONFLUENCE_WEIGHTS = {
  tech:  0.30,  // 기술적 신호
  trend: 0.20,  // 구글 트렌드
  news:  0.25,  // 네이버 뉴스 센티먼트
  flow:  0.25,  // 외인·기관 수급
};

/** 합류점수 기준 */
export const CONFLUENCE_THRESHOLD = {
  enter: 70,  // 진입
  watch: 50,  // 관망
};

/** Claude API */
export const CLAUDE_MODEL   = "claude-sonnet-4-20250514";
export const CLAUDE_MAX_TOK = 1000;

/** 가격 갱신 주기 (ms) */
export const PRICE_TICK_MS  = 2000;
/** 봇 매매 주기 (ms) */
export const BOT_INTERVAL_MS = 3800;
