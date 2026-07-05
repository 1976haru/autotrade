import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ThemeFilterCard } from "./ThemeFilterCard";


afterEach(cleanup);

const payload = (enabled = true) => ({
  catalog_version: "kr-theme-v1",
  effective_blocked_symbol_count: enabled ? 0 : 25,
  unmapped_symbol_count: 0,
  themes: [{
    id: "semiconductor", label: "반도체", enabled,
    duration: enabled ? null : "today",
    expires_at_kst: enabled ? null : "2026-07-06T00:00:00+09:00",
    mapped_symbol_count: 25,
  }],
});


describe("<ThemeFilterCard>", () => {
  it("taxonomy v2의 20개 테마 스위치를 모두 표시", async () => {
    const themes = Array.from({ length: 20 }, (_, i) => ({
      id: `theme_${i}`, label: `테마 ${i}`, enabled: true,
      duration: null, expires_at_kst: null, mapped_symbol_count: i + 1,
    }));
    const api = {
      themeFilterGet: vi.fn(async () => ({
        ...payload(), catalog_version: "kr-theme-v2", themes,
      })),
      themeFilterPatch: vi.fn(),
    };
    render(<ThemeFilterCard apiClient={api} />);
    await screen.findByTestId("theme-toggle-theme_0");
    expect(screen.getAllByRole("switch")).toHaveLength(20);
  });

  it("신규 진입만 차단하고 청산은 유지한다는 안내를 표시", async () => {
    const api = { themeFilterGet: vi.fn(async () => payload()), themeFilterPatch: vi.fn() };
    render(<ThemeFilterCard apiClient={api} />);
    expect((await screen.findByTestId("theme-filter-safety")).textContent)
      .toMatch(/신규 진입만 차단/);
    expect(screen.getByTestId("theme-filter-safety").textContent).toMatch(/손절.*청산/);
  });

  it("반도체 OFF를 오늘만으로 저장하고 서버 응답 후 OFF 표시", async () => {
    const patch = vi.fn(async () => payload(false));
    const api = { themeFilterGet: vi.fn(async () => payload()), themeFilterPatch: patch };
    render(<ThemeFilterCard apiClient={api} />);
    const toggle = await screen.findByTestId("theme-toggle-semiconductor");
    fireEvent.click(toggle);
    await waitFor(() => expect(patch).toHaveBeenCalledWith(
      "semiconductor", { enabled: false, duration: "today" },
    ));
    await waitFor(() => expect(screen.getByTestId("theme-toggle-semiconductor").textContent)
      .toBe("OFF"));
  });

  it("해제까지 선택과 ON 복귀 payload를 보낸다", async () => {
    const patch = vi.fn()
      .mockResolvedValueOnce(payload(false))
      .mockResolvedValueOnce(payload(true));
    const api = { themeFilterGet: vi.fn(async () => payload()), themeFilterPatch: patch };
    render(<ThemeFilterCard apiClient={api} />);
    fireEvent.change(await screen.findByTestId("theme-duration-semiconductor"), {
      target: { value: "until_enabled" },
    });
    fireEvent.click(screen.getByTestId("theme-toggle-semiconductor"));
    await waitFor(() => expect(patch).toHaveBeenNthCalledWith(
      1, "semiconductor", { enabled: false, duration: "until_enabled" },
    ));
    fireEvent.click(await screen.findByTestId("theme-toggle-semiconductor"));
    await waitFor(() => expect(patch).toHaveBeenNthCalledWith(
      2, "semiconductor", { enabled: true },
    ));
  });
});
