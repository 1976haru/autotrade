import { useCallback, useEffect, useRef, useState } from "react";
import { backendApi } from "../services/backend/client";

// 100: adaptive polling. 빈 큐를 5초마다 두드리는 건 트래픽만 낭비고 — 결재
// 대기가 정말 없는 시각대(장 마감 후, 새벽 등)에는 폴링을 늦춰도 무방하다.
//
//  - active(5s): 큐가 비어있지 않거나, 마지막 활동(큐 변화/액션)이 IDLE_THRESHOLD
//                안에 있을 때. 결재 흐름이 진행 중인 상태.
//  - idle(30s): 큐가 비어있고 IDLE_THRESHOLD 동안 변화 없을 때.
//
// 결재 도착이 감지되는 latency가 idle 시 최대 30s로 늘어나지만, BottomNav 배지가
// 첫 active tick에서 곧장 5s로 돌아가는 만큼 운영 영향은 미미. 사이트가 늘
// 비어 있는 경우(SIM 단독 운영) 백엔드 호출이 6배 줄어든다.
export const ACTIVE_POLL_MS = 5000;
export const IDLE_POLL_MS = 30_000;
export const IDLE_THRESHOLD_MS = 5 * 60 * 1000;

export function computePollIntervalMs({ pendingCount, lastActivityAt, now }) {
  if (pendingCount > 0) return ACTIVE_POLL_MS;
  if (now - lastActivityAt < IDLE_THRESHOLD_MS) return ACTIVE_POLL_MS;
  return IDLE_POLL_MS;
}

// 085: backend caps each fetch at 50 by default; matches the page size used
// by 064's audit pagination so the UX is consistent across tabs.
const HISTORY_PAGE_SIZE = 50;

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
  // 085: pagination state for the history list. hasMore tracks whether the
  // last page came back exactly full (more likely available); loadingMore
  // guards against double-clicks. Mirror of 064's audit pattern.
  const [historyHasMore,     setHistoryHasMore]     = useState(true);
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);

  // 100: refs feed the adaptive scheduler — accessing pending/lastActivity
  // through state would put them in the polling effect's deps and re-spawn the
  // timer chain on every fetch. Refs sidestep that while still letting the
  // schedule react to live data.
  const _pendingCountRef  = useRef(0);
  const _lastActivityRef  = useRef(Date.now());

  const refresh = useCallback(async () => {
    try {
      const list = await backendApi.listApprovals();
      // 100: any change to the pending list — count or content — counts as
      // activity. Only resetting on count change would miss replacement (an
      // approval cleared and a new one queued in the same tick) which we
      // care about because such a tick belongs in active mode.
      const prevCount = _pendingCountRef.current;
      if (list.length > 0 || prevCount > 0) {
        _lastActivityRef.current = Date.now();
      }
      _pendingCountRef.current = list.length;
      setPending(list);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // refreshHistory always resets to page 1. Post-action callers (after
  // approve/reject/cancel) want a fresh top page anyway because the row that
  // just transitioned should appear at the top. Operators who had paged
  // deeper lose that scroll position — acceptable trade for consistency.
  const refreshHistory = useCallback(async (status) => {
    try {
      const list = await backendApi.listApprovalHistory({
        status, limit: HISTORY_PAGE_SIZE, offset: 0,
      });
      setHistory(list);
      setHistoryHasMore(list.length === HISTORY_PAGE_SIZE);
    } catch (e) {
      // history 실패는 PENDING 큐 사용에 영향 없음 — 같은 error 슬롯에만 노출.
      setError(e.message);
    }
  }, []);

  // 085: append the next page. setHistory uses a functional update to avoid
  // capturing a stale list reference; the loadingMore guard prevents
  // double-clicks while the fetch is in flight.
  const loadMoreHistory = useCallback(async () => {
    if (historyLoadingMore || !historyHasMore) return;
    setHistoryLoadingMore(true);
    try {
      const offset = history.length;
      const list = await backendApi.listApprovalHistory({
        limit: HISTORY_PAGE_SIZE, offset,
      });
      setHistory((prev) => [...prev, ...list]);
      setHistoryHasMore(list.length === HISTORY_PAGE_SIZE);
    } catch (e) {
      setError(e.message);
    } finally {
      setHistoryLoadingMore(false);
    }
  }, [history.length, historyHasMore, historyLoadingMore]);

  // 100: adaptive scheduler — setTimeout 재귀라 매 fetch 직후 다음 간격을
  // 다시 계산한다. setInterval로는 동적 변경이 어렵고, hook 재마운트로 강제
  // 재시작하면 다른 effect deps까지 영향을 받는다.
  useEffect(() => {
    let cancelled = false;
    let timerId = null;
    const scheduleNext = () => {
      if (cancelled) return;
      const ms = computePollIntervalMs({
        pendingCount:   _pendingCountRef.current,
        lastActivityAt: _lastActivityRef.current,
        now: Date.now(),
      });
      timerId = setTimeout(async () => {
        if (cancelled) return;
        await refresh();
        scheduleNext();
      }, ms);
    };
    // 초기 fetch는 즉시. setState in effect는 await 다음이라 동기 X.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
    refreshHistory();
    scheduleNext();
    return () => {
      cancelled = true;
      if (timerId) clearTimeout(timerId);
    };
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
  //
  // 100: an action implies operator activity, so we mark the schedule active
  // even if refresh() then settles to an empty queue. Without this an operator
  // who just cleared a backlog would drop straight into idle mode.
  const approve = useCallback(async (id, decision) => {
    _lastActivityRef.current = Date.now();
    setBusy(true);
    try {
      await backendApi.approveApproval(id, _normalize(decision));
      await refresh();
      await refreshHistory();
      return { ok: true };
    } catch (e) {
      setError(e.message);
      // 076: backend appends to PendingApproval.attempts on re-eval-blocked
      // failure; refresh so the local row picks up the new attempts entry
      // immediately instead of waiting for the 5s polling tick.
      await refresh();
      return { ok: false, message: e.message };
    } finally {
      setBusy(false);
    }
  }, [refresh, refreshHistory]);

  const reject = useCallback(async (id, decision) => {
    _lastActivityRef.current = Date.now();
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
    _lastActivityRef.current = Date.now();
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
    _lastActivityRef.current = Date.now();
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
           historyHasMore, historyLoadingMore, loadMoreHistory,
           refresh, refreshHistory, approve, reject, cancel, cancelMany };
}
