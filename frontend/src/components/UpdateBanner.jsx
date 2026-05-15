/* eslint-disable react/prop-types */
import { useCallback, useEffect, useState } from "react";

import {
  UPDATE_STATES,
  checkForUpdate,
  openUpdateUrl,
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

const CURRENT_VERSION_FALLBACK = "1.0.0";

function _readCurrentVersion() {
  // import.meta.env.VITE_APP_VERSION 또는 build-time inject. 없으면 fallback.
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

  // FAILED
  return (
    <div
      data-testid="update-banner"
      data-state={state}
      style={{
        padding: "10px 14px",
        margin: "8px 12px",
        background: "#fffbeb",
        border: "1px solid #fde68a",
        borderRadius: "var(--r-md)",
        fontSize: "var(--fs-sm)",
        color: "#7c2d12",
      }}
    >
      <_SafetyBadges />
      <div style={{ marginBottom: 6 }}>
        ⚠ 업데이트 확인 실패: {result?.error || "unknown error"}
      </div>
      <div data-testid="update-manual-download" style={{ marginBottom: 6, fontSize: "var(--fs-xs)" }}>
        수동 다운로드 페이지에서 최신 버전을 받을 수 있습니다.
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
            color: "#7c2d12",
            border: "1px solid #fde68a",
            textDecoration: "none",
            fontSize: "var(--fs-xs)",
          }}
        >
          수동 다운로드 페이지 열기
        </a>
      </div>
    </div>
  );
}

export default UpdateBanner;
