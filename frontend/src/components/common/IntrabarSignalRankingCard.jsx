/**
 * IntrabarSignalRankingCard — 1분봉 intrabar 체결 + composite signal ranking (read-only).
 *
 * 표시: basic_5m vs intrabar_1m, earliest-first vs composite, replay coverage,
 * ambiguous trade count, fallback ratio, 선택/버려진 평균 score. 버튼은 새로고침·복사만.
 * invariant(테스트 lock): 주문/실전/적용/자동매매 버튼 0개, input 0개, 연구용 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

export function IntrabarSignalRankingCard({
  apiClient = backendApi,
  testId = "intrabar-signal-ranking-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.intrabarSignalRankingLatest !== "function") {
      setState("empty");
      return;
    }
    setState("loading");
    try {
      const r = (await apiClient.intrabarSignalRankingLatest()) || null;
      setReport(r);
      setState(r && r.available !== false ? "complete" : "empty");
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

  const cov = report?.one_minute_coverage || {};

  return (
    <div data-testid={testId}>
      <Card accentColor="#6366f133">
        <SectionLabel>🧪 Intrabar 체결 + Composite Signal Ranking (연구용 백테스트)</SectionLabel>

        <div data-testid="isr-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2",
          border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구용 백테스트이며 실전매매 권고가 아닙니다.</b> 1분봉 데이터가
          부족하면 체결 정확도가 낮을 수 있습니다(WARN).
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="isr-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="isr-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="isr-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            ⏳ 백테스트 결과 불러오는 중…
          </div>
        ) : null}
        {state === "error" ? (
          <div data-testid="isr-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
            결과를 불러올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="isr-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 intrabar/ranking 리포트가 없습니다. CLI
            <code> run_intrabar_signal_ranking.py --write-latest </code>
            실행 후 표시됩니다 (연구용 · 설치 오류 아님).
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="isr-coverage" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 6,
              padding: "6px 9px", borderRadius: 6,
              background: cov.status === "OK" ? "#dcfce7" : "#fef9c3",
              border: "1px solid var(--c-border)",
            }}>
              1분봉 coverage: <b>{cov.status}</b> · replay {cov.replayable_trades ?? 0} /
              fallback {cov.fallback_trades ?? 0} / ambiguous {cov.ambiguous_trades ?? 0}
              {cov.warning ? <div style={{ color: "#7f1d1d", marginTop: 2 }}>{cov.warning}</div> : null}
            </div>

            <div data-testid="isr-execution" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 6 }}>
              체결 모델: 보수적 stop-first <b>{report.conservative_stop_first_count ?? 0}</b>회 ·
              ambiguous <b>{report.ambiguous_trade_count ?? 0}</b>건 (5분봉 fallback vs 1분봉 replay)
            </div>

            <div data-testid="isr-ranking" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 4 }}>
              <div>earliest-first 선택: {(report.earliest_first_selected || []).join(", ") || "—"}</div>
              <div>composite 선택: <b>{(report.composite_selected || []).join(", ") || "—"}</b>
                {" "}(differs={String(report.ranking_differs)})</div>
              <div data-testid="isr-scores">
                선택 평균 score <b>{report.selected_avg_score ?? "—"}</b> vs
                버려진 평균 score <b>{report.rejected_avg_score ?? "—"}</b> · veto {report.vetoed_count ?? 0}
              </div>
            </div>

            <div data-testid="isr-cost" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 4 }}>
              비용: 수수료 {report.cost_model?.commission_bps}bps · 세금 {report.cost_model?.tax_bps}bps ·
              슬리피지 {report.cost_model?.slippage_bps}bps
            </div>

            <div data-testid="isr-live" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginTop: 4 }}>
              실전 허가 <b>{String(report.is_live_authorization)}</b> · 자동 적용
              {" "}<b>{String(report.auto_apply_allowed)}</b> · {report.disclaimer}
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default IntrabarSignalRankingCard;
