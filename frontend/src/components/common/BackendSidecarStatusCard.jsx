/**
 * #53 / 7-01 — EXE 연결 상태 카드 (Backend / Sidecar / 진단), Settings 탭, read-only.
 *
 * `GET /api/system/exe-status` 의 표준 enum 을 분리 표시해, 사용자가
 * "Backend 연결됨" 과 "연결 실패" 를 *동시에* 보지 않도록 한다. 연결 실패 시
 * 진단 / DB / KIS 상태는 "확인 불가" 로 강등되어 모순 표시가 원천 차단된다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 연결됨 / 연결 실패 동시 표시 0건 (단일 boolean 파생, normalizeExeStatus).
 *  - 매수 / 매도 / 실거래 시작 / Place Order / ENABLE_* 토글 버튼 0개.
 *  - 입력 form(input/textarea/select) 0개 — secret 입력/표시 surface 0건.
 *  - API key / Secret / 계좌번호 원문 표시 0건.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";
import { isDesktopApp } from "../../desktop/backendLauncher";
import {
  normalizeExeStatus,
  getBackendStatusLabel,
  getSidecarStatusLabel,
  getDiagnosticsStatusLabel,
  getDbStatusLabel,
  getKisPaperReadinessLabel,
  backendTone,
  sidecarTone,
  diagnosticsTone,
  dbTone,
  kisPaperTone,
} from "../../utils/exeStatus";

const POLL_INTERVAL_MS = 10_000;

const _TONE_COLORS = {
  ok:      { fg: "#166534", bg: "#dcfce7" },
  warn:    { fg: "#92400e", bg: "#fef3c7" },
  bad:     { fg: "#b91c1c", bg: "#fef2f2" },
  unknown: { fg: "#475569", bg: "#f1f5f9" },
};

function _Row({ label, value, tone, testid }) {
  const c = _TONE_COLORS[tone] || _TONE_COLORS.unknown;
  return (
    <div
      data-testid={testid}
      data-tone={tone}
      style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        padding: "5px 8px", borderRadius: 4, background: c.bg, marginBottom: 4,
        fontSize: "var(--fs-xs)",
      }}
    >
      <span style={{ color: "var(--c-text-2)" }}>{label}</span>
      <span style={{ fontWeight: "var(--fw-bold)", color: c.fg }}>{value}</span>
    </div>
  );
}

function _deriveSidecar(desktop, reachable, override) {
  if (override) return override;
  if (!desktop) return "UNKNOWN"; // 웹/브라우저 — sidecar 개념 없음
  // desktop: launcher 가 자동 기동/재시도 → 연결되면 RUNNING, 아니면 시작 중.
  return reachable ? "RUNNING" : "STARTING";
}

export function BackendSidecarStatusCard({
  apiClient = backendApi,
  testId = "backend-sidecar-status-card",
  pollIntervalMs = POLL_INTERVAL_MS,
  desktop = undefined,
  sidecarStatus = null,
} = {}) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);

  const isDesktop = desktop === undefined ? isDesktopApp() : desktop;

  const refresh = useCallback(async () => {
    if (typeof apiClient.exeStatus !== "function") {
      setLoading(false);
      return;
    }
    try {
      const raw = await apiClient.exeStatus();
      setStatus(normalizeExeStatus(raw, {
        reachable: true,
        sidecarStatus: _deriveSidecar(isDesktop, true, sidecarStatus),
      }));
    } catch (err) {
      // 연결 실패 — 단일 진실. 의존 상태는 normalizeExeStatus 가 UNKNOWN 강등.
      setStatus(normalizeExeStatus(null, {
        reachable: false,
        sidecarStatus: _deriveSidecar(isDesktop, false, sidecarStatus),
        errorMessage: err?.message || String(err),
      }));
    } finally {
      setLoading(false);
    }
  }, [apiClient, isDesktop, sidecarStatus]);

  useEffect(() => {
    refresh();
    if (!pollIntervalMs || pollIntervalMs <= 0) return undefined;
    const t = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(t);
  }, [refresh, pollIntervalMs]);

  if (loading && status == null) {
    return (
      <Card data-testid={testId}>
        <SectionLabel>🖥️ EXE 연결 상태</SectionLabel>
        <div data-testid="exe-status-loading"
             style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          상태 확인 중…
        </div>
      </Card>
    );
  }

  const s = status || normalizeExeStatus(null, { reachable: false });
  const reachable = s.backend_api_reachable === true;

  return (
    <Card data-testid={testId}>
      <SectionLabel>🖥️ EXE 연결 상태 (Backend / Sidecar / 진단)</SectionLabel>

      <div
        data-testid="exe-status-badges"
        style={{ marginBottom: 8, display: "flex", gap: 4, flexWrap: "wrap" }}
      >
        <span style={{
          padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 600,
          color: "#0f172a", background: "#e2e8f0", border: "1px solid #cbd5e1",
        }}>
          조회 전용 · 주문 기능 아님
        </span>
        <span style={{
          padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 600,
          color: "#475569", background: "#f1f5f9", border: "1px solid #cbd5e1",
        }}>
          실거래 아님
        </span>
      </div>

      {/* 연결 실패 시 단일 안내 배너 — 의존 상태는 모두 "확인 불가" 로 표시됨. */}
      {!reachable && (
        <div
          data-testid="exe-status-unreachable-banner"
          style={{
            padding: "6px 10px", borderRadius: 6, marginBottom: 8,
            fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
            background: "#fef2f2", color: "#b91c1c",
          }}
        >
          Backend가 연결되지 않아 진단 상태를 확인할 수 없습니다.
        </div>
      )}

      <_Row label="Backend API" value={getBackendStatusLabel(s)}
            tone={backendTone(s)} testid="exe-status-backend" />
      <_Row label="Sidecar" value={getSidecarStatusLabel(s)}
            tone={sidecarTone(s)} testid="exe-status-sidecar" />
      <_Row label="Diagnostics" value={getDiagnosticsStatusLabel(s)}
            tone={diagnosticsTone(s)} testid="exe-status-diagnostics" />
      <_Row label="DB" value={getDbStatusLabel(s)}
            tone={dbTone(s)} testid="exe-status-db" />
      <_Row label="KIS Paper" value={getKisPaperReadinessLabel(s)}
            tone={kisPaperTone(s)} testid="exe-status-kis-paper" />

      {s.checked_at && (
        <div
          data-testid="exe-status-checked-at"
          style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 4 }}
        >
          마지막 확인 시각: {s.checked_at}
        </div>
      )}

      {s.last_error_message && (
        <div
          data-testid="exe-status-error"
          style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginTop: 4 }}
        >
          오류: {s.last_error_message}
        </div>
      )}

      <div
        data-testid="exe-status-footer"
        style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          lineHeight: 1.6,
        }}
      >
        본 카드는 EXE 운영 상태(연결 / sidecar / 진단)를 확인하는 read-only
        화면입니다. 연결 실패 시 진단·DB·KIS 상태는 "확인 불가" 로 표시되며,
        화면에는 API key / Secret / 계좌번호가 표시되지 않습니다. 현재 설정은
        Paper / KIS 모의투자 전용이고 실거래는 OFF 입니다.
      </div>
    </Card>
  );
}

export default BackendSidecarStatusCard;
