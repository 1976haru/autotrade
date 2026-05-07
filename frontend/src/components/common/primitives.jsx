/**
 * 229 (UI-001): Premium Operator Dashboard primitives.
 *
 * 모든 컴포넌트는 index.css의 CSS 변수와 .ui-* 클래스를 참조 — 색·간격·폰트
 * 변경이 한 곳(token)으로 중앙화된다. 인라인 style은 동적 값(상태별 color)
 * 만 사용.
 *
 * 기존 components/common/index.jsx의 Card / SectionLabel / Btn / StatBox는
 * 그대로 유지(다수 callers). 이 파일은 *추가* primitives — 점진 마이그레이션.
 */

export function PageHeader({ title, subtitle, right }) {
  return (
    <header className="ui-page-header" data-testid="ui-page-header">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 12 }}>
        <h1 className="ui-page-header__title">{title}</h1>
        {right ? <div>{right}</div> : null}
      </div>
      {subtitle && (
        <div className="ui-page-header__subtitle">{subtitle}</div>
      )}
    </header>
  );
}


export function SectionHeader({ children, sub }) {
  return (
    <h2 className="ui-section-header">
      {children}
      {sub && <span className="ui-section-header__sub">{sub}</span>}
    </h2>
  );
}


// 색 prop은 token 키 또는 raw color. 동적이라 인라인 style로 노출.
export function MetricCard({ label, value, sub, color, testId }) {
  const valueStyle = color ? { color } : undefined;
  return (
    <div className="ui-metric-card" data-testid={testId}>
      <div className="ui-metric-card__label">{label}</div>
      <div className="ui-metric-card__value" style={valueStyle}>{value}</div>
      {sub && <div className="ui-metric-card__sub">{sub}</div>}
    </div>
  );
}


// status: success | warning | danger | info | neutral. raw color 직접도 허용.
const STATUS_COLOR = {
  success: "var(--c-success)",
  warning: "var(--c-warning)",
  danger:  "var(--c-danger)",
  info:    "var(--c-info)",
  neutral: "var(--c-text-3)",
};
export function StatusBadge({ status = "neutral", children, testId }) {
  const color = STATUS_COLOR[status] ?? status;
  return (
    <span className="ui-status-badge" data-testid={testId}
          style={{ color, background: `${cssColorBgFrom(status)}` }}>
      {children}
    </span>
  );
}

function cssColorBgFrom(status) {
  switch (status) {
    case "success": return "rgba(34, 197, 94, 0.10)";
    case "warning": return "rgba(245, 158, 11, 0.10)";
    case "danger":  return "rgba(239, 68, 68, 0.10)";
    case "info":    return "rgba(125, 211, 252, 0.10)";
    default:        return "transparent";
  }
}


export function StatusPill({ status = "neutral", children, testId }) {
  const color = STATUS_COLOR[status] ?? status;
  return (
    <span className="ui-status-pill" data-testid={testId} style={{ color }}>
      <span className="ui-status-pill__dot" />
      {children}
    </span>
  );
}


export function EmptyState({ icon = "ℹ", title, hint, testId }) {
  return (
    <div className="ui-empty-state" data-testid={testId}>
      <div className="ui-empty-state__icon">{icon}</div>
      {title && <div className="ui-empty-state__title">{title}</div>}
      {hint  && <div className="ui-empty-state__hint">{hint}</div>}
    </div>
  );
}


// raw error message (e.g. "Failed to fetch")는 hint에 들어가도 사용자 친화적
// 문구로 prefix가 붙어 보이게 — 호출자 책임이지만 기본 prefix를 제공.
export function ErrorState({ icon = "⚠", title = "데이터 조회 실패",
                             hint, retryLabel, onRetry, testId }) {
  return (
    <div className="ui-error-state" data-testid={testId}>
      <div className="ui-error-state__icon">{icon}</div>
      <div className="ui-error-state__title">{title}</div>
      {hint && <div className="ui-error-state__hint">{hint}</div>}
      {onRetry && (
        <button onClick={onRetry}
                style={{
                  marginTop: 4, padding: "4px 12px",
                  background: "transparent", border: "1px solid currentColor",
                  borderRadius: 4, color: "inherit", cursor: "pointer",
                  fontFamily: "inherit", fontSize: "var(--fs-sm)",
                }}>
          {retryLabel || "다시 시도"}
        </button>
      )}
    </div>
  );
}


export function LoadingState({ icon = "…", title = "로딩 중", hint, testId }) {
  return (
    <div className="ui-loading-state" data-testid={testId}>
      <div className="ui-loading-state__icon">{icon}</div>
      <div className="ui-loading-state__title">{title}</div>
      {hint && <div className="ui-loading-state__hint">{hint}</div>}
    </div>
  );
}


export function DemoModeBanner({ title = "🧪 Demo Mode (GitHub Pages)",
                                  body, hint, testId = "ui-demo-banner" }) {
  return (
    <div className="ui-demo-banner" data-testid={testId}>
      <div className="ui-demo-banner__title">{title}</div>
      {body && <div className="ui-demo-banner__body">{body}</div>}
      {hint && <div className="ui-demo-banner__hint">{hint}</div>}
    </div>
  );
}
