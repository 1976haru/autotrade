import { useCallback, useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";


/**
 * LiveStrategyEngine HTTP 엔드포인트(/api/strategies/*) 래퍼.
 * - 초기 로드 시 status fetch
 * - configure / tick / reset 후 status 자동 갱신
 * - lastResult: 최근 tick 응답 (signal + intended_order + routing)
 */
export function useLiveEngine() {
  const [status,       setStatus]       = useState(null);
  const [lastResult,   setLastResult]   = useState(null);
  const [replaySummary,setReplaySummary]= useState(null);
  const [busy,         setBusy]         = useState(false);
  const [error,        setError]        = useState("");

  const refresh = useCallback(async () => {
    try {
      const s = await backendApi.engineStatus();
      setStatus(s);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    // refresh is async; setState happens after the awaited fetch.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  const configure = useCallback(async (req) => {
    setBusy(true);
    try {
      const s = await backendApi.engineConfigure(req);
      setStatus(s);
      setLastResult(null);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, []);

  const tick = useCallback(async (bar, submit) => {
    setBusy(true);
    try {
      const result = await backendApi.engineTick({ bar, submit });
      setLastResult(result);
      setStatus((prev) => prev ? {
        ...prev,
        bars_seen: result.bars_seen,
        holding:   result.holding,
      } : prev);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, []);

  const reset = useCallback(async () => {
    setBusy(true);
    try {
      const s = await backendApi.engineReset();
      setStatus(s);
      setLastResult(null);
      setReplaySummary(null);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, []);

  const replay = useCallback(async (req) => {
    setBusy(true);
    try {
      const summary = await backendApi.engineReplay(req);
      setReplaySummary(summary);
      setStatus((prev) => prev ? {
        ...prev,
        bars_seen: summary.bars_seen,
        holding:   summary.holding,
      } : prev);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, []);

  return { status, lastResult, replaySummary, busy, error,
           refresh, configure, tick, reset, replay };
}
