import { useState, useCallback } from "react";

/**
 * 봇 컨트롤 훅.
 * 이전 버전의 Math.random 기반 가짜 매매 시뮬레이션은 제거됨.
 * 실제 체결 스트림과 통계 집계는 backend 전략/체결 엔진이 구현된 뒤 연결한다.
 */
export function useBot() {
  const [running, setRunning] = useState(false);
  const [trades]  = useState([]);
  const [stats]   = useState({ total: 0, wins: 0, losses: 0, pnl: 0 });

  const start = useCallback(() => setRunning(true),  []);
  const stop  = useCallback(() => setRunning(false), []);
  const reset = useCallback(() => {}, []);

  return {
    running, trades, stats,
    winRate: "0.0",
    start, stop, reset,
  };
}
