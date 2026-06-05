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
  const [cash, setCash]           = useState(0);   // KIS dnca_tot_amt (총예수금, fallback용)
  const [equity, setEquity]       = useState(0);   // KIS tot_evlu_amt (평가총액=실제 총자산)
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
        // 213: balance/positions가 비정상 응답이어도 빈 값으로 정규화해 .reduce/
        // .map 폭발을 막는다 (Dashboard StatBox가 NaN/undefined를 받지 않도록).
        setCash(typeof balance?.cash === "number" ? balance.cash : 0);
        setEquity(typeof balance?.equity === "number" ? balance.equity : 0);
        const list = Array.isArray(raw) ? raw : [];
        setPositions(list.map(toFrontPosition));
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
        // 시세와 함께 잔고도 갱신 — equity(평가총액)를 invested 와 같은 주기로
        // 신선하게 유지해 추정자산/예수금/주식평가가 항상 정합하도록.
        const [bal, quotes] = await Promise.all([
          backendApi.brokerBalance().catch(() => null),
          Promise.all(codes.map((c) => backendApi.brokerPrice(c))),
        ]);
        if (bal && typeof bal.cash === "number") setCash(bal.cash);
        if (bal && typeof bal.equity === "number") setEquity(bal.equity);
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
  // ★추정자산 = KIS 평가총액(tot_evlu_amt). dnca_tot_amt(cash)는 T+2 정산 전
  //   '총예수금'이라 invested 와 더하면 매수분이 이중계산된다(추정자산 과대표시).
  //   equity 가 없으면(mock broker 등) 옛 방식(cash+invested) fallback.
  const totalAsset    = equity > 0 ? equity : cash + invested;
  // ★예수금(가용현금) = 총자산 - 주식평가. equity 기반일 때 세 값이 내부 정합.
  const availableCash = equity > 0 ? Math.max(0, equity - invested) : cash;
  const totalPnL    = positions.reduce((s, p) => s + (p.cur - p.avg) * p.qty, 0);
  const totalPnLPct = invested > 0 ? (totalPnL / (totalAsset - totalPnL)) * 100 : 0;

  return {
    cash: availableCash, positions,
    invested, totalAsset, equity, totalPnL, totalPnLPct,
    loading, error,
  };
}
