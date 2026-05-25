/**
 * ForwardUniverseCard — WF-6M point-in-time universe 선별 검증 (read-only).
 *
 * 4상태: 데이터 없음 / 분석 중(loading) / 완료 / 실패(error).
 * 핵심: forward universe verdict, best selector, selector stability, EXE 재빌드 권고,
 * Paper rehearsal 여부, static vs forward, 반복 선택/제외 종목.
 * invariant(테스트 lock): 실전/주문/적용/자동매매 시작 버튼 0개(새로고침/복사만),
 * input/textarea 0개, 한글 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = {
  UNIVERSE_PAPER_CANDIDATE: "#166534", UNIVERSE_WATCH: "#0369a1",
  UNIVERSE_WEAK: "#a16207", UNIVERSE_FAIL: "#b91c1c",
};
const _V_BG = {
  UNIVERSE_PAPER_CANDIDATE: "#dcfce7", UNIVERSE_WATCH: "#e0f2fe",
  UNIVERSE_WEAK: "#fef9c3", UNIVERSE_FAIL: "#fee2e2",
};

export function ForwardUniverseCard({
  apiClient = backendApi,
  testId = "forward-universe-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.forwardUniverseLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.forwardUniverseLatest()) || null;
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

  const verdict = report?.final_universe_verdict || null;
  const best = report?.best_selector || {};
  const selectors = Array.isArray(report?.selector_results) ? report.selector_results : [];
  const svf = report?.static_vs_forward || {};
  const repSel = Array.isArray(report?.repeated_selected) ? report.repeated_selected : [];
  const repExcl = Array.isArray(report?.repeated_excluded) ? report.repeated_excluded : [];
  const conclusions = Array.isArray(report?.conclusions) ? report.conclusions : [];
  const nextSteps = Array.isArray(report?.next_steps) ? report.next_steps : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#0ea5e933">
        <SectionLabel>🧭 WF-6M Forward Universe 선별 검증 (point-in-time)</SectionLabel>

        <div data-testid="fu-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구/백테스트 결과이며 실전매매 권고가 아닙니다.</b> 종목 선별은 *과거
          lookback 만* 사용(look-ahead 금지) · 자동 적용 / EXE 빌드 0건 · 수익 보장 없음.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="fu-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="fu-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="fu-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            ⏳ forward universe 검증 분석 중입니다…
          </div>
        ) : null}
        {state === "error" ? (
          <div data-testid="fu-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
            결과를 불러올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="fu-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 forward universe 리포트가 없습니다. CLI <code>run_forward_universe_validation.py</code>
            실행 후 표시됩니다 (설치 오류 아님).
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="fu-verdict" style={{
              padding: "8px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[verdict] || "#f1f5f9", border: "1px solid var(--c-border)",
            }}>
              <div style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)",
                            color: _V_COLOR[verdict] || "var(--c-text)" }}>
                forward universe verdict: <span data-testid="fu-final-verdict">{verdict}</span>
              </div>
              <div data-testid="fu-best" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
                best selector: {best.selector} · forward {best.forward_return_pct ?? "—"}% · MDD {best.forward_mdd_pct ?? "—"}% ·
                거래 {best.total_trades ?? 0} · stability {best.universe_stability ?? "—"}
              </div>
              <div data-testid="fu-exe-rec" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 2 }}>
                EXE 재빌드 권고: <b>{report.exe_rebuild_recommendation}</b>
              </div>
              <div data-testid="fu-live" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginTop: 2 }}>
                실전매매 권고: <b>{String(report.live_trading_recommendation)}</b> (항상 false) · {report.paper_rehearsal_recommendation}
              </div>
            </div>

            <div data-testid="fu-selectors" style={{ marginBottom: 4 }}>
              <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)" }}>Selector 결과 (forward)</div>
              {selectors.filter((s) => !s.look_ahead_warning).slice(0, 8).map((s) => (
                <div key={s._label} style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                  {s.selector}: {s.verdict} · {s.forward_return_pct}% · MDD {s.forward_mdd_pct}% ·
                  거래 {s.total_trades} · stab {s.universe_stability}
                </div>
              ))}
              {selectors.some((s) => s.look_ahead_warning) ? (
                <div data-testid="fu-lookahead-note" style={{ fontSize: "var(--fs-xs)", color: "#a16207" }}>
                  ※ STATIC_IN_SAMPLE_TOP10 은 look-ahead(편향) 참고용 — 최종 후보 제외.
                </div>
              ) : null}
            </div>

            <div data-testid="fu-static-vs" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              static ALL {svf.static_all_return ?? "—"}% · in-sample top10(편향) {svf.static_in_sample_top10_return ?? "—"}% ·
              best forward {svf.best_forward_return ?? "—"}%
            </div>

            {repSel.length > 0 ? (
              <div data-testid="fu-repeated" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
                반복 선택: {repSel.join(", ")}
              </div>
            ) : null}
            {repExcl.length > 0 ? (
              <div data-testid="fu-excluded" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 4 }}>
                반복 제외: {repExcl.slice(0, 12).join(", ")}
              </div>
            ) : null}

            <div data-testid="fu-overfit" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              overfit/look-ahead 경고: {String(report?.overfit_warning?.warning)}
            </div>

            <div data-testid="fu-conclusions" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 6 }}>
              결론: {conclusions.join(" / ")}
            </div>
            {nextSteps.length > 0 ? (
              <div data-testid="fu-next" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 4 }}>
                다음 단계: {nextSteps.join(" · ")}
              </div>
            ) : null}
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default ForwardUniverseCard;
