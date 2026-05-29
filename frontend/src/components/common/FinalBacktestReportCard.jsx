/**
 * FinalBacktestReportCard — 4전략 단독 + Council 통합 최종 백테스트 (read-only 연구용).
 *
 * 표시: 4전략 PF/MDD/등급, Council PF/MDD, best single, Risk Filter 효과, verdict,
 * 자동 적용 여부(false). 버튼은 새로고침·복사만.
 * invariant(테스트 lock): 주문/실전/적용/자동매매 버튼 0개, input 0개, 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

export function FinalBacktestReportCard({
  apiClient = backendApi,
  testId = "final-backtest-report-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.finalBacktestReportLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.finalBacktestReportLatest()) || null;
      setReport(r);
      setState(r && r.available !== false ? "complete" : "notready");
    } catch { setReport(null); setState("error"); }
  }, [apiClient]);

  useEffect(() => { refresh(); }, [refresh]);

  const copyReport = useCallback(() => {
    try {
      if (navigator?.clipboard?.writeText) {
        navigator.clipboard.writeText(JSON.stringify(report || {}, null, 2));
        setCopied(true); setTimeout(() => setCopied(false), 1500);
      }
    } catch { /* noop */ }
  }, [report]);

  const ps = report?.per_strategy || {};
  const c = report?.council || {};

  return (
    <div data-testid={testId}>
      <Card accentColor="#f43f5e33">
        <SectionLabel>📋 최종 다전략 + Council 백테스트 (연구용)</SectionLabel>

        <div data-testid="fbr-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구용 백테스트이며 실전매매 권고가 아닙니다.</b> 어떤 전략/필터도 런타임에
          <b> 자동 적용되지 않습니다</b> (auto_apply=false).
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="fbr-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="fbr-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? <div data-testid="fbr-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>⏳ 불러오는 중…</div> : null}
        {state === "error" ? <div data-testid="fbr-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>결과를 불러올 수 없습니다.</div> : null}
        {state === "empty" ? <div data-testid="fbr-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>API client 미연결.</div> : null}
        {state === "notready" ? (
          <div data-testid="fbr-notready" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-2)", padding: "6px 10px",
            borderRadius: 6, background: "#fef9c3", border: "1px solid var(--c-border)",
          }}>
            verdict: <b>{report?.verdict || "BACKTEST_INFRA_INCOMPLETE"}</b> · 리포트 없음 —
            <code> run_final_multi_strategy_backtest.py --write-latest </code> 실행 후 표시.
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="fbr-verdict" style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              verdict: {report.verdict}
            </div>
            <div data-testid="fbr-strategies" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 4 }}>
              {["ORB", "MOMENTUM", "GAP", "VWAP"].map((n) => {
                const m = ps[n]?.intrabar_1m || {};
                return (
                  <div key={n}>{n}: PF {m.profit_factor ?? "—"} / MDD {m.mdd_pct ?? "—"}% / {ps[n]?.grade ?? "—"}</div>
                );
              })}
            </div>
            <div data-testid="fbr-council" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 4 }}>
              <b>Council: PF {c.basic?.profit_factor ?? "—"} / MDD {c.basic?.mdd_pct ?? "—"}%</b> ·
              +RF PF {c.with_risk_filter?.profit_factor ?? "—"} · best single {c.best_single_strategy ?? "—"}({c.best_single_pf ?? "—"})
            </div>
            <div data-testid="fbr-council-cmp" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              council vs best {c.council_vs_best_single_pf_delta ?? "—"} / vs avg {c.council_vs_avg_single_pf_delta ?? "—"} ·
              agent helped {c.agent_helped_count ?? "—"} / hurt {c.agent_hurt_count ?? "—"}
            </div>
            <div data-testid="fbr-conclusion" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              {(report.conclusion || []).join(" / ")}
            </div>
            <div data-testid="fbr-auto" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
              자동 적용 <b>{String(report.auto_apply_allowed)}</b> · 런타임 반영 <b>{String(report.applied_to_runtime)}</b>
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default FinalBacktestReportCard;
