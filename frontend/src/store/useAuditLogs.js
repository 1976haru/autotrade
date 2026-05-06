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
      // 114: list를 return — useAdaptivePollingByTopId의 polling callback이
      // setState commit 시점을 기다리지 않고 즉시 top id를 검사할 수 있게.
      // 105와 동일한 race-free 패턴.
      return list;
    } catch (e) {
      setError(e.message);
      return null;
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


// 114: AI 호출/백테스트 실행도 109 useAdaptivePollingByTopId 적용. AI는
// LIVE_AI_ASSIST 흐름에서 백엔드가 비동기로 row를 추가하므로 운영자가 별도
// reload 없이 새 호출을 본다. backtest는 보통 사용자 수동 실행이라 idle 30s에
// 머무르지만, 향후 background 실행이 도입되면 즉시 표면화. 통합 비용은 두
// 추가 hook 호출만으로 끝나 충분히 가벼움.
export const useAiAudits = () => {
  const result = useAuditList(useCallback(() => backendApi.listAiAudits(50), []));
  useAdaptivePollingByTopId(result.refresh, result.items);
  return result;
};

export const useBacktestRuns = () => {
  const result = useAuditList(useCallback(() => backendApi.listBacktestRuns(50), []));
  useAdaptivePollingByTopId(result.refresh, result.items);
  return result;
};

// 페이지 fetcher는 반드시 useCallback으로 stable 식별자를 유지해야 한다 —
// usePaginatedAuditList가 fetchPage를 deps로 잡고 있어 매 렌더 새 함수가 들어
// 오면 useEffect가 무한 재실행돼 mount fetch가 반복된다.
//
// 105/109: 100 useApprovals 패턴 재사용한 adaptive polling. 활동 정의:
// "items의 top row id가 변했는가" — 백엔드가 id desc로 내려주므로 새 row
// 발생 = top id 변화. 변화 없이 IDLE_THRESHOLD(5분)이 지나면 30s 모드.
//
// 109: useOrderAudits와 useEmergencyStopAudits가 동일 패턴이라 helper로 추출.
// items 변화 useEffect는 mount 첫 fetch (refresh가 useEffect 안에서 호출되는
// 그 경로)를 위한 fallback이고, polling callback은 list를 직접 받아 schedule
// 직전에 ref를 갱신해 React commit timing race를 회피.
function useAdaptivePollingByTopId(refresh, items) {
  const _lastTopIdRef    = useRef(null);
  const _lastActivityRef = useRef(Date.now());
  // 125: visibility 추적 — 100/useApprovals와 동일 패턴.
  const _hiddenRef = useRef(
    typeof document !== "undefined" && document.visibilityState === "hidden"
  );

  useEffect(() => {
    const topId = items[0]?.id ?? null;
    if (topId !== null && topId !== _lastTopIdRef.current) {
      _lastActivityRef.current = Date.now();
      _lastTopIdRef.current = topId;
    }
  }, [items]);

  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const handler = () => {
      const wasHidden = _hiddenRef.current;
      _hiddenRef.current = document.visibilityState === "hidden";
      if (wasHidden && !_hiddenRef.current) {
        // visible 복귀 — 다음 cycle 즉시 active로.
        _lastActivityRef.current = Date.now();
      }
    };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timerId = null;
    const scheduleNext = () => {
      if (cancelled) return;
      const ms = computePollIntervalMs({
        pendingCount: 0,  // 큐 개념 없음 — lastActivityAt만으로 판단
        lastActivityAt: _lastActivityRef.current,
        now: Date.now(),
        hidden: _hiddenRef.current,
      });
      timerId = setTimeout(async () => {
        if (cancelled) return;
        const list = await refresh();
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
  }, [refresh]);
}


// 105: Dashboard의 24h count + StatusPin idle 경고 + AuditLog timeline이 별도
// reload 없이 최신화된다.
export const useOrderAudits = () => {
  const fetchPage = useCallback(
    ({ offset, limit }) => backendApi.listOrderAudits({ offset, limit }),
    [],
  );
  const result = usePaginatedAuditList(fetchPage);
  useAdaptivePollingByTopId(result.refresh, result.items);
  return result;
};


// 백엔드 측은 046의 /api/risk/emergency-stop/history를 그대로 재사용 — 이
// 엔드포인트가 이미 id desc 정렬 + limit/offset 페이징을 지원해서 audit
// 도메인에서 별도 라우트를 만들 필요가 없다.
//
// 109: 105와 동일한 adaptive polling 적용. 긴급정지 토글은 빈도가 낮아 보통은
// idle 30s에 머물지만, 토글 직후엔 새 top id가 들어와 active 5s로 복귀해
// EmergencyStopStuckBanner / Activity24hCard / dashboard pin이 즉시 반응한다.
export const useEmergencyStopAudits = () => {
  const fetchPage = useCallback(
    ({ offset, limit }) => backendApi.emergencyStopHistory({ offset, limit }),
    [],
  );
  const result = usePaginatedAuditList(fetchPage);
  useAdaptivePollingByTopId(result.refresh, result.items);
  return result;
};
