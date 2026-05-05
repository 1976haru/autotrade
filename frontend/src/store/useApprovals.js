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
  // 075: per-row session memory of the last failed approve attempt. Modal
  // close clears the inline error (072), but the row itself loses the
  // signal that something went wrong. This map keeps {at, message} keyed by
  // approval id so the row can render a small "X분 전 거부" hint.
  const [lastFailures, setLastFailures] = useState({});

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

  // 072: each action returns {ok, message?} so the modal can stay open with
  // the failure message inline (typically 070 risk_check_failed_at_approve)
  // instead of dismissing into a top-of-page error slot. The same error is
  // also pushed to `error` for callers that don't read the return value.
  const approve = useCallback(async (id, decision) => {
    setBusy(true);
    try {
      await backendApi.approveApproval(id, _normalize(decision));
      await refresh();
      await refreshHistory();
      // Success → clear any stale failure note for this id.
      setLastFailures((prev) => {
        if (!(id in prev)) return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      });
      return { ok: true };
    } catch (e) {
      setError(e.message);
      // 075: stamp the row so the operator can see the row kept failing even
      // after closing the dialog. Stays only for this React session — refresh
      // wipes it, which is fine because the broker conditions may have
      // changed too.
      setLastFailures((prev) => ({
        ...prev, [id]: { at: Date.now(), message: e.message },
      }));
      return { ok: false, message: e.message };
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
      return { ok: true };
    } catch (e) {
      setError(e.message);
      return { ok: false, message: e.message };
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
      return { ok: true };
    } catch (e) {
      setError(e.message);
      return { ok: false, message: e.message };
    } finally {
      setBusy(false);
    }
  }, [refresh, refreshHistory]);

  // 같은 사유로 여러 건을 한 번에 취소할 때 (065: stale 일괄 청소). 백엔드는
  // 단건 라우트만 있어 순차 호출이지만 refresh/refreshHistory는 끝에 한 번만
  // 돌려 단순 N회 cancel() 루프 대비 N+1배 트래픽을 줄인다. 중간에 한 건이
  // 실패하면 거기서 멈추고 error만 surface — 운영자가 다시 실행하면 PENDING으로
  // 남아있는 항목만 자동으로 추려진다.
  const cancelMany = useCallback(async (ids, decision) => {
    if (!ids || ids.length === 0) return { ok: true };
    setBusy(true);
    try {
      const payload = _normalize(decision);
      for (const id of ids) {
        await backendApi.cancelApproval(id, payload);
      }
      await refresh();
      await refreshHistory();
      return { ok: true };
    } catch (e) {
      // refresh() clears `error` on its success path, so to keep the cancel
      // failure visible we have to refresh first and then re-set the error.
      const msg = e.message;
      await refresh();
      await refreshHistory();
      setError(msg);
      return { ok: false, message: msg };
    } finally {
      setBusy(false);
    }
  }, [refresh, refreshHistory]);

  return { pending, history, loading, error, busy,
           lastFailures,
           refresh, refreshHistory, approve, reject, cancel, cancelMany };
}
