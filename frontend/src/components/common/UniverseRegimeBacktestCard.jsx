/**
 * UniverseRegimeBacktestCard — 종목군 × 시장국면 다변화 백테스트 (CHECKLIST-05, read-only).
 *
 * 표시: universe 그룹별 PF/best, regime별 PF, best/worst group, Risk Filter 효과, verdict,
 * research_only/auto_apply(false). 버튼은 새로고침·복사만.
 * invariant(테스트 lock): 적용/실전/주문/자동매매 버튼 0개, input 0개, 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _pf = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(3));

export function UniverseRegimeBacktestCard({
  apiClient = backendApi,
  testId = "universe-regime-backtest-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.universeRegimeBacktestLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.universeRegimeBacktestLatest()) || null;
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

  const groups = report?.group_results || {};
  const regimes = report?.regime_results || {};
  const pitRegimes = report?.point_in_time_regime_results || {};
  const su = report?.strong_uptrend_comparison || null;

  return (
    <div data-testid={testId}>
      <Card accentColor="#a855f733">
        <SectionLabel>🌐 종목군 × 시장국면 다변화 백테스트 (CHECKLIST-05, 연구용)</SectionLabel>

        <div data-testid="urb-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ <b>연구용 백테스트이며 실전매매 권고가 아닙니다.</b> regime 라벨은 사후 attribution
          전용(진입 신호 미사용). 어떤 종목군/전략도 런타임에 <b>등록/자동 적용되지 않습니다</b>
          (research_only · auto_apply=false).
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="urb-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="urb-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? <div data-testid="urb-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>⏳ 불러오는 중…</div> : null}
        {state === "error" ? <div data-testid="urb-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>결과를 불러올 수 없습니다.</div> : null}
        {state === "empty" ? <div data-testid="urb-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>API client 미연결.</div> : null}
        {state === "notready" ? (
          <div data-testid="urb-notready" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-2)", padding: "6px 10px",
            borderRadius: 6, background: "#fef9c3", border: "1px solid var(--c-border)",
          }}>
            verdict: <b>{report?.verdict || "NEED_MORE_DATA"}</b> · 리포트 없음 —
            <code> run_universe_regime_backtest.py --write-latest </code> 실행 후 표시.
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="urb-verdict" style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              verdict: {report.verdict}
            </div>
            <div data-testid="urb-conclusion" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 6 }}>
              {(report.conclusion || []).join(" / ")}
            </div>

            <div data-testid="urb-groups" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 6 }}>
              <b>그룹별 best 전략 (net PF):</b>
              {Object.entries(groups).map(([g, gr]) => (
                <div key={g}>{g}: {gr.present ? `${gr.best_single_strategy || "—"}(${_pf(gr.best_single_pf)}) · Council ${_pf(gr.council?.net_pf)} · +RF ${_pf(gr.council_risk_filter_pf)}` : "데이터 없음 (NEED_MORE_DATA)"}</div>
              ))}
            </div>

            <div data-testid="urb-regimes" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 6 }}>
              <b>국면별 Council net PF — ⚠️ posthoc(사후·look-ahead, 매매 불가):</b>
              {Object.entries(regimes).map(([rg, blk]) => (
                <div key={rg}>{rg}: ORB {_pf(blk.ORB?.net_pf)} · MOM {_pf(blk.MOMENTUM?.net_pf)} · Council {_pf(blk.COUNCIL?.net_pf)}</div>
              ))}
            </div>

            <div data-testid="urb-pit-regimes" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 6 }}>
              <b>국면별 Council net PF — ✅ point-in-time(진입 시점 정보만, look-ahead 없음):</b>
              {Object.entries(pitRegimes).map(([rg, blk]) => (
                <div key={rg}>{rg}: ORB {_pf(blk.ORB?.net_pf)} · MOM {_pf(blk.MOMENTUM?.net_pf)} · Council {_pf(blk.COUNCIL?.net_pf)}</div>
              ))}
              <div style={{ color: "var(--c-text-3)" }}>posthoc↔PIT 일치율: {_pf(report.posthoc_vs_pit_agreement_rate)}</div>
            </div>

            {su ? (
              <div data-testid="urb-strong-uptrend" style={{
                fontSize: "var(--fs-xs)", color: su.hint_survives_without_lookahead ? "var(--c-text)" : "#7f1d1d",
                marginBottom: 6, padding: "4px 8px", borderRadius: 6, background: "#fff7ed",
                border: "1px solid #fed7aa",
              }}>
                <b>STRONG_UPTREND 힌트 검증:</b> posthoc(look-ahead) Council PF {_pf(su.posthoc_council_pf)} →
                PIT(entry-known) Council PF {_pf(su.pit_council_pf)} (n={su.pit_council_trade_count}) ·
                look-ahead 없이 유지: <b>{String(su.hint_survives_without_lookahead)}</b>
                {su.hint_survives_without_lookahead ? "" : " — 사후 힌트는 look-ahead 제거 시 사라짐(매매 불가)"}
              </div>
            ) : null}

            <div data-testid="urb-survivors" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 4 }}>
              살아남은(PF≥1) 그룹×전략: <b>{(report.survivors || []).length ? report.survivors.join(", ") : "없음"}</b> ·
              실패 그룹: {(report.failures || []).length ? report.failures.join(", ") : "없음"}
            </div>
            <div data-testid="urb-auto" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
              research_only <b>{String(report.is_research_only)}</b> · 자동 적용 <b>{String(report.auto_apply_allowed)}</b> · 런타임 반영 <b>{String(report.applied_to_runtime)}</b>
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default UniverseRegimeBacktestCard;
