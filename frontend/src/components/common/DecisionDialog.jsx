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
 *   - onConfirm:    ({decided_by, note}) => void | {ok, message?} | Promise of either
 *   - onCancel:     () => void
 *
 * onConfirm contract: returning {ok: false, message} keeps the dialog open
 * and shows the message inside it (072 retry-on-failure) — operator keeps
 * what they typed and can retry without re-entering anything. Returning
 * {ok: true} or undefined/null is treated as success; the parent unmounts
 * the dialog as before.
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
  // 072: when onConfirm returns {ok:false}, render the message in the
  // dialog and stay open so the operator can retry. Local to the dialog —
  // resets on each remount (parent re-mounts on each open).
  const [errorMessage, setErrorMessage] = useState(null);
  const decidedByRef = useRef(null);
  const noteRef      = useRef(null);

  // 063 a11y: focus first empty input on mount. operatorName(048) pre-fill
  // skips ahead to the note field — that's where the operator usually adds
  // context in a hurry.
  useEffect(() => {
    (defaultDecidedBy ? noteRef : decidedByRef).current?.focus();
  }, [defaultDecidedBy]);

  const _submit = async () => {
    setErrorMessage(null);
    const payload = { decided_by: decidedBy.trim(), note: note.trim() };
    const result = await onConfirm(payload);
    // null / undefined / {ok:true} are all "success" — parent closes us.
    // Only an explicit {ok:false} keeps us open with feedback.
    if (result && result.ok === false) {
      setErrorMessage(result.message || "요청 실패");
    }
  };

  // Keyboard shortcuts: Esc cancels, Enter confirms with current trimmed values.
  // Suppressed while busy so a long-running confirm can't double-submit.
  //
  // 095: IME composition guard. Korean operators commit Hangul jamo with
  // Enter, and without this guard the dialog would submit on every Hangul
  // confirmation while typing into the note field — surprise approvals
  // mid-sentence. `isComposing` is the modern check; `keyCode === 229` is
  // the legacy fallback some browsers still emit during IME composition
  // even though `isComposing` would be the spec-correct property.
  useEffect(() => {
    if (busy) return undefined;
    const handler = (e) => {
      if (e.key === "Escape") { e.preventDefault(); onCancel(); }
      else if (e.key === "Enter") {
        if (e.isComposing || e.keyCode === 229) return;
        e.preventDefault();
        _submit();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // _submit is recreated each render but captures latest state — listing it
    // would re-bind every keystroke; depend on the inputs it captures instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

        {errorMessage && (
          <div
            data-testid="decision-dialog-error"
            style={{
              fontSize: 11, color: "#fca5a5", marginBottom: 10, padding: "6px 8px",
              background: "#7f1d1d33", border: "1px solid #ef444466", borderRadius: 4,
              lineHeight: 1.4,
            }}
          >
            {errorMessage}
          </div>
        )}

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Btn color="#1a3a5c" onClick={onCancel} disabled={busy} small>{cancelLabel}</Btn>
          <Btn
            color={accent}
            onClick={_submit}
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
