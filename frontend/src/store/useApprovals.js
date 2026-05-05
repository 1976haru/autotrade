import { useCallback, useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";

const REFRESH_MS = 5000;

/**
 * 승인 대기 큐 훅 — backend `/api/approvals`를 주기적으로 폴링.
 * approve/reject 후에는 즉시 한 번 더 새로고침.
 */
export function useApprovals() {
  const [pending, setPending] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [busy,    setBusy]    = useState(false);

  const refresh = useCallback(async () => {
    try {
      const list = await backendApi.listApprovals();
      setPending(list);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // refresh is async; setState happens after the awaited fetch, not synchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const approve = useCallback(async (id, note) => {
    setBusy(true);
    try {
      await backendApi.approveApproval(id, note ? { note } : null);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const reject = useCallback(async (id, note) => {
    setBusy(true);
    try {
      await backendApi.rejectApproval(id, note ? { note } : null);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  return { pending, loading, error, busy, refresh, approve, reject };
}
