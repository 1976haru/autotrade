/**
 * DisplaySettingsCard — 단위 + 통합 테스트 (사용자 요청서 §8).
 *
 * 필수 테스트 12종:
 *  1. 카드 렌더링
 *  2-4. 글자 크기 / 화면 폭 / 화면 테마 각 3 옵션 표시
 *  5. 기본값 large / wide / highContrastLight
 *  6-8. 변경 시 localStorage 저장
 *  9. 초기화 버튼 → 기본값 복원
 *  10. 잘못된 localStorage 값 → 기본값 fallback
 *  11. root 에 data-font-size / data-layout-width / data-display-theme 반영
 *  12. 실거래 활성화 button 0개
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { DisplaySettingsCard } from "./DisplaySettingsCard";
import {
  DEFAULT_DISPLAY_SETTINGS,
  DISPLAY_SETTINGS_STORAGE_KEY,
  applyDisplaySettingsToRoot,
  loadDisplaySettings,
  normalizeDisplaySettings,
  saveDisplaySettings,
} from "../../store/useDisplaySettings";


function _mkMemoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: vi.fn((key) => (key in data ? data[key] : null)),
    setItem: vi.fn((key, value) => { data[key] = String(value); }),
    removeItem: vi.fn((key) => { delete data[key]; }),
    clear: vi.fn(() => { for (const k of Object.keys(data)) delete data[k]; }),
  };
}


function _mkRootEl() {
  // jsdom HTMLElement — getAttribute / setAttribute 지원.
  return document.createElement("div");
}


afterEach(cleanup);


// ────────────────────────────────────────────────────────────────────────────
// A. Pure helpers
// ────────────────────────────────────────────────────────────────────────────


describe("useDisplaySettings — pure helpers", () => {
  it("DEFAULT_DISPLAY_SETTINGS matches user spec §2", () => {
    // 사용자 요청서 §2 기본값 정확 매칭.
    expect(DEFAULT_DISPLAY_SETTINGS).toEqual({
      fontSize:    "large",
      layoutWidth: "wide",
      theme:       "highContrastLight",
    });
  });

  it("normalizeDisplaySettings: null/undefined -> defaults", () => {
    expect(normalizeDisplaySettings(null)).toEqual(DEFAULT_DISPLAY_SETTINGS);
    expect(normalizeDisplaySettings(undefined)).toEqual(DEFAULT_DISPLAY_SETTINGS);
    expect(normalizeDisplaySettings("not an object")).toEqual(DEFAULT_DISPLAY_SETTINGS);
  });

  it("normalizeDisplaySettings: invalid field -> fallback to default", () => {
    const r = normalizeDisplaySettings({
      fontSize: "tiny", layoutWidth: "narrow", theme: "darkExtreme",
    });
    expect(r).toEqual(DEFAULT_DISPLAY_SETTINGS);
  });

  it("normalizeDisplaySettings: partial valid + invalid -> mixed fallback", () => {
    const r = normalizeDisplaySettings({
      fontSize: "xlarge", layoutWidth: "BOGUS", theme: "softLight",
    });
    expect(r.fontSize).toBe("xlarge");
    expect(r.layoutWidth).toBe("wide");  // fallback
    expect(r.theme).toBe("softLight");
  });

  it("loadDisplaySettings: empty storage -> defaults", () => {
    const storage = _mkMemoryStorage();
    expect(loadDisplaySettings(storage)).toEqual(DEFAULT_DISPLAY_SETTINGS);
  });

  it("loadDisplaySettings: invalid JSON -> defaults (no throw)", () => {
    const storage = _mkMemoryStorage({
      [DISPLAY_SETTINGS_STORAGE_KEY]: "{ not json",
    });
    expect(() => loadDisplaySettings(storage)).not.toThrow();
    expect(loadDisplaySettings(storage)).toEqual(DEFAULT_DISPLAY_SETTINGS);
  });

  it("loadDisplaySettings: valid JSON -> normalized", () => {
    const storage = _mkMemoryStorage({
      [DISPLAY_SETTINGS_STORAGE_KEY]: JSON.stringify({
        fontSize: "xlarge", layoutWidth: "full", theme: "softLight",
      }),
    });
    expect(loadDisplaySettings(storage)).toEqual({
      fontSize: "xlarge", layoutWidth: "full", theme: "softLight",
    });
  });

  it("saveDisplaySettings: writes normalized JSON", () => {
    const storage = _mkMemoryStorage();
    saveDisplaySettings({
      fontSize: "standard", layoutWidth: "full", theme: "light",
    }, storage);
    expect(storage.setItem).toHaveBeenCalledWith(
      DISPLAY_SETTINGS_STORAGE_KEY,
      JSON.stringify({
        fontSize: "standard", layoutWidth: "full", theme: "light",
      }),
    );
  });

  it("saveDisplaySettings: invalid input is normalized before save", () => {
    const storage = _mkMemoryStorage();
    saveDisplaySettings({
      fontSize: "tiny", layoutWidth: "narrow", theme: "darkExtreme",
    }, storage);
    const stored = JSON.parse(storage.data[DISPLAY_SETTINGS_STORAGE_KEY]);
    expect(stored).toEqual(DEFAULT_DISPLAY_SETTINGS);
  });

  it("applyDisplaySettingsToRoot: sets data attributes", () => {
    const el = _mkRootEl();
    applyDisplaySettingsToRoot({
      fontSize: "xlarge", layoutWidth: "full", theme: "softLight",
    }, el);
    expect(el.getAttribute("data-font-size")).toBe("xlarge");
    expect(el.getAttribute("data-layout-width")).toBe("full");
    expect(el.getAttribute("data-display-theme")).toBe("softLight");
  });

  it("applyDisplaySettingsToRoot: invalid input -> default attributes", () => {
    const el = _mkRootEl();
    applyDisplaySettingsToRoot({ fontSize: "tiny" }, el);
    expect(el.getAttribute("data-font-size")).toBe("large");
    expect(el.getAttribute("data-layout-width")).toBe("wide");
    expect(el.getAttribute("data-display-theme")).toBe("highContrastLight");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// B. Card rendering (사용자 요청서 §8 필수 1-4)
// ────────────────────────────────────────────────────────────────────────────


describe("<DisplaySettingsCard> — rendering", () => {
  it("renders the card with section label '화면 표시 설정'", () => {
    const storage = _mkMemoryStorage();
    const { container } = render(
      <DisplaySettingsCard storage={storage} root={_mkRootEl()} />,
    );
    expect(screen.getByTestId("display-settings-card")).toBeTruthy();
    expect(container.textContent).toContain("화면 표시 설정");
  });

  it("renders 3 font size options (표준/크게/아주 크게)", () => {
    const storage = _mkMemoryStorage();
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    expect(screen.getByTestId("display-settings-card-font-size-option-standard"))
      .toBeTruthy();
    expect(screen.getByTestId("display-settings-card-font-size-option-large"))
      .toBeTruthy();
    expect(screen.getByTestId("display-settings-card-font-size-option-xlarge"))
      .toBeTruthy();
  });

  it("renders 3 layout width options (표준/넓게/전체폭)", () => {
    const storage = _mkMemoryStorage();
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    expect(screen.getByTestId("display-settings-card-layout-width-option-standard"))
      .toBeTruthy();
    expect(screen.getByTestId("display-settings-card-layout-width-option-wide"))
      .toBeTruthy();
    expect(screen.getByTestId("display-settings-card-layout-width-option-full"))
      .toBeTruthy();
  });

  it("renders 3 theme options (밝은/고대비/부드러운)", () => {
    const storage = _mkMemoryStorage();
    const { container } = render(
      <DisplaySettingsCard storage={storage} root={_mkRootEl()} />,
    );
    expect(screen.getByTestId("display-settings-card-theme-option-light"))
      .toBeTruthy();
    expect(screen.getByTestId("display-settings-card-theme-option-highContrastLight"))
      .toBeTruthy();
    expect(screen.getByTestId("display-settings-card-theme-option-softLight"))
      .toBeTruthy();
    const text = container.textContent || "";
    expect(text).toContain("밝은 테마");
    expect(text).toContain("고대비 밝은 테마");
    expect(text).toContain("부드러운 밝은 테마");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// C. Defaults + selection state (필수 5)
// ────────────────────────────────────────────────────────────────────────────


describe("<DisplaySettingsCard> — defaults", () => {
  it("default selected: large / wide / highContrastLight", () => {
    const storage = _mkMemoryStorage();
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    expect(
      screen.getByTestId("display-settings-card-font-size-option-large")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("display-settings-card-layout-width-option-wide")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("display-settings-card-theme-option-highContrastLight")
        .getAttribute("data-selected"),
    ).toBe("true");
  });

  it("data-current-* on card root reflects defaults", () => {
    const storage = _mkMemoryStorage();
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    const card = screen.getByTestId("display-settings-card");
    expect(card.getAttribute("data-current-font-size")).toBe("large");
    expect(card.getAttribute("data-current-layout-width")).toBe("wide");
    expect(card.getAttribute("data-current-display-theme")).toBe("highContrastLight");
  });

  it("기본값 badges visible on default options", () => {
    const storage = _mkMemoryStorage();
    const { container } = render(
      <DisplaySettingsCard storage={storage} root={_mkRootEl()} />,
    );
    expect(
      screen.getByTestId("display-settings-card-font-size-option-large-default-badge"),
    ).toBeTruthy();
    expect(
      screen.getByTestId("display-settings-card-layout-width-option-wide-default-badge"),
    ).toBeTruthy();
    expect(
      screen.getByTestId(
        "display-settings-card-theme-option-highContrastLight-default-badge",
      ),
    ).toBeTruthy();
    expect(container.textContent).toContain("기본값");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// D. Changing options persists + applies (필수 6-8, 11)
// ────────────────────────────────────────────────────────────────────────────


describe("<DisplaySettingsCard> — change + persist + apply", () => {
  it("changing font size saves to localStorage", async () => {
    const storage = _mkMemoryStorage();
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    fireEvent.click(screen.getByTestId("display-settings-card-font-size-option-xlarge"));
    await waitFor(() => {
      const stored = JSON.parse(storage.data[DISPLAY_SETTINGS_STORAGE_KEY] || "{}");
      expect(stored.fontSize).toBe("xlarge");
    });
    expect(
      screen.getByTestId("display-settings-card-font-size-option-xlarge")
        .getAttribute("data-selected"),
    ).toBe("true");
  });

  it("changing layout width saves to localStorage", async () => {
    const storage = _mkMemoryStorage();
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    fireEvent.click(screen.getByTestId("display-settings-card-layout-width-option-full"));
    await waitFor(() => {
      const stored = JSON.parse(storage.data[DISPLAY_SETTINGS_STORAGE_KEY] || "{}");
      expect(stored.layoutWidth).toBe("full");
    });
  });

  it("changing theme saves to localStorage", async () => {
    const storage = _mkMemoryStorage();
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    fireEvent.click(screen.getByTestId("display-settings-card-theme-option-softLight"));
    await waitFor(() => {
      const stored = JSON.parse(storage.data[DISPLAY_SETTINGS_STORAGE_KEY] || "{}");
      expect(stored.theme).toBe("softLight");
    });
  });

  it("root receives data-font-size / data-layout-width / data-display-theme", async () => {
    const storage = _mkMemoryStorage();
    const rootEl = _mkRootEl();
    render(<DisplaySettingsCard storage={storage} root={rootEl} />);
    // 초기 mount — 기본값.
    await waitFor(() => {
      expect(rootEl.getAttribute("data-font-size")).toBe("large");
      expect(rootEl.getAttribute("data-layout-width")).toBe("wide");
      expect(rootEl.getAttribute("data-display-theme")).toBe("highContrastLight");
    });
    // 변경 후.
    fireEvent.click(screen.getByTestId("display-settings-card-font-size-option-xlarge"));
    fireEvent.click(screen.getByTestId("display-settings-card-layout-width-option-full"));
    fireEvent.click(screen.getByTestId("display-settings-card-theme-option-softLight"));
    await waitFor(() => {
      expect(rootEl.getAttribute("data-font-size")).toBe("xlarge");
      expect(rootEl.getAttribute("data-layout-width")).toBe("full");
      expect(rootEl.getAttribute("data-display-theme")).toBe("softLight");
    });
  });
});


// ────────────────────────────────────────────────────────────────────────────
// E. Reset button (필수 9)
// ────────────────────────────────────────────────────────────────────────────


describe("<DisplaySettingsCard> — reset", () => {
  it("reset button restores defaults", async () => {
    const storage = _mkMemoryStorage({
      [DISPLAY_SETTINGS_STORAGE_KEY]: JSON.stringify({
        fontSize: "xlarge", layoutWidth: "full", theme: "softLight",
      }),
    });
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    // 시작 시점 — non-default 로 mount.
    expect(
      screen.getByTestId("display-settings-card-font-size-option-xlarge")
        .getAttribute("data-selected"),
    ).toBe("true");
    fireEvent.click(screen.getByTestId("display-settings-card-reset-btn"));
    await waitFor(() => {
      expect(
        screen.getByTestId("display-settings-card-font-size-option-large")
          .getAttribute("data-selected"),
      ).toBe("true");
    });
    expect(
      screen.getByTestId("display-settings-card-layout-width-option-wide")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("display-settings-card-theme-option-highContrastLight")
        .getAttribute("data-selected"),
    ).toBe("true");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// F. Invalid localStorage fallback (필수 10)
// ────────────────────────────────────────────────────────────────────────────


describe("<DisplaySettingsCard> — invalid storage fallback", () => {
  it("malformed JSON in localStorage falls back to defaults", () => {
    const storage = _mkMemoryStorage({
      [DISPLAY_SETTINGS_STORAGE_KEY]: "{ this is not valid",
    });
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    expect(
      screen.getByTestId("display-settings-card-font-size-option-large")
        .getAttribute("data-selected"),
    ).toBe("true");
  });

  it("unknown enum values in localStorage falls back to defaults", () => {
    const storage = _mkMemoryStorage({
      [DISPLAY_SETTINGS_STORAGE_KEY]: JSON.stringify({
        fontSize: "ULTRA_TINY", layoutWidth: "NARROW", theme: "midnightDark",
      }),
    });
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    expect(
      screen.getByTestId("display-settings-card-font-size-option-large")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("display-settings-card-layout-width-option-wide")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("display-settings-card-theme-option-highContrastLight")
        .getAttribute("data-selected"),
    ).toBe("true");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// G. Persistence across remount
// ────────────────────────────────────────────────────────────────────────────


describe("<DisplaySettingsCard> — persistence across remount", () => {
  it("re-mount picks up previously saved values", async () => {
    const storage = _mkMemoryStorage();
    const { unmount } = render(
      <DisplaySettingsCard storage={storage} root={_mkRootEl()} />,
    );
    fireEvent.click(screen.getByTestId("display-settings-card-font-size-option-xlarge"));
    fireEvent.click(screen.getByTestId("display-settings-card-layout-width-option-full"));
    fireEvent.click(screen.getByTestId("display-settings-card-theme-option-softLight"));
    await waitFor(() => {
      const stored = JSON.parse(storage.data[DISPLAY_SETTINGS_STORAGE_KEY] || "{}");
      expect(stored).toEqual({
        fontSize: "xlarge", layoutWidth: "full", theme: "softLight",
      });
    });
    unmount();
    cleanup();
    // Re-mount with same storage.
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    expect(
      screen.getByTestId("display-settings-card-font-size-option-xlarge")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("display-settings-card-layout-width-option-full")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("display-settings-card-theme-option-softLight")
        .getAttribute("data-selected"),
    ).toBe("true");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// H. Invariants — 실거래 / LIVE / 입력 form 0개 (필수 12)
// ────────────────────────────────────────────────────────────────────────────


describe("<DisplaySettingsCard> — invariants", () => {
  it("renders 'Paper / SIMULATION 전용 · 표시 설정' badge", () => {
    const storage = _mkMemoryStorage();
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    expect(
      screen.getByTestId("display-settings-card-paper-only-badge").textContent,
    ).toContain("Paper");
  });

  it("renders description + preview text", () => {
    const storage = _mkMemoryStorage();
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    expect(
      screen.getByTestId("display-settings-card-description").textContent,
    ).toContain("글자 크기");
    expect(
      screen.getByTestId("display-settings-card-preview").textContent,
    ).toContain("미리보기");
  });

  it("renders reset button '기본값으로 되돌리기'", () => {
    const storage = _mkMemoryStorage();
    render(<DisplaySettingsCard storage={storage} root={_mkRootEl()} />);
    expect(
      screen.getByTestId("display-settings-card-reset-btn").textContent,
    ).toBe("기본값으로 되돌리기");
  });

  it("has NO live-trading buttons or labels", () => {
    const storage = _mkMemoryStorage();
    const { container } = render(
      <DisplaySettingsCard storage={storage} root={_mkRootEl()} />,
    );
    const text = container.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "place order",
      "실거래 시작", "실거래 활성화",
      "ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
      "AI 자동매매 활성화", "LIVE_AI_EXECUTION",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
  });

  it("has NO input / textarea / select form elements", () => {
    const storage = _mkMemoryStorage();
    const { container } = render(
      <DisplaySettingsCard storage={storage} root={_mkRootEl()} />,
    );
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
    expect(container.querySelectorAll("select").length).toBe(0);
  });

  it("does not expose secret patterns", () => {
    const storage = _mkMemoryStorage();
    const { container } = render(
      <DisplaySettingsCard storage={storage} root={_mkRootEl()} />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "sk-ant-", "ghp_", "xoxb-",
      "anthropic_api_key", "openai_api_key", "kis_app_secret",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });
});
