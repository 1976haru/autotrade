// Backend log reader — Tauri `invoke("read_backend_log")` 의 wrapper.
//
// EXE 모드에서 sidecar 가 살아나지 않을 때 사용자가 *왜* 실패했는지 한눈에
// 확인할 수 있게 한다. Rust 단 (`src-tauri/src/lib.rs`) 이 sidecar stdout /
// stderr / exit code 를 %APPDATA%/Autotrade/logs/desktop-backend.log 파일에
// 기록하며, 본 모듈은 그 내용을 frontend 에 *sanitized* 형태로 전달.
//
// CLAUDE.md 절대 원칙:
//   - broker / OrderExecutor 호출 0건
//   - secret 패턴 (sk-*/ghp_*/Bearer */xox*) 발견 시 [REDACTED] 마스킹
//   - 파일에 직접 write 0건 — read-only

const _SECRET_PATTERNS = [
  /sk-[A-Za-z0-9_\-]{16,}/g,
  /sk-ant-[A-Za-z0-9_\-]{16,}/g,
  /ghp_[A-Za-z0-9]{30,}/g,
  /xox[abps]-[A-Za-z0-9-]{10,}/g,
  /Bearer\s+[A-Za-z0-9._\-]{20,}/g,
  // KIS app key / secret 같은 *환경 변수 라벨* 값 — 라벨 뒤 long token 패턴.
  /(KIS_APP_KEY|KIS_APP_SECRET|KIS_ACCOUNT_NO|ANTHROPIC_API_KEY|OPENAI_API_KEY|TELEGRAM_BOT_TOKEN)\s*[=:]\s*["']?[A-Za-z0-9_\-]{10,}["']?/g,
];

export function sanitizeLogText(s) {
  if (typeof s !== "string") return "";
  let out = s;
  for (const pat of _SECRET_PATTERNS) {
    out = out.replace(pat, (match) => {
      // KIS_* / ANTHROPIC_API_KEY 같은 패턴은 *라벨* 만 보존하고 값만 마스킹.
      const eqIdx = match.indexOf("=");
      const colIdx = match.indexOf(":");
      const sep = eqIdx >= 0 ? "=" : (colIdx >= 0 ? ":" : null);
      if (sep) {
        const label = match.slice(0, match.indexOf(sep) + 1);
        return `${label}[REDACTED]`;
      }
      return "[REDACTED]";
    });
  }
  return out;
}

/**
 * Tauri command `read_backend_log` 호출. Tauri 가 아닐 경우 null 반환 —
 * caller 가 "데스크톱 모드에서만 가능" UX 결정.
 */
export async function readBackendLog({ invokeImpl } = {}) {
  // 명시적 invokeImpl 주입 우선 (테스트 용).
  if (typeof invokeImpl === "function") {
    try {
      const raw = await invokeImpl("read_backend_log");
      return sanitizeLogText(String(raw ?? ""));
    } catch (err) {
      return `(invoke error: ${err?.message || err})`;
    }
  }

  // 실 Tauri v2 환경 — `window.__TAURI_INTERNALS__.invoke` 가능.
  if (typeof window !== "undefined" && window.__TAURI_INTERNALS__) {
    const internals = window.__TAURI_INTERNALS__;
    // Tauri v2 internals 의 invoke 는 `invoke(cmd)` 형태.
    if (typeof internals.invoke === "function") {
      try {
        const raw = await internals.invoke("read_backend_log");
        return sanitizeLogText(String(raw ?? ""));
      } catch (err) {
        return `(invoke error: ${err?.message || err})`;
      }
    }
  }

  // 브라우저 / 비-Tauri 환경 — null. caller 가 UX 처리.
  return null;
}

export function isBackendLogAvailable() {
  if (typeof window === "undefined") return false;
  return Boolean(window.__TAURI_INTERNALS__);
}
