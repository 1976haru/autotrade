/**
 * RiskFilterValidationCard — RISK_FILTER_ONLY OOS 검증 결과 (read-only 연구용).
 *
 * 표시: 고정 룰, earliest vs RISK_FILTER OOS, rolling, 필터 제거 신호 분석, stress,
 * verdict, 자동 적용 여부(false). 버튼은 새로고침·복사만.
 * invariant(테스트 lock): 주문/실전/적용/자동매매 버튼 0개, input 0개, 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

export function RiskFilterValidationCard({
  apiClient = backendApi,
  testId = "risk-filter-validation-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.riskFilterValidationLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.riskFilterValidationLatest()) || null;
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

  const ob = report?.oos?.baseline || {};
  const of = report?.oos?.risk_filter || {};
  const fo = report?.filtered_out_analysis || {};

  return (
    <div data-testid={testId}>
      <Card accentColor="#10b98133">
        <SectionLabel>🧮 RISK_FILTER_ONLY OOS 검증 (연구용 백테스트)</SectionLabel>

        <div data-testid="rfv-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구용 백테스트이며 실전매매 권고가 아닙니다.</b> RISK_FILTER 는 런타임/
          전략에 <b>자동 적용되지 않습니다</b> (auto_apply=false). paper rehearsal 도 별도 승인 후에만.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="rfv-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="rfv-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? <div data-testid="rfv-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>⏳ 불러오는 중…</div> : null}
        {state === "error" ? <div data-testid="rfv-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>결과를 불러올 수 없습니다.</div> : null}
        {state === "empty" ? <div data-testid="rfv-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>API client 미연결.</div> : null}
        {state === "notready" ? (
          <div data-testid="rfv-notready" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-2)", padding: "6px 10px",
            borderRadius: 6, background: "#fef9c3", border: "1px solid var(--c-border)",
          }}>
            verdict: <b>{report?.verdict || "RISK_FILTER_REJECTED"}</b> · 리포트 없음 —
            <code> run_risk_filter_validation.py --write-latest </code> 실행 후 표시.
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="rfv-verdict" style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              verdict: {report.verdict}
            </div>
            <div data-testid="rfv-oos" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 4 }}>
              <div>earliest (OOS): PF {ob.profit_factor ?? "—"} / MDD {ob.mdd_pct ?? "—"}% / exp {ob.expectancy ?? "—"}bps</div>
              <div><b>RISK_FILTER (OOS): PF {of.profit_factor ?? "—"} / MDD {of.mdd_pct ?? "—"}% / exp {of.expectancy ?? "—"}bps</b></div>
            </div>
            <div data-testid="rfv-rolling" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              rolling: {(report.rolling || []).map((w) => `${w.filter_ge_baseline ? "✓" : "✗"}`).join(" ")}
              {" "}({(report.rolling || []).length} 구간)
            </div>
            <div data-testid="rfv-filtered" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              제거 {fo.removed_count ?? "—"}/{fo.total_trades ?? "—"} · 손실 집중 {String(fo.loss_concentrated_in_removed)} ·
              missed winners {fo.missed_winners_count ?? "—"} / avoided losers {fo.avoided_losers_count ?? "—"}
            </div>
            <div data-testid="rfv-rec" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              추천: {report.recommendation}
            </div>
            <div data-testid="rfv-auto" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
              자동 적용 <b>{String(report.auto_apply_allowed)}</b> · 런타임 반영 <b>{String(report.applied_to_runtime)}</b>
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default RiskFilterValidationCard;
