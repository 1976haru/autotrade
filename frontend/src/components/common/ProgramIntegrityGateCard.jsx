/**
 * BUILD-01 — 최종 빌드 전 전체 프로그램 정합성 점검 카드 (Settings 탭, read-only).
 *
 * Universe → KIS readiness → 전략 vote → Agent Council → RiskOfficer → exit_plan →
 * quality → 결정 → KIS Paper decision → fake 주문 → order_quality → portfolio →
 * outcome/review → feedback → UI/API → Live safety 흐름을 한 화면에 PASS/WARN/FAIL
 * 로 표시하고 build_ready 를 보여준다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 실전 켜기 / 주문 실행 / 매수 / 매도 / 승인 버튼 0개 (새로고침/복사만 허용).
 *  - secret/account 원문 표시 0건.
 *  - "최종 빌드 전 점검용" / "실전 승인 아님" / "주문 버튼 아님" 문구 노출.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = { PASS: "#166534", WARN: "#a16207", FAIL: "#b91c1c", SKIP: "#6b7280" };
const _V_BG = { PASS: "#dcfce7", WARN: "#fef9c3", FAIL: "#fee2e2", SKIP: "#f1f5f9" };

export function ProgramIntegrityGateCard({
  apiClient = backendApi,
  testId = "program-integrity-gate-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.programIntegrity !== "function") return;
    try {
      setReport((await apiClient.programIntegrity()) || null);
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

  const sections = Array.isArray(report?.sections) ? report.sections : [];
  const overall = report?.overall_verdict || null;
  const fails = sections.filter((s) => s.verdict === "FAIL");
  const warns = sections.filter((s) => s.verdict === "WARN");

  return (
    <div data-testid={testId}>
      <Card accentColor="#0ea5e933">
        <SectionLabel>🧩 빌드 전 전체 정합성 점검 (BUILD-01)</SectionLabel>

        <div data-testid="integrity-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          최종 빌드 전 점검용입니다. 전체 흐름이 끊김 없이 연결되는지 offline/모의로
          검증합니다. 실전 승인이 아니며, 주문 버튼이 아닙니다. 실제 주문/KIS 실거래
          호출 0건(주문 결과 fake). 수익을 보장하지 않습니다.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="integrity-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>점검 새로고침</button>
          <button type="button" data-testid="integrity-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {error ? (
          <div data-testid="integrity-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>정합성 점검을 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {report ? (
          <>
            <div data-testid="integrity-overall" style={{
              padding: "6px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[overall] || "#f1f5f9", fontWeight: "var(--fw-bold)",
              fontSize: "var(--fs-xs)", color: _V_COLOR[overall] || "var(--c-text)",
            }}>
              전체 판정: {overall} · build_ready:{" "}
              <span data-testid="integrity-build-ready">
                {report.build_ready ? "예 ✅" : "아니오 ⛔"}
              </span>
              {" · "}({report.reason_code})
            </div>

            <div data-testid="integrity-counts" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 8,
            }}>
              PASS {report.counts?.PASS ?? 0} · WARN {report.counts?.WARN ?? 0} ·
              FAIL {report.counts?.FAIL ?? 0}
            </div>

            <div data-testid="integrity-sections" style={{ marginBottom: 8 }}>
              {sections.map((s) => (
                <div key={s.section} data-testid={`integrity-section-${s.section}`}
                     style={{ display: "flex", gap: 6, alignItems: "baseline",
                              fontSize: "var(--fs-xs)", padding: "2px 0" }}>
                  <span style={{ minWidth: 56, fontWeight: "var(--fw-bold)",
                                 color: _V_COLOR[s.verdict] || "var(--c-text)" }}>
                    {s.verdict}
                  </span>
                  <span style={{ minWidth: 150, color: "var(--c-text-2)" }}>{s.section}</span>
                  <span style={{ color: "var(--c-text-3)" }}>{s.detail}</span>
                </div>
              ))}
            </div>

            {fails.length > 0 ? (
              <div data-testid="integrity-fail-reasons" style={{
                padding: "5px 8px", borderRadius: 4, background: "#fef2f2",
                color: "#b91c1c", fontSize: "var(--fs-xs)", marginBottom: 6,
              }}>
                FAIL 조치 필요: {fails.map((s) => `${s.section}(${s.reason_code})`).join(", ")}
              </div>
            ) : null}
            {warns.length > 0 ? (
              <div data-testid="integrity-warn-reasons" style={{
                fontSize: "var(--fs-xs)", color: "#a16207", marginBottom: 6,
              }}>
                WARN: {warns.map((s) => `${s.section}(${s.reason_code})`).join(", ")}
              </div>
            ) : null}
          </>
        ) : null}

        <div data-testid="integrity-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 점검은 빌드 전 통합 검증 전용입니다. 실전 전환 승인과 무관하며, 자격정보
          (계좌번호/secret)는 표시되지 않습니다. 장중 실제 KIS 모의 API 테스트는 별도
          단계(BUILD-02)에서 진행합니다.
        </div>
      </Card>
    </div>
  );
}

export default ProgramIntegrityGateCard;
