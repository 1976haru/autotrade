import { useEffect, useState } from "react";
import { APP_INFO } from "../../config/appInfo";
import {
  RELEASE_NOTES,
  WELCOME_NOTES,
  latestReleaseNote,
  latestWelcomeNote,
} from "../../config/releaseNotes";

// VersionBadge — 화면 어딘가(Settings / 도움말 / footer)에 노출되어 클릭하면
// ReleaseNotesModal을 열어 버전 + 변경사항 + 안전 고지를 보여준다.
//
// 자동 팝업: 첫 접속 시 (localStorage::welcome-ack 미존재) 또는 새 버전 감지
// 시 자동으로 모달이 열린다. "이번 안내 확인" 버튼을 누르면 ack 저장 →
// 다음 접속부터 자동 팝업 X.
//
// fix/update-banner-stale-release-notes: ack 영구 저장을 위해 *welcome 전용*
// localStorage key 사용. 이전 "agent-trader-last-seen-version" 키도 backwards
// compat 읽기 지원 — 기존 사용자가 이미 1.0.0 으로 ack 한 경우 재팝업 안 함.

const _STORAGE_KEY_WELCOME = "agent-trader-welcome-ack";
const _STORAGE_KEY_LEGACY  = "agent-trader-last-seen-version";


export function _readLastSeenVersion() {
  if (typeof window === "undefined") return null;
  try {
    // 신규 welcome ack 우선, 없으면 legacy 키 확인 (backwards compat).
    const w = window.localStorage.getItem(_STORAGE_KEY_WELCOME);
    if (w !== null) return w;
    return window.localStorage.getItem(_STORAGE_KEY_LEGACY);
  } catch {
    return null;
  }
}


export function _writeLastSeenVersion(v) {
  if (typeof window === "undefined") return;
  try {
    // 신규 키에 저장. legacy 키는 그대로 두어 다른 환경 호환 유지.
    window.localStorage.setItem(_STORAGE_KEY_WELCOME, String(v));
  } catch { /* localStorage disabled (private mode etc.) — silently skip */ }
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
 * ReleaseNotesModal — 버전 / 날짜 / 변경사항 / 안전 고지 표시 + ack 버튼.
 *
 * fix/update-banner-stale-release-notes: 표시 대상은 우선순위 ① 명시 prop
 * version, ② 실 릴리스 노트 (RELEASE_NOTES), ③ 초기 안내 (WELCOME_NOTES).
 * `note.kind === "welcome"` 또는 `note.isInitialAnnouncement` 면 *"초기 안내"
 * 배지* 표시 + 명시적 disclaimer ("최신 릴리스 변경 내역이 아닙니다").
 */
export function ReleaseNotesModal({ open, onClose, version }) {
  if (!open) return null;

  // 표시할 note 선택 — 명시 version > 실 release > welcome.
  let note = null;
  if (version) {
    note =
      RELEASE_NOTES.find((r) => r.version === version)
      || WELCOME_NOTES.find((r) => r.version === version)
      || latestReleaseNote()
      || latestWelcomeNote();
  } else {
    note = latestReleaseNote() || latestWelcomeNote();
  }
  if (!note) return null;

  const isWelcome =
    note.kind === "welcome" || note.isInitialAnnouncement === true;

  const handleAck = () => {
    _writeLastSeenVersion(note.version);
    onClose();
  };

  const ackLabel = isWelcome ? "이번 안내 확인" : "이번 버전 공지 확인";

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
        data-note-kind={isWelcome ? "welcome" : "release"}
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
        {/* fix/update-banner-stale-release-notes: 초기 안내 vs 릴리스 노트
            명확 구분. welcome 일 때 "초기 안내" 배지 + disclaimer 강조. */}
        {isWelcome && (
          <div
            data-testid="release-notes-welcome-badge"
            style={{
              display: "inline-block",
              padding: "2px 8px",
              borderRadius: 6,
              fontSize: "var(--fs-xs)",
              fontWeight: 700,
              background: "#1e3a8a",
              color: "#fff",
              marginBottom: 8,
            }}
          >
            초기 안내 · 최신 릴리스 변경 내역 아님
          </div>
        )}

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

        {isWelcome && (
          <div
            data-testid="release-notes-welcome-disclaimer"
            style={{
              fontSize: "var(--fs-xs)",
              color: "var(--c-text-3)",
              marginBottom: 12,
              lineHeight: 1.5,
            }}
          >
            본 안내는 앱 첫 사용자에게 보여주는 *프로그램 소개* 입니다.
            새 릴리스 업데이트가 있으면 별도 UpdateBanner 에서 표시됩니다.
          </div>
        )}

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
          }}>{ackLabel}</button>
        </div>
      </div>
    </>
  );
}


/**
 * useReleaseNotesAutoPopup — 첫 접속 또는 새 버전 감지 시 자동으로 modal을
 * 열도록 상태 관리. App 최상단에 한 번만 mount.
 *
 * fix/update-banner-stale-release-notes: ack 비교 대상은 표시될 note 의
 * version. WELCOME 만 있을 때 latestWelcomeNote().version 과 비교.
 * 둘 다 없으면 자동 팝업 0건.
 */
export function useReleaseNotesAutoPopup() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const note = latestReleaseNote() || latestWelcomeNote();
    if (!note) return;  // 노출할 안내 자체가 없음 → 자동 팝업 0건.
    const last = _readLastSeenVersion();
    if (last !== note.version) {
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
