/**
 * 체크리스트 #63: Service Worker 등록 helper.
 *
 * 절대 원칙:
 *   - 본 모듈은 broker / 주문 / Secret 어떤 것도 다루지 않는다.
 *   - 실패해도 앱이 깨지지 않게 try/catch + 조용한 console warn.
 *   - import.meta.env.BASE_URL을 prefix로 사용해 GitHub Pages /autotrade/ 와
 *     로컬 dev / 두 경로 모두에서 작동.
 *
 * 외부 export:
 *   - registerServiceWorker(options?) — 메인 진입점.
 *   - unregisterServiceWorker() — 디버깅 / 테스트용. 운영자 토글 X.
 *   - computeSwUrl(baseUrl?) — 등록 경로 계산 — 단위 테스트에서 사용.
 */


/**
 * BASE_URL + "sw.js" 결합. baseUrl 끝에 슬래시가 없거나 빈 문자열인 경우
 * 정규화. 단위 테스트가 본 함수를 직접 호출.
 */
export function computeSwUrl(baseUrl) {
  const raw = (typeof baseUrl === "string" && baseUrl.length > 0)
    ? baseUrl
    : "/";
  const withSlash = raw.endsWith("/") ? raw : `${raw}/`;
  return `${withSlash}sw.js`;
}


/**
 * 브라우저가 Service Worker를 지원하고 운영 환경(http/https)일 때만 등록.
 * file:// 또는 SW 미지원 환경에서는 noop + console warn.
 *
 * options:
 *   - baseUrl:   override (default: import.meta.env.BASE_URL)
 *   - onError:   콜백 (테스트 / 운영 시 별도 logger 주입 가능)
 *   - log:       콘솔 로깅 enable (default: development 환경에서만 true)
 *
 * 반환: Promise<ServiceWorkerRegistration | null>
 *   - 등록 성공: registration
 *   - SW 미지원 / 실패: null (앱은 그대로 정상 동작)
 */
export async function registerServiceWorker(options = {}) {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    return null;
  }
  // SW는 http/https에서만 작동. file:// / 일부 chrome-extension 환경 회피.
  if (typeof window !== "undefined" && window.location
      && window.location.protocol !== "https:"
      && window.location.protocol !== "http:") {
    return null;
  }

  const baseUrl = options.baseUrl
    ?? (typeof import.meta !== "undefined" ? import.meta.env?.BASE_URL : null)
    ?? "/";
  const swUrl = computeSwUrl(baseUrl);
  const log = options.log
    ?? (typeof import.meta !== "undefined" && import.meta.env?.DEV);

  try {
    const registration = await navigator.serviceWorker.register(swUrl, {
      scope: baseUrl,
    });
    if (log) {
      console.log("[PWA] Service Worker registered at", swUrl,
                  "scope:", registration.scope);
    }
    return registration;
  } catch (err) {
    // 등록 실패는 *치명적이지 않다* — 앱은 계속 작동하고 PWA 기능만 비활성.
    console.warn("[PWA] Service Worker 등록 실패:", err?.message || err);
    if (typeof options.onError === "function") {
      try { options.onError(err); } catch { /* noop */ }
    }
    return null;
  }
}


/**
 * 등록된 SW를 모두 해제 + 캐시 정리. 디버깅용. 운영자가 일반적으로 호출할
 * 일은 없다 — 새 SW 버전이 deploy되면 SW_VERSION이 올라가서 activate에서
 * 자동으로 이전 캐시가 정리된다.
 */
export async function unregisterServiceWorker() {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    return false;
  }
  try {
    const regs = await navigator.serviceWorker.getRegistrations();
    await Promise.all(regs.map((r) => r.unregister()));
    if (typeof caches !== "undefined") {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
    return true;
  } catch (err) {
    console.warn("[PWA] Service Worker 해제 실패:", err?.message || err);
    return false;
  }
}
