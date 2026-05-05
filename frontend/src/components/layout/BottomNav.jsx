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

// 99건 초과는 "99+"로 표시 — 운영자에겐 정확한 큰 수치보다 "큐가 폭주한
// 상태"라는 사실 자체가 더 중요하고, 작은 배지에 3자리 숫자를 우겨넣으면
// 가독성이 떨어진다.
export function _badgeLabel(count) {
  return count > 99 ? "99+" : String(count);
}

export function BottomNav({ active, onChange, badges = {} }) {
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
      {TABS.map((t) => {
        const badge = badges[t.id] || 0;
        return (
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
            <div style={{ position: "relative" }}>
              <span style={{ fontSize: 18 }}>{t.icon}</span>
              {badge > 0 && (
                <span
                  data-testid={`badge-${t.id}`}
                  style={{
                    position:   "absolute",
                    top:        -4,
                    right:      -10,
                    minWidth:   16,
                    height:     14,
                    padding:    "0 4px",
                    borderRadius: 7,
                    background: "#ef4444",
                    color:      "#fff",
                    fontSize:   9,
                    fontWeight: 700,
                    lineHeight: "14px",
                    textAlign:  "center",
                    boxSizing:  "border-box",
                    boxShadow:  "0 0 0 2px #020e1c",
                  }}
                >
                  {_badgeLabel(badge)}
                </span>
              )}
            </div>
            <span style={{
              fontSize:      9,
              color:         active === t.id ? "#7dd3fc" : "#475569",
              letterSpacing: "0.04em",
              fontFamily:    "inherit",
            }}>
              {t.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export { TABS };
