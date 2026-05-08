import { useCallback, useEffect, useState } from "react";

import { backendApi } from "../services/backend/client";


/**
 * 관심종목 훅 (#18)
 *
 * - watchlists: full list with item counts
 * - summary: dashboard용 요약 (active + top 5)
 * - 각종 mutate 함수는 mutate 후 자동으로 list/summary 재조회
 *
 * Watchlist는 universe 후보군이며 주문 신호가 아니다 — 본 hook은 RiskManager /
 * PermissionGate / OrderExecutor 어떤 분기와도 연결되지 않는다.
 */
export function useWatchlists() {
  const [watchlists, setWatchlists] = useState([]);
  const [maxItems,         setMaxItems]         = useState(200);
  const [recommendedItems, setRecommendedItems] = useState(50);
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const body = await backendApi.listWatchlists();
      setWatchlists(body.watchlists || []);
      if (typeof body.max_items         === "number") setMaxItems(body.max_items);
      if (typeof body.recommended_items === "number") setRecommendedItems(body.recommended_items);
    } catch (e) {
      setError(e.message || "관심종목을 불러오지 못했어요.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  const create = useCallback(async (req) => {
    await backendApi.createWatchlist(req);
    await refresh();
  }, [refresh]);

  const patch = useCallback(async (id, req) => {
    await backendApi.patchWatchlist(id, req);
    await refresh();
  }, [refresh]);

  const remove = useCallback(async (id) => {
    await backendApi.deleteWatchlist(id);
    await refresh();
  }, [refresh]);

  const addItem = useCallback(async (id, req) => {
    await backendApi.addWatchlistItem(id, req);
    await refresh();
  }, [refresh]);

  const removeItem = useCallback(async (id, itemId) => {
    await backendApi.removeWatchlistItem(id, itemId);
    await refresh();
  }, [refresh]);

  const importCsv = useCallback(async (id, csv) => {
    const result = await backendApi.importWatchlistCsv(id, csv);
    await refresh();
    return result;
  }, [refresh]);

  return {
    watchlists, maxItems, recommendedItems, loading, error,
    refresh, create, patch, remove, addItem, removeItem, importCsv,
  };
}


/**
 * Dashboard 요약용 — 활성 watchlist + 종목 수 + top 5만.
 * 백엔드 미연결 시 mock summary를 반환해 빈 화면 대신 안내 카드를 보여준다.
 */
export function useWatchlistSummary() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const body = await backendApi.watchlistSummary();
      setSummary(body);
    } catch (e) {
      setError(e.message || "관심종목 요약을 불러오지 못했어요.");
      setSummary(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  return { summary, loading, error, refresh };
}
