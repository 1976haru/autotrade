// 새 홈 ▶시작/⏸중지를 *실제* Auto Paper Loop에 배선하기 위한 공용 훅.
//
// ★새 거래 로직을 만들지 않는다 — 기존 client 함수(autoPaperStart/autoPaperStop/
// autoPaperStatus)와 기존 헬퍼(toStartPayloadCapitalSettings / normalizeRiskProfile /
// normalizeAutoPaperState / canStart·StopAutoPaper)를 *그대로* 호출하는 얇은 배선.
// AutoPaperLoopCard(원클릭 카드)는 손대지 않으며, start payload(risk_profile +
// capital_settings)와 엔드포인트가 카드와 동일하다 → 시작 경로가 분기되지 않는다.
//
// 안전: broker.place_order 직접 호출 0건. 모든 주문은 backend의 RiskManager →
// PermissionGate → OrderExecutor 경로(서버)가 강제. 본 훅은 read-only status
// 폴링 + 시작/정지 토글뿐이며 안전 플래그를 바꾸지 않는다.
import { useCallback, useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";
import {
  toStartPayloadCapitalSettings,
  loadPaperCapitalSettings,
} from "./usePaperCapitalSettings";
import { normalizeRiskProfile } from "../components/AgentRiskProfileSelector";
import {
  normalizeAutoPaperState,
  canStartAutoPaper,
  canStopAutoPaper,
} from "../components/tabs/AutoPaperLoopCard";

/** err.detail에서 차단 사유 배열 추출 (서버 pre-market gate 409 등). */
function extractReasons(err) {
  const d = err && err.detail;
  const cands = [];
  if (d) {
    if (Array.isArray(d.reasons)) cands.push(...d.reasons);
    if (Array.isArray(d.blocking_reasons)) cands.push(...d.blocking_reasons);
  }
  return cands;
}

export function useAutoPaperLoop({
  api = backendApi,
  riskProfile = "BALANCED",
  capitalSettings = null,
  pollMs = 7000,
} = {}) {
  // feat/step2-01: canonical 기본 = PAUSED.
  const [state, setState] = useState("PAUSED");
  const [busy, setBusy] = useState(false);
  // 마지막 시작 시도 결과(게이트 결과 표시용). {ok, reasons[], message}
  const [startResult, setStartResult] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const s = await api.autoPaperStatus();
      setState(normalizeAutoPaperState(s && s.state));
    } catch {
      // status 조회 실패 — 마지막 상태 유지(홈을 깨지 않음).
    }
  }, [api]);

  useEffect(() => {
    refresh();
    if (!pollMs || pollMs <= 0) return undefined;
    const t = setInterval(refresh, pollMs);
    return () => clearInterval(t);
  }, [refresh, pollMs]);

  // 시작: 기존 카드와 동일한 payload(risk_profile + capital_settings)로
  // 기존 client.autoPaperStart 호출. 서버가 pre-market gate 최종 판정(BLOCK→409).
  const start = useCallback(async () => {
    setBusy(true);
    try {
      const body = {
        risk_profile: normalizeRiskProfile(riskProfile),
        capital_settings: toStartPayloadCapitalSettings(
          capitalSettings || loadPaperCapitalSettings(),
        ),
      };
      await api.autoPaperStart(body);
      await refresh();
      const res = { ok: true, reasons: [] };
      setStartResult(res);
      return res;
    } catch (err) {
      const res = { ok: false, reasons: extractReasons(err), message: err && err.message };
      setStartResult(res);
      await refresh();
      return res;
    } finally {
      setBusy(false);
    }
  }, [api, riskProfile, capitalSettings, refresh]);

  // 정지: 기존 client.autoPaperStop 호출.
  const stop = useCallback(async () => {
    setBusy(true);
    try {
      await api.autoPaperStop();
      await refresh();
      setStartResult(null);
      return { ok: true };
    } catch (err) {
      return { ok: false, message: err && err.message };
    } finally {
      setBusy(false);
    }
  }, [api, refresh]);

  const clearStartResult = useCallback(() => setStartResult(null), []);

  return {
    state,
    running: state === "RUNNING",
    startAllowed: canStartAutoPaper(state),
    stopAllowed: canStopAutoPaper(state),
    busy,
    startResult,
    clearStartResult,
    start,
    stop,
    refresh,
  };
}
