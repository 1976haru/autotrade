/**
 * #63 / 8-01 — EXE Preflight Smoke Test 카드 (Settings 탭, read-only).
 *
 * `GET /api/system/preflight` 결과를 받아 PASS/WARN/FAIL 요약 + 항목별 상태를
 * 한 화면에 표시한다. 본 검사는 *주문을 발생시키지 않는다*.
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수 / 매도 / 실거래 시작 / Place Order / ENABLE_* 버튼 0개.
 *  - 입력 form(input/textarea/select) 0개.
 *  - 주문을 발생시키는 API 호출 0건 (read-only preflight GET 만).
 *  - Secret / API key / 계좌번호 원문 표시 0건.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const POLL_INTERVAL_MS = 0; // 수동/마운트 1회 — 자동 폴링 불필요.

const _TONE = {
  PASS: { fg: "#166534", bg: "#dcfce7", label: "PASS" },
  WARN: { fg: "#92400e", bg: "#fef3c7", label: "WARN" },
  FAIL: { fg: "#b91c1c", bg: "#fef2f2", label: "FAIL" },
};

function _Badge({ status, count, testid }) {
  const t = _TONE[status] || _TONE.WARN;
  return (
    <span data-testid={testid} style={{
      padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 700,
      color: t.fg, background: t.bg,
    }}>
      {t.label} {count}
    </span>
  );
}

export function PreflightSmokeCard({
  apiClient = backendApi,
  testId = "preflight-smoke-card",
  pollIntervalMs = POLL_INTERVAL_MS,
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (typeof apiClient.preflight !== "function") { setLoading(false); return; }
    try {
      const r = await apiClient.preflight();
      setReport(r || null);
      setError(null);
    } catch (err) {
      setReport(null);
      setError(err?.message || String(err));
    } finally {
      setLoading(false);
    }
  }, [apiClient]);

  useEffect(() => {
    refresh();
    if (!pollIntervalMs || pollIntervalMs <= 0) return undefined;
    const t = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(t);
  }, [refresh, pollIntervalMs]);

  const summary = report?.summary || null;
  const checks = Array.isArray(report?.checks) ? report.checks : [];
  const overall = summary?.status || (error ? "FAIL" : "WARN");
  const fails = checks.filter((c) => c.status === "FAIL");

  return (
    <div data-testid={testId}>
      <Card>
        <SectionLabel>🩺 EXE Preflight Smoke Test</SectionLabel>

        <div
          data-testid="preflight-note"
          style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8 }}
        >
          이 검사는 주문을 발생시키지 않습니다 (read-only). 자격정보(API key /
          Secret / 계좌번호)는 표시되지 않습니다.
        </div>

        {loading && report == null && !error ? (
          <div data-testid="preflight-loading"
               style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            점검 중…
          </div>
        ) : null}

        {error ? (
          <div data-testid="preflight-error" style={{
            padding: "6px 10px", borderRadius: 6, marginBottom: 8,
            background: "#fef2f2", color: "#b91c1c", fontSize: "var(--fs-xs)",
            fontWeight: "var(--fw-bold)",
          }}>
            Preflight 결과를 가져올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}

        {summary ? (
          <>
            <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
              <span data-testid="preflight-overall" data-status={overall} style={{
                padding: "3px 10px", borderRadius: 6, fontWeight: 700,
                fontSize: "var(--fs-xs)",
                color: (_TONE[overall] || _TONE.WARN).fg,
                background: (_TONE[overall] || _TONE.WARN).bg,
              }}>
                {overall}
              </span>
              <_Badge status="PASS" count={summary.pass_count} testid="preflight-pass-count" />
              <_Badge status="WARN" count={summary.warn_count} testid="preflight-warn-count" />
              <_Badge status="FAIL" count={summary.fail_count} testid="preflight-fail-count" />
            </div>

            {fails.length > 0 ? (
              <div data-testid="preflight-fails" style={{
                padding: "6px 10px", borderRadius: 6, marginBottom: 8,
                background: "#fef2f2", color: "#b91c1c", fontSize: "var(--fs-xs)",
              }}>
                주요 실패: {fails.map((c) => c.name).join(", ")}
              </div>
            ) : null}

            <div data-testid="preflight-checks">
              {checks.map((c) => {
                const t = _TONE[c.status] || _TONE.WARN;
                return (
                  <div key={c.name} data-testid={`preflight-check-${c.name}`}
                       data-status={c.status}
                       style={{
                         display: "flex", justifyContent: "space-between",
                         gap: 8, padding: "4px 8px", borderRadius: 4,
                         background: t.bg, marginBottom: 3, fontSize: "var(--fs-xs)",
                       }}>
                    <span style={{ color: "var(--c-text-2)" }}>{c.name}</span>
                    <span style={{ fontWeight: "var(--fw-bold)", color: t.fg }}>
                      {t.label}
                    </span>
                  </div>
                );
              })}
            </div>

            {report?.generated_at ? (
              <div data-testid="preflight-generated-at" style={{
                marginTop: 6, fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
              }}>
                마지막 실행: {report.generated_at}
              </div>
            ) : null}
          </>
        ) : null}

        <div data-testid="preflight-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          장이 닫힌 날에는 일부 항목이 WARN(MARKET_CLOSED 등)으로 표시될 수 있으며
          이는 정상입니다. 본 검사는 주문 / 실거래를 수행하지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default PreflightSmokeCard;
