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

  it("briefing prop 미전달 시 브리핑 줄을 렌더하지 않는다(기존 동작 불변)", async () => {
    const api = { themeFilterGet: vi.fn(async () => payload()), themeFilterPatch: vi.fn() };
    render(<ThemeFilterCard apiClient={api} />);
    await screen.findByTestId("theme-row-semiconductor");
    expect(screen.queryByTestId("theme-briefing-semiconductor")).toBeNull();
  });

  it("briefing prop 전달 시 전일 미국 테마 ETF 등락을 표시(추천 문구 없음)", async () => {
    const api = { themeFilterGet: vi.fn(async () => payload()), themeFilterPatch: vi.fn() };
    const briefing = {
      sessionDateUs: "2026-07-03",
      byId: { semiconductor: { mapping_quality: "DIRECT", proxies: [{ ticker: "^SOX", change_pct: -3.2, status: "OK" }] } },
    };
    render(<ThemeFilterCard apiClient={api} briefing={briefing} />);
    const line = await screen.findByTestId("theme-briefing-semiconductor");
    expect(line.textContent).toBe("전일 ^SOX -3.2% ↓ (07-03 기준)");
    for (const banned of ["추천", "매수", "제외", "쉬세요", "유망"]) {
      expect(line.textContent).not.toContain(banned);
    }
  });

  it("briefing.stale=false(정상)면 갱신실패 안내를 렌더하지 않는다", async () => {
    const api = { themeFilterGet: vi.fn(async () => payload()), themeFilterPatch: vi.fn() };
    const briefing = { sessionDateUs: "2026-07-03", byId: {}, stale: false };
    render(<ThemeFilterCard apiClient={api} briefing={briefing} />);
    await screen.findByTestId("theme-row-semiconductor");
    expect(screen.queryByTestId("theme-briefing-stale-note")).toBeNull();
  });

  it("briefing.stale=true면 '갱신 실패 · 이전 기준' 안내를 렌더(이전 등락 값은 그대로 표시)", async () => {
    const api = { themeFilterGet: vi.fn(async () => payload()), themeFilterPatch: vi.fn() };
    const briefing = {
      sessionDateUs: "2026-07-03", stale: true, staleReason: "FETCH_FAILED",
      byId: { semiconductor: { mapping_quality: "DIRECT", proxies: [{ ticker: "^SOX", change_pct: -3.2, status: "OK" }] } },
    };
    render(<ThemeFilterCard apiClient={api} briefing={briefing} />);
    const note = await screen.findByTestId("theme-briefing-stale-note");
    expect(note.textContent).toContain("갱신 실패");
    // 이전에 받아둔 값(등락률)은 stale이어도 그대로 화면에 남아있어야 함(지워지지 않음).
    const line = await screen.findByTestId("theme-briefing-semiconductor");
    expect(line.textContent).toBe("전일 ^SOX -3.2% ↓ (07-03 기준)");
  });

  it("★테마 토글은 briefing/stale과 무관하게 자동으로 절대 안 바뀐다", async () => {
    const patch = vi.fn();
    const api = { themeFilterGet: vi.fn(async () => payload()), themeFilterPatch: patch };
    const briefing = {
      sessionDateUs: "2026-07-03", stale: true,
      byId: { semiconductor: { mapping_quality: "DIRECT", proxies: [{ ticker: "^SOX", change_pct: -6.0, status: "OK" }] } },
    };
    render(<ThemeFilterCard apiClient={api} briefing={briefing} />);
    await screen.findByTestId("theme-briefing-stale-note");
    // 큰 하락(-6%)이 있어도, stale이어도 사용자가 직접 누르기 전엔 patch가 호출되면 안 됨.
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByTestId("theme-toggle-semiconductor").textContent).toBe("ON");
  });
});
