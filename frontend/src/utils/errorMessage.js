// 233 (UI-005): 에러 문구 정규화. 백엔드의 raw 'Failed to fetch'나 stack
// trace 문구를 사용자에게 그대로 노출하지 않는다. 모든 호출자가 같은 분기를
// 거쳐 일관된 메시지가 나가도록.
//
// isDemoBuild 헬퍼는 BackendOfflineBanner의 것을 재사용 — Pages 빌드에서는
// "GitHub Pages 데모..." 안내, 로컬은 "uvicorn 실행 가이드".

const _NETWORK_PHRASES = [
  "Failed to fetch",
  "NetworkError",
  "Network Error",
  "ERR_NETWORK",
  "TypeError",
  "Load failed",
];

export function isDemoBuild() {
  if (typeof import.meta === "undefined") return false;
  const v = import.meta.env?.VITE_DEMO_MODE;
  return v === "true" || v === true;
}

/**
 * Convert a raw error message to an operator-friendly hint.
 *
 * - 빈 입력 → null (호출자는 hint 없이 ErrorState만 보여주면 됨).
 * - 네트워크 류 ('Failed to fetch' 등) → Demo Mode인지에 따라 다른 안내.
 * - 그 외 → 원문 그대로 (백엔드가 의미 있는 한국어 메시지를 줬을 가능성).
 *
 * 호출자는 ErrorState의 hint prop으로 직접 전달.
 */
export function friendlyErrorMessage(rawError) {
  if (rawError == null) return null;
  const msg = typeof rawError === "string" ? rawError : (rawError.message || String(rawError));
  if (!msg) return null;

  const isNetwork = _NETWORK_PHRASES.some((p) => msg.includes(p));
  if (isNetwork) {
    if (isDemoBuild()) {
      return "GitHub Pages 데모에서는 백엔드가 없어 mock 데이터만 표시됩니다. 전체 기능은 로컬에서 backend(uvicorn) + frontend(npm run dev)를 함께 실행해 사용하세요.";
    }
    return "백엔드 연결이 끊겼습니다. 'uvicorn app.main:app --reload'를 실행한 후 새로고침 해주세요.";
  }
  // 기타 — 백엔드 의미 메시지로 추정. 운영자가 그대로 봐도 도움.
  return msg;
}
