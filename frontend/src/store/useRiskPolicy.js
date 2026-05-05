import { useCallback, useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";

/**
 * 백엔드 RiskPolicy 스냅샷과 긴급 정지 토글.
 * 정책 자체는 read-only (편집은 향후 backend POST 엔드포인트 추가 후).
 * emergency_stop은 토글 응답에서 받은 값을 로컬에 보관 — 페이지 새로고침 시 OFF로 가정.
 */
export function useRiskPolicy() {
  const [policy,  setPolicy]  = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [emergencyStop, setEmergencyStop] = useState(false);
  const [busy,    setBusy]    = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const p = await backendApi.getRiskPolicy();
        if (!cancelled) setPolicy(p);
      } catch (e) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const toggleEmergency = useCallback(async () => {
    setBusy(true);
    try {
      const res = await backendApi.setEmergencyStop(!emergencyStop);
      setEmergencyStop(res.emergency_stop);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [emergencyStop]);

  return { policy, loading, error, emergencyStop, busy, toggleEmergency };
}
