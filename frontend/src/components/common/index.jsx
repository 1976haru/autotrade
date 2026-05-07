/**
 * 공통 UI 컴포넌트
 * 모든 탭에서 재사용되는 기본 빌딩 블록.
 *
 * 243 (Light-006): light theme로 일괄 전환. token 기반 색·radius·spacing.
 * 점진 마이그레이션 대신 한 번에 primitive를 light로 바꿔 모든 탭이 동시에
 * 라이트 화면으로 전환된다 — 다수 카드의 inline dark 색은 그대로 남지만,
 * 카드 컨테이너 자체와 핵심 primitive(Btn/Inp/StatBox/ScoreBar/Toggle/Slider)
 * 는 모두 light.
 */

export const Card = ({ children, accentColor, style }) => (
  <div style={{
    background: "var(--c-surface)",
    border: `1px solid ${accentColor || "var(--c-border)"}`,
    borderRadius: "var(--r-lg)",
    padding: "var(--s-4)",
    boxShadow: "var(--sh-1)",
    ...style,
  }}>
    {children}
  </div>
);

export const SectionLabel = ({ children }) => (
  <div style={{
    fontSize: "var(--fs-xs)",
    color: "var(--c-text-3)",
    letterSpacing: "0.10em",
    marginBottom: 8,
    textTransform: "uppercase",
    fontWeight: "var(--fw-bold)",
  }}>
    {children}
  </div>
);

export const Btn = ({ children, onClick, disabled, color = "#3b82f6", full, small }) => (
  <button
    onClick={onClick}
    disabled={disabled}
    style={{
      padding:     small ? "8px 16px" : "10px 20px",
      borderRadius: "var(--r-md)",
      border:      "none",
      cursor:      disabled ? "not-allowed" : "pointer",
      background:  disabled ? "var(--c-surface-3)" : color,
      color:       disabled ? "var(--c-text-4)"   : "#fff",
      fontWeight:  "var(--fw-bold)",
      fontSize:    small ? "var(--fs-sm)" : "var(--fs-base)",
      fontFamily:  "inherit",
      width:       full ? "100%" : undefined,
      transition:  "opacity .15s, transform .1s",
      letterSpacing: "0.02em",
    }}
  >
    {children}
  </button>
);

export const Inp = ({ value, onChange, placeholder, type = "text", inputRef }) => (
  <input
    ref={inputRef}
    type={type}
    value={value}
    onChange={(e) => onChange(e.target.value)}
    placeholder={placeholder}
    style={{
      width:      "100%",
      background: "var(--c-surface)",
      border:     "1px solid var(--c-border-strong)",
      borderRadius: "var(--r-md)",
      padding:    "10px 12px",
      color:      "var(--c-text)",
      fontSize:   "var(--fs-base)",
      fontFamily: "inherit",
      outline:    "none",
      boxSizing:  "border-box",
    }}
  />
);

export const ScoreBar = ({ label, value, color }) => (
  <div style={{ marginBottom: 10 }}>
    <div style={{ display: "flex", justifyContent: "space-between",
                   fontSize: "var(--fs-sm)", marginBottom: 4 }}>
      <span style={{ color: "var(--c-text-3)" }}>{label}</span>
      <span style={{ color, fontWeight: "var(--fw-bold)" }}>{Math.round(value ?? 0)}</span>
    </div>
    <div style={{ height: 6, background: "var(--c-surface-3)",
                   borderRadius: 999, overflow: "hidden" }}>
      <div style={{
        width:      `${Math.min(value ?? 0, 100)}%`,
        height:     "100%",
        background: color,
        borderRadius: 999,
        transition: "width .8s ease",
      }} />
    </div>
  </div>
);

export const StatBox = ({ label, value, color }) => (
  <div style={{ textAlign: "center" }}>
    <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
                   marginBottom: 4, textTransform: "uppercase",
                   letterSpacing: "0.06em" }}>{label}</div>
    <div style={{ fontSize: "var(--fs-2xl)", fontWeight: "var(--fw-bold)",
                   color: color || "var(--c-text)" }}>{value}</div>
  </div>
);

export const Toggle = ({ value, onChange, color }) => (
  <div
    onClick={() => onChange(!value)}
    style={{
      width: 42, height: 24, borderRadius: 12,
      background: value ? color : "var(--c-surface-3)",
      display: "flex", alignItems: "center",
      paddingLeft: value ? 20 : 2,
      transition: "all .2s", cursor: "pointer",
      boxShadow: value ? `0 0 0 1px ${color}66` : "0 0 0 1px var(--c-border)",
    }}
  >
    <div style={{ width: 20, height: 20, borderRadius: "50%",
                   background: "white",
                   boxShadow: "0 1px 3px rgba(0,0,0,0.2)" }} />
  </div>
);

export const Slider = ({ label, value, min, max, step, onChange, unit = "" }) => (
  <div style={{ marginBottom: 12 }}>
    <div style={{ display: "flex", justifyContent: "space-between",
                   fontSize: "var(--fs-sm)", marginBottom: 4 }}>
      <span style={{ color: "var(--c-text-3)" }}>{label}</span>
      <span style={{ color: "var(--c-info)", fontWeight: "var(--fw-bold)" }}>
        {value?.toLocaleString("ko-KR")}{unit}
      </span>
    </div>
    <input
      type="range" min={min} max={max} step={step} value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      style={{ width: "100%", accentColor: "var(--c-info)", cursor: "pointer" }}
    />
  </div>
);
