/**
 * Intraday1YScaledValidationCard — 1년 데이터 10/25/50 확장 검증 (read-only).
 *
 * 5상태: 데이터 없음 / 수집 중 / 분석 중(loading) / 완료 / 실패(error).
 * 핵심: scale verdict, 10/25/50 단계 결과, 1년 품질, rule hash, breadth 의존, EXE 권고,
 * Paper rehearsal 여부. invariant(테스트 lock): 실전/주문/적용/자동매매 시작 버튼 0개
 * (새로고침/복사만), input/textarea 0개, 한글 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = {
  SCALE_RESEARCH_CONFIRMED: "#166534", SCALE_PAPER_CANDIDATE: "#15803d",
  SCALE_WATCH: "#0369a1", SCALE_WEAK: "#a16207", SCALE_FAIL: "#b91c1c", SCALE_BLOCKED: "#b91c1c",
};
const _V_BG = {
  SCALE_RESEARCH_CONFIRMED: "#dcfce7", SCALE_PAPER_CANDIDATE: "#dcfce7",
  SCALE_WATCH: "#e0f2fe", SCALE_WEAK: "#fef9c3", SCALE_FAIL: "#fee2e2", SCALE_BLOCKED: "#fee2e2",
};

function _stage(x) {
  if (!x || x.forward_return_pct === undefined) return "미실행";
  return `${x.forward_return_pct}% · PF ${x.median_pf} · MDD ${x.forward_mdd_pct}% · 거래 ${x.total_trades} · ${x.verdict || ""}`;
}

export function Intraday1YScaledValidationCard({
  apiClient = backendApi,
  testId = "intraday-1y-scaled-validation-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.intraday1yScaledValidationLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.intraday1yScaledValidationLatest()) || null;
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
  const q = report?.data_quality || {};
  const mq = report?.monthly_quarterly || {};
  const breadth = report?.breadth_dependency || {};
  const conclusions = Array.isArray(report?.conclusions) ? report.conclusions : [];
  const nextSteps = Array.isArray(report?.next_steps) ? report.next_steps : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#f59e0b33">
        <SectionLabel>📅 1년 데이터 10/25/50 확장 검증 (locked rule)</SectionLabel>

        <div data-testid="y1-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구/백테스트 결과이며 실전매매 권고가 아닙니다.</b> 룰은 변경 없이(hash 검증)
          적용, 종목 선택은 직전 lookback 만(look-ahead 금지) · 자동 적용 / EXE 빌드 0건 · 수익 보장 없음.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="y1-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="y1-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="y1-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            ⏳ 1년 데이터 수집/분석 중입니다…
          </div>
        ) : null}
        {state === "error" ? (
          <div data-testid="y1-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
            결과를 불러올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="y1-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 1년 확장 검증 리포트가 없습니다. CLI <code>run_intraday_1y_scaled_validation.py</code>
            실행 후 표시됩니다 (데이터 수집 선행 · 설치 오류 아님).
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="y1-verdict" style={{
              padding: "8px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[verdict] || "#f1f5f9", border: "1px solid var(--c-border)",
            }}>
              <div style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)",
                            color: _V_COLOR[verdict] || "var(--c-text)" }}>
                scale verdict: <span data-testid="y1-final-verdict">{verdict}</span>
              </div>
              <div data-testid="y1-rule" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
                {report.locked_rule_name} · rule_hash_match={String(report.rule_hash_match)} ·
                거래일 {report.trading_days ?? 0} · 품질 {q.quality_status ?? "—"}
              </div>
              <div data-testid="y1-exe-rec" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 2 }}>
                EXE 재빌드 권고: <b>{report.exe_rebuild_recommendation}</b>
              </div>
              <div data-testid="y1-live" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginTop: 2 }}>
                실전매매 권고 <b>{String(report.live_trading_recommendation)}</b> · 실제주문 허용 <b>{String(report.real_order_allowed)}</b> ·
                dry_run 필수 <b>{String(report.dry_run_required)}</b>
              </div>
            </div>

            <div data-testid="y1-stages" style={{ marginBottom: 4 }}>
              <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)" }}>단계별 (10 → 25 → 50)</div>
              <div data-testid="y1-s10" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>10종목: {_stage(report.stage_10)}</div>
              <div data-testid="y1-s25" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>25종목: {_stage(report.stage_25)}</div>
              <div data-testid="y1-s50" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)" }}><b>50종목: {_stage(report.stage_50)}</b></div>
            </div>

            <div data-testid="y1-breadth" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              breadth: 10 {breadth.stage10_return ?? "—"}% / 25 {breadth.stage25_return ?? "—"}% /
              50 {breadth.stage50_return ?? "—"}% · split decay {breadth.split_decay_pp ?? "—"}pp
            </div>

            <div data-testid="y1-mq" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              월별: 양월 {mq.positive_months ?? "—"} / 음월 {mq.negative_months ?? "—"} ·
              worst {mq.worst_month ?? "—"} ({mq.worst_month_return ?? "—"}%)
            </div>

            <div data-testid="y1-conclusions" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 6 }}>
              결론: {conclusions.join(" / ")}
            </div>
            {nextSteps.length > 0 ? (
              <div data-testid="y1-next" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 4 }}>
                다음 단계: {nextSteps.join(" · ")}
              </div>
            ) : null}
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default Intraday1YScaledValidationCard;
