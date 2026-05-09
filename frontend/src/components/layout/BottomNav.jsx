import { useState } from "react";
import { FEATURES } from "../../config/features";

// 50: Futures 탭은 *UI 노출 전용 feature flag*(`FEATURES.futuresTab`,
// 기본 false)로 navigation에서 숨겨진다. 본 flag는 backend의
// `ENABLE_FUTURES_LIVE_TRADING`와 *무관* — UI 노출 정책일 뿐 broker 흐름
// (항상 REJECTED + adapter 미존재)에 영향을 주지 않는다.
//
// UI Final Pass: `mobileTier` 필드로 모바일 BottomNav를 핵심 5탭(primary)
// 으로 정리. secondary 탭은 모바일에서 "더보기" 슬롯을 통해 노출되며, PC
// TopNav는 모든 탭을 그대로 표시 (제한 없음).
//
//   - primary  : 모바일 BottomNav 1번 자리 (홈 / 에이전트 / 승인 / 리스크)
//   - secondary: 모바일 "더보기" 메뉴 안 (자동봇 / 차트 / 백테스트 / 로그 /
//                AI시그널 / 엔진 / 선물)
//   - 모바일 5번째 자리는 "더보기" 또는 "설정" — 본 PR은 "더보기" 슬롯 추가하고
//     설정 탭을 더보기 안에 포함 (5 슬롯: 홈/에이전트/승인/리스크/더보기)
const _ALL_TABS = [
  { id: "dash",     icon: "📊", label: "홈",       mobileTier: "primary" },
  { id: "signal",   icon: "🧠", label: "에이전트",  mobileTier: "primary" },
  { id: "approve",  icon: "🔐", label: "승인",     mobileTier: "primary" },
  { id: "strat",    icon: "🎯", label: "리스크",   mobileTier: "primary" },
  { id: "bot",      icon: "🤖", label: "자동봇",   mobileTier: "secondary" },
  { id: "chart",    icon: "📈", label: "차트",     mobileTier: "secondary" },
  { id: "backtest", icon: "🧪", label: "백테스트", mobileTier: "secondary" },
  { id: "audit",    icon: "📜", label: "로그",     mobileTier: "secondary" },
  { id: "engine",   icon: "🚀", label: "엔진",     mobileTier: "secondary" },
  // 50: Futures 탭 — feature flag로만 노출. 모바일에서는 플래그가 켜진 상태
  // 에서도 *기본 bottom tab에서 직접 노출하지 않는다* — `mobileExclude` flag로
  // BottomNav 렌더링 단에서 제외된다 (PC TopNav에는 표시).
  // UI Final Pass: futures는 secondary로 배치(flag on)되지만 mobileExclude=true
  // 가 추가로 모바일 더보기에서도 제외 — Pages demo / 안전 운영 위해.
  { id: "futures",  icon: "🪙", label: "선물",
     featureFlag: "futuresTab", mobileExclude: true,
     mobileTier: "secondary" },
  { id: "config",   icon: "⚙",  label: "설정",     mobileTier: "secondary" },
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
//
// UI Final Pass: mobileTier 분리 후 본 함수는 *기존 backwards-compat용*. 새
// 호출자는 `getMobilePrimaryTabs()` / `getMobileSecondaryTabs()`를 사용.
export function getMobileNavTabs() {
  return _ALL_TABS.filter(
    (t) => _isTabVisible(t) && !t.mobileExclude,
  );
}

// UI Final Pass — 모바일 BottomNav primary 슬롯 (홈/에이전트/승인/리스크).
// 항상 4개 (futures 등 secondary 제외).
export function getMobilePrimaryTabs() {
  return _ALL_TABS.filter(
    (t) => _isTabVisible(t) && !t.mobileExclude
            && t.mobileTier === "primary",
  );
}

// UI Final Pass — 모바일 "더보기" 메뉴 안에 노출되는 secondary 탭.
// futures는 mobileExclude=true로 더보기에서도 제외된다 (모바일은 PC에서만 접근).
export function getMobileSecondaryTabs() {
  return _ALL_TABS.filter(
    (t) => _isTabVisible(t) && !t.mobileExclude
            && t.mobileTier === "secondary",
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

// 모바일 BottomNav 1개 슬롯 (primary 탭 또는 "더보기").
function _NavSlot({ tabId, icon, label, active, badge, onClick, testId }) {
  return (
    <button
      onClick={onClick}
      data-testid={testId}
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
        borderTop:   `2px solid ${active ? "var(--c-info)" : "transparent"}`,
        transition:  "all .15s",
      }}
    >
      <div style={{ position: "relative" }}>
        <span style={{ fontSize: 20 }}>{icon}</span>
        {badge > 0 && (
          <span
            data-testid={`badge-${tabId}`}
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
        fontSize:      12,
        color:         active ? "var(--c-info)" : "var(--c-text-3)",
        fontWeight:    active ? 700 : 500,
        letterSpacing: "0.02em",
        fontFamily:    "inherit",
      }}>
        {label}
      </span>
    </button>
  );
}


// 모바일 "더보기" 메뉴 — secondary 탭 sheet. BottomNav 위에 슬라이드 업으로
// 표시. PC는 TopNav에서 모든 탭을 직접 노출하므로 본 메뉴는 모바일 전용.
function _MoreMenu({ open, onClose, active, onChange, badges }) {
  if (!open) return null;
  const secondary = getMobileSecondaryTabs();
  return (
    <>
      {/* dim backdrop */}
      <div
        data-testid="bottomnav-more-backdrop"
        onClick={onClose}
        style={{
          position: "fixed", inset: 0, zIndex: 99,
          background: "rgba(15, 23, 42, 0.45)",
        }}
      />
      <div
        data-testid="bottomnav-more-menu"
        style={{
          position: "fixed",
          bottom: 64,           // BottomNav 위
          left: "50%",
          transform: "translateX(-50%)",
          width: "100%",
          maxWidth: 520,
          background: "var(--c-surface)",
          borderTopLeftRadius: "var(--r-lg)",
          borderTopRightRadius: "var(--r-lg)",
          boxShadow: "0 -8px 32px rgba(15, 23, 42, 0.18)",
          padding: "12px 16px 16px",
          zIndex: 101,
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 12,
        }}
      >
        {secondary.length === 0 && (
          <div style={{ gridColumn: "span 4", color: "var(--c-text-3)",
                         fontSize: "var(--fs-sm)", textAlign: "center" }}>
            더보기 항목이 없습니다.
          </div>
        )}
        {secondary.map((t) => {
          const badge = badges[t.id] || 0;
          const isActive = active === t.id;
          return (
            <button
              key={t.id}
              data-testid={`bottomnav-more-${t.id}`}
              onClick={() => { onChange(t.id); onClose(); }}
              style={{
                background: isActive ? "var(--c-surface-3, #f1f5f9)" : "transparent",
                border: "1px solid var(--c-border)",
                borderRadius: "var(--r-md)",
                padding: "12px 8px",
                cursor: "pointer",
                display: "flex", flexDirection: "column",
                alignItems: "center", gap: 4,
                color: isActive ? "var(--c-info)" : "var(--c-text)",
                fontWeight: isActive ? 700 : 500,
                fontFamily: "inherit",
                position: "relative",
              }}
            >
              <span style={{ fontSize: 22 }}>{t.icon}</span>
              <span style={{ fontSize: 12 }}>{t.label}</span>
              {badge > 0 && (
                <span
                  data-testid={`bottomnav-more-badge-${t.id}`}
                  style={{
                    position: "absolute", top: 6, right: 6,
                    minWidth: 16, height: 14, padding: "0 4px",
                    borderRadius: 7, background: "#ef4444", color: "#fff",
                    fontSize: 9, fontWeight: 700, lineHeight: "14px",
                  }}
                >{_badgeLabel(badge)}</span>
              )}
            </button>
          );
        })}
      </div>
    </>
  );
}


export function BottomNav({ active, onChange, badges = {} }) {
  const [moreOpen, setMoreOpen] = useState(false);
  // UI Final Pass — mobile BottomNav는 *항상* 5 슬롯: primary 4개 + "더보기".
  // primary 4개를 초과하지 않도록 _ALL_TABS의 mobileTier=primary 갯수를 제한.
  const primary = getMobilePrimaryTabs();
  const secondaryActive = !primary.some((t) => t.id === active);
  const secondaryBadge = Object.entries(badges)
    .filter(([id]) => !primary.some((t) => t.id === id))
    .reduce((acc, [, n]) => acc + (Number(n) || 0), 0);

  return (
    // 222: max-width를 .app-bottomnav 클래스로 옮겨 미디어쿼리에서 반응형 처리.
    // 모바일 520px / PC 1280px. 나머지 위치/색은 인라인 유지.
    // 231 (UI-003): 데스크톱(≥768px)에서는 TopNav가 활성화되며 BottomNav는
    // ui-bottomnav-mobile-only 클래스로 숨겨진다.
    // 241 (Light-004): light theme — white surface + 위쪽 그림자, 토큰 색.
    <>
      <_MoreMenu
        open={moreOpen}
        onClose={() => setMoreOpen(false)}
        active={active}
        onChange={onChange}
        badges={badges}
      />
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
        {primary.map((t) => (
          <_NavSlot
            key={t.id}
            tabId={t.id} icon={t.icon} label={t.label}
            active={active === t.id}
            badge={badges[t.id] || 0}
            onClick={() => { setMoreOpen(false); onChange(t.id); }}
          />
        ))}
        <_NavSlot
          tabId="more" icon="⋯" label="더보기"
          active={moreOpen || secondaryActive}
          badge={secondaryBadge}
          onClick={() => setMoreOpen((v) => !v)}
          testId="bottomnav-more-toggle"
        />
      </div>
    </>
  );
}

// `TABS` is exported above (with feature-flag filtering) — no duplicate export needed.
