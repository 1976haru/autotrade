/**
 * 공통 UI 컴포넌트
 * 모든 탭에서 재사용되는 기본 빌딩 블록
 */

export const Card = ({ children, accentColor, style }) => (
  <div style={{
    background: "#020e1c",
    border: `1px solid ${accentColor || "#0c2035"}`,
    borderRadius: 8,
    padding: 14,
    ...style,
  }}>
    {children}
  </div>
);

export const SectionLabel = ({ children }) => (
  <div style={{
    fontSize: 10,
    color: "#475569",
    letterSpacing: "0.12em",
    marginBottom: 8,
    textTransform: "uppercase",
  }}>
    {children}
  </div>
);

export const Btn = ({ children, onClick, disabled, color = "#7dd3fc", full, small }) => (
  <button
    onClick={onClick}
    disabled={disabled}
    style={{
      padding:     small ? "6px 14px" : "9px 18px",
      borderRadius: 4,
      border:      "none",
      cursor:      disabled ? "not-allowed" : "pointer",
      background:  disabled ? "#1a2a3a" : color,
      color:       disabled ? "#475569"  : "#010a14",
      fontWeight:  700,
      fontSize:    small ? 11 : 12,
      fontFamily:  "inherit",
      width:       full ? "100%" : undefined,
      transition:  "opacity .15s",
      letterSpacing: "0.04em",
    }}
  >
    {children}
  </button>
);

export const Inp = ({ value, onChange, placeholder, type = "text" }) => (
  <input
    type={type}
    value={value}
    onChange={(e) => onChange(e.target.value)}
    placeholder={placeholder}
    style={{
      width:      "100%",
      background: "#010a14",
      border:     "1px solid #1a3a5c",
      borderRadius: 4,
      padding:    "8px 10px",
      color:      "#c9d6e3",
      fontSize:   12,
      fontFamily: "inherit",
      outline:    "none",
      boxSizing:  "border-box",
    }}
  />
);

export const ScoreBar = ({ label, value, color }) => (
  <div style={{ marginBottom: 10 }}>
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
      <span style={{ color: "#64748b" }}>{label}</span>
      <span style={{ color, fontWeight: 700 }}>{Math.round(value ?? 0)}</span>
    </div>
    <div style={{ height: 5, background: "#0c2035", borderRadius: 3, overflow: "hidden" }}>
      <div style={{
        width:      `${Math.min(value ?? 0, 100)}%`,
        height:     "100%",
        background: color,
        borderRadius: 3,
        transition: "width .8s ease",
      }} />
    </div>
  </div>
);

export const StatBox = ({ label, value, color }) => (
  <div style={{ textAlign: "center" }}>
    <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>{label}</div>
    <div style={{ fontSize: 18, fontWeight: 700, color: color || "#e2e8f0" }}>{value}</div>
  </div>
);

export const Toggle = ({ value, onChange, color }) => (
  <div
    onClick={() => onChange(!value)}
    style={{
      width: 38, height: 21, borderRadius: 11,
      background: value ? color : "#1a3a5c",
      display: "flex", alignItems: "center",
      paddingLeft: value ? 18 : 2,
      transition: "all .2s", cursor: "pointer",
    }}
  >
    <div style={{ width: 17, height: 17, borderRadius: "50%", background: "white" }} />
  </div>
);

export const Slider = ({ label, value, min, max, step, onChange, unit = "" }) => (
  <div style={{ marginBottom: 12 }}>
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
      <span style={{ color: "#64748b" }}>{label}</span>
      <span style={{ color: "#7dd3fc", fontWeight: 700 }}>
        {value?.toLocaleString("ko-KR")}{unit}
      </span>
    </div>
    <input
      type="range" min={min} max={max} step={step} value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      style={{ width: "100%", accentColor: "#0ea5e9", cursor: "pointer" }}
    />
  </div>
);
