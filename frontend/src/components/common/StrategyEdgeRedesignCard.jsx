/**
 * StrategyEdgeRedesignCard — 전략 엣지 재설계 연구 (CHECKLIST-05, read-only 연구용).
 *
 * 표시: 기존 4전략 실패 원인, 신규 후보별 PF/MDD/OOS/verdict, 살아남은 후보, exit 구조,
 * 자동 적용 여부(false). 버튼은 새로고침·복사만.
 * invariant(테스트 lock): 적용/실전/주문/자동매매 버튼 0개, input 0개, 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _pf = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(3));

export function StrategyEdgeRedesignCard({
  apiClient = backendApi,
  testId = "strategy-edge-redesign-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.strategyEdgeRedesignLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.strategyEdgeRedesignLatest()) || null;
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

  const fa = report?.failure_analysis || {};
  const cand = report?.candidate_results || {};
  const ex = report?.exit_structure_analysis || {};

  return (
    <div data-testid={testId}>
      <Card accentColor="#8b5cf633">
        <SectionLabel>🧪 전략 엣지 재설계 연구 (CHECKLIST-05, 연구용)</SectionLabel>

        <div data-testid="ser-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구용 백테스트이며 실전매매 권고가 아닙니다.</b> 신규 후보 전략은 런타임에
          <b> 등록/자동 적용되지 않습니다</b> (auto_apply=false).
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="ser-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="ser-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? <div data-testid="ser-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>⏳ 불러오는 중…</div> : null}
        {state === "error" ? <div data-testid="ser-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>결과를 불러올 수 없습니다.</div> : null}
        {state === "empty" ? <div data-testid="ser-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>API client 미연결.</div> : null}
        {state === "notready" ? (
          <div data-testid="ser-notready" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-2)", padding: "6px 10px",
            borderRadius: 6, background: "#fef9c3", border: "1px solid var(--c-border)",
          }}>
            verdict: <b>{report?.verdict || "BACKTEST_INFRA_INCOMPLETE"}</b> · 리포트 없음 —
            <code> run_strategy_edge_redesign.py --write-latest </code> 실행 후 표시.
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="ser-verdict" style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              verdict: {report.verdict}
            </div>
            <div data-testid="ser-conclusion" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 6 }}>
              {(report.conclusion || []).join(" / ")}
            </div>

            <div data-testid="ser-failure" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 6 }}>
              <b>기존 전략 실패 원인 (gross PF / net PF / stop먼저):</b>
              {["ORB", "MOMENTUM", "GAP", "VWAP"].map((n) => {
                const f = fa[n] || {};
                return <div key={n}>{n}: gross {_pf(f.gross_pf)} → net {_pf(f.net_pf)} · stop먼저 {f.stop_first_ratio ?? "—"} · MFE/MAE {f.avg_mfe_bps ?? "—"}/{f.avg_mae_bps ?? "—"}bps</div>;
              })}
            </div>

            <div data-testid="ser-candidates" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 6 }}>
              <b>신규 후보 (net PF / OOS / verdict):</b>
              {Object.entries(cand).map(([c, v]) => (
                <div key={c}>{c}: PF {_pf(v.profit_factor)} · OOS {_pf(v.oos?.oos_pf)} · MDD {v.mdd_pct ?? "—"}% · <b>{v.verdict}</b></div>
              ))}
            </div>

            <div data-testid="ser-survivors" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 4 }}>
              살아남은 후보: <b>{(report.survivors || []).length ? report.survivors.join(", ") : "없음"}</b> ·
              WATCH: {(report.watch || []).length ? report.watch.join(", ") : "없음"} ·
              버릴 전략: {(report.exclude_strategies || []).join(", ") || "없음"}
            </div>
            {ex.pf_by_structure ? (
              <div data-testid="ser-exit" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
                exit 구조({ex.evaluated_on}, {ex.scope}): {Object.entries(ex.pf_by_structure).map(([k, v]) => `${k} ${_pf(v)}`).join(" · ")}
              </div>
            ) : null}
            <div data-testid="ser-auto" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
              자동 적용 <b>{String(report.auto_apply_allowed)}</b> · 런타임 반영 <b>{String(report.applied_to_runtime)}</b>
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default StrategyEdgeRedesignCard;
