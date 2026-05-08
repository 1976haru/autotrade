import { useCallback, useEffect, useState } from "react";

import { backendApi } from "../services/backend/client";


/**
 * 테마/뉴스/트렌드 신호 훅 (#22)
 *
 * 본 훅은 *후보 필터* 데이터만 다룬다 — BUY/SELL 결정을 만들지 않으며,
 * 주문 흐름과 분리되어 있다. 모든 응답에서 used_for_order=false invariant.
 */
export function useThemes() {
  const [signals, setSignals] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [provider,   setProvider]   = useState("mock");
  const [providerEnabled, setProviderEnabled] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");
  const [scanMsg, setScanMsg] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const body = await backendApi.themeSignals({ limit: 50 });
      setSignals(body.signals || []);
    } catch (e) {
      setError(e.message || "테마 신호를 불러오지 못했어요.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  const scan = useCallback(async (req = {}) => {
    setScanMsg("");
    try {
      const out = await backendApi.themesScan(req);
      setCandidates(out.candidate_symbols || []);
      setProvider(out.provider);
      setProviderEnabled(out.is_provider_enabled);
      setScanMsg(
        `완료 — ${out.persisted}건 저장 / 후보 ${out.candidate_symbols?.length || 0}종목 ` +
        `(provider=${out.provider})`,
      );
      await refresh();
      return out;
    } catch (e) {
      setScanMsg(e.message || "스캔 실패");
      throw e;
    }
  }, [refresh]);

  return {
    signals, candidates, provider, providerEnabled,
    loading, error, scanMsg,
    refresh, scan,
  };
}


/**
 * Dashboard 요약용 — total / by_grade / top_themes만.
 * used_for_order=false invariant 유지.
 */
export function useThemesSummary() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const body = await backendApi.themesSummary();
      setSummary(body);
    } catch (e) {
      setError(e.message || "테마 요약을 불러오지 못했어요.");
      setSummary(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  return { summary, loading, error, refresh };
}
