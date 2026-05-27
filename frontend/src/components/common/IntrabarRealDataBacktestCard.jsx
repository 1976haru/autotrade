/**
 * IntrabarRealDataBacktestCard — 실제 1분봉 intrabar 백테스트 결과 (read-only).
 *
 * 표시: replay coverage, 5m vs 1m PF/MDD/return, earliest vs composite, cost stress,
 * confidence, verdict. 버튼은 새로고침·복사만. invariant(테스트 lock): 주문/실전/적용/
 * 자동매매 버튼 0개, input 0개, 연구용 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

export function IntrabarRealDataBacktestCard({
  apiClient = backendApi,
  testId = "intrabar-realdata-backtest-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.intrabarRealDataBacktestLatest !== "function") {
      setState("empty");
      return;
    }
    setState("loading");
    try {
      const r = (await apiClient.intrabarRealDataBacktestLatest()) || null;
      setReport(r);
      setState(r && r.available !== false ? "complete" : "notready");
    } catch {
      setReport(null);
      setState("error");
    }
  }, [apiClient]);

  useEffect(() => { refresh(); }, [refresh]);

  const copyReport = useCallback(() => {
    try {
      if (navigator?.clipboard?.writeText) {
        navigator.clipboard.writeText(JSON.stringify(report || {}, null, 2));
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }
    } catch { /* noop */ }
  }, [report]);

  const e = report?.execution_5m_vs_1m || {};
  const rk = report?.ranking || {};
  const cov = report?.coverage || {};

  return (
    <div data-testid={testId}>
      <Card accentColor="#0ea5e933">
        <SectionLabel>📊 실제 1분봉 Intrabar 백테스트 (연구용)</SectionLabel>

        <div data-testid="ird-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2",
          border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구용 백테스트이며 실전매매 권고가 아닙니다.</b> 결과가 좋아도
          모의 리허설 후보일 뿐이며, 결과가 나쁘면 그대로 표시합니다.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="ird-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="ird-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="ird-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            ⏳ 결과 불러오는 중…
          </div>
        ) : null}
        {state === "error" ? (
          <div data-testid="ird-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
            결과를 불러올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="ird-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            API client 미연결.
          </div>
        ) : null}
        {state === "notready" ? (
          <div data-testid="ird-notready" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-2)", padding: "6px 10px",
            borderRadius: 6, background: "#fef9c3", border: "1px solid var(--c-border)",
          }}>
            verdict: <b>{report?.verdict || "REALDATA_BACKTEST_NOT_READY"}</b> · confidence {report?.confidence_level || "LOW"}
            <div style={{ marginTop: 2 }}>
              실제 1분봉 데이터가 아직 없습니다 — 장중
              <code> collect_intrabar_1m_subset.py </code> 실행 후
              <code> run_intrabar_realdata_backtest.py --write-latest </code>로 갱신됩니다.
            </div>
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="ird-verdict" style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              verdict: {report.verdict} · confidence {report.confidence_level}
            </div>
            <div data-testid="ird-coverage" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              1분봉 종목 {cov.symbol_count_1m ?? 0} · replay {cov.replayable_trades ?? 0}
            </div>
            <div data-testid="ird-exec" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 4 }}>
              5m PF {e.five_minute?.profit_factor ?? "—"} / return {e.five_minute?.return_pct ?? "—"}% ·
              1m PF {e.intrabar_1m?.profit_factor ?? "—"} / return {e.intrabar_1m?.return_pct ?? "—"}%
              {" "}(MDD 1m {e.intrabar_1m?.mdd_pct ?? "—"}%)
            </div>
            <div data-testid="ird-ranking" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              earliest {(rk.earliest_first_selected || []).join(",") || "—"} vs composite
              {" "}{(rk.composite_selected || []).join(",") || "—"} (differs {String(rk.ranking_differs)}) ·
              선택 {rk.selected_avg_score ?? "—"} / 버려진 {rk.rejected_avg_score ?? "—"}
            </div>
            <div data-testid="ird-cost" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 4 }}>
              슬리피지 stress: {(report.cost_slippage_stress || []).map(
                (s) => `${s.slippage_bps}bps→PF${s.profit_factor ?? "—"}`).join(" · ")}
            </div>
            <div data-testid="ird-live" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginTop: 4 }}>
              실전 허가 <b>{String(report.is_live_authorization)}</b> · {report.disclaimer}
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default IntrabarRealDataBacktestCard;
