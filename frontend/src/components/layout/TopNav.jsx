import { TABS } from "./BottomNav";

// 231 (UI-003): Desktop top navigation. ≥768px에서 BottomNav 대신 가로 탭 바.
// 같은 TABS data, onChange contract, badges shape — 두 nav가 어떤 화면에서나
// 1:1 등가.

export function TopNav({ active, onChange, badges = {} }) {
  return (
    <nav className="ui-topnav" data-testid="top-nav" role="navigation" aria-label="primary">
      {TABS.map((t) => {
        const isActive = active === t.id;
        const badge = badges[t.id] || 0;
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => onChange(t.id)}
            data-testid={`top-nav-${t.id}`}
            aria-current={isActive ? "page" : undefined}
            className={`ui-topnav__item${isActive ? " is-active" : ""}`}
          >
            <span style={{ fontSize: "var(--fs-md)" }}>{t.icon}</span>
            <span className="ui-topnav__label">{t.label}</span>
            {badge > 0 && (
              <span data-testid={`top-nav-badge-${t.id}`}
                    className="ui-topnav__badge">
                {badge > 99 ? "99+" : String(badge)}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
