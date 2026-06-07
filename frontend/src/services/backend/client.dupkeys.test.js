import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

// ★가드: backendApi 객체 리터럴에 *중복 키* 가 있으면 JS 는 조용히 뒤엣것만 남긴다.
//   실제로 `preflight:` 가 두 번(/api/preflight, /api/system/preflight) 정의돼
//   홈 출발전점검 패널이 엉뚱한 엔드포인트를 때렸다(2026-06-07). 재발 방지.
describe("backendApi — 중복 키 금지", () => {
  it("client.js 메서드 키에 중복이 없다", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const src = readFileSync(resolve(here, "client.js"), "utf-8");
    // `  name: (` / `  name:        () =>` 형태의 2-space 들여쓰기 메서드 키만 수집.
    const keys = [];
    for (const m of src.matchAll(/^ {2}([a-zA-Z_$][\w$]*):\s*(?:\(|async|function)/gm)) {
      keys.push(m[1]);
    }
    const seen = new Set();
    const dups = [];
    for (const k of keys) {
      if (seen.has(k)) dups.push(k);
      seen.add(k);
    }
    expect(dups, `중복 키(뒤엣것이 앞엣것을 가림): ${dups.join(", ")}`).toEqual([]);
  });
});
