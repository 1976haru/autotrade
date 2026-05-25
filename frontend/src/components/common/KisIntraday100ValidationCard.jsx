/**
 * KIS-INTRADAY-100-VALIDATION-01 — 실제 KIS 분봉 100종목 전략 최종 판정 카드 (read-only).
 *
 * 주식일별분봉조회(read-only) 로 수집한 100종목 내외의 실제 분봉으로 현재 전략
 * (ORB/Momentum/Gap/VWAP + Agent Council + RiskOfficer + exit_plan + quality_score)
 * 의 가능성을 종합 판정한 결과를 표시한다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수 / 매도 / 실전 전환 / 자동 적용 / 승인 버튼 0개 (새로고침/복사만).
 *  - secret/account 원문 표시 0건. input/textarea 0개.
 *  - "실전 승인 아님" / "수익 보장 아님" / "KIS 주문 API 호출 0건" 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _J_COLOR = {
  PROMISING_FOR_PAPER_TEST: "#166534", WORTH_MORE_RESEARCH: "#0369a1",
  TOO_EARLY_TO_JUDGE: "#a16207", STRATEGY_NEEDS_TUNING: "#9a3412",
  NOT_PROMISING_ON_CURRENT_DATA: "#b91c1c", BLOCKED_BY_DATA: "#b91c1c",
  DATA_NOT_RELIABLE: "#b91c1c",
};
const _J_BG = {
  PROMISING_FOR_PAPER_TEST: "#dcfce7", WORTH_MORE_RESEARCH: "#e0f2fe",
  TOO_EARLY_TO_JUDGE: "#fef9c3", STRATEGY_NEEDS_TUNING: "#ffedd5",
  NOT_PROMISING_ON_CURRENT_DATA: "#fee2e2", BLOCKED_BY_DATA: "#fee2e2",
  DATA_NOT_RELIABLE: "#fee2e2",
};

function _fmt(x) {
  return x === null || x === undefined ? "평가불가" : Number(x).toFixed(2);
}

export function KisIntraday100ValidationCard({
  apiClient = backendApi,
  testId = "kis-intraday-100-validation-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.kisIntraday100ValidationLatest !== "function") return;
    try {
      setReport((await apiClient.kisIntraday100ValidationLatest()) || null);
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
    }
  }, [apiClient]);

  useEffect(() => { refresh(); }, [refresh]);

  const copyReport = useCallback(() => {
    try {
      const text = JSON.stringify(report || {}, null, 2);
      if (navigator?.clipboard?.writeText) {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }
    } catch { /* clipboard 미가용 무시 */ }
  }, [report]);

  const judgement = report?.user_final_judgement || null;
  const top10 = Array.isArray(report?.top_10_promising_symbols)
    ? report.top_10_promising_symbols : [];
  const excluded = Array.isArray(report?.excluded_symbols) ? report.excluded_symbols : [];
  const helped = Array.isArray(report?.agent_helped_symbols) ? report.agent_helped_symbols : [];
  const hurt = Array.isArray(report?.agent_hurt_symbols) ? report.agent_hurt_symbols : [];
  const noTrade = Array.isArray(report?.agent_no_trade_symbols) ? report.agent_no_trade_symbols : [];
  const nextActions = Array.isArray(report?.next_actions) ? report.next_actions : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#0ea5e933">
        <SectionLabel>📈 KIS 실제 분봉 100종목 전략 판정 (KIS-INTRADAY-100)</SectionLabel>

        <div data-testid="kis100-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          KIS <b>주식일별분봉조회(read-only 시세)</b>로 수집한 100종목 내외의 <b>실제 분봉</b>
          으로 ORB/Momentum/Gap/VWAP + Agent Council 결합 전략의 가능성을 종합 판정합니다.
          <b> 자동 적용 아님 · 실전 승인 아님 · 수익 보장 아님 · KIS 주문 API 호출 0건.</b>
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="kis100-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="kis100-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {error ? (
          <div data-testid="kis100-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>KIS 분봉 판정 결과를 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {report && report.available === false ? (
          <div data-testid="kis100-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 6,
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 KIS 분봉 수집/검증 리포트가 없습니다. CLI
            <code> run_kis_intraday_100_validation.py --write-latest </code>
            실행 후 표시됩니다 (설치 오류 아님).
          </div>
        ) : null}

        {report && report.available !== false ? (
          <>
            <div data-testid="kis100-judgement" style={{
              padding: "8px 10px", borderRadius: 6, marginBottom: 8,
              background: _J_BG[judgement] || "#f1f5f9",
              border: "1px solid var(--c-border)",
            }}>
              <div style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)",
                            color: _J_COLOR[judgement] || "var(--c-text)" }}>
                내 전략 최종 판단:{" "}
                <span data-testid="kis100-user-judgement">{judgement}</span>
              </div>
              <div data-testid="kis100-one-liner" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 2,
              }}>
                현재 KIS 실제 분봉 {report.collected_symbols ?? 0}종목 기준으로, 내 매매기법 +
                Agent 전략은 <b>{report.one_line_conclusion}</b>
              </div>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
                개발자 verdict:{" "}
                <span data-testid="kis100-dev-verdict">{report.developer_verdict}</span>
                {" · "}Paper 리허설 권고:{" "}
                <span data-testid="kis100-paper-rec">
                  {report.paper_rehearsal_recommended ? "예" : "아니오(아직)"}
                </span>
                {" · 실전 승인 아님 · 수익 보장 아님"}
              </div>
            </div>

            <div data-testid="kis100-collection" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              수집: 요청 {report.requested_symbols ?? 0} · 성공 {report.collected_symbols ?? 0} ·
              실패 {report.collection_failed ?? 0} · 거래일 {report.trading_day_count ?? 0}일 ·
              bar {report.bar_size_minutes ?? "?"}분 · total_bars {report.total_bars ?? 0}
            </div>

            <div data-testid="kis100-quality" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              품질 PASS <span data-testid="kis100-pass">{report.pass_count ?? 0}</span> ·
              WARN {report.warn_count ?? 0} · BLOCKED {report.blocked_count ?? 0}
            </div>

            <div data-testid="kis100-metrics" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              total_trades <span data-testid="kis100-trades">{report.total_trades ?? 0}</span> ·
              median win_rate {_fmt(report.median_win_rate)} · PF {_fmt(report.median_profit_factor)} ·
              expectancy {_fmt(report.median_expectancy)} · MDD {_fmt(report.median_mdd)} ·
              WF {_fmt(report.median_walk_forward_score)}
            </div>

            <div data-testid="kis100-agent" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              Agent 효과: {report.agent_value_summary} · 도움 {helped.length} · 방해 {hurt.length} ·
              무진입 {noTrade.length}
              {report.stress_fail_count !== null && report.stress_fail_count !== undefined
                ? ` · stress FAIL ${report.stress_fail_count}` : ""}
            </div>

            {top10.length > 0 ? (
              <div data-testid="kis100-top10" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>
                상위 종목: {top10.map((p) => p.symbol).join(", ")}
              </div>
            ) : null}

            {excluded.length > 0 ? (
              <div data-testid="kis100-excluded" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 4,
              }}>
                제외 종목 {excluded.length}개
              </div>
            ) : null}

            {nextActions.length > 0 ? (
              <div data-testid="kis100-next" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>다음 단계: {nextActions.join(" · ")}</div>
            ) : null}
          </>
        ) : null}

        <div data-testid="kis100-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 판정은 실제 KIS 분봉 기준 전략 가능성 평가 전용입니다. <b>threshold/파라미터는
          자동 적용되지 않으며</b>, 실전 전환 승인과 무관하고, 수익을 보장하지 않습니다.
          수집·검증 과정에서 <b>KIS 주문 API 는 호출하지 않았습니다(read-only 시세 조회만).</b>
          자격정보(계좌번호/secret)는 표시되지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default KisIntraday100ValidationCard;
