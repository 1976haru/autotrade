import { describe, it, expect, vi, afterEach } from "vitest";

import {
  BACKEND_APP_ID,
  judgeBackendIdentity,
  orderPorts,
  readDesktopPortInfo,
} from "./backendIdentity";
import { discoverBackendBaseUrl, resetBackendBaseUrl, getBackendBaseUrl } from "../services/backend/client";
import { probeBackendWithFallback } from "./backendLauncher";

afterEach(() => { resetBackendBaseUrl(); delete window.__TAURI_INTERNALS__; });

const health = (over = {}) => ({ ok: true, status: "ok", app_id: BACKEND_APP_ID,
  instance_id: "inst-NEW", build_commit: "abc1234", kis_is_paper: true,
  live_trading_enabled: false, ...over });

// port 별 /health · /api/status 응답 fake.
function fakeFetch(map) {
  return vi.fn(async (url) => {
    const port = Number(url.split(":")[2].split("/")[0]);
    const entry = map[port];
    if (!entry) throw new Error("ECONNREFUSED");
    if (url.endsWith("/health")) return { ok: true, json: async () => entry };
    if (url.endsWith("/api/status")) return { ok: true, json: async () => ({ safety_flags: {} }) };
    return { ok: false, status: 404 };
  });
}

describe("backendIdentity (WINDOWS-ONECLICK-EXE-V1)", () => {
  it("다른 프로그램 / 다른 실행본 / 실거래 플래그 백엔드는 신뢰하지 않음", () => {
    expect(judgeBackendIdentity({ ok: true, status: "ok" }).reason).toBe("OTHER_PROGRAM");
    expect(judgeBackendIdentity(health({ instance_id: "inst-OLD" }),
      { expectedInstanceId: "inst-NEW" }).reason).toBe("OTHER_INSTANCE");
    expect(judgeBackendIdentity(health({ live_trading_enabled: true })).reason).toBe("UNSAFE_LIVE_FLAGS");
    expect(judgeBackendIdentity(health(), { expectedInstanceId: "inst-NEW" }).ok).toBe(true);
    expect(judgeBackendIdentity(null).reason).toBe("NO_HEALTH");
  });

  it("orderPorts — port 파일의 포트를 먼저", () => {
    expect(orderPorts([8000, 8001, 8002], 8001)).toEqual([8001, 8000, 8002]);
    expect(orderPorts([8000, 8001], null)).toEqual([8000, 8001]);
  });

  it("readDesktopPortInfo — Tauri invoke 결과 파싱, 실패/미가용은 null", async () => {
    const ok = await readDesktopPortInfo({ invokeImpl: async () => ({ available: true, port: 8001, instance_id: "x" }) });
    expect(ok).toEqual({ port: 8001, instanceId: "x", buildCommit: null });
    expect(await readDesktopPortInfo({ invokeImpl: async () => ({ available: false }) })).toBeNull();
    expect(await readDesktopPortInfo({ invokeImpl: async () => { throw new Error("x"); } })).toBeNull();
    expect(await readDesktopPortInfo()).toBeNull();   // 웹 모드
  });

  it("데스크톱: 8000 의 *이전 실행본* 대신 port 파일의 이번 실행본(8001)에 연결", async () => {
    const fetchImpl = fakeFetch({ 8000: health({ instance_id: "inst-OLD" }), 8001: health() });
    const res = await discoverBackendBaseUrl({ fetchImpl, requireIdentity: true,
      desktopPortInfo: { port: 8001, instanceId: "inst-NEW" } });
    expect(res.ok).toBe(true);
    expect(res.port).toBe(8001);
    expect(getBackendBaseUrl()).toBe("http://127.0.0.1:8001");
  });

  it("데스크톱: 다른 프로그램만 응답하면 연결 실패(오인 0)", async () => {
    const other = { ok: true, status: "ok" };
    const fetchImpl = fakeFetch({ 8000: other, 8001: other, 8002: other });
    const res = await discoverBackendBaseUrl({ fetchImpl, requireIdentity: true, desktopPortInfo: null });
    expect(res.ok).toBe(false);
    expect(res.error).toContain("OTHER_PROGRAM");
  });

  it("launcher probe 도 같은 신원 규칙", async () => {
    const fetchImpl = fakeFetch({ 8000: health({ instance_id: "inst-OLD" }), 8002: health() });
    const res = await probeBackendWithFallback({ fetchImpl, requireIdentity: true,
      desktopPortInfo: { port: 8002, instanceId: "inst-NEW" } });
    expect(res.statusOk).toBe(true);
    expect(res.port).toBe(8002);
  });

  it("웹/개발 모드는 종전 동작 (신원 검사 강제 안 함)", async () => {
    const fetchImpl = fakeFetch({ 8000: { ok: true } });
    const res = await discoverBackendBaseUrl({ fetchImpl });
    expect(res.ok).toBe(true);
  });
});
