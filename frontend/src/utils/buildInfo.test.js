/**
 * #57 / 7-05 — buildInfo helper 단위 테스트.
 *
 * lock 하는 invariant:
 *  - commit_full → short commit 파생
 *  - build_time 없음 → "확인 불가"
 *  - dirty true 표시
 *  - secret-like 값은 결과에 노출되지 않음 (whitelist copy)
 *  - metadata missing 이어도 crash 없이 unknown
 */

import { describe, it, expect } from "vitest";

import {
  normalizeBuildInfo,
  getBuildInfo,
  getCommitShort,
  formatBuildTime,
  isUnknownBuildInfo,
  commitsMatch,
  BUILD_INFO_UNKNOWN,
} from "./buildInfo";


describe("normalizeBuildInfo", () => {
  it("derives short commit from commit_full", () => {
    const info = normalizeBuildInfo({ commit_full: "abcdef1234567890" });
    expect(info.commit).toBe("abcdef1");
    expect(info.commit_full).toBe("abcdef1234567890");
  });

  it("keeps explicit short commit", () => {
    const info = normalizeBuildInfo({ commit: "1234567", commit_full: "1234567890abcdef" });
    expect(info.commit).toBe("1234567");
  });

  it("missing fields → unknown (no crash)", () => {
    const info = normalizeBuildInfo(null);
    expect(info.version).toBe(BUILD_INFO_UNKNOWN);
    expect(info.commit).toBe(BUILD_INFO_UNKNOWN);
    expect(info.branch).toBe(BUILD_INFO_UNKNOWN);
    expect(info.build_time).toBe(BUILD_INFO_UNKNOWN);
  });

  it("is_dirty parses boolean and string", () => {
    expect(normalizeBuildInfo({ is_dirty: true }).is_dirty).toBe(true);
    expect(normalizeBuildInfo({ is_dirty: "true" }).is_dirty).toBe(true);
    expect(normalizeBuildInfo({ is_dirty: "1" }).is_dirty).toBe(true);
    expect(normalizeBuildInfo({ is_dirty: "false" }).is_dirty).toBe(false);
    expect(normalizeBuildInfo({ is_dirty: false }).is_dirty).toBe(false);
  });

  it("safety invariants always present", () => {
    const info = normalizeBuildInfo({ is_live_authorization: true, contains_secret: true });
    expect(info.is_live_authorization).toBe(false);
    expect(info.contains_secret).toBe(false);
  });

  it("does not surface secret-like fields (whitelist copy)", () => {
    const info = normalizeBuildInfo({
      version: "1.0.0",
      commit: "abc1234",
      kis_app_secret: "LEAKSECRET",
      access_token: "LEAKTOKEN",
      kis_account_no: "98765432-11",
    });
    const blob = JSON.stringify(info);
    expect(blob).not.toContain("LEAKSECRET");
    expect(blob).not.toContain("LEAKTOKEN");
    expect(blob).not.toContain("98765432");
    expect(blob).not.toContain("kis_app_secret");
  });
});


describe("getCommitShort", () => {
  it("truncates to 7 by default", () => {
    expect(getCommitShort("abcdef1234567890")).toBe("abcdef1");
  });
  it("unknown stays unknown", () => {
    expect(getCommitShort(BUILD_INFO_UNKNOWN)).toBe(BUILD_INFO_UNKNOWN);
    expect(getCommitShort(null)).toBe(BUILD_INFO_UNKNOWN);
  });
});


describe("formatBuildTime", () => {
  it("formats ISO to YYYY-MM-DD HH:mm", () => {
    const out = formatBuildTime("2026-05-24T10:30:00+09:00");
    expect(out).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
  });
  it("unknown / invalid → 확인 불가", () => {
    expect(formatBuildTime(BUILD_INFO_UNKNOWN)).toBe("확인 불가");
    expect(formatBuildTime("not-a-date")).toBe("확인 불가");
    expect(formatBuildTime(null)).toBe("확인 불가");
  });
});


describe("isUnknownBuildInfo", () => {
  it("true when commit unknown", () => {
    expect(isUnknownBuildInfo(normalizeBuildInfo({ version: "1.0.0" }))).toBe(true);
  });
  it("true when version unknown placeholder", () => {
    expect(isUnknownBuildInfo(normalizeBuildInfo({
      version: "0.0.0-unknown", commit: "abc1234",
    }))).toBe(true);
  });
  it("false when both present", () => {
    expect(isUnknownBuildInfo(normalizeBuildInfo({
      version: "1.0.0", commit: "abc1234",
    }))).toBe(false);
  });
});


describe("commitsMatch", () => {
  it("true when same short commit", () => {
    expect(commitsMatch({ commit: "abc1234x" }, { commit: "abc1234y" })).toBe(true);
  });
  it("false when different", () => {
    expect(commitsMatch({ commit: "abc1234" }, { commit: "def5678" })).toBe(false);
  });
  it("null when either unknown", () => {
    expect(commitsMatch({ commit: "abc1234" }, { commit: "unknown" })).toBe(null);
  });
});


describe("getBuildInfo (Vite-injected)", () => {
  it("returns a normalized shape without crashing", () => {
    const info = getBuildInfo();
    expect(info).toHaveProperty("version");
    expect(info).toHaveProperty("commit");
    expect(info).toHaveProperty("build_time");
    expect(info.is_live_authorization).toBe(false);
    expect(info.contains_secret).toBe(false);
  });
});
