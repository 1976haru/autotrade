/**
 * 체크리스트 #63: registerServiceWorker 단위 테스트.
 *
 * 검증 invariant:
 *   - navigator.serviceWorker 없을 때 안전하게 null 반환 (앱 깨지지 않음)
 *   - 등록 경로가 BASE_URL + sw.js
 *   - scope는 BASE_URL 그대로
 *   - register가 throw해도 null 반환 + onError 콜백 호출
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  computeSwUrl,
  registerServiceWorker,
  unregisterServiceWorker,
} from "./registerServiceWorker";


describe("computeSwUrl", () => {
  it("appends sw.js to a base ending in slash", () => {
    expect(computeSwUrl("/autotrade/")).toBe("/autotrade/sw.js");
  });

  it("normalizes a base without trailing slash", () => {
    expect(computeSwUrl("/autotrade")).toBe("/autotrade/sw.js");
  });

  it("defaults to /sw.js for empty / null", () => {
    expect(computeSwUrl("")).toBe("/sw.js");
    expect(computeSwUrl(null)).toBe("/sw.js");
    expect(computeSwUrl(undefined)).toBe("/sw.js");
  });

  it("handles root base", () => {
    expect(computeSwUrl("/")).toBe("/sw.js");
  });
});


describe("registerServiceWorker", () => {
  const originalNavigator = globalThis.navigator;
  const originalWindow    = globalThis.window;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.navigator = originalNavigator;
    globalThis.window    = originalWindow;
  });

  it("returns null when navigator is undefined (SSR / non-browser)", async () => {
    // @ts-ignore
    globalThis.navigator = undefined;
    const result = await registerServiceWorker({ baseUrl: "/" });
    expect(result).toBeNull();
  });

  it("returns null when serviceWorker not supported", async () => {
    globalThis.navigator = {};   // no serviceWorker property
    const result = await registerServiceWorker({ baseUrl: "/" });
    expect(result).toBeNull();
  });

  it("calls navigator.serviceWorker.register with BASE_URL + sw.js", async () => {
    const fakeRegistration = { scope: "/autotrade/" };
    const register = vi.fn().mockResolvedValue(fakeRegistration);
    globalThis.navigator = { serviceWorker: { register } };
    globalThis.window = { location: { protocol: "https:" } };

    const result = await registerServiceWorker({ baseUrl: "/autotrade/", log: false });
    expect(register).toHaveBeenCalledWith("/autotrade/sw.js",
      expect.objectContaining({ scope: "/autotrade/" }));
    expect(result).toBe(fakeRegistration);
  });

  it("returns null and invokes onError when registration throws", async () => {
    const err = new Error("install failed");
    const register = vi.fn().mockRejectedValue(err);
    globalThis.navigator = { serviceWorker: { register } };
    globalThis.window = { location: { protocol: "https:" } };

    const onError = vi.fn();
    const result = await registerServiceWorker({
      baseUrl: "/", onError, log: false,
    });
    expect(result).toBeNull();
    expect(onError).toHaveBeenCalledWith(err);
  });

  it("returns null on non-http(s) protocols (file://, chrome-extension://)", async () => {
    const register = vi.fn();
    globalThis.navigator = { serviceWorker: { register } };
    globalThis.window = { location: { protocol: "file:" } };

    const result = await registerServiceWorker({ baseUrl: "/", log: false });
    expect(result).toBeNull();
    expect(register).not.toHaveBeenCalled();
  });
});


describe("unregisterServiceWorker", () => {
  const originalNavigator = globalThis.navigator;
  const originalCaches    = globalThis.caches;

  afterEach(() => {
    globalThis.navigator = originalNavigator;
    globalThis.caches    = originalCaches;
  });

  it("returns false when navigator.serviceWorker not supported", async () => {
    globalThis.navigator = {};
    expect(await unregisterServiceWorker()).toBe(false);
  });

  it("unregisters all registrations and clears caches", async () => {
    const reg1 = { unregister: vi.fn().mockResolvedValue(true) };
    const reg2 = { unregister: vi.fn().mockResolvedValue(true) };
    globalThis.navigator = {
      serviceWorker: {
        getRegistrations: vi.fn().mockResolvedValue([reg1, reg2]),
      },
    };
    globalThis.caches = {
      keys:   vi.fn().mockResolvedValue(["a", "b"]),
      delete: vi.fn().mockResolvedValue(true),
    };

    const ok = await unregisterServiceWorker();
    expect(ok).toBe(true);
    expect(reg1.unregister).toHaveBeenCalled();
    expect(reg2.unregister).toHaveBeenCalled();
    expect(globalThis.caches.delete).toHaveBeenCalledWith("a");
    expect(globalThis.caches.delete).toHaveBeenCalledWith("b");
  });
});
