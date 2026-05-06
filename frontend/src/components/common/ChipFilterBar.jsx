/**
 * ChipFilterBar — radio-style filter chip row.
 *
 * Consolidates 052's KindFilterBar, 073's TimeBucketBar, and 083's
 * HistoryStatusFilterBar — three identical skeletons differing only in
 * `items` and `ariaLabel`. CLAUDE.md's three-similar-lines bar.
 *
 * Each item is { id, label, color }. The active item is highlighted using
 * its color (background tint, border, text). Inactive chips share a
 * neutral border. The primitive doesn't own state — consumers wire id
 * comparison + onChange like a controlled radiogroup.
 *
 * Required props:
 *   - items:     [{ id, label, color }]
 *   - active:    currently-selected id
 *   - onChange:  (id) => void
 *   - ariaLabel: localizes the radiogroup (e.g. "이벤트 종류 필터")
 */
export function ChipFilterBar({ items, active, onChange, ariaLabel }) {
  return (
    <div role="radiogroup" aria-label={ariaLabel}
         style={{ display: "flex", gap: 4 }}>
      {items.map((item) => {
        const isActive = active === item.id;
        return (
          <button
            key={item.id}
            role="radio"
            aria-checked={isActive}
            onClick={() => onChange(item.id)}
            style={{
              padding: "5px 10px", borderRadius: 12, cursor: "pointer",
              fontFamily: "inherit", fontSize: 10, fontWeight: 700,
              background: isActive ? `${item.color}22` : "transparent",
              border:     `1px solid ${isActive ? item.color : "#1a3a5c"}`,
              color:      isActive ? item.color : "#475569",
            }}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
