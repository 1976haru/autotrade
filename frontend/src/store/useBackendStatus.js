import { useEffect, useState } from "react";

import {
  backendApi,
  discoverBackendBaseUrl,
  getBackendBaseUrl,
} from "../services/backend/client";


// fix/desktop-nonblocking-migration-health: db_ready=false 인 동안 본 hook 은
// N초마다 /api/status 를 재호출해 db_ready=true 가 되는 즉시 React state 가
// 자동 갱신되도록 한다. backend offline 으로 *오인하지 않고* "초기 DB 준비 중"
// 배너가 자연스럽게 "연결 완료" 로 전환됨.
const _DB_PENDING_REPOLL_MS = 2_000;


/**
 * useBackendStatus — fetch backend `/api/status` (default_mode, safety flags,
 * mode_capabilities) on mount.
 *
 * fix/frontend-detects-fallback-backend-port: 8000 이 stale port 충돌로 실패해도
 * 8001/8002 로 자동 fallback discover. 성공한 baseUrl 을 backendApi (전역
 * client) 에 반영하므로 *이후 모든* API 호출 (audit / agents / dashboard /
 * auto-paper) 이 같은 포트를 사용한다.
 *
 * fix/desktop-nonblocking-migration-health: payload 의 db_ready / migration_status
 * 를 read-only carry. db_ready=false 면 N초마다 재폴링.
 *
 * 반환:
 *   - status: GET /api/status payload (default_mode / safety_flags 등) 또는 null
 *   - loading: boolean
 *   - error:   string (빈 문자열 = no error)
 *   - baseUrl: 현재 backend baseUrl (port fallback 후 결정된 값)
 *   - viaFallback: true 면 8000 외 fallback port 에 연결됨
 *   - dbReady: 편의 — `status?.db_ready === true` (null/undefined 면 false)
 */
export function useBackendStatus() {
  const [status,  setStatus]  = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [baseUrl, setBaseUrl] = useState(getBackendBaseUrl());
  const [viaFallback, setViaFallback] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let repollTimer = null;

    const fetchStatus = async () => {
      try {
        const s = await backendApi.getStatus();
        if (cancelled) return;
        setStatus(s);
        // db_ready=false 면 backend 는 살아있지만 alembic 진행 중 — N초 후
        // 다시 fetch 해 자동 전환 트리거.
        if (s && s.db_ready === false) {
          repollTimer = setTimeout(fetchStatus, _DB_PENDING_REPOLL_MS);
        }
      } catch (e) {
        if (!cancelled) {
          // /api/status 가 실패하면 backend offline 의심 — error 라벨만 표시.
          setError(e?.message || String(e));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    (async () => {
      // 1단계: multi-port discover (8000 → 8001 → 8002).
      const disc = await discoverBackendBaseUrl();
      if (cancelled) return;
      if (disc.ok) {
        setBaseUrl(disc.baseUrl);
        setViaFallback(disc.baseUrl !== "http://127.0.0.1:8000");
      }
      // 2단계: 발견된 baseUrl 로 /api/status fetch (db_ready=false 면 자동 재폴링).
      fetchStatus();
    })();

    return () => {
      cancelled = true;
      if (repollTimer != null) clearTimeout(repollTimer);
    };
  }, []);

  const dbReady = status?.db_ready === true;
  return { status, loading, error, baseUrl, viaFallback, dbReady };
}
