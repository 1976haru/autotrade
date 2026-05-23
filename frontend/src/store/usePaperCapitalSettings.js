/**
 * P-15: Paper 자금 설정 — localStorage-backed hook + pure helpers.
 *
 * EXE 사용자가 Paper / AI Paper 운용 *전* 직접 설정하는 자금 기준을 단일
 * 진실로 관리한다. broker / OrderExecutor / route_order / 실거래 API 와는
 * 결합 0건 — *Paper 정책 입력값* 만 보관.
 *
 * 설정 6종:
 *   1. totalPaperCapital     — Paper 시드머니 (KRW)
 *   2. perSymbolAllocation   — 종목당 투자금 (KRW)
 *   3. maxPositions          — 최대 보유 종목 수 (정수)
 *   4. maxDailyBuyAmount     — 일일 최대 매수금액 (KRW)
 *   5. maxSymbolWeightPct    — 종목별 최대 비중 (0~1 float)
 *   6. allowAdditionalBuy    — 동일 종목 추가매수 허용 (boolean, default false)
 *
 * 절대 invariant (테스트로 lock):
 *  - 본 hook 은 broker / 실거래 API / OrderExecutor 호출 0건.
 *  - "지금 매수" / "Place Order" / "실거래 시작" / "ENABLE_*" / "BUY/SELL/HOLD"
 *    같은 라벨을 *발신* 하지 않음 (라벨 emit 0건 — UI 의 책임).
 *  - allowAdditionalBuy default = false (사용자 요청서 §1 6번).
 *  - 잘못된 값은 *저장하지 않음* — caller 가 errors 반환을 표시.
 *  - localStorage key: "agent_trader_paper_capital_settings".
 */

import { useCallback, useEffect, useState } from "react";

import { normalizeRiskProfile } from "../components/AgentRiskProfileSelector";


// ----- 상수 -----


export const PAPER_CAPITAL_SETTINGS_LS_KEY = "agent_trader_paper_capital_settings";


export const DEFAULT_PAPER_CAPITAL_SETTINGS = Object.freeze({
  totalPaperCapital:    10_000_000,  // 1,000만원
  perSymbolAllocation:  1_000_000,   // 100만원
  maxPositions:         5,
  maxDailyBuyAmount:    3_000_000,   // 300만원
  maxSymbolWeightPct:   0.2,         // 20%
  allowAdditionalBuy:   false,
  riskProfile:          "BALANCED",  // AI 운용 성향 (CONSERVATIVE/BALANCED/AGGRESSIVE)
});


// 사용자 요청서 §6 validation 정책.
export const PAPER_CAPITAL_LIMITS = Object.freeze({
  totalPaperCapital:   { min: 100_000,   max: 10_000_000_000 },
  perSymbolAllocation: { min: 10_000,    max: 10_000_000_000 },
  maxPositions:        { min: 1,         max: 100 },
  maxDailyBuyAmount:   { min: 10_000,    max: 10_000_000_000 },
  maxSymbolWeightPct:  { min: 0,         max: 1,   exclusiveMin: true },
});


// ----- preset 옵션 (UI 가 그대로 사용) -----


export const PAPER_CAPITAL_PRESETS = Object.freeze({
  totalPaperCapital: [
    { label: "1,000만원", value: 10_000_000 },
    { label: "3,000만원", value: 30_000_000 },
    { label: "5,000만원", value: 50_000_000 },
  ],
  perSymbolAllocation: [
    { label: "50만원",  value: 500_000 },
    { label: "100만원", value: 1_000_000 },
    { label: "200만원", value: 2_000_000 },
  ],
  maxPositions: [
    { label: "3개", value: 3 },
    { label: "5개", value: 5 },
    { label: "8개", value: 8 },
  ],
  maxDailyBuyAmount: [
    { label: "100만원", value: 1_000_000 },
    { label: "300만원", value: 3_000_000 },
    { label: "500만원", value: 5_000_000 },
  ],
  maxSymbolWeightPct: [
    { label: "10%", value: 0.10 },
    { label: "20%", value: 0.20 },
    { label: "30%", value: 0.30 },
  ],
});


// ----- pure helpers (테스트 가능) -----


function _isInt(value) {
  return typeof value === "number"
    && Number.isFinite(value)
    && Math.floor(value) === value;
}


/**
 * 임의 입력값을 안전한 자금 설정 객체로 정규화.
 *
 * 잘못된 / 누락된 필드는 default 로 fallback. 결과는 *항상* 같은 shape.
 * `errors` 는 *입력값을 그대로 적용했더라면 위반했을 사유* 라벨 (caller 가
 * UI 에 표시 — 자동 적용 안 함).
 *
 * @param {object|null|undefined} input
 * @returns {{settings: object, errors: string[]}}
 */
export function normalizePaperCapitalSettings(input) {
  const errors = [];
  const out = { ...DEFAULT_PAPER_CAPITAL_SETTINGS };
  if (!input || typeof input !== "object") {
    return { settings: out, errors };
  }

  // 1. totalPaperCapital.
  if (input.totalPaperCapital !== undefined && input.totalPaperCapital !== null) {
    const v = Number(input.totalPaperCapital);
    const { min, max } = PAPER_CAPITAL_LIMITS.totalPaperCapital;
    if (Number.isFinite(v) && v >= min && v <= max && v > 0) {
      out.totalPaperCapital = Math.floor(v);
    } else {
      errors.push(`시드머니는 ${min.toLocaleString("ko-KR")}원 이상이어야 합니다.`);
    }
  }

  // 2. perSymbolAllocation — total 보다 작거나 같아야.
  if (input.perSymbolAllocation !== undefined && input.perSymbolAllocation !== null) {
    const v = Number(input.perSymbolAllocation);
    const { min, max } = PAPER_CAPITAL_LIMITS.perSymbolAllocation;
    if (Number.isFinite(v) && v >= min && v <= max && v > 0) {
      if (v > out.totalPaperCapital) {
        errors.push("종목당 투자금은 시드머니보다 클 수 없습니다.");
      } else {
        out.perSymbolAllocation = Math.floor(v);
      }
    } else {
      errors.push(`종목당 투자금은 ${min.toLocaleString("ko-KR")}원 이상이어야 합니다.`);
    }
  }

  // 3. maxPositions.
  if (input.maxPositions !== undefined && input.maxPositions !== null) {
    const v = Number(input.maxPositions);
    const { min, max } = PAPER_CAPITAL_LIMITS.maxPositions;
    if (_isInt(v) && v >= min && v <= max) {
      out.maxPositions = v;
    } else {
      errors.push("최대 보유 종목 수는 1개 이상 100개 이하의 정수여야 합니다.");
    }
  }

  // 4. maxDailyBuyAmount.
  if (input.maxDailyBuyAmount !== undefined && input.maxDailyBuyAmount !== null) {
    const v = Number(input.maxDailyBuyAmount);
    const { min, max } = PAPER_CAPITAL_LIMITS.maxDailyBuyAmount;
    if (Number.isFinite(v) && v >= min && v <= max && v > 0) {
      out.maxDailyBuyAmount = Math.floor(v);
    } else {
      errors.push(`일일 최대 매수금액은 ${min.toLocaleString("ko-KR")}원 이상이어야 합니다.`);
    }
  }

  // 5. maxSymbolWeightPct — 0 초과 1 이하.
  if (input.maxSymbolWeightPct !== undefined && input.maxSymbolWeightPct !== null) {
    const v = Number(input.maxSymbolWeightPct);
    if (Number.isFinite(v) && v > 0 && v <= 1) {
      out.maxSymbolWeightPct = v;
    } else {
      errors.push("종목별 최대 비중은 0% 초과 100% 이하이어야 합니다.");
    }
  }

  // 6. allowAdditionalBuy — boolean 만.
  if (input.allowAdditionalBuy !== undefined) {
    if (typeof input.allowAdditionalBuy === "boolean") {
      out.allowAdditionalBuy = input.allowAdditionalBuy;
    } else {
      errors.push("동일 종목 추가매수 허용은 boolean 만 허용됩니다.");
    }
  }

  // 7. riskProfile — 소문자/한국어/별칭 모두 표준 enum 으로 정규화 (실패값
  //    없이 항상 유효 enum). 명시 입력 없으면 default(BALANCED) 유지.
  if (input.riskProfile !== undefined && input.riskProfile !== null) {
    out.riskProfile = normalizeRiskProfile(input.riskProfile);
  }

  return { settings: out, errors };
}


/**
 * localStorage 에서 설정 로드. 잘못된 JSON / 없는 키 → default fallback.
 *
 * @param {Storage} [storage] — 테스트 주입용.
 * @returns {object} settings (normalized)
 */
export function loadPaperCapitalSettings(storage) {
  const ls = storage || (typeof window !== "undefined" ? window.localStorage : null);
  if (!ls) return { ...DEFAULT_PAPER_CAPITAL_SETTINGS };
  let raw;
  try {
    raw = ls.getItem(PAPER_CAPITAL_SETTINGS_LS_KEY);
  } catch {
    return { ...DEFAULT_PAPER_CAPITAL_SETTINGS };
  }
  if (!raw) return { ...DEFAULT_PAPER_CAPITAL_SETTINGS };
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ...DEFAULT_PAPER_CAPITAL_SETTINGS };
  }
  return normalizePaperCapitalSettings(parsed).settings;
}


/**
 * 설정 저장 (이미 검증된 값만 호출 — 잘못된 값은 caller 가 차단).
 *
 * @param {object} settings — normalized 값.
 * @param {Storage} [storage] — 테스트 주입용.
 */
export function savePaperCapitalSettings(settings, storage) {
  const ls = storage || (typeof window !== "undefined" ? window.localStorage : null);
  if (!ls) return;
  try {
    ls.setItem(
      PAPER_CAPITAL_SETTINGS_LS_KEY,
      JSON.stringify(settings),
    );
  } catch {
    // localStorage quota / SecurityError — 조용히 무시 (메모리 상태만 유지).
  }
}


/**
 * 설정 초기화.
 *
 * @param {Storage} [storage] — 테스트 주입용.
 */
export function resetPaperCapitalSettings(storage) {
  const ls = storage || (typeof window !== "undefined" ? window.localStorage : null);
  if (!ls) return;
  try {
    ls.removeItem(PAPER_CAPITAL_SETTINGS_LS_KEY);
  } catch {
    // 무시.
  }
}


// ----- 표시용 helper -----


/** 0.20 → "20%" (소수 1자리, 정수면 정수). */
export function formatPctLabel(pct) {
  if (pct == null || !Number.isFinite(Number(pct))) return "—";
  const v = Number(pct) * 100;
  if (Math.abs(v - Math.round(v)) < 1e-9) return `${Math.round(v)}%`;
  return `${v.toFixed(1)}%`;
}


/** "20" 또는 "20%" → 0.20. NaN 이면 null. */
export function parsePctInput(input) {
  if (input == null) return null;
  const s = String(input).trim().replace(/%$/, "");
  if (!s) return null;
  const v = Number(s);
  if (!Number.isFinite(v)) return null;
  return v / 100;
}


/** 1234567 → "1,234,567원". */
export function formatKrwLabel(amount) {
  if (amount == null || !Number.isFinite(Number(amount))) return "—";
  return `${Math.floor(Number(amount)).toLocaleString("ko-KR")}원`;
}


/** AutoPaperLoopCard 가 요약 한 줄로 표시할 라벨. */
export function buildPaperCapitalSummary(settings) {
  const s = normalizePaperCapitalSettings(settings).settings;
  return (
    `시드머니 ${formatKrwLabel(s.totalPaperCapital)} · ` +
    `종목당 ${formatKrwLabel(s.perSymbolAllocation)} · ` +
    `최대 ${s.maxPositions}종목 · ` +
    `일일 ${formatKrwLabel(s.maxDailyBuyAmount)} · ` +
    `종목비중 ${formatPctLabel(s.maxSymbolWeightPct)}`
  );
}


/** start payload 에 동봉할 snake_case 객체 — backend 친화. */
export function toStartPayloadCapitalSettings(settings) {
  const s = normalizePaperCapitalSettings(settings).settings;
  return {
    total_paper_capital:    s.totalPaperCapital,
    per_symbol_allocation:  s.perSymbolAllocation,
    max_positions:          s.maxPositions,
    max_daily_buy_amount:   s.maxDailyBuyAmount,
    max_symbol_weight_pct:  s.maxSymbolWeightPct,
    allow_additional_buy:   s.allowAdditionalBuy,
    risk_profile:           s.riskProfile,
  };
}


/**
 * P-16: backend 가 반환한 snake_case settings → camelCase 입력값.
 *
 * backend(`/api/auto-paper/paper-capital-settings`)는 snake_case 로 settings 를
 * 돌려준다. normalizePaperCapitalSettings 가 소비할 수 있는 camelCase 로 매핑.
 * 알 수 없는/누락 필드는 무시 (normalize 가 default 로 채움).
 */
export function fromBackendSettings(snake) {
  if (!snake || typeof snake !== "object") return {};
  const out = {};
  if (snake.total_paper_capital !== undefined)
    out.totalPaperCapital = snake.total_paper_capital;
  if (snake.per_symbol_allocation !== undefined)
    out.perSymbolAllocation = snake.per_symbol_allocation;
  if (snake.max_positions !== undefined)
    out.maxPositions = snake.max_positions;
  if (snake.max_daily_buy_amount !== undefined)
    out.maxDailyBuyAmount = snake.max_daily_buy_amount;
  if (snake.max_symbol_weight_pct !== undefined)
    out.maxSymbolWeightPct = snake.max_symbol_weight_pct;
  if (snake.allow_additional_buy !== undefined)
    out.allowAdditionalBuy = snake.allow_additional_buy;
  if (snake.risk_profile !== undefined)
    out.riskProfile = snake.risk_profile;
  return out;
}


// ----- React hook -----


/**
 * Paper 자금 설정 hook.
 *
 * 사용:
 *   const { settings, errors, setField, setAll, reset } = usePaperCapitalSettings();
 *
 * `setField(name, value)` — 단일 필드 변경 + 즉시 localStorage 저장.
 *   잘못된 값이면 errors 에 사유 carry, 값은 *적용되지 않음*.
 * `setAll(newSettings)` — 여러 필드 한 번에 변경.
 * `reset()` — default 로 초기화 + localStorage 비움.
 *
 * P-16 backend 영속 (opt-in):
 *   `api` (backend client) 를 넘기면 mount 시 backend 에서 저장된 설정을
 *   로드(없으면 localStorage fallback), setAll/reset 시 backend + localStorage
 *   *양쪽* 에 mirror 한다. backend 가 없는/실패한 환경에서는 localStorage 만으로
 *   동작 (기존 P-15 behavior 그대로). `api` 미주입 시 backend 호출 0건.
 *   - `source`: "PERSISTED" | "DEFAULT" | "DEFAULT_CORRUPTED" | "LOCAL" | "UNKNOWN"
 *   - `saveStatus`: "idle" | "saving" | "saved" | "error"
 *   - `configLabel`: 저장 폴더 라벨 (예: "%APPDATA%/Autotrade/config")
 *   - `persisted`: backend 영속이 실제로 적용 중인지 (api 주입 + 로드 성공)
 */
export function usePaperCapitalSettings({ storage, api } = {}) {
  const [settings, setSettings] = useState(() => loadPaperCapitalSettings(storage));
  const [errors, setErrors] = useState([]);
  const [source, setSource] = useState(api ? "UNKNOWN" : "LOCAL");
  const [saveStatus, setSaveStatus] = useState("idle");
  const [configLabel, setConfigLabel] = useState("");
  const [persisted, setPersisted] = useState(false);

  // mount: localStorage → state. api 가 있으면 backend 로드로 덮어씀.
  useEffect(() => {
    let cancelled = false;
    const local = loadPaperCapitalSettings(storage);
    setSettings(local);
    if (!api || typeof api.paperCapitalSettingsGet !== "function") {
      setSource("LOCAL");
      return undefined;
    }
    (async () => {
      try {
        const res = await api.paperCapitalSettingsGet();
        if (cancelled || !res) return;
        const merged = normalizePaperCapitalSettings(
          fromBackendSettings(res.settings),
        ).settings;
        setSettings(merged);
        // backend 값을 localStorage 에도 mirror — AutoPaperLoopCard(localStorage
        // 읽기) 와 동기화.
        savePaperCapitalSettings(merged, storage);
        setSource(res.source || "PERSISTED");
        setConfigLabel(res.config_label || "");
        setPersisted(true);
      } catch {
        // backend 미가용 — localStorage 값 유지 (이미 setSettings(local)).
        setSource("LOCAL");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setAll = useCallback((input) => {
    const { settings: next, errors: nextErrors } =
      normalizePaperCapitalSettings({ ...settings, ...input });
    setErrors(nextErrors);
    if (nextErrors.length === 0) {
      setSettings(next);
      savePaperCapitalSettings(next, storage);
      // backend mirror (opt-in) — 실패해도 localStorage 저장은 유효.
      if (api && typeof api.paperCapitalSettingsSave === "function") {
        setSaveStatus("saving");
        api.paperCapitalSettingsSave(toStartPayloadCapitalSettings(next))
          .then((res) => {
            setSaveStatus("saved");
            if (res) {
              setSource(res.source || "PERSISTED");
              setConfigLabel(res.config_label || "");
              setPersisted(true);
            }
          })
          .catch(() => setSaveStatus("error"));
      } else {
        setSaveStatus("saved");
      }
      return { settings: next, errors: [], applied: true };
    }
    // 부분 적용은 *하지 않는다* — 잘못된 입력은 보류 (사용자 요청서 §6).
    return { settings, errors: nextErrors, applied: false };
  }, [settings, storage, api]);

  const setField = useCallback((name, value) => {
    return setAll({ [name]: value });
  }, [setAll]);

  const reset = useCallback(() => {
    resetPaperCapitalSettings(storage);
    setSettings({ ...DEFAULT_PAPER_CAPITAL_SETTINGS });
    setErrors([]);
    setSaveStatus("idle");
    if (api && typeof api.paperCapitalSettingsReset === "function") {
      api.paperCapitalSettingsReset()
        .then((res) => {
          setSource(res?.source || "DEFAULT");
          setConfigLabel(res?.config_label || "");
        })
        .catch(() => { /* backend 미가용 — localStorage reset 은 유효. */ });
    } else {
      setSource(api ? "DEFAULT" : "LOCAL");
    }
  }, [storage, api]);

  return {
    settings, errors, setField, setAll, reset,
    source, saveStatus, configLabel, persisted,
  };
}
