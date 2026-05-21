/**
 * P-15: Paper 자금 설정 카드 — Settings 탭에 노출.
 *
 * 사용자가 EXE 에서 Paper / AI Paper 운용 *전* 자금 기준을 직접 설정하는
 * UI. localStorage 에 저장되며, AutoPaperLoopCard 가 시작 payload 에 동봉.
 *
 * 절대 invariant (테스트로 lock — `PaperCapitalSettingsCard.test.jsx`):
 *  - 본 카드는 broker / 실거래 API / OrderExecutor 호출 0건.
 *  - "지금 매수" / "Place Order" / "실거래 시작" / "실거래 활성화" /
 *    "ENABLE_*" / "BUY/SELL/HOLD signal" 라벨 button 0개.
 *  - API key / secret / 계좌번호 입력 field 0개.
 *  - allow_additional_buy default OFF — ON 토글 시 경고 문구 표시.
 *  - "이 설정은 Paper / AI Paper 기준입니다" / "실거래 활성화 설정이
 *    아닙니다" / "RiskManager 와 PermissionGate 는 계속 적용됩니다" 안내
 *    영구 노출.
 */

import { useCallback, useState } from "react";

import { Card, SectionLabel } from "./index";
import {
  DEFAULT_PAPER_CAPITAL_SETTINGS,
  PAPER_CAPITAL_PRESETS,
  formatKrwLabel,
  formatPctLabel,
  parsePctInput,
  usePaperCapitalSettings,
} from "../../store/usePaperCapitalSettings";


function _PresetRow({ presets, current, onPick, formatter, testid }) {
  return (
    <div
      data-testid={testid}
      style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}
    >
      {presets.map((p) => {
        const active = formatter
          ? formatter(p.value) === formatter(current)
          : p.value === current;
        return (
          <button
            key={p.label}
            type="button"
            data-testid={`${testid}-${p.label}`}
            onClick={() => onPick(p.value)}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: `1px solid ${active ? "#22c55e" : "#cbd5e1"}`,
              background: active ? "#dcfce7" : "transparent",
              color: active ? "#166534" : "#334155",
              fontSize: "var(--fs-xs)",
              fontWeight: "var(--fw-bold)",
              cursor: "pointer",
            }}
          >
            {p.label}
          </button>
        );
      })}
    </div>
  );
}


function _Field({ label, hint, children, testid }) {
  return (
    <div data-testid={testid} style={{ marginBottom: 14 }}>
      <div
        style={{
          fontSize: "var(--fs-sm)",
          fontWeight: "var(--fw-bold)",
          color: "var(--c-text)",
          marginBottom: 4,
        }}
      >
        {label}
      </div>
      {hint && (
        <div
          style={{
            fontSize: "var(--fs-xs)",
            color: "var(--c-text-3)",
            marginBottom: 6,
          }}
        >
          {hint}
        </div>
      )}
      {children}
    </div>
  );
}


function _NumberInput({ value, onChange, testid, placeholder }) {
  return (
    <input
      type="number"
      data-testid={testid}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        width: "100%",
        padding: "6px 10px",
        borderRadius: 4,
        border: "1px solid #cbd5e1",
        background: "#fff",
        color: "#0f172a",
        fontSize: "var(--fs-sm)",
        boxSizing: "border-box",
      }}
    />
  );
}


export function PaperCapitalSettingsCard({
  testId = "paper-capital-settings-card",
  storage,
} = {}) {
  const { settings, errors, setField, reset } = usePaperCapitalSettings({ storage });
  // 사용자 입력 중 임시 문자열 보존 (잘못된 값 입력 시 즉시 reset 되지 않도록).
  const [draftPct, setDraftPct] = useState(
    () => String(Math.round(settings.maxSymbolWeightPct * 100)),
  );

  const onPickTotal = useCallback((v) => setField("totalPaperCapital", v), [setField]);
  const onPickPer   = useCallback((v) => setField("perSymbolAllocation", v), [setField]);
  const onPickMax   = useCallback((v) => setField("maxPositions", v), [setField]);
  const onPickDaily = useCallback((v) => setField("maxDailyBuyAmount", v), [setField]);
  const onPickPct   = useCallback((v) => {
    setField("maxSymbolWeightPct", v);
    setDraftPct(String(Math.round(v * 100)));
  }, [setField]);

  const onChangeTotal = useCallback(
    (v) => setField("totalPaperCapital", Number(v)),
    [setField],
  );
  const onChangePer = useCallback(
    (v) => setField("perSymbolAllocation", Number(v)),
    [setField],
  );
  const onChangeMax = useCallback(
    (v) => setField("maxPositions", Number(v)),
    [setField],
  );
  const onChangeDaily = useCallback(
    (v) => setField("maxDailyBuyAmount", Number(v)),
    [setField],
  );
  const onChangePct = useCallback((v) => {
    setDraftPct(v);
    const parsed = parsePctInput(v);
    if (parsed != null) setField("maxSymbolWeightPct", parsed);
  }, [setField]);

  const onToggleAdditional = useCallback(() => {
    setField("allowAdditionalBuy", !settings.allowAdditionalBuy);
  }, [setField, settings.allowAdditionalBuy]);

  const onReset = useCallback(() => {
    reset();
    setDraftPct(String(Math.round(DEFAULT_PAPER_CAPITAL_SETTINGS.maxSymbolWeightPct * 100)));
  }, [reset]);

  return (
    <Card accentColor="#22c55e33">
      <div data-testid={testId}>
      <SectionLabel>💰 Paper 자금 설정</SectionLabel>

      <div
        data-testid="paper-capital-settings-intro"
        style={{
          fontSize: "var(--fs-xs)",
          color: "var(--c-text-3)",
          marginBottom: 12,
          lineHeight: 1.6,
        }}
      >
        AI Paper 자동매매에 사용할 시드머니, 종목당 투자금, 최대 보유 종목
        수를 설정합니다. 이 설정은 모의/Paper 운용 기준이며 실거래 권한이
        아닙니다.
      </div>

      {/* 안전 배지 — 영구 노출 */}
      <div
        data-testid="paper-capital-settings-badges"
        style={{ marginBottom: 12, display: "flex", flexWrap: "wrap", gap: 4 }}
      >
        <span
          data-testid="badge-paper-only"
          style={{
            padding: "3px 8px", borderRadius: 4,
            fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
            background: "#1e3a8a", color: "#fff",
          }}
        >
          Paper / AI Paper 기준
        </span>
        <span
          data-testid="badge-not-live-authorization"
          style={{
            padding: "3px 8px", borderRadius: 4,
            fontSize: "var(--fs-xs)",
            background: "#0ea5e9", color: "#fff",
          }}
        >
          실거래 활성화 아님
        </span>
        <span
          data-testid="badge-risk-still-applied"
          style={{
            padding: "3px 8px", borderRadius: 4,
            fontSize: "var(--fs-xs)",
            background: "#6b7280", color: "#fff",
          }}
        >
          RiskManager / PermissionGate 계속 적용
        </span>
      </div>

      {/* 1. Paper 시드머니 */}
      <_Field
        label="Paper 시드머니"
        hint={`현재: ${formatKrwLabel(settings.totalPaperCapital)} · 기본 1,000만원`}
        testid="field-total-paper-capital"
      >
        <_PresetRow
          presets={PAPER_CAPITAL_PRESETS.totalPaperCapital}
          current={settings.totalPaperCapital}
          onPick={onPickTotal}
          testid="preset-total-paper-capital"
        />
        <_NumberInput
          value={settings.totalPaperCapital}
          onChange={onChangeTotal}
          testid="input-total-paper-capital"
          placeholder="직접 입력 (KRW)"
        />
      </_Field>

      {/* 2. 종목당 투자금 */}
      <_Field
        label="종목당 투자금"
        hint={`현재: ${formatKrwLabel(settings.perSymbolAllocation)} · 기본 100만원`}
        testid="field-per-symbol-allocation"
      >
        <_PresetRow
          presets={PAPER_CAPITAL_PRESETS.perSymbolAllocation}
          current={settings.perSymbolAllocation}
          onPick={onPickPer}
          testid="preset-per-symbol-allocation"
        />
        <_NumberInput
          value={settings.perSymbolAllocation}
          onChange={onChangePer}
          testid="input-per-symbol-allocation"
          placeholder="직접 입력 (KRW)"
        />
      </_Field>

      {/* 3. 최대 보유 종목 수 */}
      <_Field
        label="최대 보유 종목 수"
        hint={`현재: ${settings.maxPositions}개 · 기본 5개`}
        testid="field-max-positions"
      >
        <_PresetRow
          presets={PAPER_CAPITAL_PRESETS.maxPositions}
          current={settings.maxPositions}
          onPick={onPickMax}
          testid="preset-max-positions"
        />
        <_NumberInput
          value={settings.maxPositions}
          onChange={onChangeMax}
          testid="input-max-positions"
          placeholder="직접 입력 (개수)"
        />
      </_Field>

      {/* 4. 일일 최대 매수금액 */}
      <_Field
        label="일일 최대 매수금액"
        hint={`현재: ${formatKrwLabel(settings.maxDailyBuyAmount)} · 기본 300만원`}
        testid="field-max-daily-buy-amount"
      >
        <_PresetRow
          presets={PAPER_CAPITAL_PRESETS.maxDailyBuyAmount}
          current={settings.maxDailyBuyAmount}
          onPick={onPickDaily}
          testid="preset-max-daily-buy-amount"
        />
        <_NumberInput
          value={settings.maxDailyBuyAmount}
          onChange={onChangeDaily}
          testid="input-max-daily-buy-amount"
          placeholder="직접 입력 (KRW)"
        />
      </_Field>

      {/* 5. 종목별 최대 비중 */}
      <_Field
        label="종목별 최대 비중"
        hint={`현재: ${formatPctLabel(settings.maxSymbolWeightPct)} · 기본 20%`}
        testid="field-max-symbol-weight-pct"
      >
        <_PresetRow
          presets={PAPER_CAPITAL_PRESETS.maxSymbolWeightPct}
          current={settings.maxSymbolWeightPct}
          onPick={onPickPct}
          formatter={(v) => (Math.round(Number(v) * 1000) / 1000).toFixed(3)}
          testid="preset-max-symbol-weight-pct"
        />
        <_NumberInput
          value={draftPct}
          onChange={onChangePct}
          testid="input-max-symbol-weight-pct"
          placeholder="직접 입력 (%)"
        />
      </_Field>

      {/* 6. 동일 종목 추가매수 허용 */}
      <_Field
        label="동일 종목 추가매수"
        hint="기본값은 반드시 OFF — 손실 확대 위험"
        testid="field-allow-additional-buy"
      >
        <button
          type="button"
          data-testid="toggle-allow-additional-buy"
          onClick={onToggleAdditional}
          style={{
            padding: "6px 14px",
            borderRadius: 6,
            border: `1px solid ${settings.allowAdditionalBuy ? "#ef4444" : "#22c55e"}`,
            background: settings.allowAdditionalBuy ? "#fee2e2" : "#dcfce7",
            color: settings.allowAdditionalBuy ? "#991b1b" : "#166534",
            fontWeight: "var(--fw-bold)",
            fontSize: "var(--fs-xs)",
            cursor: "pointer",
          }}
        >
          {settings.allowAdditionalBuy ? "허용 (위험)" : "비허용 (기본)"}
        </button>
        {settings.allowAdditionalBuy && (
          <div
            data-testid="additional-buy-warning"
            style={{
              marginTop: 8,
              padding: "8px 10px",
              background: "#fef2f2",
              border: "1px solid #fecaca",
              borderRadius: 4,
              color: "#7f1d1d",
              fontSize: "var(--fs-xs)",
              lineHeight: 1.5,
            }}
          >
            ⚠ 추가매수는 손실 확대 위험이 있어 기본적으로 권장하지 않습니다.
            Paper 에서만 충분히 검증하세요.
          </div>
        )}
      </_Field>

      {/* 오류 메시지 */}
      {errors.length > 0 && (
        <div
          data-testid="paper-capital-settings-errors"
          style={{
            marginBottom: 10,
            padding: "8px 10px",
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: 4,
            color: "#7f1d1d",
            fontSize: "var(--fs-xs)",
            lineHeight: 1.6,
          }}
        >
          <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
            ⚠ 입력값 오류 — 적용되지 않음
          </div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}

      {/* 기본값 되돌리기 */}
      <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
        <button
          type="button"
          data-testid="paper-capital-settings-reset"
          onClick={onReset}
          style={{
            padding: "6px 14px",
            borderRadius: 6,
            border: "1px solid #cbd5e1",
            background: "transparent",
            color: "#334155",
            fontSize: "var(--fs-xs)",
            cursor: "pointer",
          }}
        >
          기본값으로 되돌리기
        </button>
      </div>

      <div
        data-testid="paper-capital-settings-footer"
        style={{
          marginTop: 12,
          fontSize: "var(--fs-xs)",
          color: "var(--c-text-3)",
          lineHeight: 1.6,
        }}
      >
        이 설정은 Paper / AI Paper 기준입니다. 실거래 활성화 설정이 아닙니다.
        RiskManager 와 PermissionGate 는 계속 적용됩니다.
      </div>
      </div>
    </Card>
  );
}

export default PaperCapitalSettingsCard;
