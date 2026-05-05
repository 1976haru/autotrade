import { useCallback, useState } from "react";


/**
 * usePersistedState — useState that mirrors a string into localStorage.
 *
 * Replaces the inline read/write/validate pattern that grew copy-pasted
 * across 048 (operatorName), 054 (eventKindFilter), and 073 (eventTimeBucket).
 * Behavior:
 *
 *   - On mount: read localStorage[key]; if null or fails the validate
 *     predicate, fall back to defaultValue.
 *   - On set: update React state and write localStorage[key]. Storage
 *     failures (sandboxed embeds, quota) are swallowed so the value at
 *     least lives for the current session.
 *
 * Strings only — every current caller persists a string id. Objects/numbers
 * would need JSON serialization; cross that bridge when a caller actually
 * needs it rather than over-generalizing now.
 *
 * Cross-component writes (e.g. 057's setEventKindFilter, which writes the
 * key from outside the AuditLog component before jumping tabs) bypass this
 * hook and call localStorage.setItem directly — that's intentional, the
 * hook owns the React state, not the storage key itself.
 */
export function usePersistedState(key, defaultValue, validate) {
  const initial = () => {
    try {
      const stored = localStorage.getItem(key);
      if (stored == null) return defaultValue;
      if (validate && !validate(stored)) return defaultValue;
      return stored;
    } catch {
      return defaultValue;
    }
  };
  const [value, setValueState] = useState(initial);
  const setValue = useCallback((newValue) => {
    setValueState(newValue);
    try {
      localStorage.setItem(key, newValue);
    } catch {
      // In-memory state still updated; future renders just lose persistence.
    }
  }, [key]);
  return [value, setValue];
}
