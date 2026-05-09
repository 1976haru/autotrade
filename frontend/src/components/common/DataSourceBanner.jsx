import { friendlyErrorMessage } from "../../utils/errorMessage";
import { isDemoBuild } from "../BackendOfflineBanner";

// #59 Frontend Integration — DataSourceBanner / DemoModeBadge 공용 표지자.
//
// 화면이 어떤 데이터 출처로부터 채워졌는지 *명시적*으로 운영자에게 보여준다.
// `BackendOfflineBanner`(전체 화면용)와 별개로, *카드 단위* 또는 *섹션 단위*로
// "지금 보는 숫자가 실제 backend / demo / virtual 어디서 왔나"를 작게 라벨링.
//
// 운영 원칙:
// - 랜덤 시뮬레이션 데이터 / 가짜 수익을 마치 실거래처럼 표시하지 않는다.
// - mock/virtual 카드에는 작은 배지를 prominent하게 노출.
// - backend가 unreachable이고 isDemoBuild=true면 자동으로 demo 모드로 전환.
// - 실 broker / 실 AI provider / 실거래 변경 0건 — 표시만 담당.

const _MODE_PALETTE = {
  backend:      { color: "#22c55e", bg: "#ecfdf5", label: "백엔드 연결됨" },
  demo:         { color: "#7c3aed", bg: "#f5f3ff", label: "🧪 Demo Mode" },
  offline:      { color: "#ef4444", bg: "#fef2f2", label: "⚠ 백엔드 연결 대기" },
  "mock-virtual": { color: "#fbbf24", bg: "#fefce8", label: "Mock / Virtual" },
};


/**
 * Resolve effective data source given a backend connection state. Used so that
 * callers don't need to know about isDemoBuild() — they just pass the hook's
 * loading/error and `mode` prop.
 *
 * @param {{ loading: boolean, error: string|null|undefined, mode: string }} args
 * @returns {string} one of: backend / demo / offline / mock-virtual
 */
export function resolveDataSource({ loading, error, mode }) {
  if (mode === "mock-virtual") return "mock-virtual";
  if (loading) return mode || "backend";
  if (error) {
    return isDemoBuild() ? "demo" : "offline";
  }
  return mode || "backend";
}


/**
 * DataSourceBanner — 카드 또는 섹션의 상단에 한 줄로 "데이터 출처" 표시.
 * 작고 시각적으로 절제된 라벨. 사용자가 "지금 이 숫자는 실제인가 데모인가"를
 * 한눈에 알 수 있도록 한다.
 *
 * Props:
 *  - mode (string): backend / demo / offline / mock-virtual (default backend)
 *  - error (string): unfriendly raw error message; 본 컴포넌트가 friendlyErrorMessage로
 *    변환해서 표시 (Failed to fetch 원문 노출 X).
 *  - hint (string): optional 추가 한 줄 안내.
 *  - compact (bool): true면 inline 배지만 (banner 박스 X).
 *  - testId (string): 테스트용 data-testid prefix.
 */
export function DataSourceBanner({
  mode = "backend", error = "", hint = "", compact = false, testId,
}) {
  const palette = _MODE_PALETTE[mode] || _MODE_PALETTE.backend;
  const tid = testId || "data-source-banner";

  if (compact) {
    return (
      <span data-testid={tid} style={{
        display: "inline-block",
        fontSize: "var(--fs-xs, 11px)", fontWeight: "var(--fw-bold, 700)",
        color: palette.color,
        padding: "1px 6px", borderRadius: 3,
        border: `1px solid ${palette.color}55`,
        background: palette.bg,
      }}>{palette.label}</span>
    );
  }

  // Full banner with optional friendly error / hint
  const friendly = error ? friendlyErrorMessage(error) : "";
  return (
    <div data-testid={tid} style={{
      padding: "8px 12px", marginBottom: 8,
      background: palette.bg, borderRadius: 4,
      border: `1px solid ${palette.color}55`,
      color: palette.color,
      fontSize: "var(--fs-sm, 12px)",
      lineHeight: 1.6,
    }}>
      <div style={{ fontWeight: "var(--fw-bold, 700)" }}>{palette.label}</div>
      {friendly && (
        <div data-testid={`${tid}-friendly-error`} style={{
          color: "var(--c-text-2, #475569)",
          fontSize: "var(--fs-xs, 11px)", marginTop: 3,
        }}>
          {friendly}
        </div>
      )}
      {hint && (
        <div data-testid={`${tid}-hint`} style={{
          color: "var(--c-text-3, #64748b)",
          fontSize: "var(--fs-xs, 11px)", marginTop: 3,
        }}>
          {hint}
        </div>
      )}
    </div>
  );
}


/**
 * DemoModeBadge — 카드 헤더 옆에 inline으로 붙이는 작은 배지.
 *
 * Usage:
 *   <SectionLabel>오늘 손익</SectionLabel>
 *   <DemoModeBadge mode={resolveDataSource({...})} />
 *
 * `mode`가 "backend"이면 *아무것도 렌더하지 않는다* (정상 출처 시 시각 노이즈
 * 줄이기 위함). demo / offline / mock-virtual일 때만 표시.
 */
export function DemoModeBadge({ mode = "backend", testId }) {
  if (mode === "backend") return null;
  return (
    <DataSourceBanner mode={mode} compact testId={testId || "demo-mode-badge"} />
  );
}


/**
 * BackendDataSourceBanner — backend 연결 상태(useBackendStatus 같은 hook)에서
 * 자동으로 적절한 mode를 판정해 banner 렌더. compact 변형(`compact=true`)이
 * 카드 헤더에 inline 배지로 활용 가능.
 */
export function BackendDataSourceBanner({
  loading, error, mode, hint, compact = false, testId,
}) {
  // loading 중이고 아직 error / 데이터 없음 → 일반 backend label로 보여줌
  // (혼란 줄이기 위해 loading 중에는 absent label 대신 'backend'로 보여줌)
  const resolved = resolveDataSource({ loading, error, mode });
  if (compact) {
    return <DemoModeBadge mode={resolved} testId={testId} />;
  }
  return <DataSourceBanner mode={resolved} error={error || ""} hint={hint}
                            testId={testId} />;
}
