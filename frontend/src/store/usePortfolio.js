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
  const [ready, setReady]         = useState(false);  // G2: 첫 성공 여부(숫자 표시 게이트)
  const [stale, setStale]         = useState(false);  // T2: 옛 정상값(레이트리밋) 표시중
  const [asOf, setAsOf]           = useState(null);   // T2: stale 기준시각(KST HH:MM)
  const [brokerHealthy, setBrokerHealthy] = useState(true); // T3: KIS 헬스(배너 ③ 판정)

  // B4: 앱 로드 시 1회 조회. ★최초 조회가 실패하면(백엔드 미준비) 첫 성공까지 복구
  //   재시도 — 예전엔 deps [] 로 1회만 시도해, 그 1회가 실패하면 새로고침 전까지 영구히
  //   "못 불러왔어요"에 갇혔다. 연속 폴링 아님(성공하면 멈춤).
  // G2: 첫 성공 전엔 loading=true 유지(빈 잔고 0 을 숫자로 그리지 않게) — 계좌카드가
  //   ready 전에는 "불러오는 중", 재시도 소진(MAX) 후 실패면 "불러오지 못했어요" 표시.
  useEffect(() => {
    let cancelled = false;
    let timer;
    let attempts = 0;
    const MAX_ATTEMPTS = 12; // ~60s — KIS 토큰 레이트리밋(EGW00133, 1/min) 창 커버
    const attempt = async () => {
      attempts += 1;
      try {
        const [balance, raw] = await Promise.all([
          backendApi.brokerBalance(),
          backendApi.brokerPositions(),
        ]);
        if (cancelled) return;
        // 213: 비정상 응답이어도 빈 값으로 정규화(.reduce/.map 폭발 방지).
        setCash(typeof balance?.cash === "number" ? balance.cash : 0);
        setEquity(typeof balance?.equity === "number" ? balance.equity : 0);
        const list = Array.isArray(raw) ? raw : [];
        setPositions(list.map(toFrontPosition));
        setError("");
        setReady(true);
        setLoading(false);
        // T2/T3: stale(레이트리밋 옛값)·기준시각·KIS 헬스 반영. stale 도 ready(숫자 있음).
        setStale(!!balance?.stale);
        setAsOf(balance?.as_of_kst || null);
        setBrokerHealthy(balance?.broker_healthy !== false);
      } catch (e) {
        if (cancelled) return;
        setError(e.message);
        setBrokerHealthy(false);              // T3: 조회 실패 = KIS 헬스 불량
        setLoading(false);                    // 각 시도 종료(loading 무한대 금지 — 가짜 0
        //   은 ready 게이트로 막는다; loading 은 '최초 시도 진행 중'만 의미).
        // T2: 고정 5초 → 지수 백오프(5→10→20→40→60 상한). 토큰 백오프(60s)와 박자 분리.
        if (attempts < MAX_ATTEMPTS) {
          const delay = Math.min(60000, 5000 * 2 ** (attempts - 1));
          timer = setTimeout(attempt, delay);
        }
      }
    };
    attempt();
    return () => { cancelled = true; clearTimeout(timer); };
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
    loading, error, ready, stale, asOf, brokerHealthy,
  };
}
