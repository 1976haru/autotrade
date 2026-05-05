import { useCallback, useState } from "react";
import { backendApi } from "../services/backend/client";

/**
 * 백테스트 실행 훅.
 *
 * - submit(req):  단일 백테스트 실행 (`/api/backtest/run`).
 * - compare(req): 동일 데이터에 여러 param set을 흘려보내는 sweep
 *                  (`/api/backtest/compare`). sort_by 메트릭으로 내림차순 정렬된
 *                  BacktestResponse[] 형태의 comparison 객체를 반환한다.
 *
 * Single 결과(run)와 비교 결과(comparison)는 별도 슬롯이라 모드 전환 시 서로
 * 덮어쓰지 않는다.
 */
export function useBacktest() {
  const [run,        setRun]        = useState(null);
  const [comparison, setComparison] = useState(null);
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState("");

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

  const compare = useCallback(async (req) => {
    setLoading(true);
    setError("");
    try {
      const result = await backendApi.compareBacktests(req);
      setComparison(result);
      return result;
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  return { run, comparison, loading, error, submit, compare };
}
