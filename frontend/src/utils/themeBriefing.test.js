import { describe, it, expect } from "vitest";

import { themeBriefingById, formatThemeBriefingLine, themeBriefingStaleNote } from "./themeBriefing";

describe("themeBriefingById", () => {
  it("theme_id로 인덱싱하고 session_date_us를 보존", () => {
    const resp = {
      session_date_us: "2026-07-03",
      themes: [{ theme_id: "semiconductor", label: "반도체", mapping_quality: "DIRECT", proxies: [], status: "OK" }],
    };
    const { sessionDateUs, byId } = themeBriefingById(resp);
    expect(sessionDateUs).toBe("2026-07-03");
    expect(byId.semiconductor.label).toBe("반도체");
  });

  it("빈 응답에도 크래시 없이 빈 맵", () => {
    expect(themeBriefingById(null)).toEqual({
      sessionDateUs: null, byId: {}, stale: false, staleReason: null,
    });
  });

  it("stale=true 응답은 stale/staleReason을 보존(값은 이전 데이터 그대로)", () => {
    const resp = {
      session_date_us: "2026-07-03", stale: true, stale_reason: "FETCH_FAILED",
      themes: [{ theme_id: "semiconductor", label: "반도체", mapping_quality: "DIRECT", proxies: [], status: "OK" }],
    };
    const { stale, staleReason } = themeBriefingById(resp);
    expect(stale).toBe(true);
    expect(staleReason).toBe("FETCH_FAILED");
  });
});

describe("themeBriefingStaleNote", () => {
  it("stale=false면 빈 문자열(추천/판단 문구 아님, 그냥 안 보임)", () => {
    expect(themeBriefingStaleNote({ stale: false })).toBe("");
    expect(themeBriefingStaleNote(null)).toBe("");
  });

  it("stale=true면 갱신 실패 안내를 반환", () => {
    expect(themeBriefingStaleNote({ stale: true })).toBe("갱신 실패 · 이전 기준 표시 중");
  });
});

describe("formatThemeBriefingLine", () => {
  it("매핑 없음(NONE) → 대시", () => {
    expect(formatThemeBriefingLine({ mapping_quality: "NONE", proxies: [] }, "2026-07-03")).toBe("—");
    expect(formatThemeBriefingLine(undefined, "2026-07-03")).toBe("—");
  });

  it("단일 proxy 하락 → 화살표/부호/기준일 표시, 추천 문구 없음", () => {
    const entry = { mapping_quality: "DIRECT", proxies: [{ ticker: "^SOX", change_pct: -3.2, status: "OK" }] };
    const line = formatThemeBriefingLine(entry, "2026-07-03");
    expect(line).toBe("전일 ^SOX -3.2% ↓ (07-03 기준)");
    for (const banned of ["추천", "매수", "제외", "쉬세요", "유망"]) {
      expect(line).not.toContain(banned);
    }
  });

  it("복수 proxy(PARTIAL)는 각각 병기(합성 없음)", () => {
    const entry = {
      mapping_quality: "PARTIAL",
      proxies: [
        { ticker: "URA", change_pct: 1.1, status: "OK" },
        { ticker: "XLU", change_pct: -0.4, status: "OK" },
      ],
    };
    expect(formatThemeBriefingLine(entry, "2026-07-03")).toBe("전일 URA +1.1% ↑ · XLU -0.4% ↓ (07-03 기준)");
  });

  it("매핑은 있으나 조회 실패(FETCH_ERROR)뿐이면 '데이터 없음'", () => {
    const entry = { mapping_quality: "DIRECT", proxies: [{ ticker: "^SOX", change_pct: null, status: "FETCH_ERROR" }] };
    expect(formatThemeBriefingLine(entry, "2026-07-03")).toBe("데이터 없음");
  });

  it("기준일 없으면 괄호 생략", () => {
    const entry = { mapping_quality: "DIRECT", proxies: [{ ticker: "^SOX", change_pct: 0, status: "OK" }] };
    expect(formatThemeBriefingLine(entry, null)).toBe("전일 ^SOX 0.0% →");
  });
});
