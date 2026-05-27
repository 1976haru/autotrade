/**
 * MeanReversionStrategyCard — 장중 평균회귀 전략 연구 (CHECKLIST-05, read-only 연구용).
 *
 * 표시: 평균회귀 가설 근거, 후보별 PF/MDD/OOS/비용stress/verdict, 살아남은 후보, verdict,
 * research_only/auto_apply(false). 버튼은 새로고침·복사만.
 * invariant(테스트 lock): 적용/실전/주문/자동매매 버튼 0개, input 0개, 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _pf = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(3));

export function MeanReversionStrategyCard({
  apiClient = backendApi,
  testId = "mean-reversion-strategy-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.meanReversionStrategyLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.meanReversionStrategyLatest()) || null;
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

  const cand = report?.candidate_results || {};
  const hyp = report?.hypothesis_analysis || {};
  const fwd = hyp?.trend_follower_forward_returns || {};

  return (
    <div data-testid={testId}>
      <Card accentColor="#0ea5e933">
        <SectionLabel>🔄 장중 평균회귀 전략 연구 (CHECKLIST-05, 연구용)</SectionLabel>

        <div data-testid="mrs-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ <b>연구용 백테스트이며 실전매매 권고가 아닙니다.</b> 평균회귀 후보는 런타임 전략으로
          <b> 등록/자동 적용되지 않습니다</b> (research_only · auto_apply=false).
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="mrs-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="mrs-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? <div data-testid="mrs-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>⏳ 불러오는 중…</div> : null}
        {state === "error" ? <div data-testid="mrs-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>결과를 불러올 수 없습니다.</div> : null}
        {state === "empty" ? <div data-testid="mrs-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>API client 미연결.</div> : null}
        {state === "notready" ? (
          <div data-testid="mrs-notready" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-2)", padding: "6px 10px",
            borderRadius: 6, background: "#fef9c3", border: "1px solid var(--c-border)",
          }}>
            verdict: <b>{report?.verdict || "BACKTEST_INFRA_INCOMPLETE"}</b> · 리포트 없음 —
            <code> run_mean_reversion_strategy.py --write-latest </code> 실행 후 표시.
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="mrs-verdict" style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              verdict: {report.verdict}
            </div>
            <div data-testid="mrs-conclusion" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 6 }}>
              {(report.conclusion || []).join(" / ")}
            </div>

            <div data-testid="mrs-hypothesis" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 6 }}>
              <b>가설 근거 (추세추종 BUY 후 forward return):</b> h30 {fwd.mean_h30_bps ?? "—"}bps (n={fwd.n ?? "—"})
              {(hyp.interpretation || []).map((m, idx) => <div key={idx}>· {m}</div>)}
            </div>

            <div data-testid="mrs-candidates" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 6 }}>
              <b>후보 (net PF / OOS / 10bps / cost판정 / verdict):</b>
              {Object.entries(cand).map(([c, v]) => (
                <div key={c}>{c}: PF {_pf(v.net_pf)} · OOS {_pf(v.oos?.oos_pf)} · 10bps {_pf(v.slippage_stress?.["10.0bps"])} · MDD {v.mdd_pct ?? "—"}% · {v.cost_verdict} · <b>{v.verdict}</b></div>
              ))}
            </div>

            <div data-testid="mrs-survivors" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 4 }}>
              살아남은 후보: <b>{(report.survivors || []).length ? report.survivors.join(", ") : "없음"}</b> ·
              WATCH: {(report.watch || []).length ? report.watch.join(", ") : "없음"} ·
              LOW_CONFIDENCE: {(report.low_confidence || []).length ? report.low_confidence.join(", ") : "없음"} ·
              REJECT: {(report.reject || []).length}
            </div>
            <div data-testid="mrs-auto" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
              research_only <b>{String(report.is_research_only)}</b> · 자동 적용 <b>{String(report.auto_apply_allowed)}</b> · 런타임 반영 <b>{String(report.applied_to_runtime)}</b>
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default MeanReversionStrategyCard;
