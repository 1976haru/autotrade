import { useCallback, useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";


/**
 * LiveStrategyEngine HTTP 엔드포인트(/api/strategies/*) 래퍼.
 * - 초기 로드 시 status + registry fetch
 * - configure / tick / reset 후 status 자동 갱신
 * - lastResult: 최근 tick 응답 (signal + intended_order + routing)
 * - registry: 서버가 알려주는 전략 목록 + 각 전략의 파라미터 스키마
 *   (configure 폼을 하드코딩 없이 렌더링하기 위함)
 */
export function useLiveEngine() {
  const [status,       setStatus]       = useState(null);
  const [registry,     setRegistry]     = useState(null);
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

  const refreshRegistry = useCallback(async () => {
    try {
      const r = await backendApi.engineRegistry();
      setRegistry(r);
    } catch (e) {
      // Registry fetch 실패는 치명적이지 않다 — 폼이 비어 보일 뿐이므로
      // status error 와는 분리해서 동일한 error 슬롯에만 노출한다.
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    // refresh + refreshRegistry는 async — setState는 await 이후에만 발생.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
    refreshRegistry();
  }, [refresh, refreshRegistry]);

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

  return { status, registry, lastResult, replaySummary, busy, error,
           refresh, refreshRegistry, configure, tick, reset, replay };
}
