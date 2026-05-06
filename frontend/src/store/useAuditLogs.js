import { useCallback, useEffect, useRef, useState } from "react";
import { backendApi } from "../services/backend/client";

import { computePollIntervalMs } from "./useApprovals";


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
      // 105: useOrderAudits의 polling callback이 새 top id를 즉시 봐야 schedule
      // 결정에서 stale activity를 피한다. setItems는 React commit이 끝난 후
      // 다음 useEffect에서 비동기 반영되므로, 그 사이 callback이 schedule을
      // 결정하면 ref가 stale인 채로 idle을 또 고른다. list를 return해 호출자가
      // 직접 받아 처리할 수 있게 한다 — 기존 호출자(결과 안 보는 코드)는 영향 X.
      return list;
    } catch (e) {
      setError(e.message);
      return null;
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
//
// 105: order audits에 adaptive polling 추가. 100 useApprovals 패턴 재사용
// (computePollIntervalMs export). Dashboard의 24h count + StatusPin idle 경고
// + AuditLog timeline이 별도 reload 없이 최신화된다. 활동 정의:
// "items의 top row id가 변했는가" — 백엔드가 created_at desc로 내려주므로
// 새 주문 발생 = top id 변화. 변화 없이 IDLE_THRESHOLD(5분)이 지나면 30s 모드.
export const useOrderAudits = () => {
  const fetchPage = useCallback(
    ({ offset, limit }) => backendApi.listOrderAudits({ offset, limit }),
    [],
  );
  const result = usePaginatedAuditList(fetchPage);

  const _lastTopIdRef    = useRef(null);
  const _lastActivityRef = useRef(Date.now());

  useEffect(() => {
    const topId = result.items[0]?.id ?? null;
    // 첫 mount 직후 _lastTopIdRef는 null이고, items 첫 도착도 활동으로 셈한다 —
    // 그 시점이 mount Date.now()와 거의 같아 어차피 active 윈도우 안이라 효과
    // 동일하지만, idle 동안 빈 result로 polling하다가 마침내 row를 받은 경우엔
    // 명백히 새 활동이고 5s 모드로 복귀해야 한다.
    if (topId !== null && topId !== _lastTopIdRef.current) {
      _lastActivityRef.current = Date.now();
      _lastTopIdRef.current = topId;
    }
  }, [result.items]);

  useEffect(() => {
    let cancelled = false;
    let timerId = null;
    const scheduleNext = () => {
      if (cancelled) return;
      // pendingCount=0 — order audits에는 큐 개념이 없으므로 lastActivityAt만으로
      // 판단. computePollIntervalMs가 그 경로(activity within threshold → active,
      // 그 외 → idle)를 제공한다.
      const ms = computePollIntervalMs({
        pendingCount: 0,
        lastActivityAt: _lastActivityRef.current,
        now: Date.now(),
      });
      timerId = setTimeout(async () => {
        if (cancelled) return;
        const list = await result.refresh();
        // schedule을 결정하기 *전에* 활동 신호를 즉시 반영. useEffect#1는
        // mount 첫 fetch (refresh가 useEffect 안에서 호출되는 그 경로)를 위한
        // fallback으로 남겨둔다.
        const topId = list && list[0] ? list[0].id : null;
        if (topId !== null && topId !== _lastTopIdRef.current) {
          _lastActivityRef.current = Date.now();
          _lastTopIdRef.current = topId;
        }
        scheduleNext();
      }, ms);
    };
    scheduleNext();
    return () => {
      cancelled = true;
      if (timerId) clearTimeout(timerId);
    };
  }, [result.refresh]);

  return result;
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
