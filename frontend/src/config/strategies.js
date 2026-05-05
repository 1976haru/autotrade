/**
 * 단타 전략 정의 모듈
 * 새 전략 추가 시 이 파일과 strategies/ 폴더에 파일 추가
 */
export const STRATEGIES = {
  orb: {
    id: "orb",
    icon: "🎯",
    name: "ORB 브레이크아웃",
    color: "#ec4899",
    winRate: 67,
    desc: "장 초반 레인지 돌파 → 방향성 추종",
    detail: "9:00~9:15 형성 고저 레인지를 돌파하면 방향으로 진입. 단타 중 승률 최고.",
    bestTime: "09:15~10:00",
    bestTarget: "당일 이슈 테마주",
    params: {
      rangePeriod:  { label: "레인지 형성(분)", default: 15, min: 5,   max: 30,  step: 5    },
      breakoutPct:  { label: "돌파 확인률(%)",  default: 0.3,min: 0.1, max: 1,   step: 0.05 },
      maxEntryMin:  { label: "최대 진입(분)",   default: 60, min: 15,  max: 120, step: 15   },
      stopLoss:     { label: "손절률(%)",       default: 1.2,min: 0.5, max: 3,   step: 0.1  },
      targetPct:    { label: "목표수익률(%)",   default: 2.5,min: 1,   max: 8,   step: 0.1  },
    },
  },

  scalp: {
    id: "scalp",
    icon: "⚔️",
    name: "스캘핑",
    color: "#a855f7",
    winRate: 65,
    desc: "볼린저밴드 이탈 반전 | 1~5분 초단타",
    detail: "하단밴드 터치 시 매수, 상단밴드 시 매도. 소폭 수익 반복 누적.",
    bestTime: "09:00~14:00",
    bestTarget: "코스닥 변동성 종목",
    params: {
      bbPeriod:  { label: "볼린저 기간",   default: 20,  min: 10,  max: 40, step: 1   },
      bbStd:     { label: "표준편차 배수", default: 2.0, min: 1.5, max: 3,  step: 0.1 },
      stopLoss:  { label: "손절률(%)",     default: 0.8, min: 0.3, max: 2,  step: 0.1 },
      targetPct: { label: "목표수익률(%)", default: 1.0, min: 0.3, max: 3,  step: 0.1 },
    },
  },

  momentum: {
    id: "momentum",
    icon: "🚀",
    name: "모멘텀 트레이딩",
    color: "#00d4aa",
    winRate: 62,
    desc: "RSI·MACD·거래량 폭증 기반 추세 추종",
    detail: "강한 상승 모멘텀 포착 후 추세 편승. RSI 과매수 도달 시 청산.",
    bestTime: "09:30~11:00",
    bestTarget: "거래량 급증 상위 종목",
    params: {
      rsiEntry:  { label: "RSI 진입 임계", default: 55,  min: 40, max: 70, step: 1   },
      rsiExit:   { label: "RSI 청산 임계", default: 73,  min: 60, max: 85, step: 1   },
      volMulti:  { label: "거래량 배율(배)",default: 2.5, min: 1,  max: 10, step: 0.5 },
      stopLoss:  { label: "손절률(%)",     default: 1.5, min: 0.5,max: 5,  step: 0.1 },
      targetPct: { label: "목표수익률(%)", default: 3.0, min: 1,  max: 10, step: 0.1 },
    },
  },

  vwap: {
    id: "vwap",
    icon: "📊",
    name: "VWAP 트레이딩",
    color: "#f59e0b",
    winRate: 61,
    desc: "거래량 가중 평균가 이탈 시 역추세 진입",
    detail: "VWAP 하방 이탈 과매도 반등 매수, 상방 과매수 청산.",
    bestTime: "10:00~14:00",
    bestTarget: "대형주 (삼성전자, SK하이닉스)",
    params: {
      devPct:    { label: "VWAP 이탈률(%)",  default: 1.5, min: 0.5, max: 5,  step: 0.1 },
      timeStart: { label: "적용 시작(분)",   default: 30,  min: 10,  max: 60, step: 5   },
      stopLoss:  { label: "손절률(%)",       default: 1.0, min: 0.5, max: 3,  step: 0.1 },
      targetPct: { label: "목표수익률(%)",   default: 1.5, min: 0.5, max: 5,  step: 0.1 },
    },
  },

  gap: {
    id: "gap",
    icon: "⚡",
    name: "갭 트레이딩",
    color: "#ff6b35",
    winRate: 58,
    desc: "시초가 갭 방향성 포착 → 눌림목 진입",
    detail: "갭업 +2~8% 후 눌림목 확인 매수 또는 갭다운 반등 포착.",
    bestTime: "09:00~09:30",
    bestTarget: "전일 급등/급락 종목",
    params: {
      minGap:    { label: "최소 갭(%)",      default: 2.0, min: 0.5, max: 10, step: 0.1 },
      maxGap:    { label: "최대 갭(%)",      default: 8.0, min: 3,   max: 20, step: 0.5 },
      entryMin:  { label: "진입 제한(분)",   default: 15,  min: 5,   max: 60, step: 5   },
      stopLoss:  { label: "손절률(%)",       default: 1.0, min: 0.3, max: 3,  step: 0.1 },
      targetPct: { label: "목표수익률(%)",   default: 2.5, min: 1,   max: 8,  step: 0.1 },
    },
  },
};

/** 기본 활성화 전략 */
export const DEFAULT_STRATEGY_ON = {
  orb: true, scalp: false, momentum: true, vwap: false, gap: true,
};

/** 각 전략의 기본 파라미터 추출 */
export const getDefaultParams = () =>
  Object.fromEntries(
    Object.entries(STRATEGIES).map(([k, v]) => [
      k,
      Object.fromEntries(Object.entries(v.params).map(([pk, pv]) => [pk, pv.default])),
    ])
  );
