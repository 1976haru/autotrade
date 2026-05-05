import { useState, useRef, useCallback, useEffect } from "react";
import { MOCK_STOCKS, BOT_INTERVAL_MS } from "../config/constants";
import { STRATEGIES } from "../config/strategies";
import { nowTime } from "../utils/format";

/**
 * 자동매매 봇 훅
 * - 봇 시작/정지
 * - 매매 시뮬레이션 엔진
 * - 체결 로그 관리
 * - 통계 집계
 */
export function useBot(strategyOn) {
  const [running, setRunning]   = useState(false);
  const [trades,  setTrades]    = useState([]);
  const [stats,   setStats]     = useState({ total: 0, wins: 0, losses: 0, pnl: 0 });
  const intervalRef = useRef(null);

  /** 활성 전략 목록 */
  const activeStrats = Object.entries(strategyOn)
    .filter(([, v]) => v)
    .map(([k]) => STRATEGIES[k]);

  /** 봇 시작 */
  const start = useCallback(() => {
    if (running) return;
    setRunning(true);

    intervalRef.current = setInterval(() => {
      // 30% 확률로 스킵 (실제 시장처럼 매 틱 매매 안 함)
      if (Math.random() < 0.30) return;

      const isWin  = Math.random() > 0.37; // ~63% 승률 목표
      const stock  = MOCK_STOCKS[Math.floor(Math.random() * MOCK_STOCKS.length)];
      const strat  = activeStrats.length > 0
        ? activeStrats[Math.floor(Math.random() * activeStrats.length)]
        : STRATEGIES.orb;
      const entry  = Math.round(30_000 + Math.random() * 200_000);
      const pnlAmt = isWin
        ? Math.round(Math.random() * 140_000 + 8_000)
        : -Math.round(Math.random() * 70_000 + 4_000);
      const exit   = Math.round(entry * (1 + pnlAmt / (entry * 10)));

      const trade = {
        id:    Date.now(),
        ts:    nowTime(),
        code:  stock.code,
        name:  stock.name,
        strat: `${strat.icon} ${strat.name}`,
        entry,
        exit,
        pnl:   pnlAmt,
        win:   isWin,
      };

      setTrades((prev) => [trade, ...prev.slice(0, 99)]);
      setStats((prev) => ({
        total:  prev.total + 1,
        wins:   prev.wins + (isWin ? 1 : 0),
        losses: prev.losses + (isWin ? 0 : 1),
        pnl:    prev.pnl + pnlAmt,
      }));
    }, BOT_INTERVAL_MS);
  }, [running, activeStrats]);

  /** 봇 정지 */
  const stop = useCallback(() => {
    setRunning(false);
    clearInterval(intervalRef.current);
  }, []);

  /** 로그 초기화 */
  const reset = useCallback(() => {
    setTrades([]);
    setStats({ total: 0, wins: 0, losses: 0, pnl: 0 });
  }, []);

  // 언마운트 시 정리
  useEffect(() => () => clearInterval(intervalRef.current), []);

  const winRate = stats.total > 0
    ? ((stats.wins / stats.total) * 100).toFixed(1)
    : "0.0";

  return { running, trades, stats, winRate, start, stop, reset };
}
