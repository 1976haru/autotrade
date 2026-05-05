import { useState, useCallback } from "react";
import { DEFAULT_RISK } from "../config/constants";

/**
 * 리스크 관리 훅
 * - 리스크 파라미터 상태
 * - 서킷브레이커 로직
 * - 진입 가능 여부 판단
 */
export function useRisk() {
  const [risk, setRisk] = useState(DEFAULT_RISK);
  const [triggered, setTriggered] = useState(false); // 서킷브레이커 발동

  /** 단일 항목 업데이트 */
  const update = (key, value) =>
    setRisk((prev) => ({ ...prev, [key]: value }));

  /**
   * 신규 매매 진입 가능 여부 체크
   * @param {object} opts - { dailyPnL, positions, streakLoss }
   */
  const canEnter = useCallback(
    ({ dailyPnL = 0, positions = [], streakLoss = 0 }) => {
      if (!risk.circuitBreaker) return { ok: true };

      if (-dailyPnL >= risk.maxDailyLoss)
        return { ok: false, reason: `일일 손실 한도 ${(risk.maxDailyLoss/10000).toFixed(0)}만원 초과` };

      if (positions.length >= risk.maxPositions)
        return { ok: false, reason: `최대 보유 종목 ${risk.maxPositions}개 초과` };

      if (streakLoss >= risk.pauseOnStreak)
        return { ok: false, reason: `연속 손실 ${risk.pauseOnStreak}회 → 일시 정지` };

      // 장마감 강제 청산 시간 체크
      const [h, m] = risk.forceCloseAt.split(":").map(Number);
      const now = new Date();
      if (now.getHours() > h || (now.getHours() === h && now.getMinutes() >= m))
        return { ok: false, reason: `장마감 강제청산 시간(${risk.forceCloseAt}) 경과` };

      return { ok: true };
    },
    [risk]
  );

  /** 서킷브레이커 수동 리셋 */
  const resetCircuit = () => setTriggered(false);

  return { risk, update, canEnter, triggered, resetCircuit };
}
