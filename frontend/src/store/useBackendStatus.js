import { useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";


/**
 * useBackendStatus — read-only fetch of /api/status (default_mode, safety
 * flags, mode_capabilities) on mount. Used by 078's Settings warning banner
 * to detect dangerous mode + flag combinations and by future status-derived
 * UX (079+).
 *
 * Single fetch on mount, no polling — these values are env-set at backend
 * startup and don't change without a restart that would reset everything
 * else anyway. If a future runtime toggle endpoint is added, this hook can
 * grow a refresh() at that point.
 */
export function useBackendStatus() {
  const [status,  setStatus]  = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");

  useEffect(() => {
    let cancelled = false;
    backendApi.getStatus()
      .then((s) => { if (!cancelled) setStatus(s); })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  return { status, loading, error };
}
