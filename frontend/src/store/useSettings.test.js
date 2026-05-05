import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useSettings } from "./useSettings";


const STORAGE_KEY = "autotrade.operatorName";


describe("useSettings · operatorName", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(()  => { localStorage.clear(); });

  it("initializes operatorName as empty string when localStorage is empty", () => {
    const { result } = renderHook(() => useSettings());
    expect(result.current.operatorName).toBe("");
  });

  it("hydrates operatorName from localStorage on mount", () => {
    localStorage.setItem(STORAGE_KEY, "ops1");
    const { result } = renderHook(() => useSettings());
    expect(result.current.operatorName).toBe("ops1");
  });

  it("setOperatorName updates state and persists to localStorage", () => {
    const { result } = renderHook(() => useSettings());
    act(() => { result.current.setOperatorName("trader-x"); });
    expect(result.current.operatorName).toBe("trader-x");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("trader-x");
  });

  it("clearing the field persists empty string (lets the user opt out)", () => {
    localStorage.setItem(STORAGE_KEY, "ops1");
    const { result } = renderHook(() => useSettings());
    act(() => { result.current.setOperatorName(""); });
    expect(result.current.operatorName).toBe("");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("");
  });
});
