import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { usePersistedState } from "./usePersistedState";


describe("usePersistedState", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(()  => { localStorage.clear(); });

  it("returns the default value when no entry exists", () => {
    const { result } = renderHook(() => usePersistedState("k", "default"));
    expect(result.current[0]).toBe("default");
  });

  it("hydrates from localStorage on mount when an entry exists", () => {
    localStorage.setItem("k", "hello");
    const { result } = renderHook(() => usePersistedState("k", "default"));
    expect(result.current[0]).toBe("hello");
  });

  it("setter updates state and writes to localStorage", () => {
    const { result } = renderHook(() => usePersistedState("k", "default"));
    act(() => { result.current[1]("changed"); });
    expect(result.current[0]).toBe("changed");
    expect(localStorage.getItem("k")).toBe("changed");
  });

  it("falls back to default when stored value fails validate", () => {
    localStorage.setItem("k", "bogus");
    const validate = (v) => v === "ok" || v === "default";
    const { result } = renderHook(() => usePersistedState("k", "default", validate));
    expect(result.current[0]).toBe("default");
  });

  it("hydrates a stored value that passes validate", () => {
    localStorage.setItem("k", "ok");
    const validate = (v) => v === "ok" || v === "default";
    const { result } = renderHook(() => usePersistedState("k", "default", validate));
    expect(result.current[0]).toBe("ok");
  });

  it("setting an empty string still persists (caller can opt out by clearing)", () => {
    const { result } = renderHook(() => usePersistedState("k", "fallback"));
    act(() => { result.current[1](""); });
    expect(result.current[0]).toBe("");
    expect(localStorage.getItem("k")).toBe("");
  });

  it("does not throw when localStorage is unavailable on read", () => {
    // Simulate a locked-down environment by replacing getItem with a thrower.
    const orig = Storage.prototype.getItem;
    Storage.prototype.getItem = () => { throw new Error("blocked"); };
    try {
      const { result } = renderHook(() => usePersistedState("k", "default"));
      expect(result.current[0]).toBe("default");
    } finally {
      Storage.prototype.getItem = orig;
    }
  });

  it("does not throw when localStorage is unavailable on write", () => {
    const orig = Storage.prototype.setItem;
    Storage.prototype.setItem = () => { throw new Error("blocked"); };
    try {
      const { result } = renderHook(() => usePersistedState("k", "default"));
      act(() => { result.current[1]("changed"); });
      // In-memory state still updates even though persistence failed
      expect(result.current[0]).toBe("changed");
    } finally {
      Storage.prototype.setItem = orig;
    }
  });
});
