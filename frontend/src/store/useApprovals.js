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

  // 결재 모달이 보내는 { decided_by?, note? } 중 빈 값은 제거해 백엔드 audit
  // 행에 ""가 아니라 null이 저장되도록 한다 — useRiskPolicy.toggleEmergency와
  // 동일한 정규화 규칙.
  const _normalize = (decision) => {
    const payload = {};
    if (decision?.decided_by) payload.decided_by = decision.decided_by;
    if (decision?.note)       payload.note       = decision.note;
    return Object.keys(payload).length ? payload : null;
  };

  const approve = useCallback(async (id, decision) => {
    setBusy(true);
    try {
      await backendApi.approveApproval(id, _normalize(decision));
      await refresh();
      await refreshHistory();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [refresh, refreshHistory]);

  const reject = useCallback(async (id, decision) => {
    setBusy(true);
    try {
      await backendApi.rejectApproval(id, _normalize(decision));
      await refresh();
      await refreshHistory();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [refresh, refreshHistory]);

  const cancel = useCallback(async (id, decision) => {
    setBusy(true);
    try {
      await backendApi.cancelApproval(id, _normalize(decision));
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
