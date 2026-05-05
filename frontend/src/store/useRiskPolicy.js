import { useCallback, useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";

/**
 * 백엔드 RiskPolicy 스냅샷 + 긴급 정지 토글 + 토글 이력.
 *
 * - policy: 부팅 시 fetch, read-only (편집은 향후 추가).
 * - emergencyStop: 토글 응답에서 받은 런타임 플래그. 백엔드 재시작 시 OFF로
 *                  리셋되는 것이 의도된 설계 — 운영자가 재시작 후 의도적으로
 *                  다시 켜야 한다.
 * - history:      `/api/risk/emergency-stop/history`에서 가져온 토글 이력.
 *                 토글 직후 자동 새로고침 + 마운트 시 fetch.
 */
export function useRiskPolicy() {
  const [policy,  setPolicy]  = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [emergencyStop, setEmergencyStop] = useState(false);
  const [busy,    setBusy]    = useState(false);

  const refreshHistory = useCallback(async () => {
    try {
      const list = await backendApi.emergencyStopHistory();
      setHistory(list);
    } catch (e) {
      // History 실패는 정책 표시에 영향 없음 — 같은 error 슬롯에만 노출.
      setError(e.message);
    }
  }, []);

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
    // refreshHistory is async — setState happens after the awaited fetch.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshHistory();
    return () => { cancelled = true; };
  }, [refreshHistory]);

  const toggleEmergency = useCallback(async (decision) => {
    // decision: { decided_by?, note? } | null | undefined.
    // Only forward keys with non-empty values so the audit row records null
    // for fields the operator left blank rather than "".
    const payload = {};
    if (decision?.decided_by) payload.decided_by = decision.decided_by;
    if (decision?.note)       payload.note       = decision.note;
    setBusy(true);
    try {
      const res = await backendApi.setEmergencyStop(
        !emergencyStop,
        Object.keys(payload).length ? payload : null,
      );
      setEmergencyStop(res.emergency_stop);
      await refreshHistory();
      return { ok: true };
    } catch (e) {
      setError(e.message);
      return { ok: false, message: e.message };
    } finally {
      setBusy(false);
    }
  }, [emergencyStop, refreshHistory]);

  return { policy, history, loading, error, emergencyStop, busy,
           toggleEmergency, refreshHistory };
}
