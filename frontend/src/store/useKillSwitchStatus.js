import { useCallback, useEffect, useRef, useState } from "react";
import { backendApi } from "../services/backend/client";

// incident: 07-13 13:08 KST 오퍼레이터가 크래시 대응으로 킬스위치를 켠 뒤 끄는
// 것을 잊어, 07-14 하루 전체(BUY 185건 + SELL 239건) 주문이 전부
// "emergency stop is enabled"로 거부됐다. 화면 어디에도 "지금 켜져 있다"는
// 표시가 없었던 게 근본 원인 — useRiskPolicy.emergencyStop은 *이 세션에서
// 토글한 값*만 반영하고 서버 실제 상태를 폴링하지 않는다. 본 훅이 그 갭을
// 메운다: 서버 상태를 주기적으로 poll해 최상단 배너가 항상 실제 상태를 반영.
//
// GET /api/risk/emergency-stop/status는 RiskManager in-memory 상태 + DB
// 후보 카운트만 읽는 순수 조회라 broker(KIS)를 호출하지 않는다 — EGW
// rate-limit 예산과 무관하다.
export const KS_ACTIVE_POLL_MS = 5_000;   // level !== OFF — 배너가 살아있는 동안은 빠르게
export const KS_IDLE_POLL_MS   = 30_000;  // level === OFF — 평상시
export const KS_HIDDEN_POLL_MS = 60_000;  // 탭이 backgrounded

export function computeKillSwitchPollIntervalMs({ level, hidden = false }) {
  if (hidden) return KS_HIDDEN_POLL_MS;
  if (level && level !== "OFF") return KS_ACTIVE_POLL_MS;
  return KS_IDLE_POLL_MS;
}

/**
 * 킬스위치(emergency-stop) 상태를 주기적으로 폴링. App shell 최상단 배너가
 * 어떤 탭을 보고 있든 실제 서버 상태를 놓치지 않도록 하는 것이 목적.
 */
export function useKillSwitchStatus() {
  const [status,  setStatus]  = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [busy,    setBusy]    = useState(false);

  const _levelRef  = useRef(null);
  const _hiddenRef = useRef(
    typeof document !== "undefined" && document.visibilityState === "hidden"
  );

  const refresh = useCallback(async () => {
    try {
      const s = await backendApi.emergencyStopStatus();
      _levelRef.current = s?.level ?? null;
      setStatus(s);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const handler = () => {
      const wasHidden = _hiddenRef.current;
      _hiddenRef.current = document.visibilityState === "hidden";
      if (wasHidden && !_hiddenRef.current) refresh();
    };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, [refresh]);

  useEffect(() => {
    let cancelled = false;
    let timerId = null;
    const scheduleNext = () => {
      if (cancelled) return;
      const ms = computeKillSwitchPollIntervalMs({
        level:  _levelRef.current,
        hidden: _hiddenRef.current,
      });
      timerId = setTimeout(async () => {
        if (cancelled) return;
        await refresh();
        scheduleNext();
      }, ms);
    };
    // scheduleNext()는 첫 refresh()가 끝난 *뒤* 호출해야 한다 — 그렇지 않으면
    // _levelRef.current가 아직 null인 채로 첫 간격이 계산돼(예: LEVEL_1로
    // 시작해도 idle 30s가 잡힘) 배너가 켜진 직후 첫 재폴링이 예상보다 느려진다.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    (async () => {
      await refresh();
      scheduleNext();
    })();
    return () => {
      cancelled = true;
      if (timerId) clearTimeout(timerId);
    };
  }, [refresh]);

  const disable = useCallback(async (decision) => {
    const payload = {};
    if (decision?.decided_by) payload.decided_by = decision.decided_by;
    if (decision?.note)       payload.note       = decision.note;
    setBusy(true);
    try {
      await backendApi.setEmergencyStop(false, Object.keys(payload).length ? payload : null);
      await refresh();
      return { ok: true };
    } catch (e) {
      setError(e.message);
      return { ok: false, message: e.message };
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  return { status, loading, error, busy, disable, refresh };
}
