/**
 * P-30: Paper Gate 성과 리포트 카드 — Agent 탭, read-only.
 *
 * 최소 100건 Paper 모의매매 기록 기반 성과 + 실전 전환 가능성 등급을 표시한다.
 * **실전 전환 / LIVE 승인 / 자동매매 ON / 매수·매도 버튼 0개** — 100건 미만이면
 * 실전 전환 검토 불가. 수익을 보장하지 않으며 실제 계좌 성과가 아니다.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

const _GRADE_LABEL = {
  INSUFFICIENT_SAMPLE: "표본 부족 (실전 전환 검토 불가)",
  BLOCKED_BY_RISK: "리스크로 인해 차단",
  NOT_READY: "실전 전환 보류",
  READY_FOR_EXTENDED_PAPER: "추가 Paper 검증 필요",
  READY_FOR_SMALL_LIVE_CANARY_REVIEW: "소액 실전(canary) 검토 가능",
};
const _GRADE_COLOR = {
  INSUFFICIENT_SAMPLE: "#64748b", BLOCKED_BY_RISK: "#dc2626",
  NOT_READY: "#b45309", READY_FOR_EXTENDED_PAPER: "#0ea5e9",
  READY_FOR_SMALL_LIVE_CANARY_REVIEW: "#16a34a",
};

const _pct = (v) => (v == null ? "n/a" : `${(v * 100).toFixed(1)}%`);
const _signed = (v) => (v == null ? "n/a" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`);
const _num = (v) => (v == null ? "n/a" : Number(v).toFixed(2));

export function PaperGateReportCard({
  apiClient = backendApi,
  testId = "paper-gate-report-card",
  limit = 1000,
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (typeof apiClient.agentPaperGateReport !== "function") return;
    try {
      const r = await apiClient.agentPaperGateReport({ limit });
      setReport(r || null);
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
    }
  }, [apiClient, limit]);

  useEffect(() => { refresh(); }, [refresh]);

  const s = report?.sample || {};
  const p = report?.performance || {};
  const rd = report?.readiness || {};
  const grade = rd.grade || "UNKNOWN";
  const strategies = report?.strategy_performance?.strategies || [];
  const blocked = report?.blocked_reasons_top || [];
  const oq = report?.order_quality || {};
  const pi = report?.portfolio_integrity || {};
  const actions = report?.required_actions || [];

  return (
    <Card accentColor="#22c55e33">
      <div data-testid={testId}>
        <SectionLabel>🧪 Paper Gate 성과 리포트</SectionLabel>

        <div data-testid="pgr-disclaimer"
             style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8 }}>
          본 리포트는 Paper 모의매매 기준이며 실제 계좌 성과가 아닙니다.
          100건 미만이면 실전 전환 검토 불가입니다.
          실전 전환은 별도 수동 승인과 Live 자금 검토가 필요하며 수익을 보장하지 않습니다.
        </div>

        {error && (
          <div data-testid="pgr-error"
               style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6 }}>
            {error}
          </div>
        )}

        {/* 최종 등급 */}
        {report && (
          <div data-testid="pgr-grade"
               style={{ marginBottom: 8, padding: "6px 10px", borderRadius: 6,
                        background: "var(--c-surface-2)",
                        borderLeft: `4px solid ${_GRADE_COLOR[grade] || "#64748b"}` }}>
            <span style={{ fontWeight: "var(--fw-bold)", color: _GRADE_COLOR[grade] }}>
              실전 전환 가능성: {grade}
            </span>
            <span style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
              {" "}· {_GRADE_LABEL[grade] || grade}
            </span>
            <div data-testid="pgr-canary"
                 style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
              소액 실전(canary) 검토 가능: {rd.can_review_live_canary ? "예 (자동 전환 아님)" : "아니오"}
            </div>
          </div>
        )}

        {/* 표본 */}
        {report && (
          <div data-testid="pgr-sample"
               style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 8 }}>
            판단 {s.total_decisions ?? 0} · 주문 {s.total_orders ?? 0} · 체결 {s.filled_orders ?? 0}
            {" · 평가 "}{s.evaluated_trades ?? 0}건
            {" · 100건 기준 "}{s.meets_100_sample ? "충족" : "미충족"}
            {" · 거래일 "}{report.trading_days ?? 0}일
          </div>
        )}

        {/* 전체 성과 */}
        {report && (
          <div data-testid="pgr-performance"
               style={{ fontSize: "var(--fs-xs)", marginBottom: 8 }}>
            승률 {_pct(p.win_rate)} · 평균 {_signed(p.average_return)} ·
            손익비 {_num(p.payoff_ratio)} · PF {_num(p.profit_factor)} ·
            MDD {_pct(p.max_drawdown)} · 연속손실 {p.max_consecutive_losses ?? 0}회
          </div>
        )}

        {/* 전략별 성과 */}
        {strategies.length > 0 && (
          <details data-testid="pgr-strategies" style={{ fontSize: "var(--fs-xs)", marginBottom: 6 }}>
            <summary style={{ cursor: "pointer", color: "var(--c-text-3)" }}>전략별 성과</summary>
            <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
              {strategies.map((b) => (
                <li key={b.strategy} data-testid={`pgr-strategy-${b.strategy}`}>
                  {b.strategy}: 평가 {b.evaluated_count ?? 0} · 승률 {_pct(b.win_rate)} ·
                  PF {_num(b.profit_factor)} · MDD {_pct(b.max_drawdown)}
                </li>
              ))}
            </ul>
          </details>
        )}

        {/* 매수불가 사유 TOP */}
        <div data-testid="pgr-blocked" style={{ fontSize: "var(--fs-xs)", marginBottom: 6 }}>
          <strong>매수불가 사유 TOP:</strong>{" "}
          {blocked.length === 0 ? "없음"
            : blocked.slice(0, 5).map((r) => `${r.reason_code}(${r.count})`).join(", ")}
        </div>

        {/* 주문품질 / 포트폴리오 정합성 */}
        <div data-testid="pgr-quality" style={{ fontSize: "var(--fs-xs)", marginBottom: 6 }}>
          주문품질: 거절 {oq.rejected_count ?? 0} · 부분체결 {oq.partial_fill_count ?? 0} ·
          평균지연 {oq.avg_latency_ms ?? "n/a"}ms · 평균슬리피지 {oq.avg_slippage_bps ?? "n/a"}bps
        </div>
        <div data-testid="pgr-integrity" style={{ fontSize: "var(--fs-xs)", marginBottom: 6 }}>
          포트폴리오 정합성: {pi.status || "n/a"} (점검 {pi.checked ?? 0} · 불일치 {pi.mismatches ?? 0})
        </div>

        {/* 실전 전환 전 필수 보완 */}
        {actions.length > 0 && (
          <div data-testid="pgr-required-actions"
               style={{ fontSize: "var(--fs-xs)", color: "#b45309", marginBottom: 6 }}>
            <strong>실전 전환 전 필수 보완:</strong>
            <ul style={{ margin: "2px 0 0 16px", padding: 0 }}>
              {actions.map((a, i) => (<li key={i}>{a}</li>))}
            </ul>
          </div>
        )}

        <div data-testid="pgr-footer"
             style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          분석/검토 자료입니다. 등급이 좋아도 자동으로 실전 전환되지 않으며 수익을 보장하지 않습니다.
        </div>
      </div>
    </Card>
  );
}

export default PaperGateReportCard;
