import { describe, expect, it } from "vitest";

import { MODE_DISPLAY, findModeDisplay } from "./modes";


describe("MODE_DISPLAY (108 / 093)", () => {
  it("covers all six operating modes from backend modes.py", () => {
    const ids = MODE_DISPLAY.map((m) => m.id);
    expect(ids).toEqual([
      "SIMULATION", "PAPER", "LIVE_SHADOW",
      "LIVE_MANUAL_APPROVAL", "LIVE_AI_ASSIST", "LIVE_AI_EXECUTION",
    ]);
  });

  it("each entry has a non-empty short label and a hex color", () => {
    MODE_DISPLAY.forEach((m) => {
      expect(m.label.length).toBeGreaterThan(0);
      expect(m.color).toMatch(/^#[0-9a-f]{6}$/i);
    });
  });
});


describe("findModeDisplay (108)", () => {
  it("returns the canonical entry for a known id", () => {
    const sim = findModeDisplay("SIMULATION");
    expect(sim.label).toBe("SIM");
    expect(sim.color).toBeDefined();

    const ai = findModeDisplay("LIVE_AI_ASSIST");
    expect(ai.label).toBe("AI 보조");
  });

  it("returns null for null/undefined/empty input", () => {
    expect(findModeDisplay(null)).toBeNull();
    expect(findModeDisplay(undefined)).toBeNull();
    expect(findModeDisplay("")).toBeNull();
  });

  it("returns a fallback entry (raw id + neutral color) for unknown modes", () => {
    const future = findModeDisplay("FUTURES_SIMULATION");
    expect(future).not.toBeNull();
    expect(future.id).toBe("FUTURES_SIMULATION");
    expect(future.label).toBe("FUTURES_SIMULATION");
    expect(future.color).toBe("#475569");
  });
});
