import { useEffect, useState } from "react";

import {
  backendApi,
  discoverBackendBaseUrl,
  getBackendBaseUrl,
} from "../services/backend/client";


/**
 * useBackendStatus — fetch backend `/api/status` (default_mode, safety flags,
 * mode_capabilities) on mount.
 *
 * fix/frontend-detects-fallback-backend-port: 8000 이 stale port 충돌로 실패해도
 * 8001/8002 로 자동 fallback discover. 성공한 baseUrl 을 backendApi (전역
 * client) 에 반영하므로 *이후 모든* API 호출 (audit / agents / dashboard /
 * auto-paper) 이 같은 포트를 사용한다.
 *
 * 반환:
 *   - status: GET /api/status payload (default_mode / safety_flags 등) 또는 null
 *   - loading: boolean
 *   - error:   string (빈 문자열 = no error)
 *   - baseUrl: 현재 backend baseUrl (port fallback 후 결정된 값)
 *   - viaFallback: true 면 8000 외 fallback port 에 연결됨
 */
export function useBackendStatus() {
  const [status,  setStatus]  = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [baseUrl, setBaseUrl] = useState(getBackendBaseUrl());
  const [viaFallback, setViaFallback] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 1단계: multi-port discover (8000 → 8001 → 8002).
      const disc = await discoverBackendBaseUrl();
      if (cancelled) return;
      if (disc.ok) {
        setBaseUrl(disc.baseUrl);
        setViaFallback(disc.baseUrl !== "http://127.0.0.1:8000");
      }
      // 2단계: 발견된 baseUrl 로 /api/status fetch.
      try {
        const s = await backendApi.getStatus();
        if (!cancelled) setStatus(s);
      } catch (e) {
        if (!cancelled) {
          // /api/status 가 실패해도 disc.ok 면 backend 는 살아있음 — error 라벨만 표시.
          setError(e?.message || String(e));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return { status, loading, error, baseUrl, viaFallback };
}
