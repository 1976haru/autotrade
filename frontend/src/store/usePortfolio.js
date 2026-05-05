import { useState, useEffect } from "react";
import { MOCK_STOCKS, PRICE_TICK_MS } from "../config/constants";
import { backendApi } from "../services/backend/client";

const lookupName = (symbol) =>
  MOCK_STOCKS.find((s) => s.code === symbol)?.name ?? symbol;

const toFrontPosition = (p) => ({
  code: p.symbol,
  name: lookupName(p.symbol),
  qty:  p.quantity,
  avg:  p.avg_price,
  cur:  p.market_price,
});

export function usePortfolio() {
  const [cash, setCash]           = useState(0);
  const [positions, setPositions] = useState([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [balance, raw] = await Promise.all([
          backendApi.brokerBalance(),
          backendApi.brokerPositions(),
        ]);
        if (cancelled) return;
        setCash(balance.cash);
        setPositions(raw.map(toFrontPosition));
        setError("");
      } catch (e) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const codesKey = positions.map((p) => p.code).sort().join(",");
  useEffect(() => {
    if (!codesKey) return;
    const codes = codesKey.split(",");
    const t = setInterval(async () => {
      try {
        const quotes = await Promise.all(codes.map((c) => backendApi.brokerPrice(c)));
        setPositions((prev) =>
          prev.map((p) => {
            const q = quotes.find((x) => x.symbol === p.code);
            return q ? { ...p, cur: q.price } : p;
          })
        );
      } catch {
        // 폴링 실패는 다음 틱에 자동 재시도
      }
    }, PRICE_TICK_MS);
    return () => clearInterval(t);
  }, [codesKey]);

  const invested    = positions.reduce((s, p) => s + p.cur * p.qty, 0);
  const totalAsset  = cash + invested;
  const totalPnL    = positions.reduce((s, p) => s + (p.cur - p.avg) * p.qty, 0);
  const totalPnLPct = invested > 0 ? (totalPnL / (totalAsset - totalPnL)) * 100 : 0;

  return {
    cash, positions,
    invested, totalAsset, totalPnL, totalPnLPct,
    loading, error,
  };
}
