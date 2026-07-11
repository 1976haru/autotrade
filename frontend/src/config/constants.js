/** 앱 전역 상수 */
export const APP_VERSION = "2.1.0";
export const APP_NAME    = "AI 단타 자동매매";

/** 종목 코드 → 한글명 매핑 (백엔드 응답에는 종목명이 없어 프론트에서 보강) */
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

/** 가격 갱신 주기 (ms).
 *  2026-06-05: 2000ms → 15000ms. usePortfolio 가 보유 종목마다 KIS 실시간
 *  시세(brokerPrice)를 PRICE_TICK_MS 마다 한꺼번에(Promise.all) 호출하는데,
 *  보유 5종 이상이면 2초당 5+콜로 KIS 초당 한도(2 req/s)를 넘겨 EGW00201
 *  ("초당 거래건수 초과") 를 유발하고 브라우저가 느려졌다. 자동매매 루프 자체도
 *  KIS 시세를 스캔하므로 프론트 폴링은 보수적이어야 한다.
 *  2026-07-11(ratelimit_fix v2): 15000ms → 20000ms. 15초 폴링 < 20초 quote
 *  캐시 TTL이라 대부분의 재조회가 캐시가 아직 유효한 시점에 또 시도하는
 *  낭비였다 — 폴링 주기를 캐시 TTL 이상으로 올려 정합(results/ratelimit_fix/
 *  design_v2.md). 동시에 usePortfolio 가 보유종목별 개별 brokerPrice 호출을
 *  brokerPrices(일괄) 1콜로 교체 — N+1콜/tick → 2콜/tick(balance+prices). */
export const PRICE_TICK_MS  = 20000;

/** 테마 브리핑(전일 미국 테마 ETF 등락) 재조회 주기 (ms). 하루 1회만 값이
 *  바뀌는 데이터라 PRICE_TICK_MS급 폴링은 과함 — 30분이면 미국장 마감(~06:00
 *  KST) 이후 개장(09:00) 전까지 화면이 자동 갱신되기에 충분. 서버 12h TTL
 *  캐시가 실외부 호출 비용을 흡수하므로 이 주기로 때려도 yfinance 남용 아님. */
export const THEME_BRIEFING_REFRESH_MS = 30 * 60 * 1000;

/** 손절/익절 *양수 magnitude* 폴백 (손절 2=−2%, 익절 3.5=+3.5%). C1(2026-06-10):
 *  히어로/카드는 runtime-config 실효값(SSOT)을 우선 읽고, 미로딩 시에만 이 폴백 사용.
 *  표시 부호는 컴포넌트가 붙인다(손절 -, 익절 +). config 기본값과 일치 유지. */
export const EXIT_PLAN_DEFAULTS = { stopLossPct: 2, takeProfitPct: 3.5 };

/** 종목당 투자금 / 설정 종목 수 fallback (capital-config 조회 실패 시). */
export const PAPER_ALLOC_FALLBACK = { perSymbolKrw: 1_000_000, maxSymbols: 5 };

/** 종목코드 → 한글명. 화면에 코드(예: 373220)가 그대로 노출되지 않도록 — Paper
 *  유니버스에 등장할 법한 KOSPI/KOSDAQ 대형·중형주를 폭넓게 수록. 없으면 코드 표시. */
export const KR_STOCK_NAMES = {
  "005930": "삼성전자", "005935": "삼성전자우", "000660": "SK하이닉스",
  "035420": "NAVER", "035720": "카카오", "051910": "LG화학",
  "373220": "LG에너지솔루션", "006400": "삼성SDI", "247540": "에코프로비엠",
  "086520": "에코프로", "003670": "포스코퓨처엠", "028260": "삼성물산",
  "068270": "셀트리온", "091990": "셀트리온헬스케어", "207940": "삼성바이오로직스",
  "005380": "현대차", "000270": "기아", "012330": "현대모비스",
  "005490": "POSCO홀딩스", "010130": "고려아연", "022100": "포스코DX",
  "066570": "LG전자", "009150": "삼성전기", "018260": "삼성에스디에스",
  "105560": "KB금융", "055550": "신한지주", "086790": "하나금융지주",
  "032830": "삼성생명", "000810": "삼성화재", "316140": "우리금융지주",
  "015760": "한국전력", "017670": "SK텔레콤", "030200": "KT",
  "034730": "SK", "096770": "SK이노베이션", "010950": "S-Oil",
  "011200": "HMM", "042700": "한미반도체", "051900": "LG생활건강",
  "090430": "아모레퍼시픽", "259960": "크래프톤", "036570": "엔씨소프트",
  "251270": "넷마블", "352820": "하이브", "326030": "SK바이오팜",
  "196170": "알테오젠",
};
