import { useCallback, useState } from "react";
import { backendApi } from "../services/backend/client";

/**
 * 시장 데이터 조회 훅 — fetch()는 backend `/api/market/bars`로 GET.
 * 응답에 source("cache"|"upstream"), count, bars가 포함된다.
 */
export function useMarketBars() {
  const [bars,    setBars]    = useState(null);
  const [source,  setSource]  = useState(null);
  const [count,   setCount]   = useState(0);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");

  const fetch = useCallback(async ({ symbol, start, end, interval = "1d" }) => {
    setLoading(true);
    setError("");
    try {
      const res = await backendApi.marketBars({ symbol, start, end, interval });
      setBars(res.bars);
      setSource(res.source);
      setCount(res.count);
      return res;
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  return { bars, source, count, loading, error, fetch };
}
