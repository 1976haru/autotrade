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
