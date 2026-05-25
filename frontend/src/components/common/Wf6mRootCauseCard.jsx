/**
 * WF-6M Root Cause Card — 손실 원인분해 (read-only).
 *
 * 4상태: 데이터 없음 / 분석 중(loading) / 완료 / 실패(error).
 * 절대 invariant (테스트로 lock): 매수/매도/실전/적용/자동매매 시작 버튼 0개
 * (새로고침/복사만), input/textarea 0개, 한글 위험 경고 문구 노출.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

function _fmt(x, suf = "") {
  return x === null || x === undefined ? "—" : `${Number(x).toFixed(2)}${suf}`;
}

export function Wf6mRootCauseCard({
  apiClient = backendApi,
  testId = "wf6m-root-cause-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading"); // loading|complete|empty|error
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.wf6mRootCauseLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.wf6mRootCauseLatest()) || null;
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

  const b = report?.baseline || {};
  const causes = Array.isArray(report?.loss_causes_top) ? report.loss_causes_top : [];
  const conclusions = Array.isArray(report?.conclusions) ? report.conclusions : [];
  const cost = report?.cost_sensitivity || {};
  const agent = report?.agent_damage || {};
  const sym = report?.symbol_group || {};

  return (
    <div data-testid={testId}>
      <Card accentColor="#ef444433">
        <SectionLabel>🔬 WF-6M 손실 원인분해 (Root Cause)</SectionLabel>

        <div data-testid="wf6mrc-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구/백테스트 결과이며 실전매매 권고가 아닙니다.</b> 자동 적용 / 실전 전환 /
          주문 0건. 수익을 보장하지 않습니다.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="wf6mrc-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="wf6mrc-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="wf6mrc-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            ⏳ 원인분해 분석 중입니다…
          </div>
        ) : null}
        {state === "error" ? (
          <div data-testid="wf6mrc-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
            결과를 불러올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="wf6mrc-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 원인분해 리포트가 없습니다. CLI <code>run_wf_6m_root_cause_analysis.py</code>
            실행 후 표시됩니다 (설치 오류 아님).
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="wf6mrc-baseline" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 6,
            }}>
              Baseline: 1000만원 → {b.final_equity ? Math.round(b.final_equity).toLocaleString() : "—"} KRW
              ({_fmt(b.total_return_pct, "%")}) · PF {_fmt(b.profit_factor)} ·
              MDD {_fmt(b.max_drawdown_pct, "%")} · 거래 {b.trade_count ?? 0} ·
              평균보유 {_fmt(b.avg_hold_minutes)}분
            </div>

            <div data-testid="wf6mrc-causes" style={{ marginBottom: 6 }}>
              <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)", marginBottom: 2 }}>
                손실 원인 TOP
              </div>
              {causes.map((c) => (
                <div key={c.rank} style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                  [{c.rank}] {c.cause} — <span style={{ color: "var(--c-text-3)" }}>{c.evidence}</span>
                  {" → "}{c.fix}
                </div>
              ))}
            </div>

            <div data-testid="wf6mrc-cost" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              비용 민감도: baseline {_fmt(cost?.baseline_cost?.total_return_pct, "%")} →
              비용0 {_fmt(cost?.zero_all_cost?.total_return_pct, "%")} (왕복비용 {cost?.roundtrip_cost_pct ?? "—"}%)
            </div>
            <div data-testid="wf6mrc-agent" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              Agent: council {_fmt(agent?.council_baseline_return_pct, "%")} vs
              AGENT_OFF {_fmt(agent?.agent_off_return_pct, "%")} · veto 도움 {String(agent?.veto_helped)}
            </div>
            <div data-testid="wf6mrc-exclude" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              EXCLUDE {Array.isArray(sym?.exclude_symbols) ? sym.exclude_symbols.length : 0}종목 제거 →
              {" "}{_fmt(sym?.exclude_removed_result?.total_return_pct, "%")}
              {" "}({_fmt(sym?.improvement_pct_points, "%p")})
            </div>

            <div data-testid="wf6mrc-conclusions" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 6 }}>
              결론: {conclusions.join(" / ")}
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default Wf6mRootCauseCard;
