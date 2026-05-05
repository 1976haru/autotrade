import { useCallback, useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";


export const AUDIT_PAGE_SIZE = 50;


function useAuditList(fetcher) {
  const [items,   setItems]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetcher();
      setItems(list);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [fetcher]);

  useEffect(() => {
    // refresh is async; setState happens after the awaited fetch.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  return { items, loading, error, refresh };
}


// 064: 사고 분석 시 50건을 넘어 거슬러 올라가야 하는 경우를 위해 load-more 패턴.
// fetchPage({offset, limit}) → row[]. 백엔드가 정확히 limit개를 돌려주면 더 있을
// 가능성이 있다고 보고 hasMore=true; 그보다 적으면 끝까지 내려간 것으로 간주.
// AI/Backtest 처럼 페이징이 필요 없는 도메인은 기존 useAuditList 그대로 둔다.
function usePaginatedAuditList(fetchPage) {
  const [items,        setItems]        = useState([]);
  const [loading,      setLoading]      = useState(true);
  const [loadingMore,  setLoadingMore]  = useState(false);
  const [hasMore,      setHasMore]      = useState(true);
  const [error,        setError]        = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchPage({ offset: 0, limit: AUDIT_PAGE_SIZE });
      setItems(list);
      setHasMore(list.length === AUDIT_PAGE_SIZE);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [fetchPage]);

  const loadMore = useCallback(async () => {
    // setItems uses functional update so we don't need items in deps —
    // double-clicks while loading are filtered by the loadingMore guard.
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const offset = items.length;
      const list = await fetchPage({ offset, limit: AUDIT_PAGE_SIZE });
      setItems((prev) => [...prev, ...list]);
      setHasMore(list.length === AUDIT_PAGE_SIZE);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingMore(false);
    }
  }, [fetchPage, items.length, loadingMore, hasMore]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  return { items, loading, loadingMore, hasMore, error, refresh, loadMore };
}


export const useAiAudits     = () => useAuditList(() => backendApi.listAiAudits(50));
export const useBacktestRuns = () => useAuditList(() => backendApi.listBacktestRuns(50));

// 페이지 fetcher는 반드시 useCallback으로 stable 식별자를 유지해야 한다 —
// usePaginatedAuditList가 fetchPage를 deps로 잡고 있어 매 렌더 새 함수가 들어
// 오면 useEffect가 무한 재실행돼 mount fetch가 반복된다.
export const useOrderAudits = () => {
  const fetchPage = useCallback(
    ({ offset, limit }) => backendApi.listOrderAudits({ offset, limit }),
    [],
  );
  return usePaginatedAuditList(fetchPage);
};

// 백엔드 측은 046의 /api/risk/emergency-stop/history를 그대로 재사용 — 이
// 엔드포인트가 이미 id desc 정렬 + limit/offset 페이징을 지원해서 audit
// 도메인에서 별도 라우트를 만들 필요가 없다.
export const useEmergencyStopAudits = () => {
  const fetchPage = useCallback(
    ({ offset, limit }) => backendApi.emergencyStopHistory({ offset, limit }),
    [],
  );
  return usePaginatedAuditList(fetchPage);
};
