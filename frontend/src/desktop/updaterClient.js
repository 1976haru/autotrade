// Tauri Auto Updater client (A 단계 — UI/UX + release check).
//
// A 단계: GitHub Releases REST API 로 latest 버전을 *조회*만 한다 — 실제 자동
// 설치는 Tauri updater plugin 활성화 (B 단계, TAURI_PRIVATE_KEY 준비 후) 까지
// 보류. 본 단계에서 "업데이트 적용" 은 GitHub Release 페이지를 *브라우저로*
// 여는 fallback 동작.
//
// 절대 원칙 (CLAUDE.md):
//   - broker / OrderExecutor / route_order 호출 0건
//   - 사용자 .env / Secret / API key 를 *읽지도 쓰지도 않는다*
//   - 응답에서 Secret 패턴 발견 시 redacted 처리 (방어적 — GitHub API 는
//     보내지 않지만 응답이 변조될 경우 대비)
//   - 자동 *재시작* / *파일 덮어쓰기* 0건 — A 단계는 안내만
//
// fix/step5-stale-release-popup-guard (#5-04) — STATIC INVARIANT:
//   본 파일은 `../config/releaseNotes` 를 *import 하지 않는다*. GitHub Release
//   응답이 없으면 `state: FAILED + error` 만 반환하며, 로컬 하드코딩 노트는
//   절대 결과에 섞이지 않는다 (test 로 lock).

export const UPDATE_STATES = Object.freeze({
  IDLE:              "IDLE",
  CHECKING:          "CHECKING",
  UP_TO_DATE:        "UP_TO_DATE",
  UPDATE_AVAILABLE:  "UPDATE_AVAILABLE",
  FAILED:            "FAILED",
});

// 기본 repo. 운영자가 다른 fork 를 사용하면 env 또는 prop 으로 override.
const DEFAULT_OWNER = "1976haru";
const DEFAULT_REPO  = "autotrade";
const DEFAULT_TIMEOUT_MS = 8_000;

// SemVer 비교 — "1.0.10" > "1.0.2" 같은 정렬 정확.
// "v1.0.1" / "1.0.1" / "1.0.1-beta.3" 모두 허용. tag prefix 'v' 제거.
export function parseVersion(s) {
  if (typeof s !== "string") return null;
  const cleaned = s.trim().replace(/^v/i, "");
  if (!cleaned) return null;
  // pre-release / build metadata 분리.
  const [main, ...rest] = cleaned.split("-");
  const parts = main.split(".").map((p) => {
    const n = parseInt(p, 10);
    return Number.isFinite(n) ? n : 0;
  });
  while (parts.length < 3) parts.push(0);
  return {
    parts:       parts.slice(0, 3),
    pre:         rest.join("-") || "",
    raw:         cleaned,
  };
}

/**
 * a > b 면 1, a < b 면 -1, 같으면 0. invalid 입력은 0.
 * pre-release 는 release 보다 *낮은* 우선순위 (SemVer 규칙).
 */
export function compareVersions(a, b) {
  const pa = parseVersion(a);
  const pb = parseVersion(b);
  if (!pa || !pb) return 0;
  for (let i = 0; i < 3; i += 1) {
    if (pa.parts[i] > pb.parts[i]) return 1;
    if (pa.parts[i] < pb.parts[i]) return -1;
  }
  // pre-release 있는 쪽이 낮음.
  if (pa.pre && !pb.pre) return -1;
  if (!pa.pre && pb.pre) return 1;
  if (pa.pre < pb.pre) return -1;
  if (pa.pre > pb.pre) return 1;
  return 0;
}

/**
 * isNewer(latest, current) — latest 가 current 보다 newer 면 true.
 */
export function isNewer(latest, current) {
  return compareVersions(latest, current) > 0;
}

// 방어적 sanitization — 응답이 변조된 경우에도 secret 패턴은 표시 0건.
// 본 함수는 release body / asset url 등의 *문자열* 만 통과. 입력에 키 패턴이
// 있으면 redacted. 실 GitHub API 는 secret 을 보내지 않지만 fork / proxy
// 케이스를 위한 방어.
const _SECRET_PATTERNS = [
  /sk-[A-Za-z0-9_\-]{16,}/g,               // OpenAI / Anthropic style
  /sk-ant-[A-Za-z0-9_\-]{16,}/g,
  /ghp_[A-Za-z0-9]{30,}/g,                  // GitHub PAT
  /xox[abps]-[A-Za-z0-9-]{10,}/g,           // Slack
  /Bearer\s+[A-Za-z0-9._\-]{20,}/g,         // Bearer tokens
];
export function sanitizeText(s) {
  if (typeof s !== "string") return "";
  let out = s;
  for (const pat of _SECRET_PATTERNS) {
    out = out.replace(pat, "[REDACTED]");
  }
  return out;
}

/**
 * GitHub releases/latest 조회. 본 함수는 read-only — install / 재시작 / 파일
 * 쓰기 0건.
 *
 * 반환: { ok, version, name, body, htmlUrl, publishedAt, assets, raw? }
 * 실패: { ok: false, error }
 */
export async function fetchLatestRelease({
  owner = DEFAULT_OWNER,
  repo  = DEFAULT_REPO,
  fetchImpl = (typeof globalThis !== "undefined" && globalThis.fetch),
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  if (typeof fetchImpl !== "function") {
    return { ok: false, error: "fetch not available" };
  }
  const url = `https://api.github.com/repos/${owner}/${repo}/releases/latest`;
  let controller, timer;
  if (typeof AbortController !== "undefined") {
    controller = new AbortController();
    timer = setTimeout(() => controller.abort(), timeoutMs);
  }
  try {
    const res = await fetchImpl(url, {
      headers: { Accept: "application/vnd.github+json" },
      signal: controller?.signal,
    });
    if (!res || !res.ok) {
      return { ok: false, error: `http ${res?.status}` };
    }
    const j = await res.json();
    // 응답 필드 추출 — 알 수 없는 필드는 무시. secret 의심 패턴은 redact.
    return {
      ok: true,
      version: j.tag_name || "",
      name:    j.name || "",
      body:    sanitizeText(j.body || ""),
      htmlUrl: j.html_url || "",
      publishedAt: j.published_at || "",
      assets:  Array.isArray(j.assets)
        ? j.assets
            .filter((a) => a && a.name && a.browser_download_url)
            .map((a) => ({
              name: String(a.name),
              size: Number(a.size) || 0,
              downloadUrl: String(a.browser_download_url),
            }))
        : [],
    };
  } catch (err) {
    return { ok: false, error: err?.message || String(err) };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * checkForUpdate({ currentVersion }) — 단일 probe.
 *
 * 반환:
 *   { state: UP_TO_DATE | UPDATE_AVAILABLE | FAILED,
 *     currentVersion, latestVersion?, releaseUrl?, releaseNotes?,
 *     setupExeAsset?, error? }
 *
 * 본 함수는 *어떤 파일도 덮어쓰지 않는다* — 응답 데이터만 carry.
 */
export async function checkForUpdate({
  currentVersion,
  owner,
  repo,
  fetchImpl,
  timeoutMs,
} = {}) {
  const res = await fetchLatestRelease({ owner, repo, fetchImpl, timeoutMs });
  if (!res.ok) {
    return {
      state:         UPDATE_STATES.FAILED,
      currentVersion: currentVersion || "",
      error:          res.error || "unknown error",
    };
  }
  const latest = res.version;
  // *-setup.exe asset 우선 — Windows installer.
  const setupAsset =
    res.assets.find((a) => /-setup\.exe$/i.test(a.name))
    || res.assets.find((a) => /\.exe$/i.test(a.name))
    || res.assets[0]
    || null;

  if (!isNewer(latest, currentVersion)) {
    return {
      state:         UPDATE_STATES.UP_TO_DATE,
      currentVersion: currentVersion || "",
      latestVersion:  latest,
      releaseUrl:     res.htmlUrl,
      publishedAt:    res.publishedAt,
    };
  }
  return {
    state:         UPDATE_STATES.UPDATE_AVAILABLE,
    currentVersion: currentVersion || "",
    latestVersion:  latest,
    releaseUrl:     res.htmlUrl,
    releaseNotes:   res.body,
    publishedAt:    res.publishedAt,
    setupExeAsset:  setupAsset,
  };
}

/**
 * "업데이트 적용" 클릭 시 호출. A 단계에서는 *수동 다운로드 링크 열기* 만 —
 * 실제 자동 설치는 Tauri updater plugin (B 단계) 에서.
 *
 * 본 함수는 broker / OrderExecutor / .env 어떤 파일도 변경하지 않는다.
 */
export function openUpdateUrl(url, { windowImpl } = {}) {
  if (!url || typeof url !== "string") return false;
  const w = windowImpl || (typeof window !== "undefined" ? window : null);
  if (!w || typeof w.open !== "function") return false;
  // noopener / noreferrer — 부모 컨텍스트 누출 방지.
  w.open(url, "_blank", "noopener,noreferrer");
  return true;
}
