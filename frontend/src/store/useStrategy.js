import { useState } from "react";
import { DEFAULT_STRATEGY_ON, getDefaultParams } from "../config/strategies";

/**
 * 전략 상태 훅
 * - 전략 ON/OFF 토글
 * - 파라미터 개별 수정
 */
export function useStrategy() {
  const [strategyOn,     setStrategyOn]     = useState(DEFAULT_STRATEGY_ON);
  const [strategyParams, setStrategyParams] = useState(getDefaultParams());

  /** 전략 토글 */
  const toggle = (id) =>
    setStrategyOn((prev) => ({ ...prev, [id]: !prev[id] }));

  /** 단일 파라미터 업데이트 */
  const updateParam = (stratId, paramKey, value) =>
    setStrategyParams((prev) => ({
      ...prev,
      [stratId]: { ...prev[stratId], [paramKey]: value },
    }));

  /** 활성 전략 목록 */
  const activeIds = Object.entries(strategyOn)
    .filter(([, v]) => v)
    .map(([k]) => k);

  return {
    strategyOn, setStrategyOn,
    strategyParams, setStrategyParams,
    toggle, updateParam, activeIds,
  };
}
