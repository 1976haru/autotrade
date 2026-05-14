/**
 * #85 Strategy Selection hook.
 *
 * `POST /api/agents/strategy-selection` 를 호출해 4개 단타 전략 vote 의 advisory
 * 합산 리포트를 받는다. 본 hook 은 *주문을 만들지 않는다* — 단순 fetch wrapper.
 *
 * 기본 입력은 빈 vote (백엔드가 NO_SIGNAL 리포트 반환). caller 가 실제 vote 를
 * 채워 전달하면 그대로 위임.
 */

import { useCallback, useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";


export function useStrategySelection(initialBody) {
  const [body, setBody] = useState(initialBody || { votes: [] });
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async (override) => {
    setLoading(true); setError("");
    try {
      const res = await backendApi.strategySelection(override || body);
      setReport(res);
    } catch (e) {
      setError(e?.message || "전략 조합 조회 실패");
    }
    setLoading(false);
  }, [body]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setError("");
      try {
        const res = await backendApi.strategySelection(body);
        if (!cancelled) setReport(res);
      } catch (e) {
        if (!cancelled) setError(e?.message || "전략 조합 조회 실패");
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(body)]);

  return { report, loading, error, refresh, body, setBody };
}
