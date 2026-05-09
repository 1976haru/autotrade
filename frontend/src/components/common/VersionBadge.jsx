import { useEffect, useState } from "react";
import { APP_INFO } from "../../config/appInfo";
import { RELEASE_NOTES, latestReleaseNote } from "../../config/releaseNotes";

// VersionBadge — 화면 어딘가(Settings / 도움말 / footer)에 노출되어 클릭하면
// ReleaseNotesModal을 열어 버전 + 변경사항 + 안전 고지를 보여준다.
//
// 자동 팝업: 첫 접속 또는 새 버전 감지 시 (localStorage::lastSeenVersion vs
// APP_INFO.version 비교) 자동으로 모달이 열린다. 닫으면 lastSeenVersion에
// 현재 버전이 저장되어 다음 접속부터 자동 팝업 X.

const _STORAGE_KEY = "agent-trader-last-seen-version";


export function _readLastSeenVersion() {
  if (typeof window === "undefined") return null;
  try { return window.localStorage.getItem(_STORAGE_KEY); }
  catch { return null; }
}


export function _writeLastSeenVersion(v) {
  if (typeof window === "undefined") return;
  try { window.localStorage.setItem(_STORAGE_KEY, String(v)); }
  catch { /* localStorage disabled (private mode etc.) — silently skip */ }
}


/**
 * VersionBadge — 클릭 시 onClick 호출. 자체 모달은 렌더하지 않으며 caller가
 * `<ReleaseNotesModal>`을 별도로 mount.
 */
export function VersionBadge({ onClick, testId = "version-badge" }) {
  return (
    <button
      data-testid={testId}
      onClick={onClick}
      style={{
        background: "var(--c-surface-2, #f1f5f9)",
        border: "1px solid var(--c-border)",
        borderRadius: "var(--r-md)",
        padding: "3px 8px",
        cursor: "pointer",
        color: "var(--c-text-2)",
        fontSize: "var(--fs-xs, 11px)",
        fontFamily: "monospace",
        fontWeight: 700,
      }}
    >
      📦 {APP_INFO.releaseLabel} · {APP_INFO.version}
    </button>
  );
}


/**
 * ReleaseNotesModal — 버전 / 날짜 / 변경사항 / 안전 고지 표시 + "이번 버전
 * 공지 확인" 버튼. 닫으면 lastSeenVersion 저장.
 */
export function ReleaseNotesModal({ open, onClose, version }) {
  if (!open) return null;
  // 특정 버전을 보고 싶으면 prop으로 전달, 아니면 latest.
  const note = version
    ? RELEASE_NOTES.find((r) => r.version === version) || latestReleaseNote()
    : latestReleaseNote();
  if (!note) return null;

  const handleAck = () => {
    _writeLastSeenVersion(note.version);
    onClose();
  };

  return (
    <>
      <div
        data-testid="release-notes-backdrop"
        onClick={onClose}
        style={{
          position: "fixed", inset: 0, zIndex: 200,
          background: "rgba(15, 23, 42, 0.55)",
        }}
      />
      <div
        data-testid="release-notes-modal"
        role="dialog"
        aria-labelledby="release-notes-title"
        style={{
          position: "fixed",
          top: "50%", left: "50%",
          transform: "translate(-50%, -50%)",
          width: "min(560px, 92vw)",
          maxHeight: "85vh",
          overflowY: "auto",
          background: "var(--c-surface, #ffffff)",
          border: "1px solid var(--c-border)",
          borderRadius: "var(--r-lg)",
          boxShadow: "0 16px 48px rgba(15, 23, 42, 0.25)",
          zIndex: 201,
          padding: "20px 22px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "baseline", marginBottom: 10,
                       gap: 8, flexWrap: "wrap" }}>
          <h2 id="release-notes-title"
              style={{ margin: 0, fontSize: "var(--fs-xl, 18px)",
                        color: "var(--c-text)", fontWeight: 800 }}>
            {note.title}
          </h2>
          <span data-testid="release-notes-version-line"
                style={{ fontSize: "var(--fs-xs)",
                          color: "var(--c-text-3)",
                          fontFamily: "monospace" }}>
            {note.label} · v{note.version} · {note.date}
          </span>
        </div>

        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: "var(--fs-sm)", color: "var(--c-text-2)",
                         fontWeight: 700, marginBottom: 6 }}>
            ✨ 핵심 변경사항
          </div>
          <ul data-testid="release-notes-highlights"
              style={{ margin: 0, paddingLeft: 20,
                        color: "var(--c-text)",
                        fontSize: "var(--fs-sm)",
                        lineHeight: 1.7 }}>
            {(note.highlights || []).map((h, i) => (
              <li key={i}>{h}</li>
            ))}
          </ul>
        </div>

        <div style={{
          padding: "10px 12px",
          background: "#fef9c3",
          border: "1px solid #fbbf24",
          borderRadius: "var(--r-md)",
          marginBottom: 14,
        }}>
          <div style={{ fontSize: "var(--fs-sm)",
                         color: "#92400e",
                         fontWeight: 700, marginBottom: 4 }}>
            ⚠ 안전 고지
          </div>
          <ul data-testid="release-notes-safety"
              style={{ margin: 0, paddingLeft: 20,
                        color: "#78350f",
                        fontSize: "var(--fs-sm)",
                        lineHeight: 1.7 }}>
            {(note.safetyNotes || []).map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button data-testid="release-notes-close"
                  onClick={onClose}
                  style={{
            padding: "6px 14px",
            background: "transparent",
            border: "1px solid var(--c-border)",
            borderRadius: "var(--r-md)",
            cursor: "pointer",
            color: "var(--c-text-2)",
            fontSize: "var(--fs-sm)",
            fontFamily: "inherit",
          }}>닫기</button>
          <button data-testid="release-notes-ack"
                  onClick={handleAck}
                  style={{
            padding: "6px 14px",
            background: "var(--c-info)",
            border: "1px solid var(--c-info)",
            borderRadius: "var(--r-md)",
            cursor: "pointer",
            color: "#ffffff",
            fontSize: "var(--fs-sm)",
            fontWeight: 700,
            fontFamily: "inherit",
          }}>이번 버전 공지 확인</button>
        </div>
      </div>
    </>
  );
}


/**
 * useReleaseNotesAutoPopup — 첫 접속 또는 새 버전 감지 시 자동으로 modal을
 * 열도록 상태 관리. App 최상단(또는 Dashboard)에 한 번만 mount.
 *
 * Returns `{ open, openModal, closeModal }`.
 */
export function useReleaseNotesAutoPopup() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const last = _readLastSeenVersion();
    if (last !== APP_INFO.version) {
      setOpen(true);
    }
    // 본 effect는 mount 1회만 — 의존성 비움.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    open,
    openModal:  () => setOpen(true),
    closeModal: () => setOpen(false),
  };
}
