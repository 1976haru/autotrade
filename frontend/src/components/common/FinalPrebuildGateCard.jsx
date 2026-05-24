/**
 * 체크리스트 11-00 — EXE 빌드 전 통합 검증 Gate 카드 (Settings 탭, read-only).
 *
 * health/config/security/DB/Universe/Portfolio/Agent/Order/Stress/Backtest/Walk-forward/
 * Intraday/Live safety/UI·API/문서/CI 를 한 번에 점검한 종합 판정(BUILD_READY /
 * BUILD_READY_WITH_WARNINGS / BUILD_BLOCKED) + EXE 빌드 가능 여부를 표시한다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수 / 매도 / 실전 전환 / 자동 적용 / 승인 버튼 0개 (새로고침/복사만).
 *  - secret/account 원문 표시 0건. input/textarea 0개.
 *  - "실전 승인 아님" / "주문 실행 아님" / "수익 보장 아님" 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _ST_COLOR = {
  BUILD_READY: "#166534", BUILD_READY_WITH_WARNINGS: "#a16207", BUILD_BLOCKED: "#b91c1c",
};
const _ST_BG = {
  BUILD_READY: "#dcfce7", BUILD_READY_WITH_WARNINGS: "#fef9c3", BUILD_BLOCKED: "#fee2e2",
};
const _SEC_COLOR = { PASS: "#166534", WARN: "#a16207", FAIL: "#b91c1c", SKIP: "#6b7280" };

export function FinalPrebuildGateCard({
  apiClient = backendApi,
  testId = "final-prebuild-gate-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.finalPrebuildGate !== "function") return;
    try {
      setReport((await apiClient.finalPrebuildGate()) || null);
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

  const status = report?.overall_status || null;
  const sections = Array.isArray(report?.sections) ? report.sections : [];
  const blocked = Array.isArray(report?.blocked_reasons) ? report.blocked_reasons : [];
  const warns = Array.isArray(report?.warn_reasons) ? report.warn_reasons : [];
  const nextActions = Array.isArray(report?.next_actions) ? report.next_actions : [];
  const c = report?.counts || {};

  return (
    <div data-testid={testId}>
      <Card accentColor="#0ea5e933">
        <SectionLabel>🏗️ EXE 빌드 전 통합 검증 Gate (11-00)</SectionLabel>

        <div data-testid="final-prebuild-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          EXE 빌드 전 전체(health/config/security/DB/Agent/Order/Backtest/Intraday/Live
          safety/UI·API/문서)를 한 번에 점검해 <b>빌드 가능 여부</b>를 판정합니다.
          <b> 실전 승인 아님 · 주문 실행 아님 · 수익 보장 아님.</b> '빌드 가능'과 '전략 유망'은
          다릅니다.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="final-prebuild-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>점검 새로고침</button>
          <button type="button" data-testid="final-prebuild-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {error ? (
          <div data-testid="final-prebuild-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>빌드 전 점검 결과를 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {report ? (
          <>
            <div data-testid="final-prebuild-status" style={{
              padding: "6px 10px", borderRadius: 6, marginBottom: 8,
              background: _ST_BG[status] || "#f1f5f9", fontWeight: "var(--fw-bold)",
              fontSize: "var(--fs-xs)", color: _ST_COLOR[status] || "var(--c-text)",
            }}>
              {status} · EXE 빌드 가능:{" "}
              <span data-testid="final-prebuild-exe-allowed">
                {report.exe_build_allowed ? "예 ✅" : "아니오 ⛔"}
              </span>
            </div>

            <div data-testid="final-prebuild-counts" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 8,
            }}>
              PASS {c.PASS ?? 0} · WARN {c.WARN ?? 0} · FAIL {c.FAIL ?? 0} · SKIP {c.SKIP ?? 0}
              {" · "}Paper 리허설: {report.ready_for_paper_rehearsal ? "가능" : "보류"}
              {" · "}장중 리허설: {report.ready_for_market_rehearsal ? "가능" : "보류(KIS 자격)"}
            </div>

            {blocked.length > 0 ? (
              <div data-testid="final-prebuild-blocked" style={{
                padding: "5px 8px", borderRadius: 4, background: "#fef2f2",
                color: "#b91c1c", fontSize: "var(--fs-xs)", marginBottom: 6,
              }}>BLOCKER: {blocked.join(" · ")}</div>
            ) : null}
            {warns.length > 0 ? (
              <div data-testid="final-prebuild-warns" style={{
                fontSize: "var(--fs-xs)", color: "#a16207", marginBottom: 6,
              }}>WARN: {warns.join(" · ")}</div>
            ) : null}

            <div data-testid="final-prebuild-sections" style={{ marginBottom: 8 }}>
              {sections.map((s) => (
                <div key={s.name} data-testid={`final-prebuild-section-${s.name}`}
                     style={{ display: "flex", gap: 6, fontSize: "var(--fs-xs)", padding: "1px 0" }}>
                  <span style={{ minWidth: 46, fontWeight: "var(--fw-bold)",
                                 color: _SEC_COLOR[s.status] || "var(--c-text)" }}>{s.status}</span>
                  <span style={{ minWidth: 180, color: "var(--c-text-2)" }}>{s.name}</span>
                  <span style={{ color: "var(--c-text-3)" }}>{s.detail}</span>
                </div>
              ))}
            </div>

            {nextActions.length > 0 ? (
              <div data-testid="final-prebuild-next" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>다음 단계: {nextActions.join(" · ")}</div>
            ) : null}
          </>
        ) : (
          <div data-testid="final-prebuild-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          }}>표시할 점검 결과가 없습니다.</div>
        )}

        <div data-testid="final-prebuild-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 Gate 는 빌드 가능 여부 판정 전용입니다. <b>실전 승인 아님 · 주문 실행 아님 ·
          수익 보장 아님.</b> 전체 backend pytest/frontend build 는 CLI
          <code>run_final_prebuild_gate.py --full</code> 또는 CI 에서 검증합니다.
        </div>
      </Card>
    </div>
  );
}

export default FinalPrebuildGateCard;
