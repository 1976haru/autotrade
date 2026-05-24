/**
 * BUILD-02A — 장 열리기 전 사전 검증 카드 (Settings 탭, read-only).
 *
 * 장이 열리기 전에도 확인 가능한 항목(환경변수/KIS 자격 present/Paper·Live 분리/
 * Universe/Portfolio/Agent 카드/BUILD-01 정합성/preflight/문서/스크립트)을 한 화면에
 * PASS/WARN/FAIL 로 표시하고 premarket_ready / ready_for_market_open_rehearsal 을 보여준다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 실전 켜기 / 모의 주문 실행 / 매수 / 매도 / 승인 버튼 0개 (새로고침/복사만).
 *  - secret/account 원문 표시 0건.
 *  - "실제 KIS API 호출 없음" / "실전 승인 아님" / "주문 버튼 아님" 문구 노출.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = { PASS: "#166534", WARN: "#a16207", FAIL: "#b91c1c", SKIP: "#6b7280" };
const _V_BG = { PASS: "#dcfce7", WARN: "#fef9c3", FAIL: "#fee2e2", SKIP: "#f1f5f9" };

export function PremarketReadinessGateCard({
  apiClient = backendApi,
  testId = "premarket-readiness-gate-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.premarketReadiness !== "function") return;
    try {
      setReport((await apiClient.premarketReadiness()) || null);
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
  const overall = report?.overall_status || null;
  const kisSec = sections.find((s) => s.section === "KIS_CREDENTIALS");
  const kisWarn = kisSec && kisSec.verdict === "WARN";

  return (
    <div data-testid={testId}>
      <Card accentColor="#0284c733">
        <SectionLabel>🟢 장 열리기 전 사전 검증 (BUILD-02A)</SectionLabel>

        <div data-testid="premarket-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          장이 열리기 전에도 확인 가능한 항목을 자동 점검합니다. 실제 KIS API 호출
          없음, 실전 승인 아님, 주문 버튼이 아닙니다. KIS 자격은 present 여부만
          확인합니다. 수익을 보장하지 않습니다.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="premarket-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>점검 새로고침</button>
          <button type="button" data-testid="premarket-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "결과 복사"}</button>
        </div>

        {error ? (
          <div data-testid="premarket-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>사전 검증을 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {report ? (
          <>
            <div data-testid="premarket-overall" style={{
              padding: "6px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[overall] || "#f1f5f9", fontWeight: "var(--fw-bold)",
              fontSize: "var(--fs-xs)", color: _V_COLOR[overall] || "var(--c-text)",
            }}>
              전체 상태: {overall}
            </div>

            <div data-testid="premarket-ready" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              premarket_ready: <strong>{report.premarket_ready ? "예 ✅" : "아니오 ⛔"}</strong>
              {" · "}offline 빌드 가능: {report.build_ready_for_offline ? "예" : "아니오"}
            </div>
            <div data-testid="premarket-rehearsal" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 8,
            }}>
              ready_for_market_open_rehearsal:{" "}
              <strong>{report.ready_for_market_open_rehearsal ? "예" : "아니오"}</strong>
            </div>

            {kisWarn ? (
              <div data-testid="premarket-kis-warn" style={{
                padding: "5px 8px", borderRadius: 4, background: "#fef9c3",
                color: "#92400e", fontSize: "var(--fs-xs)", marginBottom: 8,
              }}>
                ⚠️ KIS 자격 미설정/미상 — offline 빌드는 가능하지만, 장중 리허설
                (BUILD-02B) 전 KIS 모의 자격을 입력해야 합니다.
              </div>
            ) : null}

            <div data-testid="premarket-sections" style={{ marginBottom: 8 }}>
              {sections.map((s) => (
                <div key={s.section} data-testid={`premarket-section-${s.section}`}
                     style={{ display: "flex", gap: 6, fontSize: "var(--fs-xs)", padding: "1px 0" }}>
                  <span style={{ minWidth: 50, fontWeight: "var(--fw-bold)",
                                 color: _V_COLOR[s.verdict] || "var(--c-text)" }}>{s.verdict}</span>
                  <span style={{ minWidth: 170, color: "var(--c-text-2)" }}>{s.section}</span>
                  <span style={{ color: "var(--c-text-3)" }}>{s.note}</span>
                </div>
              ))}
            </div>

            <div data-testid="premarket-next" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 4,
            }}>
              다음 단계(BUILD-02B, 장중): KIS 모의 자격 입력 → 실제 KIS 모의 현재가/주문/
              체결 polling 리허설. 본 화면은 그 *전* 준비 상태 확인입니다.
            </div>
          </>
        ) : null}

        <div data-testid="premarket-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 점검은 장 열리기 전 사전 검증 전용입니다. 실제 KIS API 호출 없음, 실전
          전환 승인과 무관하며, 자격정보(계좌번호/secret)는 표시되지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default PremarketReadinessGateCard;
