// 50: Frontend feature flags.
//
// 본 모듈은 *UI 노출* 전용 — backend invariant(`ENABLE_FUTURES_LIVE_TRADING=False`)
// 와 *무관*. 본 flag는 Futures 탭이 navigation에 보이는지만 제어하며, 실제 선물
// 주문이 broker로 가는지에는 어떤 영향도 주지 않는다 (broker 호출은 backend의
// `FuturesRiskManager.evaluate_order` 항상 REJECTED + adapter 미존재로 0건).
//
// 사용 예:
//   import { FEATURES } from "./config/features";
//   if (FEATURES.futuresTab) { ... }
//
// 테스트에서는 `__setFeatureForTest("futuresTab", true)`로 override 가능.

// Vite는 `import.meta.env.*`로 컴파일 타임에 값을 inline. SSR/Node 환경에서
// import.meta.env가 없을 때를 대비한 fallback.
function _readEnv(key, fallback) {
  try {
    if (typeof import.meta !== "undefined"
        && import.meta.env
        && key in import.meta.env) {
      return import.meta.env[key];
    }
  } catch {
    // ignore
  }
  return fallback;
}

function _truthy(v) {
  if (v === undefined || v === null || v === "") return false;
  if (typeof v === "boolean") return v;
  const s = String(v).toLowerCase().trim();
  return s === "true" || s === "1" || s === "yes" || s === "on";
}


// flag default — *false*. 운영자가 `.env`에서 명시적으로 켜야만 노출.
// 본 flag는 UI 노출 정책 — backend safety flag와 별개.
const _DEFAULTS = Object.freeze({
  futuresTab: _truthy(_readEnv("VITE_ENABLE_FUTURES_TAB", false)),
});


let _overrides = {};

export const FEATURES = new Proxy({}, {
  get(_target, key) {
    if (key in _overrides) return _overrides[key];
    return _DEFAULTS[key] ?? false;
  },
  has(_target, key) {
    return key in _DEFAULTS || key in _overrides;
  },
  ownKeys() {
    return Object.keys(_DEFAULTS);
  },
  getOwnPropertyDescriptor(_t, key) {
    if (key in _DEFAULTS || key in _overrides) {
      return { configurable: true, enumerable: true, value: this.get(_t, key) };
    }
    return undefined;
  },
});


// 테스트 전용 override helper. production 코드에서는 호출하지 말 것.
export function __setFeatureForTest(key, value) {
  if (!(key in _DEFAULTS)) {
    throw new Error(
      `unknown feature flag '${key}' — declare in _DEFAULTS first`
    );
  }
  _overrides[key] = !!value;
}

export function __resetFeaturesForTest() {
  _overrides = {};
}


// production helper — 운영자 안내 / debug 용.
export function getActiveFeatureSnapshot() {
  return Object.fromEntries(
    Object.keys(_DEFAULTS).map((k) => [k, FEATURES[k]]),
  );
}
