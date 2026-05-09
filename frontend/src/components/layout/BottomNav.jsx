import { FEATURES } from "../../config/features";

// 50: Futures 탭은 *UI 노출 전용 feature flag*(`FEATURES.futuresTab`,
// 기본 false)로 navigation에서 숨겨진다. 본 flag는 backend의
// `ENABLE_FUTURES_LIVE_TRADING`와 *무관* — UI 노출 정책일 뿐 broker 흐름
// (항상 REJECTED + adapter 미존재)에 영향을 주지 않는다.
const _ALL_TABS = [
  { id: "dash",     icon: "📊", label: "대시보드" },
  { id: "strat",    icon: "🎯", label: "전략·리스크" },
  { id: "bot",      icon: "🤖", label: "자동봇" },
  { id: "approve",  icon: "🔐", label: "승인" },
  { id: "chart",    icon: "📈", label: "차트" },
  { id: "backtest", icon: "🧪", label: "백테스트" },
  { id: "audit",    icon: "📜", label: "로그" },
  { id: "signal",   icon: "🧠", label: "AI시그널" },
  { id: "engine",   icon: "🚀", label: "엔진" },
  // 50: Futures 탭 — feature flag로만 노출. 모바일에서는 플래그가 켜진 상태
  // 에서도 *기본 bottom tab에서 직접 노출하지 않는다* — `mobileExclude` flag로
  // BottomNav 렌더링 단에서 제외된다 (PC TopNav에는 표시).
  { id: "futures",  icon: "🪙", label: "선물",
     featureFlag: "futuresTab", mobileExclude: true },
  { id: "config",   icon: "⚙",  label: "설정" },
];

// 운영자에게 보여지는 *최종* 탭 목록. feature flag가 꺼진 탭은 제외.
// 50: 본 함수는 *호출 시점*에 매번 FEATURES를 평가 — 테스트에서
// `__setFeatureForTest`로 flag를 toggle하면 다음 호출에 즉시 반영된다.
function _isTabVisible(tab) {
  if (!tab.featureFlag) return true;
  return Boolean(FEATURES[tab.featureFlag]);
}

// PC TopNav가 사용하는 가시 탭 목록. 호출 시점에 평가.
export function getNavTabs() {
  return _ALL_TABS.filter(_isTabVisible);
}

// 모바일 BottomNav 전용 — `mobileExclude=true`인 탭은 추가로 제외된다.
// 50: Futures는 mobile bottom tab에서 항상 숨김 (flag 켜져 있어도 PC에만 노출).
export function getMobileNavTabs() {
  return _ALL_TABS.filter(
    (t) => _isTabVisible(t) && !t.mobileExclude,
  );
}

// 50: tab id가 현재 navigation에서 노출되는지 (PC + mobile 통합).
export function isTabVisible(tabId) {
  return _ALL_TABS.some((t) => t.id === tabId && _isTabVisible(t));
}

// Backwards-compat: 기존 호출자가 `import { TABS }`로 사용. 본 export는
// Proxy로 매 access마다 `getNavTabs()`를 호출해 최신 결과를 반환한다.
export const TABS = new Proxy([], {
  get(_target, key) {
    const tabs = getNavTabs();
    if (key === Symbol.iterator) return tabs[Symbol.iterator].bind(tabs);
    if (key === "length") return tabs.length;
    const value = tabs[key];
    return typeof value === "function" ? value.bind(tabs) : value;
  },
  has(_target, key) {
    return key in getNavTabs();
  },
});

// 99건 초과는 "99+"로 표시 — 운영자에겐 정확한 큰 수치보다 "큐가 폭주한
// 상태"라는 사실 자체가 더 중요하고, 작은 배지에 3자리 숫자를 우겨넣으면
// 가독성이 떨어진다.
export function _badgeLabel(count) {
  return count > 99 ? "99+" : String(count);
}

export function BottomNav({ active, onChange, badges = {} }) {
  return (
    // 222: max-width를 .app-bottomnav 클래스로 옮겨 미디어쿼리에서 반응형 처리.
    // 모바일 520px / PC 1280px. 나머지 위치/색은 인라인 유지.
    // 231 (UI-003): 데스크톱(≥768px)에서는 TopNav가 활성화되며 BottomNav는
    // ui-bottomnav-mobile-only 클래스로 숨겨진다.
    // 241 (Light-004): light theme — white surface + 위쪽 그림자, 토큰 색.
    <div className="app-bottomnav ui-bottomnav-mobile-only" style={{
      position:   "fixed",
      bottom:     0,
      left:       "50%",
      transform:  "translateX(-50%)",
      width:      "100%",
      background: "var(--c-surface)",
      borderTop:  "1px solid var(--c-border)",
      boxShadow:  "0 -2px 12px rgba(15, 23, 42, 0.08)",
      display:    "flex",
      zIndex:     100,
    }}>
      {getMobileNavTabs().map((t) => {
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
              borderTop:   `2px solid ${active === t.id ? "var(--c-info)" : "transparent"}`,
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
              fontSize:      11,
              color:         active === t.id ? "var(--c-info)" : "var(--c-text-3)",
              fontWeight:    active === t.id ? 700 : 500,
              letterSpacing: "0.02em",
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

// `TABS` is exported above (with feature-flag filtering) — no duplicate export needed.
