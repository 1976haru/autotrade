/**
 * Display Settings Card — 화면 표시 설정 (글자 크기 / 화면 폭 / 테마).
 *
 * 사용자 요청서 §1~3 정확 구현:
 *  - 카드 제목: "🖥 화면 표시 설정"
 *  - 3 섹션: 글자 크기 / 화면 폭 / 화면 테마 (각 3 선택지)
 *  - 기본값: large / wide / highContrastLight
 *  - "기본값으로 되돌리기" 버튼
 *  - 하단 미리보기 문구
 *
 * 절대 invariant (테스트로 lock):
 *  - 본 카드는 broker / 실거래 API / OrderExecutor 호출 0건.
 *  - "지금 매수" / "Place Order" / "실거래 시작" / "실거래 활성화" /
 *    "ENABLE_LIVE_TRADING" 라벨 button 0개.
 *  - "Paper / SIMULATION 전용 - 표시 설정" 영구 배지.
 *  - input / textarea / select 0개 — 모든 옵션은 radio-like button.
 */

import { useMemo } from "react";

import { Card, SectionLabel } from "./index";
import { useDisplaySettings } from "../../store/useDisplaySettings";


// 사용자 요청서 §2 정확 라벨 + 설명.
const FONT_SIZE_OPTIONS = [
  {
    value: "standard",
    label: "표준",
    hint:  "일반 모니터 기준 기본 크기",
  },
  {
    value: "large",
    label: "크게",
    hint:  "데스크톱 EXE 권장, 기본값",
    isDefault: true,
  },
  {
    value: "xlarge",
    label: "아주 크게",
    hint:  "글자가 작게 느껴질 때 사용",
  },
];

const LAYOUT_WIDTH_OPTIONS = [
  {
    value: "standard",
    label: "표준",
    hint:  "기본 폭, 작은 화면용",
  },
  {
    value: "wide",
    label: "넓게",
    hint:  "데스크톱 EXE 권장, 기본값",
    isDefault: true,
  },
  {
    value: "full",
    label: "전체폭",
    hint:  "좌우 여백을 최소화하여 넓은 모니터에 적합",
  },
];

const THEME_OPTIONS = [
  {
    value: "light",
    label: "밝은 테마",
    hint:  "기본 밝은 화면",
  },
  {
    value: "highContrastLight",
    label: "고대비 밝은 테마",
    hint:  "검정 배경을 줄이고 글자 대비를 높인 화면, 권장",
    isDefault: true,
  },
  {
    value: "softLight",
    label: "부드러운 밝은 테마",
    hint:  "눈부심을 줄인 연한 배경",
  },
];


function _OptionGrid({ testId, options, value, onSelect, ariaLabel }) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      data-testid={testId}
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
        gap: 8,
      }}
    >
      {options.map((opt) => {
        const isSel = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={isSel}
            data-testid={`${testId}-option-${opt.value}`}
            data-selected={isSel ? "true" : "false"}
            data-value={opt.value}
            onClick={() => onSelect(opt.value)}
            style={{
              padding: "10px 12px",
              borderRadius: 6,
              border: `1px solid ${isSel ? "#2563eb" : "var(--c-border, #cbd5e1)"}`,
              background: isSel ? "#eff6ff" : "var(--c-surface, #ffffff)",
              color: isSel ? "#1e3a8a" : "var(--c-text, #111827)",
              fontWeight: isSel ? 700 : 500,
              fontSize: 13,
              cursor: "pointer",
              textAlign: "center",
              display: "flex",
              flexDirection: "column",
              gap: 2,
              lineHeight: 1.3,
            }}
          >
            <span>{opt.label}</span>
            <span
              style={{
                fontSize: 10,
                color: isSel ? "#1e3a8a" : "var(--c-text-3, #64748b)",
                fontWeight: 400,
              }}
            >
              {opt.hint}
            </span>
            <div style={{ display: "flex", gap: 4, justifyContent: "center",
                            marginTop: 2 }}>
              {isSel && (
                <span style={{ fontSize: 9, fontWeight: 700, color: "#2563eb" }}>
                  ✓ 선택됨
                </span>
              )}
              {opt.isDefault && (
                <span
                  data-testid={`${testId}-option-${opt.value}-default-badge`}
                  style={{ fontSize: 9, color: "#475569" }}
                >
                  기본값
                </span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}


export function DisplaySettingsCard({
  testId = "display-settings-card",
  // Test injection — storage / root override.
  storage,
  root,
}) {
  const { settings, setFontSize, setLayoutWidth, setTheme, reset } =
    useDisplaySettings({ storage, root });

  // 미리보기 문구 — 현재 fontSize 에 따라 약간 크기를 조정해 사용자가
  // *체감* 할 수 있게.
  const previewStyle = useMemo(() => {
    const baseSize =
      settings.fontSize === "xlarge" ? 20 :
      settings.fontSize === "large"  ? 18 : 16;
    return {
      padding: "10px 12px",
      background: "var(--c-surface-2, #f1f5f9)",
      border: "1px solid var(--c-border, #cbd5e1)",
      borderRadius: 6,
      fontSize: baseSize,
      color: "var(--c-text, #111827)",
      lineHeight: 1.5,
    };
  }, [settings.fontSize]);

  return (
    <Card>
      <div
        data-testid={testId}
        data-current-font-size={settings.fontSize}
        data-current-layout-width={settings.layoutWidth}
        data-current-display-theme={settings.theme}
        style={{ display: "flex", flexDirection: "column", gap: 12 }}
      >
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "baseline", flexWrap: "wrap", gap: 6 }}>
          <SectionLabel>🖥 화면 표시 설정</SectionLabel>
          <span
            data-testid={`${testId}-paper-only-badge`}
            style={{
              fontSize: 9, fontWeight: 700,
              padding: "1px 6px", borderRadius: 3,
              background: "#94a3b820",
              border: "1px solid #94a3b855",
              color: "#475569",
            }}
          >
            Paper / SIMULATION 전용 · 표시 설정
          </span>
        </div>

        <div
          data-testid={`${testId}-description`}
          style={{
            padding: "6px 10px",
            background: "var(--c-surface-2, #eef6ff)",
            border: "1px solid var(--c-border, #bfdbfe)",
            borderRadius: 4,
            fontSize: 12,
            color: "var(--c-text-2, #1e3a8a)",
            lineHeight: 1.5,
          }}
        >
          글자 크기, 화면 폭, 테마를 조정해 데스크톱 EXE 화면을 더 보기 쉽게
          설정합니다. EXE 화면이 작거나 어둡게 보이면 여기서 글자 크기와
          화면 폭을 조정하세요.
        </div>

        {/* 글자 크기 */}
        <div
          data-testid={`${testId}-section-font-size`}
          style={{ display: "flex", flexDirection: "column", gap: 6 }}
        >
          <div style={{ fontSize: 13, fontWeight: 700,
                          color: "var(--c-text, #111827)" }}>
            글자 크기
          </div>
          <_OptionGrid
            testId={`${testId}-font-size`}
            options={FONT_SIZE_OPTIONS}
            value={settings.fontSize}
            onSelect={setFontSize}
            ariaLabel="글자 크기"
          />
        </div>

        {/* 화면 폭 */}
        <div
          data-testid={`${testId}-section-layout-width`}
          style={{ display: "flex", flexDirection: "column", gap: 6 }}
        >
          <div style={{ fontSize: 13, fontWeight: 700,
                          color: "var(--c-text, #111827)" }}>
            화면 폭
          </div>
          <_OptionGrid
            testId={`${testId}-layout-width`}
            options={LAYOUT_WIDTH_OPTIONS}
            value={settings.layoutWidth}
            onSelect={setLayoutWidth}
            ariaLabel="화면 폭"
          />
        </div>

        {/* 화면 테마 */}
        <div
          data-testid={`${testId}-section-theme`}
          style={{ display: "flex", flexDirection: "column", gap: 6 }}
        >
          <div style={{ fontSize: 13, fontWeight: 700,
                          color: "var(--c-text, #111827)" }}>
            화면 테마
          </div>
          <_OptionGrid
            testId={`${testId}-theme`}
            options={THEME_OPTIONS}
            value={settings.theme}
            onSelect={setTheme}
            ariaLabel="화면 테마"
          />
        </div>

        {/* 미리보기 + 초기화 버튼 */}
        <div
          data-testid={`${testId}-preview`}
          style={previewStyle}
        >
          미리보기: 이 문장이 실제 설정에 가까운 크기와 대비로 표시됩니다.
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button
            type="button"
            data-testid={`${testId}-reset-btn`}
            onClick={reset}
            style={{
              padding: "6px 14px",
              borderRadius: 4,
              border: "1px solid var(--c-border, #cbd5e1)",
              background: "var(--c-surface, #ffffff)",
              color: "var(--c-text, #111827)",
              fontSize: 12,
              cursor: "pointer",
              fontWeight: 500,
            }}
          >
            기본값으로 되돌리기
          </button>
        </div>
      </div>
    </Card>
  );
}


export default DisplaySettingsCard;
