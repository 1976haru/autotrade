/**
 * ForwardValidationCard — WF-6M forward/OOS 고정룰 검증 (read-only).
 *
 * 4상태: 데이터 없음 / 분석 중(loading) / 완료 / 실패(error).
 * 핵심: forward verdict, best candidate, 후보별 forward 점수, worst-month holdout,
 * Agent forward, overfit/ decay, EXE 재빌드 권고, Paper rehearsal 여부.
 * invariant(테스트 lock): 실전/주문/적용/자동매매 시작 버튼 0개(새로고침/복사만),
 * input/textarea 0개, 한글 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = {
  PAPER_REHEARSAL_CONFIRMED: "#166534", FORWARD_WATCH: "#0369a1",
  FORWARD_WEAK: "#a16207", FORWARD_FAIL: "#b91c1c",
};
const _V_BG = {
  PAPER_REHEARSAL_CONFIRMED: "#dcfce7", FORWARD_WATCH: "#e0f2fe",
  FORWARD_WEAK: "#fef9c3", FORWARD_FAIL: "#fee2e2",
};

export function ForwardValidationCard({
  apiClient = backendApi,
  testId = "forward-validation-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.forwardValidationLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.forwardValidationLatest()) || null;
      setReport(r);
      setState(r && r.available !== false ? "complete" : "empty");
    } catch (e) {
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

  const verdict = report?.final_forward_verdict || null;
  const best = report?.best || {};
  const cs = report?.candidate_scores || {};
  const holdout = report?.worst_month_holdout || {};
  const agentFwd = report?.agent_forward || {};
  const conclusions = Array.isArray(report?.conclusions) ? report.conclusions : [];
  const nextSteps = Array.isArray(report?.next_steps) ? report.next_steps : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#6366f133">
        <SectionLabel>🔭 WF-6M Forward / OOS 검증 (Forward Validation)</SectionLabel>

        <div data-testid="fv-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구/백테스트 결과이며 실전매매 권고가 아닙니다.</b> forward 통과해도 실전매매
          금지 · 자동 적용 / EXE 빌드 0건 · 수익 보장 없음. (룰은 검증 전 고정: rule_locked)
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="fv-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="fv-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="fv-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            ⏳ forward 검증 분석 중입니다…
          </div>
        ) : null}
        {state === "error" ? (
          <div data-testid="fv-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
            결과를 불러올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="fv-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 forward 검증 리포트가 없습니다. CLI <code>run_wf_6m_forward_validation.py</code>
            실행 후 표시됩니다 (설치 오류 아님).
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="fv-verdict" style={{
              padding: "8px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[verdict] || "#f1f5f9", border: "1px solid var(--c-border)",
            }}>
              <div style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)",
                            color: _V_COLOR[verdict] || "var(--c-text)" }}>
                forward verdict: <span data-testid="fv-final-verdict">{verdict}</span>
              </div>
              <div data-testid="fv-best" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
                best: {best.best_candidate} · forward {best.forward_return_pct ?? "—"}% · PF {best.median_pf ?? "—"} ·
                MDD {best.forward_mdd_pct ?? "—"}% · 거래 {best.total_trades ?? 0} · worst월 방어 {String(best.worst_month_defended)}
              </div>
              <div data-testid="fv-exe-rec" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 2 }}>
                EXE 재빌드 권고: <b>{report.exe_rebuild_recommendation}</b>
              </div>
              <div data-testid="fv-live" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginTop: 2 }}>
                실전매매 권고: <b>{String(report.live_trading_recommendation)}</b> (항상 false) · {report.paper_rehearsal_recommendation}
              </div>
            </div>

            <div data-testid="fv-candidates" style={{ marginBottom: 4 }}>
              <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)" }}>후보별 forward (Monthly)</div>
              {Object.entries(cs).map(([c, v]) => (
                <div key={c} style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                  {c}: {v.verdict} · {v.forward_return_pct}% · PF {v.median_pf} · MDD {v.forward_mdd_pct}% ·
                  거래 {v.total_trades}
                </div>
              ))}
            </div>

            {holdout.worst_month ? (
              <div data-testid="fv-holdout" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
                Worst-month holdout ({holdout.worst_month}, baseline {holdout.baseline_worst_month_return}%):{" "}
                {Object.entries(holdout).filter(([k, v]) => typeof v === "object" && v && "holdout_return_pct" in v)
                  .map(([c, v]) => `${c} ${v.holdout_return_pct}%(방어 ${v.defended})`).join(", ")}
              </div>
            ) : null}

            <div data-testid="fv-agent" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              Agent forward: {Object.entries(agentFwd).map(([m, v]) => `${m.replace("AGENT_", "")} ${v.forward_return_pct}%`).join(" · ")}
            </div>

            <div data-testid="fv-overfit" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              overfit 경고: {String(report?.overfit_warning?.warning)}
            </div>

            <div data-testid="fv-conclusions" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 6 }}>
              결론: {conclusions.join(" / ")}
            </div>
            {nextSteps.length > 0 ? (
              <div data-testid="fv-next" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 4 }}>
                다음 단계: {nextSteps.join(" · ")}
              </div>
            ) : null}
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default ForwardValidationCard;
