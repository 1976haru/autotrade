/**
 * 체크리스트 #63: Service Worker — *모바일 관제* PWA용.
 *
 * 절대 원칙 (CLAUDE.md):
 *   1. API 응답을 *절대* 캐시하지 않는다. /api/* 요청은 모두 network-only.
 *   2. 계좌 정보 / 주문 정보 / Secret / KIS / Anthropic 토큰 캐시 0건.
 *   3. 오프라인 상태에서 주문이 가능한 것처럼 보이게 *하지 않는다* —
 *      API 호출 실패 시 503 + JSON 안내로 응답해 클라이언트가 "백엔드 연결 필요"
 *      메시지를 표시하도록 한다.
 *   4. Push 알림 / Background Sync / Periodic Sync 어떤 것도 등록하지 않는다.
 *   5. 정적 자산(HTML / JS / CSS / SVG / manifest)만 캐시 — 그것도 SW 버전이
 *      올라가면 즉시 폐기.
 *
 * 캐시 전략:
 *   - install 시 핵심 정적 자산을 PRECACHE에 담는다 (offline fallback용 최소).
 *   - fetch 시:
 *     a) /api/* / WebSocket → network-only, 실패 시 503 sentinel JSON
 *     b) Navigation (mode === 'navigate') → network-first, 실패 시 offline.html
 *     c) 같은 origin의 정적 자산 → stale-while-revalidate (캐시 우선 + 백그라
 *        운드 갱신). 자산 URL이 hash 기반(vite 빌드)이라 stale serving이 위험
 *        하지 않음 — 다음 deploy에서 hash가 바뀌면 새 파일을 받음.
 *     d) 외부 origin → 그대로 통과 (캐시 X)
 *   - activate 시 이전 버전 캐시 모두 정리.
 *
 * 본 SW는 빌드 시 hash가 없는 정적 경로(`/sw.js`)에 그대로 서빙된다 — vite의
 * public/ 폴더 패턴. 캐시 버전은 본 파일의 SW_VERSION 상수로 관리.
 */

// 캐시 버전 — 코드 / 정책이 바뀌면 이 값을 올린다. 이전 캐시는 activate에서 폐기.
const SW_VERSION = "agent-trader-v1";
const PRECACHE   = `${SW_VERSION}-precache`;
const RUNTIME    = `${SW_VERSION}-runtime`;

// install 시 즉시 가져올 최소 자산. base URL은 manifest와 동일한 상대 경로 패턴.
// (GitHub Pages /autotrade/ 와 로컬 dev / 모두에서 동작하도록 self.registration.scope를
//  기준으로 계산.)
const PRECACHE_PATHS = [
  "./",
  "./index.html",
  "./offline.html",
  "./manifest.webmanifest",
  "./favicon.svg",
  "./icons/icon-192.svg",
  "./icons/icon-512.svg",
  "./icons/maskable-512.svg",
];


// ---------- install ----------

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(PRECACHE).then((cache) =>
      // 개별 add 실패가 install 전체를 망치지 않도록 Promise.allSettled.
      Promise.allSettled(PRECACHE_PATHS.map((p) =>
        cache.add(new Request(p, { cache: "reload" })),
      )),
    ).then(() => self.skipWaiting()),
  );
});


// ---------- activate ----------

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys
        .filter((k) => k !== PRECACHE && k !== RUNTIME)
        .map((k) => caches.delete(k)),
      ),
    ).then(() => self.clients.claim()),
  );
});


// ---------- helpers ----------

function isApiRequest(url) {
  // /api 로 시작하면 무조건 API. WebSocket (ws/wss) 도 같은 취급.
  if (url.pathname.startsWith("/api/")) return true;
  if (url.pathname.includes("/api/")) return true;
  return false;
}

function isWebSocket(request) {
  return request.headers.get("upgrade") === "websocket";
}

function isHtmlNavigation(request) {
  return request.mode === "navigate"
      || (request.method === "GET"
          && (request.headers.get("accept") || "").includes("text/html"));
}

function sameOrigin(url) {
  return url.origin === self.location.origin;
}


// /api/* 요청이 오프라인일 때 반환할 sentinel JSON.
function apiOfflineResponse() {
  const body = JSON.stringify({
    error:   "network_offline",
    detail:  "백엔드 연결이 필요합니다. 네트워크 또는 backend(uvicorn) 상태를 확인하세요.",
    offline: true,
  });
  return new Response(body, {
    status: 503,
    statusText: "Service Unavailable (offline)",
    headers: {
      "Content-Type":  "application/json; charset=utf-8",
      "X-Pwa-Offline": "true",
    },
  });
}


// ---------- fetch ----------

self.addEventListener("fetch", (event) => {
  const request = event.request;
  // POST / PUT / DELETE는 캐시 의미가 없고, idempotent 가정도 안 맞으므로
  // 항상 네트워크로 직행 — 실패 시 sentinel 응답.
  if (request.method !== "GET") {
    if (isApiRequest(new URL(request.url))) {
      event.respondWith(fetch(request).catch(() => apiOfflineResponse()));
    }
    return;  // 그 외 비-GET은 SW가 가로채지 않는다 (보수적)
  }

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }

  // 다른 origin은 통과 — 캐시도 안 함.
  if (!sameOrigin(url)) return;

  // WebSocket — 절대 캐시 안 함.
  if (isWebSocket(request)) return;

  // 1) /api/* — network-only. 실패 시 503 sentinel JSON (캐시 안 함).
  if (isApiRequest(url)) {
    event.respondWith(
      fetch(request, { cache: "no-store" }).catch(() => apiOfflineResponse()),
    );
    return;
  }

  // 2) Navigation — network-first, 실패 시 offline.html (캐시 안 함).
  if (isHtmlNavigation(request)) {
    event.respondWith(
      fetch(request).then((response) => {
        // 정상 응답은 그대로 반환 — HTML은 캐시하지 않는다 (always-fresh).
        return response;
      }).catch(async () => {
        const offline = await caches.match("./offline.html");
        return offline || new Response(
          "<h1>오프라인</h1><p>백엔드 연결이 필요합니다.</p>",
          { headers: { "Content-Type": "text/html; charset=utf-8" } },
        );
      }),
    );
    return;
  }

  // 3) 동일 origin 정적 자산 — stale-while-revalidate.
  //    매니페스트 / 아이콘 / vite hashed assets / favicon 등이 대상.
  event.respondWith(
    caches.match(request).then((cached) => {
      const fetchPromise = fetch(request).then((networkResp) => {
        // 200대 응답만 캐시. opaque(외부 cors 미허용) 등은 캐시 안 함.
        if (networkResp && networkResp.status === 200
            && networkResp.type === "basic") {
          const copy = networkResp.clone();
          caches.open(RUNTIME).then((cache) => cache.put(request, copy))
            .catch(() => undefined);
        }
        return networkResp;
      }).catch(() => undefined);
      return cached || fetchPromise || apiOfflineResponse();
    }),
  );
});


// ---------- message: SKIP_WAITING for update prompts ----------
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});


// ====================================================================
// Invariants (lock by code review + tests):
//
// - Push API: 본 파일은 SW 의 push subscription 관리자나 'push' event
//   listener 를 *등록하지 않는다*. 푸시 알림은 별도 보안 검토 후에만 도입.
// - Background Sync / Periodic Sync: sync registration 호출 0건.
// - Notification API: notification 표시 호출 0건.
// - LocalStorage / IndexedDB / cookie 접근 0건 (SW context에선 localStorage
//   접근 불가지만, IndexedDB는 가능 — 본 파일은 그것도 쓰지 않는다).
// - API 응답 / 계좌 / 주문 / Secret 캐시 0건 — /api/* 분기에서 *전혀* put을
//   호출하지 않으며, network-only 처리 후 503 sentinel만 반환.
// ====================================================================
