import { useCallback, useEffect, useState } from "react";

import {
  UPDATE_STATES,
  checkForUpdate,
  openUpdateUrl,
  sanitizeText,
} from "../desktop/updaterClient";

// Auto Update banner — A 단계: GitHub Release latest 조회 + 사용자에게 변경
// 내용 표시 + 수동 다운로드 링크. 자동 설치는 B 단계 (TAURI_PRIVATE_KEY 활성화)
// 에서 추가.
//
// 절대 invariant:
//   - 본 컴포넌트는 broker / OrderExecutor / 실거래 API 호출 0건
//   - 사용자 .env / Secret 을 *읽거나 쓰지* 않는다
//   - "업데이트" 버튼 = 새 release page 열기 (브라우저). 자동 설치 0건
//   - "실거래" / "매수" / "매도" / "Place Order" 라벨 0건 (테스트로 lock)
//
// fix/step5-stale-release-popup-guard (#5-04) — STATIC INVARIANT:
//   본 파일은 `../config/releaseNotes` 의 어떤 export 도 *import 하지 않는다*
//   (`RELEASE_NOTES` / `WELCOME_NOTES` / `latestReleaseNote` /
//   `latestWelcomeNote`). GitHub Release fetch 가 실패해도 *로컬 하드코딩 노트
//   (v1.0.0 / 2026-05-08 등)* 가 "최신 업데이트" 인 척 둔갑하지 않도록.
//   초기 안내 (welcome) 와 release update 는 별도 컴포넌트 (`ReleaseNotesModal`)
//   가 책임지며, 본 banner 는 *오직 fetch 결과* 만 표시한다. UpdateBanner.test.jsx
//   가 본 파일 소스에서 위 4개 export 의 import 부재를 정적 검증한다.

// fix/update-banner-stale-release-notes: fallback 은 의도적으로 부자연스러운
// "0.0.0-unknown" — vite.config.js 의 build-time inject 가 작동 안 하면 화면에서
// 즉시 감지 가능 (stale 1.0.0 이 표시되어 정상값처럼 보이지 않도록).
const CURRENT_VERSION_FALLBACK = "0.0.0-unknown";

function _readCurrentVersion() {
  // build-time inject (vite.config.js 의 define) — package.json::version.
  if (typeof import.meta !== "undefined" && import.meta.env) {
    const v = import.meta.env.VITE_APP_VERSION;
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return CURRENT_VERSION_FALLBACK;
}

export function UpdateBanner({
  currentVersion = _readCurrentVersion(),
  checkImpl = checkForUpdate,
  openImpl = openUpdateUrl,
  autoCheckOnMount = true,
  owner,
  repo,
} = {}) {
  const [state, setState] = useState(UPDATE_STATES.IDLE);
  const [result, setResult] = useState(null);
  const [dismissed, setDismissed] = useState(false);

  const runCheck = useCallback(async () => {
    setState(UPDATE_STATES.CHECKING);
    try {
      const r = await checkImpl({ currentVersion, owner, repo });
      setResult(r);
      setState(r.state);
    } catch (err) {
      setResult({ error: err?.message || String(err) });
      setState(UPDATE_STATES.FAILED);
    }
  }, [checkImpl, currentVersion, owner, repo]);

  useEffect(() => {
    if (autoCheckOnMount) runCheck();
  }, [autoCheckOnMount, runCheck]);

  const onApply = useCallback(() => {
    // A 단계: 수동 다운로드 페이지 열기. 자동 설치는 B 단계 (signing key).
    const url = result?.releaseUrl;
    if (url) openImpl(url);
  }, [openImpl, result]);

  const onLater = useCallback(() => setDismissed(true), []);

  if (dismissed) return null;

  // 안전 invariant 배지 — 어떤 상태에서도 노출.
  const _SafetyBadges = () => (
    <div data-testid="update-safety-badges" style={{ marginBottom: 6 }}>
      <span
        data-testid="badge-no-env-overwrite"
        style={{
          display: "inline-block",
          padding: "2px 8px",
          borderRadius: 6,
          fontSize: "var(--fs-xs)",
          background: "#1e3a8a",
          color: "#fff",
          marginRight: 6,
        }}
      >
        사용자 .env 보존
      </span>
      <span
        data-testid="badge-no-live-flag-change"
        style={{
          display: "inline-block",
          padding: "2px 8px",
          borderRadius: 6,
          fontSize: "var(--fs-xs)",
          background: "#0ea5e9",
          color: "#fff",
          marginRight: 6,
        }}
      >
        실거래 OFF 유지
      </span>
      <span
        data-testid="badge-not-order-trigger"
        style={{
          display: "inline-block",
          padding: "2px 8px",
          borderRadius: 6,
          fontSize: "var(--fs-xs)",
          background: "#6b7280",
          color: "#fff",
        }}
      >
        주문 기능 아님 · 앱 코드 업데이트만
      </span>
    </div>
  );

  if (state === UPDATE_STATES.IDLE || state === UPDATE_STATES.CHECKING) {
    return (
      <div
        data-testid="update-banner"
        data-state={state}
        style={{
          padding: "10px 14px",
          margin: "8px 12px",
          background: "#f8fafc",
          border: "1px solid #e2e8f0",
          borderRadius: "var(--r-md)",
          fontSize: "var(--fs-sm)",
        }}
      >
        <_SafetyBadges />
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span data-testid="update-current-version">
            현재 버전: <b>v{currentVersion}</b>
          </span>
          <span
            data-testid="update-checking-label"
            style={{ color: "var(--c-text-3)" }}
          >
            {state === UPDATE_STATES.CHECKING ? "새 버전 확인 중..." : ""}
          </span>
          <button
            data-testid="btn-update-check"
            onClick={runCheck}
            disabled={state === UPDATE_STATES.CHECKING}
            style={{
              marginLeft: "auto",
              padding: "4px 10px",
              borderRadius: "var(--r-md)",
              background: "#0ea5e9",
              color: "#fff",
              border: "none",
              cursor: state === UPDATE_STATES.CHECKING ? "wait" : "pointer",
              fontSize: "var(--fs-xs)",
            }}
          >
            업데이트 확인
          </button>
        </div>
      </div>
    );
  }

  if (state === UPDATE_STATES.UP_TO_DATE) {
    return (
      <div
        data-testid="update-banner"
        data-state={state}
        style={{
          padding: "10px 14px",
          margin: "8px 12px",
          background: "#f0fdf4",
          border: "1px solid #bbf7d0",
          borderRadius: "var(--r-md)",
          fontSize: "var(--fs-sm)",
          color: "#065f46",
        }}
      >
        <_SafetyBadges />
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span data-testid="update-uptodate">
            ✅ 최신 버전입니다 (v{currentVersion})
          </span>
          <button
            data-testid="btn-update-check"
            onClick={runCheck}
            style={{
              marginLeft: "auto",
              padding: "4px 10px",
              borderRadius: "var(--r-md)",
              background: "#fff",
              color: "#065f46",
              border: "1px solid #bbf7d0",
              cursor: "pointer",
              fontSize: "var(--fs-xs)",
            }}
          >
            다시 확인
          </button>
        </div>
      </div>
    );
  }

  if (state === UPDATE_STATES.UPDATE_AVAILABLE) {
    const notes = (result?.releaseNotes || "").slice(0, 600);
    return (
      <div
        data-testid="update-banner"
        data-state={state}
        style={{
          padding: "12px 16px",
          margin: "8px 12px",
          background: "#eff6ff",
          border: "1px solid #bfdbfe",
          borderRadius: "var(--r-lg)",
          fontSize: "var(--fs-sm)",
          color: "#1e3a8a",
        }}
      >
        <_SafetyBadges />
        <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
          🆕 새 버전이 있습니다 — v{result?.latestVersion}
        </div>
        <div style={{ marginBottom: 6, fontSize: "var(--fs-xs)" }}>
          새 버전이 있습니다. 업데이트하면 다음 실행부터 최신 기능이 반영됩니다.
          (현재 v{currentVersion} → v{result?.latestVersion})
        </div>
        {notes && (
          <details
            data-testid="update-release-notes"
            style={{
              background: "#fff",
              border: "1px solid #bfdbfe",
              borderRadius: "var(--r-md)",
              padding: "6px 10px",
              marginBottom: 8,
              fontSize: "var(--fs-xs)",
              color: "var(--c-text)",
            }}
          >
            <summary>변경 내용 보기</summary>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                margin: 0,
                marginTop: 6,
                fontFamily: "inherit",
              }}
            >
              {notes}
            </pre>
          </details>
        )}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            data-testid="btn-update-apply"
            onClick={onApply}
            style={{
              padding: "6px 14px",
              borderRadius: "var(--r-md)",
              background: "#2563eb",
              color: "#fff",
              border: "none",
              cursor: "pointer",
              fontWeight: "var(--fw-bold)",
              fontSize: "var(--fs-xs)",
            }}
          >
            업데이트 적용 (다운로드 페이지 열기)
          </button>
          {/* fix/step5-github-release-artifact-link (#5-05): GitHub Release
              에 setup.exe 가 첨부되어 있으면 *직접 다운로드 링크* 도 노출.
              자동 설치 0건 — `<a download>` 로 브라우저가 받는다. assets 가
              없거나 setup.exe 가 없으면 본 링크는 렌더되지 않는다. */}
          {result?.setupExeAsset?.downloadUrl && (
            <a
              data-testid="link-setup-exe-direct"
              href={result.setupExeAsset.downloadUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                padding: "6px 14px",
                borderRadius: "var(--r-md)",
                background: "#fff",
                color: "#1e3a8a",
                border: "1px solid #1e3a8a",
                textDecoration: "none",
                fontSize: "var(--fs-xs)",
                fontWeight: "var(--fw-bold)",
              }}
            >
              setup.exe 직접 받기
            </a>
          )}
          <button
            data-testid="btn-update-later"
            onClick={onLater}
            style={{
              padding: "6px 14px",
              borderRadius: "var(--r-md)",
              background: "#fff",
              color: "#1e3a8a",
              border: "1px solid #bfdbfe",
              cursor: "pointer",
              fontSize: "var(--fs-xs)",
            }}
          >
            나중에
          </button>
        </div>
        <div
          data-testid="update-restart-hint"
          style={{
            marginTop: 8,
            fontSize: "var(--fs-xs)",
            color: "var(--c-text-3)",
          }}
        >
          ⚠️ 업데이트 후 앱을 *재시작* 해야 새 기능이 반영됩니다. 업데이트는
          앱 코드만 갱신하며, 사용자가 입력한 .env / API 키는 그대로 보존됩니다.
        </div>
      </div>
    );
  }

  // FAILED — GitHub Release fetch 실패.
  // fix/update-banner-stale-release-notes: 메시지를 "업데이트 확인 실패" 에서
  // "최신 버전 확인 불가" 로 변경 — backend 연결 실패와 *명확히 다른* 항목
  // 임을 사용자에게 전달. 또한 이 상태에서는 *어떠한 릴리스 노트도* 표시하지
  // 않는다 (RELEASE_NOTES 의 stale 안내가 "최신 업데이트"로 둔갑하지 않도록).
  return (
    <div
      data-testid="update-banner"
      data-state={state}
      style={{
        padding: "10px 14px",
        margin: "8px 12px",
        background: "#f1f5f9",
        border: "1px solid #cbd5e1",
        borderRadius: "var(--r-md)",
        fontSize: "var(--fs-sm)",
        color: "#334155",
      }}
    >
      <_SafetyBadges />
      <div data-testid="update-fail-headline" style={{ marginBottom: 4, fontWeight: "var(--fw-bold)" }}>
        ℹ️ 최신 버전 확인 불가
      </div>
      <div data-testid="update-fail-detail" style={{ marginBottom: 6, fontSize: "var(--fs-xs)" }}>
        GitHub Release 정보를 가져오지 못했습니다. 네트워크 단절이거나 아직
        공개된 Release 가 없을 수 있습니다. 현재 설치된 버전(v{currentVersion})은
        그대로 사용 가능합니다.
      </div>
      <div
        data-testid="update-fail-not-backend"
        style={{
          marginBottom: 6,
          fontSize: "var(--fs-xs)",
          color: "var(--c-text-3)",
        }}
      >
        ※ 이 메시지는 *백엔드 연결 실패와는 다른 별개 항목* 입니다 (앱 코드 업데이트 확인 전용).
      </div>
      <details
        data-testid="update-fail-tech-detail"
        style={{ marginBottom: 6, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}
      >
        <summary>기술 상세</summary>
        {/* fix/step5-stale-release-popup-guard: error 문자열에 token / secret
            패턴이 섞여 있을 경우 raw 노출 방지를 위해 sanitizeText 적용.
            GitHub API 가 보낼 일은 없지만 fork / proxy 변조 케이스 대비. */}
        <div style={{ marginTop: 4 }}>
          error: {sanitizeText(result?.error || "unknown error")}
        </div>
      </details>
      <div data-testid="update-manual-download" style={{ marginBottom: 6, fontSize: "var(--fs-xs)" }}>
        새 버전 공지가 있는지 GitHub Release 페이지에서 직접 확인할 수 있습니다.
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button
          data-testid="btn-update-check"
          onClick={runCheck}
          style={{
            padding: "4px 10px",
            borderRadius: "var(--r-md)",
            background: "#0ea5e9",
            color: "#fff",
            border: "none",
            cursor: "pointer",
            fontSize: "var(--fs-xs)",
          }}
        >
          다시 시도
        </button>
        <a
          data-testid="link-manual-download"
          href="https://github.com/1976haru/autotrade/releases"
          target="_blank"
          rel="noopener noreferrer"
          style={{
            padding: "4px 10px",
            borderRadius: "var(--r-md)",
            background: "#fff",
            color: "#334155",
            border: "1px solid #cbd5e1",
            textDecoration: "none",
            fontSize: "var(--fs-xs)",
          }}
        >
          GitHub Release 페이지 열기
        </a>
      </div>
    </div>
  );
}

export default UpdateBanner;
