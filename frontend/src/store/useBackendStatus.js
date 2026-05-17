import { useEffect, useState } from "react";

import {
  backendApi,
  discoverBackendBaseUrl,
  getBackendBaseUrl,
} from "../services/backend/client";


// fix/step1-backend-autoconnect-final: 명시적 connection state 머신.
// 기존 (error / loading) bool 조합으로는 *처음 폴링 실패*(CONNECTING) 와 *영구
// 오프라인*(OFFLINE) 을 구분 못 함 → frontend 가 backend 가 살아있는데도
// "Demo Mode / Backend 미연결" 로 stuck. ConnectionState 가 단일 진실.
export const CONNECTION_STATES = Object.freeze({
  CONNECTING:   "CONNECTING",      // 초기 / 재시도 중. 아직 한 번도 성공 못 함.
  DB_PREPARING: "DB_PREPARING",    // /api/status OK 지만 db_ready=false (alembic 진행).
  CONNECTED:    "CONNECTED",       // /api/status OK + db_ready=true.
  OFFLINE:      "OFFLINE",         // (예약) 영구 실패 — 본 PR 시점에는 사용 안 함 (계속 재시도).
});

// db_ready=false 일 때 재폴링 간격.
const _DB_PENDING_REPOLL_MS = 2_000;
// CONNECTED 상태에서 periodic refresh — mode flip 감지용 (10초마다).
const _CONNECTED_REFRESH_MS = 10_000;
// CONNECTING 상태에서 retry backoff: 1s, 2s, 4s, 5s (cap).
const _RETRY_BACKOFF_MS = [1_000, 2_000, 4_000, 5_000];


function _retryDelayForAttempt(attempt /* 1-based */) {
  const idx = Math.min(attempt - 1, _RETRY_BACKOFF_MS.length - 1);
  return _RETRY_BACKOFF_MS[Math.max(0, idx)];
}


/**
 * useBackendStatus — backend 자동 발견 + 연결 상태 머신.
 *
 * 핵심: backend sidecar 가 EXE 부팅 직후에는 listen 안 함 → 첫 fetch 실패는
 * 정상. 본 hook 은 *영구* retry (지수 backoff) 로 backend 가 살아나는 즉시
 * connected 상태로 전환. discoverBackendBaseUrl 이 8000 → 8001 → 8002 fallback
 * 을 매번 다시 시도하므로 sidecar 가 8001 로 bind 한 경우도 자동 잡힘.
 *
 * 반환:
 *   - status: GET /api/status payload 또는 null (아직 한 번도 성공 못 한 경우)
 *   - loading: 첫 응답 도착 전까지만 true (backwards compat)
 *   - error: 마지막 시도 실패 메시지 (재시도 진행 중에도 set — UI 가 자체 분기)
 *   - baseUrl: 발견된 backend baseUrl
 *   - viaFallback: 8000 외 fallback port 사용 중이면 true
 *   - dbReady: status?.db_ready === true 편의 boolean
 *   - connectionState: CONNECTION_STATES enum (UI 분기 핵심)
 *   - attemptCount: 재시도 횟수 (진단)
 *   - lastAttemptError: 마지막 실패 사유 (진단)
 */
export function useBackendStatus() {
  const [status,  setStatus]  = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [baseUrl, setBaseUrl] = useState(getBackendBaseUrl());
  const [viaFallback, setViaFallback] = useState(false);
  const [connectionState, setConnectionState] = useState(CONNECTION_STATES.CONNECTING);
  const [attemptCount, setAttemptCount] = useState(0);
  const [lastAttemptError, setLastAttemptError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    let attempts = 0;

    const scheduleRetry = (delayMs) => {
      if (cancelled) return;
      timer = setTimeout(tryConnect, delayMs);  // eslint-disable-line no-use-before-define
    };

    const tryConnect = async () => {
      if (cancelled) return;
      attempts += 1;
      try {
        // 매 시도마다 *전체* discover (8000 → 8001 → 8002) 다시 — sidecar 가
        // 늦게 listen 한 경우 / 다른 포트로 bind 한 경우 자동 대응.
        const disc = await discoverBackendBaseUrl();
        if (cancelled) return;
        if (!disc.ok) {
          throw new Error(disc.error || "discovery failed");
        }
        setBaseUrl(disc.baseUrl);
        setViaFallback(disc.baseUrl !== "http://127.0.0.1:8000");

        const s = await backendApi.getStatus();
        if (cancelled) return;
        setStatus(s);
        setError("");                    // 성공하면 stale error clear.
        setLastAttemptError("");
        setAttemptCount(attempts);
        if (s && s.db_ready === false) {
          setConnectionState(CONNECTION_STATES.DB_PREPARING);
          scheduleRetry(_DB_PENDING_REPOLL_MS);
        } else {
          setConnectionState(CONNECTION_STATES.CONNECTED);
          // 주기적 refresh — mode 변경 / safety flag 변경 감지.
          scheduleRetry(_CONNECTED_REFRESH_MS);
        }
      } catch (e) {
        if (cancelled) return;
        const msg = e?.message || String(e);
        setLastAttemptError(msg);
        setAttemptCount(attempts);
        // backwards compat — `error` 도 set (기존 UI 가 error 로 분기).
        // 단, status 가 null 이면 *아직 한 번도 성공 못 함* → CONNECTING.
        // status 가 있었는데 일시 실패면 (예: 일시 network blip) 이전 status
        // 유지하면서 CONNECTING 표시.
        setError(msg);
        setConnectionState(CONNECTION_STATES.CONNECTING);
        scheduleRetry(_retryDelayForAttempt(attempts));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    tryConnect();

    return () => {
      cancelled = true;
      if (timer != null) clearTimeout(timer);
    };
  }, []);

  const dbReady = status?.db_ready === true;
  return {
    status,
    loading,
    error,
    baseUrl,
    viaFallback,
    dbReady,
    connectionState,
    attemptCount,
    lastAttemptError,
  };
}
