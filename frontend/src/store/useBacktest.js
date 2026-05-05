import { useCallback, useState } from "react";
import { backendApi } from "../services/backend/client";

/**
 * 백테스트 실행 훅. submit() 호출 시 backend `/api/backtest/run`에 요청을 보내고
 * 결과 객체(run_id, 지표, trades 포함)를 보관한다.
 */
export function useBacktest() {
  const [run,     setRun]     = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");

  const submit = useCallback(async (req) => {
    setLoading(true);
    setError("");
    try {
      const result = await backendApi.runBacktest(req);
      setRun(result);
      return result;
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  return { run, loading, error, submit };
}
