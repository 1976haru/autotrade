import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import { useLiveEngine } from "./useLiveEngine";


vi.mock("../services/backend/client", () => ({
  backendApi: {
    engineStatus:    vi.fn(),
    engineRegistry:  vi.fn(),
    engineConfigure: vi.fn(),
    engineTick:      vi.fn(),
    engineReset:     vi.fn(),
    engineReplay:    vi.fn(),
  },
}));


const _STATUS = { configured: false, bars_seen: 0, holding: false };
const _REGISTRY = [{
  name: "sma_crossover",
  class_name: "SmaCrossoverStrategy",
  description: "단기/장기 이동평균 교차 전략",
  params: [
    { name: "short", type: "int", default: 5,  required: false },
    { name: "long",  type: "int", default: 20, required: false },
  ],
}];


describe("useLiveEngine", () => {
  beforeEach(() => {
    backendApi.engineStatus.mockReset();
    backendApi.engineRegistry.mockReset();
  });

  it("fetches status and registry on mount", async () => {
    backendApi.engineStatus.mockResolvedValue(_STATUS);
    backendApi.engineRegistry.mockResolvedValue(_REGISTRY);

    const { result } = renderHook(() => useLiveEngine());

    await waitFor(() => {
      expect(result.current.status).toEqual(_STATUS);
      expect(result.current.registry).toEqual(_REGISTRY);
    });

    expect(backendApi.engineStatus).toHaveBeenCalledTimes(1);
    expect(backendApi.engineRegistry).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBe("");
  });

  it("registry stays null and error set when registry fetch fails", async () => {
    backendApi.engineStatus.mockResolvedValue(_STATUS);
    backendApi.engineRegistry.mockRejectedValue(new Error("registry down"));

    const { result } = renderHook(() => useLiveEngine());

    await waitFor(() => expect(result.current.error).toBe("registry down"));
    expect(result.current.registry).toBeNull();
    // status fetch independently succeeded
    expect(result.current.status).toEqual(_STATUS);
  });

  it("status fetch failure does not block registry fetch", async () => {
    backendApi.engineStatus.mockRejectedValue(new Error("status down"));
    backendApi.engineRegistry.mockResolvedValue(_REGISTRY);

    const { result } = renderHook(() => useLiveEngine());

    await waitFor(() => expect(result.current.registry).toEqual(_REGISTRY));
    // status remained null but registry is loaded
    expect(result.current.status).toBeNull();
  });
});
