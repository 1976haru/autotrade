/**
 * WF-6M-50SYMBOLS-01 — 6개월·50종목·1000만원 전략 종합 검증 카드 (read-only).
 *
 * 포트폴리오 자금곡선(1000만원) + 종목 등급화 + 전략 생존/사망 + 실전 가능성 + 업그레이드
 * 방향 + 최종 판정을 표시한다. Paper/Backtest only.
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수 / 매도 / 실전 전환 / 자동 적용 / 승인 버튼 0개 (새로고침/복사만).
 *  - secret/account 원문 표시 0건. input/textarea 0개.
 *  - "Paper/Backtest" / "실전 승인 아님" / "수익 보장 아님" 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = {
  PAPER_REHEARSAL_WORTHY: "#166534", WORTH_MORE_RESEARCH: "#0369a1",
  RESEARCH_ONLY: "#a16207", NOT_RECOMMENDED: "#b91c1c",
};
const _V_BG = {
  PAPER_REHEARSAL_WORTHY: "#dcfce7", WORTH_MORE_RESEARCH: "#e0f2fe",
  RESEARCH_ONLY: "#fef9c3", NOT_RECOMMENDED: "#fee2e2",
};

function _fmt(x, suf = "") {
  return x === null || x === undefined ? "평가불가" : `${Number(x).toFixed(2)}${suf}`;
}
function _krw(x) {
  return x === null || x === undefined ? "—" : `${Math.round(Number(x)).toLocaleString()} KRW`;
}

export function Wf6m50SymbolsCard({
  apiClient = backendApi,
  testId = "wf-6m-50symbols-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.wf6m50SymbolsLatest !== "function") return;
    try {
      setReport((await apiClient.wf6m50SymbolsLatest()) || null);
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

  const verdict = report?.final_verdict || null;
  const grades = report?.grades || {};
  const top10 = Array.isArray(report?.top_10) ? report.top_10 : [];
  const bottom10 = Array.isArray(report?.bottom_10) ? report.bottom_10 : [];
  const weaknesses = Array.isArray(report?.weaknesses_top3) ? report.weaknesses_top3 : [];
  const strengths = Array.isArray(report?.strengths_top3) ? report.strengths_top3 : [];
  const upgrades = Array.isArray(report?.upgrade_directions) ? report.upgrade_directions : [];
  const nextSteps = Array.isArray(report?.next_steps) ? report.next_steps : [];
  const alive = Array.isArray(report?.strategy_alive) ? report.strategy_alive : [];
  const dead = Array.isArray(report?.strategy_dead) ? report.strategy_dead : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#8b5cf633">
        <SectionLabel>💼 6개월·50종목·1000만원 종합 검증 (WF-6M-50SYMBOLS)</SectionLabel>

        <div data-testid="wf6m-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          실제 KIS 5분봉으로 1000만원·동시보유 5·종목당 1~2백만원 기준 자금곡선을 시뮬레이션하고
          종목 등급화 + 전략 생존/사망 + 실전 가능성 + 업그레이드 방향을 종합합니다.
          <b> Paper/Backtest only · 자동 적용 아님 · 실전 승인 아님 · 수익 보장 아님.</b>
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="wf6m-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="wf6m-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {error ? (
          <div data-testid="wf6m-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>종합 검증 결과를 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {/* 수집 진행 중 안내 (데이터 없음 / 진행 중 / 완료 구분). */}
        {report && report.collection_status === "IN_PROGRESS" ? (
          <div data-testid="wf6m-collecting" style={{
            fontSize: "var(--fs-xs)", color: "#1e3a8a", marginBottom: 6,
            padding: "6px 10px", borderRadius: 6, background: "#eff6ff",
            border: "1px solid #bfdbfe",
          }}>
            ⏳ <b>데이터 수집 진행 중</b> — 완료{" "}
            {report.collection_counts?.completed ?? 0}/{report.collection_target ?? 50} ·
            실패 {report.collection_counts?.failed ?? 0} ·
            대기 {report.collection_counts?.pending ?? 0}. 수집 완료 후 종합 검증이 실행됩니다.
          </div>
        ) : null}

        {report && report.available === false && report.collection_status !== "IN_PROGRESS" ? (
          <div data-testid="wf6m-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 6,
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 6개월·50종목 종합 검증 리포트가 없습니다. CLI
            <code> run_wf_6m_50symbols_report.py --write-latest </code>
            실행 후 표시됩니다 (설치 오류 아님).
          </div>
        ) : null}

        {report && report.available !== false ? (
          <>
            <div data-testid="wf6m-verdict" style={{
              padding: "8px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[verdict] || "#f1f5f9", border: "1px solid var(--c-border)",
            }}>
              <div style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)",
                            color: _V_COLOR[verdict] || "var(--c-text)" }}>
                종합 판정: <span data-testid="wf6m-final-verdict">{verdict}</span>
                {" · 실전 가능성: "}
                <span data-testid="wf6m-live-possibility">{report.live_possibility}</span>
              </div>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
                6개월 history 충족: {report.enough_history ? "예" : `아니오(${report.trading_days}거래일)`}
                {" · 실전 승인 아님 · 수익 보장 아님"}
              </div>
            </div>

            <div data-testid="wf6m-capital" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              1000만원 → <span data-testid="wf6m-final-equity">{_krw(report.final_equity)}</span>
              {" (수익률 "}<span data-testid="wf6m-return">{_fmt(report.total_return_pct, "%")}</span>{")"}
              {" · 거래일 "}{report.trading_days ?? 0}{" · 하루 평균 거래 "}{_fmt(report.daily_avg_trades)}
            </div>

            <div data-testid="wf6m-metrics" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              승률 {_fmt(report.win_rate)} · 손익비 {_fmt(report.payoff_ratio)} ·
              PF {_fmt(report.profit_factor)} · MDD {_fmt(report.max_drawdown_pct, "%")} ·
              WF {_fmt(report.median_walk_forward_score)} · 월간 {_fmt(report.monthly_avg_return_pct, "%")}
            </div>

            <div data-testid="wf6m-grades" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              종목 등급: GO <span data-testid="wf6m-go">{grades.GO ?? 0}</span> ·
              WATCH {grades.WATCH ?? 0} · TUNE {grades.TUNE ?? 0} · EXCLUDE {grades.EXCLUDE ?? 0}
            </div>

            <div data-testid="wf6m-strategies" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              전략 생존: {alive.length ? alive.join(", ") : "없음"} · 사망:{" "}
              {dead.length ? dead.join(", ") : "없음"} · Agent 효과: {report.agent_value_summary}
            </div>

            {top10.length > 0 ? (
              <div data-testid="wf6m-top10" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>Top: {top10.map((g) => g.symbol).join(", ")}</div>
            ) : null}
            {bottom10.length > 0 ? (
              <div data-testid="wf6m-bottom10" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 4,
              }}>Bottom: {bottom10.map((g) => g.symbol).join(", ")}</div>
            ) : null}

            {strengths.length > 0 ? (
              <div data-testid="wf6m-strengths" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>강점: {strengths.join(" · ")}</div>
            ) : null}
            {weaknesses.length > 0 ? (
              <div data-testid="wf6m-weaknesses" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>약점: {weaknesses.join(" · ")}</div>
            ) : null}

            {report.agent_optimal_role ? (
              <div data-testid="wf6m-agent-role" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>Agent 최적 역할: {report.agent_optimal_role}</div>
            ) : null}

            {upgrades.length > 0 ? (
              <div data-testid="wf6m-upgrades" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>업그레이드 방향: {upgrades.join(" · ")}</div>
            ) : null}

            {nextSteps.length > 0 ? (
              <div data-testid="wf6m-next" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>다음 단계: {nextSteps.join(" · ")}</div>
            ) : null}
          </>
        ) : null}

        <div data-testid="wf6m-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 검증은 실제 5분봉 기준 <b>Paper/Backtest</b> 결과입니다. 비용(수수료+세금+슬리피지)
          반영 후 수치이며, <b>자동 적용되지 않고</b>, 실전 전환 승인과 무관하며, 수익을 보장하지
          않습니다. <b>실주문 / 실체결 0건.</b> 자격정보(계좌번호/secret)는 표시되지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default Wf6m50SymbolsCard;
