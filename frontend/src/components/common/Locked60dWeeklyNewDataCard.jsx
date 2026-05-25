/**
 * Locked60dWeeklyNewDataCard — 고정 룰을 추가 기간 새 데이터로 재검증 (read-only).
 *
 * 5상태: 데이터 없음 / 수집 중 / 분석 중(loading) / 완료 / 실패(error).
 * 핵심: new data verdict, 추가 기간/품질, rule lock·hash, old vs new, EXE 재빌드 권고,
 * Paper rehearsal 여부. invariant(테스트 lock): 실전/주문/적용/자동매매 시작 버튼 0개
 * (새로고침/복사만), input/textarea 0개, 한글 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = {
  NEW_DATA_RESEARCH_CONFIRMED: "#166534", NEW_DATA_PAPER_CANDIDATE: "#15803d",
  NEW_DATA_WATCH: "#0369a1", NEW_DATA_WEAK: "#a16207",
  NEW_DATA_INSUFFICIENT: "#6b7280", NEW_DATA_FAIL: "#b91c1c", NEW_DATA_BLOCKED: "#b91c1c",
};
const _V_BG = {
  NEW_DATA_RESEARCH_CONFIRMED: "#dcfce7", NEW_DATA_PAPER_CANDIDATE: "#dcfce7",
  NEW_DATA_WATCH: "#e0f2fe", NEW_DATA_WEAK: "#fef9c3",
  NEW_DATA_INSUFFICIENT: "#f1f5f9", NEW_DATA_FAIL: "#fee2e2", NEW_DATA_BLOCKED: "#fee2e2",
};

export function Locked60dWeeklyNewDataCard({
  apiClient = backendApi,
  testId = "locked-60d-weekly-new-data-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.locked60dWeeklyNewDataLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.locked60dWeeklyNewDataLatest()) || null;
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
  const q = report?.new_data_quality || {};
  const ndo = report?.new_data_only || {};
  const decay = report?.old_vs_new_decay || {};
  const conclusions = Array.isArray(report?.conclusions) ? report.conclusions : [];
  const nextSteps = Array.isArray(report?.next_steps) ? report.next_steps : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#a855f733">
        <SectionLabel>🆕 60d/weekly 고정 룰 — 추가 기간 새 데이터 재검증</SectionLabel>

        <div data-testid="nd-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구/백테스트 결과이며 실전매매 권고가 아닙니다.</b> 룰은 변경 없이(hash 검증)
          적용, 종목 선택은 직전 lookback 만(look-ahead 금지) · 자동 적용 / EXE 빌드 0건 · 수익 보장 없음.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="nd-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="nd-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="nd-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            ⏳ 수집/분석 중입니다…
          </div>
        ) : null}
        {state === "error" ? (
          <div data-testid="nd-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
            결과를 불러올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="nd-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 새 데이터 재검증 리포트가 없습니다. CLI
            <code> run_locked_60d_weekly_new_data_validation.py</code> 실행 후 표시됩니다 (설치 오류 아님).
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="nd-verdict" style={{
              padding: "8px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[verdict] || "#f1f5f9", border: "1px solid var(--c-border)",
            }}>
              <div style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)",
                            color: _V_COLOR[verdict] || "var(--c-text)" }}>
                new data verdict: <span data-testid="nd-final-verdict">{verdict}</span>
              </div>
              <div data-testid="nd-rule" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
                {report.locked_rule_name} · rule_hash_match={String(report.rule_hash_match)} ·
                no_parameter_change={String(report.no_parameter_change)} · no_look_ahead={String(report.no_look_ahead)}
              </div>
              <div data-testid="nd-exe-rec" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 2 }}>
                EXE 재빌드 권고: <b>{report.exe_rebuild_recommendation}</b>
              </div>
              <div data-testid="nd-live" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginTop: 2 }}>
                실전매매 권고: <b>{String(report.live_trading_recommendation)}</b> · 실제주문 허용: <b>{String(report.real_order_allowed)}</b> ·
                dry_run 필수: <b>{String(report.dry_run_required)}</b>
              </div>
            </div>

            <div data-testid="nd-data" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              추가 데이터: 새 거래일 <span data-testid="nd-new-days">{report.new_trading_days ?? 0}</span> ·
              기존 최신 {q.newest_existing ?? "—"} / 추가 최신 {q.newest_extra ?? "—"} ·
              중복제거 {q.duplicate_removed_count ?? 0} · 품질 {q.quality_status ?? "—"}
            </div>

            {verdict === "NEW_DATA_INSUFFICIENT" ? (
              <div data-testid="nd-insufficient" style={{
                fontSize: "var(--fs-xs)", color: "#6b7280", marginBottom: 4,
                padding: "5px 9px", borderRadius: 6, background: "#f1f5f9", border: "1px solid #e2e8f0",
              }}>
                ℹ️ 추가 forward 기간이 사실상 미존재(KIS 시세 최신일이 기존 데이터에 이미 포함).
                새 거래일이 ≥20 쌓이면 동일 고정 룰로 자동 재검증됩니다. (설치/오류 아님)
              </div>
            ) : null}

            <div data-testid="nd-newonly" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              New Data Only: {ndo.forward_return_pct !== undefined
                ? `${ndo.forward_return_pct}% · PF ${ndo.median_pf} · MDD ${ndo.forward_mdd_pct}% · 거래 ${ndo.total_trades}`
                : "미실행(데이터 부족)"}
            </div>
            {decay.decay_pp !== undefined ? (
              <div data-testid="nd-decay" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
                old vs new decay: {decay.decay_pp}pp (old {decay.old_full_return}% → new {decay.new_only_return}%)
              </div>
            ) : null}

            <div data-testid="nd-conclusions" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 6 }}>
              결론: {conclusions.join(" / ")}
            </div>
            {nextSteps.length > 0 ? (
              <div data-testid="nd-next" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 4 }}>
                다음 단계: {nextSteps.join(" · ")}
              </div>
            ) : null}
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default Locked60dWeeklyNewDataCard;
