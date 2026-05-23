/**
 * P-15: usePaperCapitalSettings — pure helpers + hook 단위 테스트.
 *
 * invariant:
 *  - default allowAdditionalBuy = false 영구.
 *  - 잘못된 입력은 *저장되지 않음*, errors 라벨만 carry.
 *  - localStorage key = "agent_trader_paper_capital_settings".
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import {
  DEFAULT_PAPER_CAPITAL_SETTINGS,
  PAPER_CAPITAL_SETTINGS_LS_KEY,
  buildPaperCapitalSummary,
  formatKrwLabel,
  formatPctLabel,
  fromBackendSettings,
  loadPaperCapitalSettings,
  normalizePaperCapitalSettings,
  parsePctInput,
  resetPaperCapitalSettings,
  savePaperCapitalSettings,
  toStartPayloadCapitalSettings,
  usePaperCapitalSettings,
} from "./usePaperCapitalSettings";


function _mkStorage() {
  const data = new Map();
  return {
    getItem:    (k) => (data.has(k) ? data.get(k) : null),
    setItem:    (k, v) => data.set(k, v),
    removeItem: (k) => data.delete(k),
    _dump:      () => Object.fromEntries(data),
  };
}


describe("normalizePaperCapitalSettings", () => {
  it("default 모두 normalize", () => {
    const { settings, errors } = normalizePaperCapitalSettings({});
    expect(settings).toEqual(DEFAULT_PAPER_CAPITAL_SETTINGS);
    expect(errors).toEqual([]);
  });

  it("default allowAdditionalBuy=false", () => {
    expect(DEFAULT_PAPER_CAPITAL_SETTINGS.allowAdditionalBuy).toBe(false);
  });

  it("부분 입력 적용 + 나머지 default 유지", () => {
    const { settings } = normalizePaperCapitalSettings({ maxPositions: 8 });
    expect(settings.maxPositions).toBe(8);
    expect(settings.totalPaperCapital)
      .toBe(DEFAULT_PAPER_CAPITAL_SETTINGS.totalPaperCapital);
  });

  it("종목당 투자금 > 시드머니 → error + 적용 안 함", () => {
    const { settings, errors } = normalizePaperCapitalSettings({
      totalPaperCapital: 1_000_000,
      perSymbolAllocation: 2_000_000,
    });
    expect(errors.some((e) => /시드머니/.test(e))).toBe(true);
    expect(settings.perSymbolAllocation)
      .toBe(DEFAULT_PAPER_CAPITAL_SETTINGS.perSymbolAllocation);
  });

  it("시드머니가 최소 미만이면 error", () => {
    const { errors } = normalizePaperCapitalSettings({ totalPaperCapital: 50_000 });
    expect(errors.length).toBeGreaterThan(0);
  });

  it("maxSymbolWeightPct 0 / 음수 / 1 초과 모두 거부", () => {
    expect(normalizePaperCapitalSettings({ maxSymbolWeightPct: 0 }).errors.length)
      .toBeGreaterThan(0);
    expect(normalizePaperCapitalSettings({ maxSymbolWeightPct: -0.1 }).errors.length)
      .toBeGreaterThan(0);
    expect(normalizePaperCapitalSettings({ maxSymbolWeightPct: 1.5 }).errors.length)
      .toBeGreaterThan(0);
  });

  it("maxSymbolWeightPct=1.0 허용 (경계값)", () => {
    const { settings, errors } = normalizePaperCapitalSettings({
      maxSymbolWeightPct: 1.0,
    });
    expect(errors).toEqual([]);
    expect(settings.maxSymbolWeightPct).toBe(1.0);
  });

  it("maxPositions 0 / 음수 / 101 거부", () => {
    expect(normalizePaperCapitalSettings({ maxPositions: 0 }).errors.length)
      .toBeGreaterThan(0);
    expect(normalizePaperCapitalSettings({ maxPositions: 101 }).errors.length)
      .toBeGreaterThan(0);
  });

  it("allowAdditionalBuy boolean 만 허용", () => {
    const { settings, errors } = normalizePaperCapitalSettings({
      allowAdditionalBuy: "yes",
    });
    expect(errors.length).toBeGreaterThan(0);
    expect(settings.allowAdditionalBuy).toBe(false);
  });

  it("null / undefined 입력 → default", () => {
    expect(normalizePaperCapitalSettings(null).settings)
      .toEqual(DEFAULT_PAPER_CAPITAL_SETTINGS);
    expect(normalizePaperCapitalSettings(undefined).settings)
      .toEqual(DEFAULT_PAPER_CAPITAL_SETTINGS);
  });
});


describe("loadPaperCapitalSettings", () => {
  it("빈 storage → default", () => {
    const s = _mkStorage();
    expect(loadPaperCapitalSettings(s)).toEqual(DEFAULT_PAPER_CAPITAL_SETTINGS);
  });

  it("잘못된 JSON → default fallback", () => {
    const s = _mkStorage();
    s.setItem(PAPER_CAPITAL_SETTINGS_LS_KEY, "}{NOT_JSON");
    expect(loadPaperCapitalSettings(s)).toEqual(DEFAULT_PAPER_CAPITAL_SETTINGS);
  });

  it("저장 → 로드 round-trip", () => {
    const s = _mkStorage();
    const payload = { ...DEFAULT_PAPER_CAPITAL_SETTINGS, maxPositions: 8 };
    savePaperCapitalSettings(payload, s);
    expect(loadPaperCapitalSettings(s).maxPositions).toBe(8);
  });

  it("잘못된 값이 섞여 있어도 normalize 후 안전 값 반환", () => {
    const s = _mkStorage();
    s.setItem(
      PAPER_CAPITAL_SETTINGS_LS_KEY,
      JSON.stringify({ maxPositions: -7, totalPaperCapital: 30_000_000 }),
    );
    const loaded = loadPaperCapitalSettings(s);
    // bad maxPositions → default 5.
    expect(loaded.maxPositions).toBe(5);
    expect(loaded.totalPaperCapital).toBe(30_000_000);
  });
});


describe("savePaperCapitalSettings + reset", () => {
  it("저장 후 LS key 에 JSON", () => {
    const s = _mkStorage();
    savePaperCapitalSettings(DEFAULT_PAPER_CAPITAL_SETTINGS, s);
    expect(s._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]).toBeTruthy();
  });

  it("reset 후 LS key 제거", () => {
    const s = _mkStorage();
    savePaperCapitalSettings(DEFAULT_PAPER_CAPITAL_SETTINGS, s);
    resetPaperCapitalSettings(s);
    expect(s._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]).toBeUndefined();
  });
});


describe("percent + KRW helpers", () => {
  it("formatPctLabel 정수 변환", () => {
    expect(formatPctLabel(0.2)).toBe("20%");
    expect(formatPctLabel(0.1)).toBe("10%");
  });
  it("formatPctLabel 소수 1자리", () => {
    expect(formatPctLabel(0.123)).toBe("12.3%");
  });
  it("formatPctLabel null/NaN → —", () => {
    expect(formatPctLabel(null)).toBe("—");
    expect(formatPctLabel(NaN)).toBe("—");
  });
  it("parsePctInput 정수 → 비율", () => {
    expect(parsePctInput("20")).toBe(0.2);
    expect(parsePctInput("20%")).toBe(0.2);
  });
  it("parsePctInput 잘못된 입력 → null", () => {
    expect(parsePctInput("abc")).toBeNull();
    expect(parsePctInput("")).toBeNull();
    expect(parsePctInput(null)).toBeNull();
  });
  it("formatKrwLabel 콤마", () => {
    expect(formatKrwLabel(1234567)).toBe("1,234,567원");
  });
});


describe("buildPaperCapitalSummary + toStartPayloadCapitalSettings", () => {
  it("요약 한 줄 — 5 항목 포함", () => {
    const s = buildPaperCapitalSummary(DEFAULT_PAPER_CAPITAL_SETTINGS);
    expect(s).toMatch(/시드머니/);
    expect(s).toMatch(/종목당/);
    expect(s).toMatch(/최대/);
    expect(s).toMatch(/일일/);
    expect(s).toMatch(/종목비중/);
  });

  it("start payload snake_case + 7 키 (risk_profile 포함)", () => {
    const p = toStartPayloadCapitalSettings(DEFAULT_PAPER_CAPITAL_SETTINGS);
    expect(p).toEqual({
      total_paper_capital:    10_000_000,
      per_symbol_allocation:  1_000_000,
      max_positions:          5,
      max_daily_buy_amount:   3_000_000,
      max_symbol_weight_pct:  0.2,
      allow_additional_buy:   false,
      risk_profile:           "BALANCED",
    });
  });
});


describe("usePaperCapitalSettings hook", () => {
  afterEach(() => {
    // 기본 window.localStorage 청소.
    try { window.localStorage.removeItem(PAPER_CAPITAL_SETTINGS_LS_KEY); } catch { /* ignore */ }
  });

  it("초기값 default", () => {
    const storage = _mkStorage();
    const { result } = renderHook(() => usePaperCapitalSettings({ storage }));
    expect(result.current.settings.allowAdditionalBuy).toBe(false);
    expect(result.current.settings.totalPaperCapital).toBe(10_000_000);
  });

  it("setField 적용 + LS 저장", () => {
    const storage = _mkStorage();
    const { result } = renderHook(() => usePaperCapitalSettings({ storage }));
    act(() => {
      result.current.setField("maxPositions", 8);
    });
    expect(result.current.settings.maxPositions).toBe(8);
    const raw = storage._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY];
    expect(JSON.parse(raw).maxPositions).toBe(8);
  });

  it("잘못된 값은 적용 안 됨 + errors", () => {
    const storage = _mkStorage();
    const { result } = renderHook(() => usePaperCapitalSettings({ storage }));
    let outcome;
    act(() => {
      outcome = result.current.setField("maxPositions", -1);
    });
    expect(result.current.errors.length).toBeGreaterThan(0);
    expect(outcome.applied).toBe(false);
    expect(result.current.settings.maxPositions).toBe(5);
  });

  it("reset → default 복원 + LS 비움", () => {
    const storage = _mkStorage();
    const { result } = renderHook(() => usePaperCapitalSettings({ storage }));
    act(() => result.current.setField("maxPositions", 8));
    act(() => result.current.reset());
    expect(result.current.settings.maxPositions).toBe(5);
    expect(storage._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]).toBeUndefined();
  });

  it("api 미주입 시 backend 호출 0건 (source=LOCAL)", () => {
    const storage = _mkStorage();
    const { result } = renderHook(() => usePaperCapitalSettings({ storage }));
    expect(result.current.source).toBe("LOCAL");
  });
});


describe("fromBackendSettings", () => {
  it("snake_case → camelCase 매핑", () => {
    expect(fromBackendSettings({
      total_paper_capital: 30_000_000,
      per_symbol_allocation: 2_000_000,
      max_positions: 8,
      max_daily_buy_amount: 5_000_000,
      max_symbol_weight_pct: 0.3,
      allow_additional_buy: true,
      risk_profile: "AGGRESSIVE",
    })).toEqual({
      totalPaperCapital: 30_000_000,
      perSymbolAllocation: 2_000_000,
      maxPositions: 8,
      maxDailyBuyAmount: 5_000_000,
      maxSymbolWeightPct: 0.3,
      allowAdditionalBuy: true,
      riskProfile: "AGGRESSIVE",
    });
  });

  it("null/비객체 → 빈 객체", () => {
    expect(fromBackendSettings(null)).toEqual({});
    expect(fromBackendSettings(undefined)).toEqual({});
    expect(fromBackendSettings("x")).toEqual({});
  });
});


describe("usePaperCapitalSettings backend 영속 (P-16)", () => {
  function _mkApi({ getResult, saveResult } = {}) {
    return {
      paperCapitalSettingsGet: vi.fn().mockResolvedValue(getResult),
      paperCapitalSettingsSave: vi.fn().mockResolvedValue(saveResult),
      paperCapitalSettingsReset: vi.fn().mockResolvedValue({ source: "DEFAULT" }),
    };
  }

  it("mount 시 backend GET → 적용 + localStorage mirror", async () => {
    const storage = _mkStorage();
    const api = _mkApi({
      getResult: {
        settings: { total_paper_capital: 50_000_000, max_positions: 3 },
        source: "PERSISTED",
        config_label: "%APPDATA%/Autotrade/config",
      },
    });
    const { result } = renderHook(() =>
      usePaperCapitalSettings({ storage, api }));
    await waitFor(() => {
      expect(result.current.settings.totalPaperCapital).toBe(50_000_000);
    });
    expect(result.current.settings.maxPositions).toBe(3);
    expect(result.current.source).toBe("PERSISTED");
    expect(result.current.persisted).toBe(true);
    // localStorage mirror.
    const raw = JSON.parse(storage._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]);
    expect(raw.totalPaperCapital).toBe(50_000_000);
  });

  it("backend GET 실패 → localStorage fallback (source=LOCAL)", async () => {
    const storage = _mkStorage();
    savePaperCapitalSettings(
      { ...DEFAULT_PAPER_CAPITAL_SETTINGS, maxPositions: 7 }, storage);
    const api = {
      paperCapitalSettingsGet: vi.fn().mockRejectedValue(new Error("no backend")),
    };
    const { result } = renderHook(() =>
      usePaperCapitalSettings({ storage, api }));
    await waitFor(() => expect(result.current.source).toBe("LOCAL"));
    expect(result.current.settings.maxPositions).toBe(7);
  });

  it("setField → backend save mirror + saveStatus saved", async () => {
    const storage = _mkStorage();
    const api = _mkApi({
      getResult: { settings: {}, source: "DEFAULT" },
      saveResult: { source: "PERSISTED", config_label: "x" },
    });
    const { result } = renderHook(() =>
      usePaperCapitalSettings({ storage, api }));
    await waitFor(() => expect(api.paperCapitalSettingsGet).toHaveBeenCalled());
    await act(async () => { result.current.setField("maxPositions", 8); });
    await waitFor(() => expect(result.current.saveStatus).toBe("saved"));
    expect(api.paperCapitalSettingsSave).toHaveBeenCalledWith(
      expect.objectContaining({ max_positions: 8 }),
    );
  });

  it("backend save 실패 → saveStatus error (localStorage 는 저장)", async () => {
    const storage = _mkStorage();
    const api = {
      paperCapitalSettingsGet: vi.fn().mockResolvedValue({ settings: {}, source: "DEFAULT" }),
      paperCapitalSettingsSave: vi.fn().mockRejectedValue(new Error("save fail")),
    };
    const { result } = renderHook(() =>
      usePaperCapitalSettings({ storage, api }));
    await waitFor(() => expect(api.paperCapitalSettingsGet).toHaveBeenCalled());
    await act(async () => { result.current.setField("maxPositions", 9); });
    await waitFor(() => expect(result.current.saveStatus).toBe("error"));
    // localStorage 는 저장됨.
    const raw = JSON.parse(storage._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]);
    expect(raw.maxPositions).toBe(9);
  });

  it("reset → backend reset 호출", async () => {
    const storage = _mkStorage();
    const api = _mkApi({ getResult: { settings: {}, source: "DEFAULT" } });
    const { result } = renderHook(() =>
      usePaperCapitalSettings({ storage, api }));
    await waitFor(() => expect(api.paperCapitalSettingsGet).toHaveBeenCalled());
    await act(async () => { result.current.reset(); });
    expect(api.paperCapitalSettingsReset).toHaveBeenCalled();
  });
});
