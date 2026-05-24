/**
 * BUILD-02B-0 — KIS 모의 AI 자동매매 코드 감사 카드 (Settings 탭, read-only).
 *
 * AI 판단 → KIS Paper 주문 결정 → fake 주문 → order_quality → portfolio →
 * outcome/review/feedback 전 흐름 + 권한 게이트 + Live safety 를 fake 로 감사한
 * 결과를 PASS/WARN/FAIL 로 표시하고 paper_autotrade_ready /
 * ready_for_market_open_rehearsal 을 보여준다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수 / 매도 / 실전 켜기 / 승인 / 주문 실행 버튼 0개 (새로고침/복사만).
 *  - secret/account 원문 표시 0건.
 *  - "실제 KIS API 호출 없음" / "실전 승인 아님" / "주문 버튼 아님" 문구 노출.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = { PASS: "#166534", WARN: "#a16207", FAIL: "#b91c1c", SKIP: "#6b7280" };
const _V_BG = { PASS: "#dcfce7", WARN: "#fef9c3", FAIL: "#fee2e2", SKIP: "#f1f5f9" };

export function KisPaperAiAutotradeAuditCard({
  apiClient = backendApi,
  testId = "kis-paper-ai-autotrade-audit-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.kisPaperAutotradeAudit !== "function") return;
    try {
      setReport((await apiClient.kisPaperAutotradeAudit()) || null);
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
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
    } catch { /* clipboard 미가용 무시 */ }
  }, [report]);

  const sections = Array.isArray(report?.sections) ? report.sections : [];
  const overall = report?.overall_verdict || null;
  const fails = sections.filter((s) => s.verdict === "FAIL");

  return (
    <div data-testid={testId}>
      <Card accentColor="#7c3aed33">
        <SectionLabel>🔍 KIS 모의 AI 자동매매 코드 감사 (BUILD-02B-0)</SectionLabel>

        <div data-testid="kis-audit-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          AI 판단부터 KIS Paper 주문 결정·결과·체결 품질·포트폴리오·복기까지 전체
          흐름을 fake 로 감사합니다. 실제 KIS API 호출 없음, 실전 승인 아님, 주문
          버튼이 아닙니다. KIS 자격은 present 여부만 확인합니다. 수익을 보장하지 않습니다.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="kis-audit-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>점검 새로고침</button>
          <button type="button" data-testid="kis-audit-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "결과 복사"}</button>
        </div>

        {error ? (
          <div data-testid="kis-audit-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>코드 감사를 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {report ? (
          <>
            <div data-testid="kis-audit-overall" style={{
              padding: "6px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[overall] || "#f1f5f9", fontWeight: "var(--fw-bold)",
              fontSize: "var(--fs-xs)", color: _V_COLOR[overall] || "var(--c-text)",
            }}>
              전체 판정: {overall}
            </div>

            <div data-testid="kis-audit-ready" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              paper_autotrade_ready:{" "}
              <strong>{report.paper_autotrade_ready ? "예 ✅" : "아니오 ⛔"}</strong>
            </div>
            <div data-testid="kis-audit-rehearsal" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 8,
            }}>
              ready_for_market_open_rehearsal:{" "}
              <strong>{report.ready_for_market_open_rehearsal ? "예" : "아니오"}</strong>
            </div>

            <div data-testid="kis-audit-sections" style={{ marginBottom: 8 }}>
              {sections.map((s) => (
                <div key={s.section} data-testid={`kis-audit-section-${s.section}`}
                     style={{ display: "flex", gap: 6, fontSize: "var(--fs-xs)", padding: "1px 0" }}>
                  <span style={{ minWidth: 48, fontWeight: "var(--fw-bold)",
                                 color: _V_COLOR[s.verdict] || "var(--c-text)" }}>{s.verdict}</span>
                  <span style={{ minWidth: 190, color: "var(--c-text-2)" }}>{s.section}</span>
                  <span style={{ color: "var(--c-text-3)" }}>{s.note}</span>
                </div>
              ))}
            </div>

            {fails.length > 0 ? (
              <div data-testid="kis-audit-fail-reasons" style={{
                padding: "5px 8px", borderRadius: 4, background: "#fef2f2",
                color: "#b91c1c", fontSize: "var(--fs-xs)", marginBottom: 6,
              }}>
                FAIL 조치 필요: {fails.map((s) => s.section).join(", ")}
              </div>
            ) : null}

            <div data-testid="kis-audit-next" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 4,
            }}>
              다음 단계(BUILD-02B, 장중): KIS 모의 자격 입력 → 실제 KIS 모의 현재가/
              주문/체결 polling 리허설. 본 화면은 그 *전* 코드 감사입니다.
            </div>
          </>
        ) : null}

        <div data-testid="kis-audit-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 점검은 KIS 모의 AI 자동매매 코드 감사 전용입니다. 실제 KIS API 호출 없음,
          실전 전환 승인과 무관하며, 자격정보(계좌번호/secret)는 표시되지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default KisPaperAiAutotradeAuditCard;
