import { useCallback, useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";

const REFRESH_MS = 5000;

/**
 * 승인 큐 훅 — backend `/api/approvals` (PENDING)와 `/api/approvals/history`
 * (decided)를 함께 관리.
 *
 * - pending: 주기적 폴링 (5s)
 * - history: 결정 액션(approve/reject/cancel) 직후 + 마운트 시 fetch.
 *   주기적 폴링은 안 함 — decided rows는 새로 추가될 때(=결정 시점)에만
 *   변하므로 액션 직후만 갱신해도 충분하고 트래픽이 절감된다.
 */
export function useApprovals() {
  const [pending, setPending] = useState([]);
  const [history, setHistory] = useState([]);
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

  const refreshHistory = useCallback(async (status) => {
    try {
      const list = await backendApi.listApprovalHistory({ status });
      setHistory(list);
    } catch (e) {
      // history 실패는 PENDING 큐 사용에 영향 없음 — 같은 error 슬롯에만 노출.
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    // refresh / refreshHistory are async; setState is after await, not synchronous.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
    refreshHistory();
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh, refreshHistory]);

  const approve = useCallback(async (id, note) => {
    setBusy(true);
    try {
      await backendApi.approveApproval(id, note ? { note } : null);
      await refresh();
      await refreshHistory();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [refresh, refreshHistory]);

  const reject = useCallback(async (id, note) => {
    setBusy(true);
    try {
      await backendApi.rejectApproval(id, note ? { note } : null);
      await refresh();
      await refreshHistory();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [refresh, refreshHistory]);

  const cancel = useCallback(async (id, note) => {
    setBusy(true);
    try {
      await backendApi.cancelApproval(id, note ? { note } : null);
      await refresh();
      await refreshHistory();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [refresh, refreshHistory]);

  return { pending, history, loading, error, busy,
           refresh, refreshHistory, approve, reject, cancel };
}
