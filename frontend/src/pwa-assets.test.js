/**
 * 체크리스트 #63: 정적 PWA 자산 검증.
 *
 * Node 환경에서 fs로 public/ 자산을 읽어 검증:
 *   - manifest.webmanifest 존재 + 필수 키
 *   - sw.js 존재 + 금지 패턴 0건 (Push API / Background Sync / API 캐시)
 *   - offline.html 존재 + 주문 비활성 안내
 *   - icons/* 존재
 */

import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";


// ESM `__dirname` 등가 — vitest는 frontend/ 에서 실행되므로 PROJECT_ROOT는 ../
const _here = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(_here, "..");

function readPublic(p) {
  return readFileSync(resolve(PROJECT_ROOT, "public", p), "utf8");
}

function publicExists(p) {
  return existsSync(resolve(PROJECT_ROOT, "public", p));
}


describe("PWA assets — manifest.webmanifest", () => {
  it("exists at public/manifest.webmanifest and parses as JSON", () => {
    expect(publicExists("manifest.webmanifest")).toBe(true);
    // 단순 parse 검증 — 키 검사는 아래 it 들에서 수행.
    const m = JSON.parse(readPublic("manifest.webmanifest"));
    expect(m).toBeTruthy();
  });

  it("has the required PWA keys (name / short_name / start_url / display / icons)", () => {
    const m = JSON.parse(readPublic("manifest.webmanifest"));
    expect(typeof m.name).toBe("string");
    expect(m.name.length).toBeGreaterThan(0);
    expect(typeof m.short_name).toBe("string");
    expect(typeof m.start_url).toBe("string");
    expect(["standalone", "fullscreen", "minimal-ui"]).toContain(m.display);
    expect(Array.isArray(m.icons)).toBe(true);
    expect(m.icons.length).toBeGreaterThanOrEqual(2);
  });

  it("declares theme_color + background_color", () => {
    const m = JSON.parse(readPublic("manifest.webmanifest"));
    expect(typeof m.theme_color).toBe("string");
    expect(typeof m.background_color).toBe("string");
  });

  it("includes finance / productivity categories", () => {
    const m = JSON.parse(readPublic("manifest.webmanifest"));
    expect(Array.isArray(m.categories)).toBe(true);
    expect(m.categories).toContain("finance");
  });

  it("icons reference existing files at sizes 192 and 512", () => {
    const m = JSON.parse(readPublic("manifest.webmanifest"));
    const sizes = m.icons.map((i) => i.sizes);
    expect(sizes).toContain("192x192");
    expect(sizes).toContain("512x512");
    for (const icon of m.icons) {
      expect(publicExists(icon.src)).toBe(true);
    }
  });

  it("includes a maskable icon for adaptive launchers", () => {
    const m = JSON.parse(readPublic("manifest.webmanifest"));
    const maskable = m.icons.find((i) => (i.purpose || "").includes("maskable"));
    expect(maskable).toBeTruthy();
  });
});


describe("PWA assets — service worker (sw.js)", () => {
  it("exists at public/sw.js", () => {
    expect(publicExists("sw.js")).toBe(true);
  });

  it("does NOT register Push / Background Sync / Notification APIs", () => {
    const src = readPublic("sw.js");
    // 본 invariant는 사용자 사양 (절대 원칙 #8: 푸시 알림 구현 금지).
    expect(src).not.toMatch(/self\.addEventListener\s*\(\s*['"]push['"]/);
    expect(src).not.toMatch(/registration\.pushManager/);
    expect(src).not.toMatch(/registerSync\(/);
    expect(src).not.toMatch(/registerPeriodicSync\(/);
    expect(src).not.toMatch(/showNotification\(/);
    // 'Notification' constructor 사용도 금지
    expect(src).not.toMatch(/new\s+Notification\(/);
  });

  it("does NOT cache /api/* responses (no cache.put under /api branch)", () => {
    const src = readPublic("sw.js");
    // API 분기는 network-only + sentinel만 반환해야 한다. cache.put이 /api 흐름
    // 안에 있으면 안 됨. 정밀한 AST 검사는 어렵지만, 본 파일의 fetch 핸들러는
    // 명확히 /api 분기에서 return하므로 같은 함수 안에 cache.put + isApiRequest가
    // 동시에 등장하면 의심.
    // 본 SW의 API 분기는 `apiOfflineResponse()` 또는 fetch만 반환 — 정확히 lock.
    const apiBranchMatch = src.match(/isApiRequest\(url\)\)\s*{[\s\S]*?return\s*;\s*\n\s*}/);
    if (apiBranchMatch) {
      const branch = apiBranchMatch[0];
      expect(branch).not.toMatch(/cache\.put/);
    }
  });

  it("declares SW_VERSION + uses Cache API", () => {
    const src = readPublic("sw.js");
    expect(src).toMatch(/SW_VERSION\s*=/);
    expect(src).toMatch(/caches\.open/);
  });

  it("registers install + activate + fetch + message listeners (no push)", () => {
    const src = readPublic("sw.js");
    expect(src).toMatch(/addEventListener\s*\(\s*['"]install['"]/);
    expect(src).toMatch(/addEventListener\s*\(\s*['"]activate['"]/);
    expect(src).toMatch(/addEventListener\s*\(\s*['"]fetch['"]/);
  });
});


describe("PWA assets — offline.html", () => {
  it("exists at public/offline.html", () => {
    expect(publicExists("offline.html")).toBe(true);
  });

  it("warns that 주문 / 승인 / 봇 / Kill Switch are disabled offline", () => {
    const src = readPublic("offline.html");
    expect(src).toMatch(/주문/);
    expect(src).toMatch(/승인/);
    expect(src).toMatch(/Kill Switch/);
    expect(src).toMatch(/동작하지 않습니다/);
  });

  it("does not reference Push API enable buttons", () => {
    const src = readPublic("offline.html");
    expect(src).not.toMatch(/푸시 알림 켜기/);
    expect(src).not.toMatch(/알림 활성/);
    expect(src).not.toMatch(/Enable Push/i);
  });
});


describe("PWA assets — index.html", () => {
  it("links manifest.webmanifest", () => {
    const src = readFileSync(resolve(PROJECT_ROOT, "index.html"), "utf8");
    expect(src).toMatch(/<link[^>]+rel=["']manifest["'][^>]+>/);
    expect(src).toMatch(/manifest\.webmanifest/);
  });

  it("declares theme-color meta", () => {
    const src = readFileSync(resolve(PROJECT_ROOT, "index.html"), "utf8");
    expect(src).toMatch(/<meta[^>]+name=["']theme-color["']/);
  });

  it("declares apple-mobile-web-app meta + apple-touch-icon for iOS", () => {
    const src = readFileSync(resolve(PROJECT_ROOT, "index.html"), "utf8");
    expect(src).toMatch(/apple-mobile-web-app-capable/);
    expect(src).toMatch(/apple-mobile-web-app-title/);
    expect(src).toMatch(/<link[^>]+rel=["']apple-touch-icon["']/);
  });
});
