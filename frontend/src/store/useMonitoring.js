import { useEffect, useRef, useState } from "react";
import { backendApi } from "../services/backend/client";

/**
 * useMonitoring — read-only fetch of /api/monitoring/metrics.
 *
 * #70: 시스템 안정성 우선 — fetch 실패도 throw하지 않고 error 상태로 carry.
 * UI는 backend down 시 회색 "측정 불가" 표시로 fallback.
 *
 * 정기 polling은 30s 주기. 탭 가시성 변경 시 즉시 refresh.
 */
export function useMonitoring({ intervalMs = 30_000 } = {}) {
  const [snapshot, setSnapshot] = useState(null);
  const [loading,  setLoading]  = useState(true);
  const [error,    setError]    = useState("");

  const cancelledRef = useRef(false);
  const timerRef     = useRef(null);

  const load = async () => {
    try {
      const s = await backendApi.monitoringMetrics();
      if (!cancelledRef.current) {
        setSnapshot(s);
        setError("");
      }
    } catch (e) {
      if (!cancelledRef.current) {
        setError(e?.message || "monitoring fetch failed");
      }
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  };

  useEffect(() => {
    cancelledRef.current = false;
    load();
    timerRef.current = window.setInterval(load, intervalMs);
    return () => {
      cancelledRef.current = true;
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs]);

  return { snapshot, loading, error, refresh: load };
}
