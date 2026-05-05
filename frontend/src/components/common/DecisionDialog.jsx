import { useEffect, useRef, useState } from "react";

import { Btn, Card, Inp } from "./index";


/**
 * DecisionDialog — operator-decision modal primitive.
 *
 * Consolidates the modal pattern emerged across 047 (emergency stop), 049
 * (per-row approval), and 065 (bulk cancel stale). Each had its own backdrop
 * + Card + (decided_by, note) inputs + 063 keyboard a11y, with only the
 * title/summary/labels differing. Three identical skeletons hit the bar
 * CLAUDE.md draws for extraction.
 *
 * The decision payload is { decided_by, note }, both trimmed; consumers
 * normalize empty values further if they want null vs "" semantics.
 *
 * Required props:
 *   - title:        bold accent-colored heading
 *   - accent:       hex color used for border, title, confirm button
 *   - confirmLabel: confirm button label (e.g. "✓ 승인", "⊘ 3건 취소")
 *   - busy:         disables both buttons + suppresses keyboard shortcuts
 *   - onConfirm:    ({decided_by, note}) => void
 *   - onCancel:     () => void
 *
 * Optional:
 *   - ariaLabel:             dialog aria-label (defaults to title)
 *   - summary:               ReactNode rendered between title and inputs
 *                             (e.g. order summary card, stale row list)
 *   - description:           explanatory paragraph above the inputs
 *   - cancelLabel:           defaults to "닫기"
 *   - defaultDecidedBy:      pre-filled into decided_by; also drives initial focus
 *                             (focus jumps to note input when this is non-empty)
 *   - decidedByPlaceholder:  defaults to "예: ops1"
 *   - notePlaceholder:       e.g. "예: vol spike, circuit-breaker"
 */
export function DecisionDialog({
  title, ariaLabel, accent,
  summary, description,
  confirmLabel, cancelLabel = "닫기",
  busy, defaultDecidedBy = "",
  decidedByPlaceholder = "예: ops1",
  notePlaceholder = "",
  onConfirm, onCancel,
}) {
  const [decidedBy, setDecidedBy] = useState(defaultDecidedBy);
  const [note,      setNote]      = useState("");
  const decidedByRef = useRef(null);
  const noteRef      = useRef(null);

  // 063 a11y: focus first empty input on mount. operatorName(048) pre-fill
  // skips ahead to the note field — that's where the operator usually adds
  // context in a hurry.
  useEffect(() => {
    (defaultDecidedBy ? noteRef : decidedByRef).current?.focus();
  }, [defaultDecidedBy]);

  // Keyboard shortcuts: Esc cancels, Enter confirms with current trimmed values.
  // Suppressed while busy so a long-running confirm can't double-submit.
  useEffect(() => {
    if (busy) return undefined;
    const handler = (e) => {
      if (e.key === "Escape") { e.preventDefault(); onCancel(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        onConfirm({ decided_by: decidedBy.trim(), note: note.trim() });
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [busy, onCancel, onConfirm, decidedBy, note]);

  return (
    <div
      role="dialog"
      aria-label={ariaLabel || title}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 1000,
      }}
    >
      <Card accentColor={`${accent}55`} style={{ width: 380, maxWidth: "90vw" }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: accent, marginBottom: 8 }}>
          {title}
        </div>

        {summary}

        {description && (
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 10, lineHeight: 1.5 }}>
            {description}
          </div>
        )}

        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 10, color: "#64748b", marginBottom: 4 }}>운영자명 (decided_by)</div>
          <Inp value={decidedBy} onChange={setDecidedBy}
                placeholder={decidedByPlaceholder} inputRef={decidedByRef} />
        </div>
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 10, color: "#64748b", marginBottom: 4 }}>사유 (note)</div>
          <Inp value={note} onChange={setNote}
                placeholder={notePlaceholder} inputRef={noteRef} />
        </div>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Btn color="#1a3a5c" onClick={onCancel} disabled={busy} small>{cancelLabel}</Btn>
          <Btn
            color={accent}
            onClick={() => onConfirm({ decided_by: decidedBy.trim(), note: note.trim() })}
            disabled={busy}
            small
          >
            {busy ? "처리 중…" : confirmLabel}
          </Btn>
        </div>
      </Card>
    </div>
  );
}
