/**
 * Locked60dWeeklyValidationCard — 60d/weekly 고정 룰 holdout 재검증 (read-only).
 *
 * 4상태: 데이터 없음 / 분석 중(loading) / 완료 / 실패(error).
 * 핵심: locked rule verdict, holdout 결과(original/last20/last40/worst-month), 40d/monthly vs
 * 60d/weekly 비교, EXE 재빌드 권고, Paper rehearsal 여부.
 * invariant(테스트 lock): 실전/주문/적용/자동매매 시작 버튼 0개(새로고침/복사만),
 * input/textarea 0개, 한글 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = {
  LOCKED_RULE_RESEARCH_PROMISING: "#166534", LOCKED_RULE_PAPER_CANDIDATE: "#15803d",
  LOCKED_RULE_WATCH: "#0369a1", LOCKED_RULE_WEAK: "#a16207", LOCKED_RULE_FAIL: "#b91c1c",
};
const _V_BG = {
  LOCKED_RULE_RESEARCH_PROMISING: "#dcfce7", LOCKED_RULE_PAPER_CANDIDATE: "#dcfce7",
  LOCKED_RULE_WATCH: "#e0f2fe", LOCKED_RULE_WEAK: "#fef9c3", LOCKED_RULE_FAIL: "#fee2e2",
};

function _h(x) {
  if (!x) return "—";
  return `${x.forward_return_pct ?? "—"}% · PF ${x.median_pf ?? "—"} · MDD ${x.forward_mdd_pct ?? "—"}% · 거래 ${x.total_trades ?? 0}`;
}

export function Locked60dWeeklyValidationCard({
  apiClient = backendApi,
  testId = "locked-60d-weekly-validation-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.locked60dWeeklyLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.locked60dWeeklyLatest()) || null;
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

  const verdict = report?.final_verdict || null;
  const cmp = report?.compare_40d_monthly || {};
  const rv = report?.risk_veto_recheck || {};
  const wm = report?.worst_month_holdout || {};
  const conclusions = Array.isArray(report?.conclusions) ? report.conclusions : [];
  const nextSteps = Array.isArray(report?.next_steps) ? report.next_steps : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#22c55e33">
        <SectionLabel>🔒 60d/weekly 고정 룰 재검증 (LOCKED_V1 · holdout)</SectionLabel>

        <div data-testid="lk-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구/백테스트 결과이며 실전매매 권고가 아닙니다.</b> 룰은 검증 전 고정,
          종목 선택은 직전 lookback 만(look-ahead 금지) · 자동 적용 / EXE 빌드 0건 · 수익 보장 없음.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="lk-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="lk-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="lk-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            ⏳ 고정 룰 holdout 재검증 분석 중입니다…
          </div>
        ) : null}
        {state === "error" ? (
          <div data-testid="lk-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
            결과를 불러올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="lk-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 고정 룰 재검증 리포트가 없습니다. CLI <code>run_locked_60d_weekly_validation.py</code>
            실행 후 표시됩니다 (설치 오류 아님).
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="lk-verdict" style={{
              padding: "8px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[verdict] || "#f1f5f9", border: "1px solid var(--c-border)",
            }}>
              <div style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)",
                            color: _V_COLOR[verdict] || "var(--c-text)" }}>
                locked rule verdict: <span data-testid="lk-final-verdict">{verdict}</span>
              </div>
              <div data-testid="lk-rule" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
                {report.locked_rule_name} · rule_locked={String(report.rule_locked_before_validation)} ·
                no_look_ahead={String(report.no_look_ahead)}
              </div>
              <div data-testid="lk-exe-rec" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 2 }}>
                EXE 재빌드 권고: <b>{report.exe_rebuild_recommendation}</b>
              </div>
              <div data-testid="lk-live" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginTop: 2 }}>
                실전매매 권고: <b>{String(report.live_trading_recommendation)}</b> (항상 false) · {report.paper_rehearsal_recommendation}
              </div>
            </div>

            <div data-testid="lk-holdouts" style={{ marginBottom: 4 }}>
              <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)" }}>Holdout 결과 (point-in-time)</div>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                original 6M: {_h(report.original_6m_replay)}
              </div>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                last-20D: {_h(report.last20_holdout)}
              </div>
              <div data-testid="lk-h40" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)" }}>
                <b>last-40D (주 판정): {_h(report.last40_holdout)}</b>
              </div>
              <div data-testid="lk-wm" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                worst-month({wm.worst_month ?? "—"}): {_h(wm)} · 방어 {String(wm.defended)}
                {" (baseline "}{wm.baseline_worst_month_return ?? "—"}%)
              </div>
            </div>

            <div data-testid="lk-compare" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              비교: static_all {cmp?.static_all?.forward_return_pct ?? "—"}% · 40d/monthly {cmp?.forward_40d_monthly?.forward_return_pct ?? "—"}% ·
              60d/weekly full {cmp?.locked_60d_weekly_full?.forward_return_pct ?? "—"}%
            </div>

            <div data-testid="lk-rv" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              RISK_VETO 우위: {String(rv.risk_veto_better)} (RV {rv?.risk_veto_only?.forward_return_pct ?? "—"}% vs OFF {rv?.agent_off?.forward_return_pct ?? "—"}%)
            </div>

            <div data-testid="lk-slip" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              slippage stress 10bps OK: {String(report?.slippage_stress?.slippage_10bps_ok)}
            </div>

            <div data-testid="lk-conclusions" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 6 }}>
              결론: {conclusions.join(" / ")}
            </div>
            {nextSteps.length > 0 ? (
              <div data-testid="lk-next" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 4 }}>
                다음 단계: {nextSteps.join(" · ")}
              </div>
            ) : null}
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default Locked60dWeeklyValidationCard;
