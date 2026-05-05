import { useCallback, useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";


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


export const useOrderAudits  = () => useAuditList(() => backendApi.listOrderAudits(50));
export const useAiAudits     = () => useAuditList(() => backendApi.listAiAudits(50));
export const useBacktestRuns = () => useAuditList(() => backendApi.listBacktestRuns(50));
// 백엔드 측은 046의 /api/risk/emergency-stop/history를 그대로 재사용 — 이
// 엔드포인트가 이미 id desc 정렬 + limit/offset 페이징을 지원해서 audit
// 도메인에서 별도 라우트를 만들 필요가 없다.
export const useEmergencyStopAudits = () =>
  useAuditList(() => backendApi.emergencyStopHistory({ limit: 50 }));
