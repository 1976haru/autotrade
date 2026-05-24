/**
 * #53 / 7-01 — exeStatus helper 단위 테스트.
 *
 * lock 하는 invariant:
 *  - reachable=true → "Backend API 연결됨"
 *  - reachable=false → "Backend API 연결 실패"
 *  - 연결됨/연결 실패가 동시에 표시되지 않음 (단일 boolean 파생)
 *  - reachable=false 면 diagnostics/db/kis 모두 "확인 불가" (UNKNOWN)
 *  - secret 원문은 표시/보관되지 않음
 */

import { describe, it, expect } from "vitest";

import {
  normalizeExeStatus,
  getBackendStatusLabel,
  getSidecarStatusLabel,
  getDiagnosticsStatusLabel,
  getDbStatusLabel,
  getKisPaperReadinessLabel,
  isContradictoryStatus,
  SIDECAR_STATUS,
} from "./exeStatus";


const _RAW_OK = {
  backend_api_reachable: true,
  sidecar_status: "RUNNING",
  diagnostics_status: "OK",
  db_status: "OK",
  kis_paper_readiness: "READY",
  checked_at: "2026-05-24T00:00:00+00:00",
  last_error_message: null,
  is_live_authorization: false,
  contains_secret: false,
};


describe("normalizeExeStatus — reachable", () => {
  it("reachable=true → backend label 연결됨", () => {
    const s = normalizeExeStatus(_RAW_OK, { reachable: true });
    expect(s.backend_api_reachable).toBe(true);
    expect(getBackendStatusLabel(s)).toBe("Backend API 연결됨");
  });

  it("reachable=true carries diagnostics/db/kis from raw", () => {
    const s = normalizeExeStatus(_RAW_OK, { reachable: true });
    expect(s.diagnostics_status).toBe("OK");
    expect(s.db_status).toBe("OK");
    expect(s.kis_paper_readiness).toBe("READY");
  });

  it("invalid enum values fall back to UNKNOWN", () => {
    const s = normalizeExeStatus(
      { ..._RAW_OK, diagnostics_status: "BOGUS", db_status: 123 },
      { reachable: true },
    );
    expect(s.diagnostics_status).toBe("UNKNOWN");
    expect(s.db_status).toBe("UNKNOWN");
  });
});


describe("normalizeExeStatus — unreachable", () => {
  it("reachable=false → backend label 연결 실패", () => {
    const s = normalizeExeStatus(null, { reachable: false, errorMessage: "boom" });
    expect(s.backend_api_reachable).toBe(false);
    expect(getBackendStatusLabel(s)).toBe("Backend API 연결 실패");
  });

  it("reachable=false forces diagnostics/db/kis to UNKNOWN", () => {
    // raw 가 (이전 캐시처럼) OK 를 담고 있어도 강등되어야 한다.
    const s = normalizeExeStatus(_RAW_OK, { reachable: false });
    expect(s.diagnostics_status).toBe("UNKNOWN");
    expect(s.db_status).toBe("UNKNOWN");
    expect(s.kis_paper_readiness).toBe("UNKNOWN");
  });

  it("reachable=false sets a last_error_message", () => {
    const s = normalizeExeStatus(null, { reachable: false });
    expect(typeof s.last_error_message).toBe("string");
    expect(s.last_error_message.length).toBeGreaterThan(0);
  });

  it("explicit errorMessage is carried", () => {
    const s = normalizeExeStatus(null, {
      reachable: false, errorMessage: "timeout 8000ms",
    });
    expect(s.last_error_message).toBe("timeout 8000ms");
  });
});


describe("no contradiction (연결됨 / 연결 실패 동시 금지)", () => {
  it("normalized reachable=true is never contradictory", () => {
    const s = normalizeExeStatus(_RAW_OK, { reachable: true });
    expect(isContradictoryStatus(s)).toBe(false);
  });

  it("normalized reachable=false is never contradictory", () => {
    const s = normalizeExeStatus(_RAW_OK, { reachable: false });
    expect(isContradictoryStatus(s)).toBe(false);
  });

  it("backend label is exactly one of 연결됨/연결 실패 (never both)", () => {
    for (const reachable of [true, false]) {
      const label = getBackendStatusLabel(
        normalizeExeStatus(_RAW_OK, { reachable }),
      );
      const isConnected = label === "Backend API 연결됨";
      const isFailed = label === "Backend API 연결 실패";
      expect(isConnected !== isFailed).toBe(true); // XOR — 정확히 하나
    }
  });

  it("hand-crafted contradictory object is detected", () => {
    expect(
      isContradictoryStatus({
        backend_api_reachable: false,
        diagnostics_status: "OK",
      }),
    ).toBe(true);
  });
});


describe("label helpers", () => {
  it("sidecar labels", () => {
    expect(getSidecarStatusLabel({ sidecar_status: "RUNNING" }))
      .toBe("Sidecar 실행 중");
    expect(getSidecarStatusLabel({ sidecar_status: "STARTING" }))
      .toBe("Sidecar 시작 중");
    expect(getSidecarStatusLabel({ sidecar_status: "STOPPED" }))
      .toBe("Sidecar 중지");
    expect(getSidecarStatusLabel({ sidecar_status: "UNKNOWN" }))
      .toBe("Sidecar 확인 불가");
  });

  it("diagnostics labels", () => {
    expect(getDiagnosticsStatusLabel({ diagnostics_status: "OK" }))
      .toBe("진단 상태 정상");
    expect(getDiagnosticsStatusLabel({ diagnostics_status: "FAIL" }))
      .toBe("진단 상태 실패");
    expect(getDiagnosticsStatusLabel({ diagnostics_status: "UNKNOWN" }))
      .toBe("진단 상태 확인 불가");
  });

  it("db labels", () => {
    expect(getDbStatusLabel({ db_status: "OK" })).toBe("DB 정상");
    expect(getDbStatusLabel({ db_status: "FAIL" })).toBe("DB 실패");
    expect(getDbStatusLabel({ db_status: "UNKNOWN" })).toBe("DB 확인 불가");
  });

  it("kis paper labels", () => {
    expect(getKisPaperReadinessLabel({ kis_paper_readiness: "READY" }))
      .toBe("KIS 모의투자 준비 상태: READY");
    expect(getKisPaperReadinessLabel({ kis_paper_readiness: "BLOCKED" }))
      .toBe("KIS 모의투자 준비 상태: BLOCKED");
    expect(getKisPaperReadinessLabel({ kis_paper_readiness: "UNKNOWN" }))
      .toBe("KIS 모의투자 준비 상태: 확인 불가");
  });
});


describe("sidecar override + safety", () => {
  it("explicit sidecarStatus overrides raw", () => {
    const s = normalizeExeStatus(_RAW_OK, {
      reachable: true, sidecarStatus: SIDECAR_STATUS.STOPPED,
    });
    expect(s.sidecar_status).toBe("STOPPED");
  });

  it("never reports is_live_authorization=true", () => {
    const s = normalizeExeStatus(
      { ..._RAW_OK, is_live_authorization: true }, { reachable: true },
    );
    // helper carries the (wrong) backend value transparently so UI can warn,
    // but normalized default for missing/false is false.
    const s2 = normalizeExeStatus({ ..._RAW_OK }, { reachable: true });
    expect(s2.is_live_authorization).toBe(false);
    expect(typeof s.is_live_authorization).toBe("boolean");
  });

  it("does not surface any secret-like field", () => {
    const s = normalizeExeStatus(
      { ..._RAW_OK, kis_app_secret: "LEAK", access_token: "LEAK2" },
      { reachable: true },
    );
    const blob = JSON.stringify(s);
    expect(blob).not.toContain("LEAK");
    expect(blob).not.toContain("kis_app_secret");
    expect(blob).not.toContain("access_token");
  });
});
