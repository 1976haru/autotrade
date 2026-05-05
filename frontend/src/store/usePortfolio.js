import { useState, useEffect } from "react";
import { DEFAULT_PORTFOLIO, PRICE_TICK_MS } from "../config/constants";

/**
 * 포트폴리오 상태 훅
 * - 포지션 관리
 * - 실시간 가격 시뮬레이션
 * - 손익 계산
 */
export function usePortfolio() {
  const [cash, setCash]       = useState(DEFAULT_PORTFOLIO.cash);
  const [positions, setPositions] = useState(DEFAULT_PORTFOLIO.positions);

  // 실시간 가격 틱 (시뮬레이션)
  useEffect(() => {
    const t = setInterval(() => {
      setPositions((prev) =>
        prev.map((p) => ({
          ...p,
          cur: Math.max(
            Math.round(p.cur + (Math.random() - 0.48) * p.cur * 0.003),
            100
          ),
        }))
      );
    }, PRICE_TICK_MS);
    return () => clearInterval(t);
  }, []);

  // 파생 계산
  const invested   = positions.reduce((s, p) => s + p.cur * p.qty, 0);
  const totalAsset = cash + invested;
  const totalPnL   = positions.reduce((s, p) => s + (p.cur - p.avg) * p.qty, 0);
  const totalPnLPct = invested > 0 ? (totalPnL / (totalAsset - totalPnL)) * 100 : 0;

  /** 포지션 추가 (실전 연동 시 사용) */
  const addPosition = (pos) =>
    setPositions((prev) => {
      const idx = prev.findIndex((p) => p.code === pos.code);
      if (idx >= 0) {
        // 평균단가 재계산
        const old = prev[idx];
        const totalQty = old.qty + pos.qty;
        const avgPrice = (old.avg * old.qty + pos.avg * pos.qty) / totalQty;
        const next = [...prev];
        next[idx] = { ...old, qty: totalQty, avg: Math.round(avgPrice) };
        return next;
      }
      return [...prev, pos];
    });

  /** 포지션 제거 */
  const removePosition = (code) =>
    setPositions((prev) => prev.filter((p) => p.code !== code));

  /** 포지션 업데이트 (현재가 실시간 반영) */
  const updatePrice = (code, price) =>
    setPositions((prev) =>
      prev.map((p) => (p.code === code ? { ...p, cur: price } : p))
    );

  return {
    cash, setCash,
    positions, setPositions,
    invested, totalAsset, totalPnL, totalPnLPct,
    addPosition, removePosition, updatePrice,
  };
}
