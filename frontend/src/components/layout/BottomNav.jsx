const TABS = [
  { id: "dash",     icon: "📊", label: "대시보드" },
  { id: "strat",    icon: "🎯", label: "전략·리스크" },
  { id: "bot",      icon: "🤖", label: "자동봇" },
  { id: "approve",  icon: "🔐", label: "승인" },
  { id: "chart",    icon: "📈", label: "차트" },
  { id: "backtest", icon: "🧪", label: "백테스트" },
  { id: "audit",    icon: "📜", label: "로그" },
  { id: "signal",   icon: "🧠", label: "AI시그널" },
  { id: "engine",   icon: "🚀", label: "엔진" },
  { id: "futures",  icon: "🪙", label: "선물" },
  { id: "config",   icon: "⚙",  label: "설정" },
];

export function BottomNav({ active, onChange }) {
  return (
    <div style={{
      position:   "fixed",
      bottom:     0,
      left:       "50%",
      transform:  "translateX(-50%)",
      width:      "100%",
      maxWidth:   520,
      background: "#020e1c",
      borderTop:  "1px solid #0c2035",
      display:    "flex",
      zIndex:     100,
    }}>
      {TABS.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          style={{
            flex:        1,
            padding:     "10px 0 8px",
            border:      "none",
            cursor:      "pointer",
            background:  "transparent",
            display:     "flex",
            flexDirection: "column",
            alignItems:  "center",
            gap:         3,
            borderTop:   `2px solid ${active === t.id ? "#7dd3fc" : "transparent"}`,
            transition:  "all .15s",
          }}
        >
          <span style={{ fontSize: 18 }}>{t.icon}</span>
          <span style={{
            fontSize:      9,
            color:         active === t.id ? "#7dd3fc" : "#475569",
            letterSpacing: "0.04em",
            fontFamily:    "inherit",
          }}>
            {t.label}
          </span>
        </button>
      ))}
    </div>
  );
}

export { TABS };
