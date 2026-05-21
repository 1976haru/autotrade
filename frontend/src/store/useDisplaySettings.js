/**
 * useDisplaySettings — Display preferences (font size / layout width / theme)
 * with localStorage persistence and root data-attribute side effects.
 *
 * 사용자 요청서 §4 저장 구조 정확 일치:
 *   { fontSize: "large", layoutWidth: "wide", theme: "highContrastLight" }
 *
 * 절대 invariant (테스트로 lock):
 *  - 본 store 는 broker / 실거래 API / OrderExecutor 호출 0건.
 *  - localStorage 외 다른 persistence 없음.
 *  - 잘못된 값은 *항상* 기본값으로 fallback.
 *  - root 또는 document.documentElement 에 data-font-size / data-layout-width /
 *    data-display-theme 적용.
 */

import { useCallback, useEffect, useState } from "react";


export const DISPLAY_SETTINGS_STORAGE_KEY = "agent_trader_display_settings";


// 허용 값 — 사용자 요청서 §2 explicit 리스트.
export const ALLOWED_FONT_SIZES   = ["standard", "large", "xlarge"];
export const ALLOWED_LAYOUT_WIDTHS = ["standard", "wide", "full"];
export const ALLOWED_THEMES        = ["light", "highContrastLight", "softLight"];


// 사용자 요청서 §2 정확 일치 기본값.
export const DEFAULT_DISPLAY_SETTINGS = Object.freeze({
  fontSize:    "large",
  layoutWidth: "wide",
  theme:       "highContrastLight",
});


/** 단일 필드를 허용값과 비교 — 잘못된 입력은 *항상* 기본값으로 fallback. */
function _normalizeField(value, allowed, fallback) {
  if (typeof value !== "string") return fallback;
  return allowed.includes(value) ? value : fallback;
}


/**
 * 임의 객체를 안전한 DisplaySettings shape 으로 정규화.
 *
 * 사용자 요청서 §4 요구사항 3번: "잘못된 값이면 기본값으로 fallback."
 */
export function normalizeDisplaySettings(raw) {
  if (!raw || typeof raw !== "object") {
    return { ...DEFAULT_DISPLAY_SETTINGS };
  }
  return {
    fontSize: _normalizeField(
      raw.fontSize, ALLOWED_FONT_SIZES, DEFAULT_DISPLAY_SETTINGS.fontSize,
    ),
    layoutWidth: _normalizeField(
      raw.layoutWidth, ALLOWED_LAYOUT_WIDTHS, DEFAULT_DISPLAY_SETTINGS.layoutWidth,
    ),
    theme: _normalizeField(
      raw.theme, ALLOWED_THEMES, DEFAULT_DISPLAY_SETTINGS.theme,
    ),
  };
}


/** localStorage 에서 안전하게 읽어 정규화된 DisplaySettings 반환. */
export function loadDisplaySettings(storage) {
  const store = storage
    || (typeof window !== "undefined" ? window.localStorage : null);
  if (!store) return { ...DEFAULT_DISPLAY_SETTINGS };
  let raw;
  try {
    raw = store.getItem(DISPLAY_SETTINGS_STORAGE_KEY);
  } catch {
    return { ...DEFAULT_DISPLAY_SETTINGS };
  }
  if (!raw) return { ...DEFAULT_DISPLAY_SETTINGS };
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ...DEFAULT_DISPLAY_SETTINGS };
  }
  return normalizeDisplaySettings(parsed);
}


/** localStorage 에 안전하게 저장 — 실패하면 silent (UI 흐름을 막지 않음). */
export function saveDisplaySettings(settings, storage) {
  const store = storage
    || (typeof window !== "undefined" ? window.localStorage : null);
  if (!store) return;
  const normalized = normalizeDisplaySettings(settings);
  try {
    store.setItem(DISPLAY_SETTINGS_STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // quota / disabled — 무시.
  }
}


/**
 * root element 에 data-attribute 적용. 사용자 요청서 §4 권장.
 *
 * @param {Object} settings — normalized settings
 * @param {Element|null} root — default: document.documentElement
 */
export function applyDisplaySettingsToRoot(settings, root) {
  const target = root
    || (typeof document !== "undefined" ? document.documentElement : null);
  if (!target || typeof target.setAttribute !== "function") return;
  const n = normalizeDisplaySettings(settings);
  target.setAttribute("data-font-size", n.fontSize);
  target.setAttribute("data-layout-width", n.layoutWidth);
  target.setAttribute("data-display-theme", n.theme);
}


/**
 * React hook — DisplaySettings state + setters + reset.
 *
 * 초기값: localStorage → normalizeDisplaySettings. 모든 setter 는 *동기*
 * state 업데이트 + localStorage 저장 + root data-attribute 적용을 한 번에 수행.
 *
 * Options (테스트 주입):
 *  - storage: localStorage-like (getItem/setItem)
 *  - root:    HTMLElement (default: document.documentElement)
 */
export function useDisplaySettings({ storage, root } = {}) {
  const [settings, setSettings] = useState(() => loadDisplaySettings(storage));

  // 초기 mount 시 root 에도 한 번 적용.
  useEffect(() => {
    applyDisplaySettingsToRoot(settings, root);
    // 의도적으로 effect 의 dep 에서 settings 제외하고 별도 effect 로 split
    // (race 방지: setSettings 호출 시 다음 effect 에서 root 갱신).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    applyDisplaySettingsToRoot(settings, root);
    saveDisplaySettings(settings, storage);
  }, [settings, root, storage]);

  const setFontSize = useCallback((next) => {
    setSettings((prev) => ({
      ...prev,
      fontSize: _normalizeField(
        next, ALLOWED_FONT_SIZES, prev.fontSize,
      ),
    }));
  }, []);

  const setLayoutWidth = useCallback((next) => {
    setSettings((prev) => ({
      ...prev,
      layoutWidth: _normalizeField(
        next, ALLOWED_LAYOUT_WIDTHS, prev.layoutWidth,
      ),
    }));
  }, []);

  const setTheme = useCallback((next) => {
    setSettings((prev) => ({
      ...prev,
      theme: _normalizeField(next, ALLOWED_THEMES, prev.theme),
    }));
  }, []);

  const reset = useCallback(() => {
    setSettings({ ...DEFAULT_DISPLAY_SETTINGS });
  }, []);

  return {
    settings,
    setFontSize,
    setLayoutWidth,
    setTheme,
    reset,
  };
}


export default useDisplaySettings;
