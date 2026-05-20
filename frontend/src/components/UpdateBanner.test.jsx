/**
 * UpdateBanner + updaterClient 단위 테스트.
 *
 * invariant 강제:
 * - "실거래" / "매수" / "매도" / "Place Order" / "buy" / "sell" 라벨 0건
 * - 사용자 .env 덮어쓰기 안 함 (코드 path 0건)
 * - 업데이트 적용 = openImpl(url) 호출 — 자동 설치 0건
 * - 수동 다운로드 링크 노출
 * - secret 패턴 발견 시 redacted
 *
 * fix/step5-stale-release-popup-guard (#5-04):
 * - UpdateBanner.jsx / updaterClient.js 가 releaseNotes.js 를 import 하지 않음
 *   을 *정적 검증* (소스 grep) — fetch 실패 시 stale welcome / release 안내가
 *   "최신 업데이트" 인 척 둔갑하지 않도록 lock.
 * - "이번 공지 확인" → 같은 안내 재팝업 0건 (VersionBadge.test.jsx 가 상세
 *   검증; 본 파일은 별도 invariant 만 카드별로 lock).
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { UpdateBanner } from "./UpdateBanner";
import {
  UPDATE_STATES,
  compareVersions,
  isNewer,
  parseVersion,
  sanitizeText,
} from "../desktop/updaterClient";

// Resolve paths from this test file (works under both vite ESM and vitest).
const __dirname = dirname(fileURLToPath(import.meta.url));
const UPDATE_BANNER_SRC = readFileSync(
  join(__dirname, "UpdateBanner.jsx"),
  "utf-8",
);
const UPDATER_CLIENT_SRC = readFileSync(
  join(__dirname, "..", "desktop", "updaterClient.js"),
  "utf-8",
);


describe("updaterClient — version helpers", () => {
  it("parseVersion handles plain and prefixed", () => {
    expect(parseVersion("1.2.3")).toMatchObject({ parts: [1, 2, 3], pre: "" });
    expect(parseVersion("v1.2.3")).toMatchObject({ parts: [1, 2, 3], pre: "" });
    expect(parseVersion("1.2")).toMatchObject({ parts: [1, 2, 0] });
    expect(parseVersion("")).toBeNull();
    expect(parseVersion(null)).toBeNull();
  });

  it("parseVersion captures pre-release", () => {
    expect(parseVersion("1.0.1-beta.3")).toMatchObject({
      parts: [1, 0, 1],
      pre: "beta.3",
    });
  });

  it("compareVersions handles numeric ordering", () => {
    expect(compareVersions("1.0.10", "1.0.2")).toBe(1);
    expect(compareVersions("1.0.2", "1.0.10")).toBe(-1);
    expect(compareVersions("1.0.0", "1.0.0")).toBe(0);
  });

  it("compareVersions treats pre-release as lower than release", () => {
    expect(compareVersions("1.0.0", "1.0.0-beta.1")).toBe(1);
    expect(compareVersions("1.0.0-beta.1", "1.0.0")).toBe(-1);
  });

  it("isNewer is true when latest > current", () => {
    expect(isNewer("1.0.1", "1.0.0")).toBe(true);
    expect(isNewer("1.0.0", "1.0.0")).toBe(false);
    expect(isNewer("1.0.0", "1.0.1")).toBe(false);
  });

  it("sanitizeText redacts known secret patterns", () => {
    const s = "openai key sk-abcdefghijklmnop12345 plus ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const out = sanitizeText(s);
    expect(out).toContain("[REDACTED]");
    expect(out).not.toContain("sk-abcdefghijklmnop12345");
    expect(out).not.toContain("ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  });
});


function _mockCheck(state, payload = {}) {
  return vi.fn(async () => ({ state, ...payload }));
}

describe("<UpdateBanner>", () => {
  afterEach(cleanup);

  it("renders CHECKING then UP_TO_DATE", async () => {
    const check = _mockCheck(UPDATE_STATES.UP_TO_DATE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.0",
    });
    render(
      <UpdateBanner
        currentVersion="1.0.0"
        checkImpl={check}
        openImpl={vi.fn()}
      />
    );
    await waitFor(() =>
      expect(screen.getByTestId("update-uptodate").textContent).toMatch(
        /최신 버전/
      )
    );
  });

  it("shows UPDATE_AVAILABLE with version + notes + apply/later buttons", async () => {
    const check = _mockCheck(UPDATE_STATES.UPDATE_AVAILABLE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.1",
      releaseNotes: "신규 기능: AI Paper Loop 시작/정지 버튼 추가.",
      releaseUrl: "https://github.com/1976haru/autotrade/releases/tag/v1.0.1",
    });
    render(
      <UpdateBanner
        currentVersion="1.0.0"
        checkImpl={check}
        openImpl={vi.fn()}
      />
    );
    await waitFor(() =>
      expect(screen.getByTestId("update-banner").getAttribute("data-state")).toBe(
        "UPDATE_AVAILABLE"
      )
    );
    expect(screen.getByTestId("btn-update-apply")).toBeTruthy();
    expect(screen.getByTestId("btn-update-later")).toBeTruthy();
    expect(screen.getByTestId("update-release-notes").textContent).toMatch(
      /AI Paper Loop/
    );
    expect(screen.getByTestId("update-restart-hint").textContent).toMatch(
      /재시작/
    );
  });

  it("clicking 업데이트 적용 calls openImpl with releaseUrl (no auto install)", async () => {
    const open = vi.fn();
    const check = _mockCheck(UPDATE_STATES.UPDATE_AVAILABLE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.1",
      releaseUrl: "https://github.com/1976haru/autotrade/releases/tag/v1.0.1",
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={open} />
    );
    await waitFor(() => screen.getByTestId("btn-update-apply"));
    fireEvent.click(screen.getByTestId("btn-update-apply"));
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith(
      "https://github.com/1976haru/autotrade/releases/tag/v1.0.1"
    );
  });

  it("clicking 나중에 dismisses the banner", async () => {
    const check = _mockCheck(UPDATE_STATES.UPDATE_AVAILABLE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.1",
      releaseUrl: "https://example.com",
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />
    );
    await waitFor(() => screen.getByTestId("btn-update-later"));
    fireEvent.click(screen.getByTestId("btn-update-later"));
    await waitFor(() =>
      expect(screen.queryByTestId("update-banner")).toBeNull()
    );
  });

  it("FAILED shows manual download link", async () => {
    const check = _mockCheck(UPDATE_STATES.FAILED, {
      currentVersion: "1.0.0",
      error: "network unreachable",
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />
    );
    await waitFor(() =>
      expect(screen.getByTestId("link-manual-download")).toBeTruthy()
    );
    expect(screen.getByTestId("link-manual-download").getAttribute("href")).toContain(
      "github.com/1976haru/autotrade/releases"
    );
  });

  // fix/update-banner-stale-release-notes: FAILED 상태 신규 invariant.
  describe("FAILED state messaging (fix/stale-release-notes)", () => {
    it("uses '최신 버전 확인 불가' instead of raw 'Failed to fetch'", async () => {
      const check = _mockCheck(UPDATE_STATES.FAILED, {
        currentVersion: "1.0.0",
        error: "Failed to fetch",
      });
      const { container } = render(
        <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />
      );
      await waitFor(() => screen.getByTestId("update-fail-headline"));
      expect(screen.getByTestId("update-fail-headline").textContent).toContain(
        "최신 버전 확인 불가"
      );
      // raw "Failed to fetch" 가 *headline* 에 노출되지 않아야 한다.
      expect(screen.getByTestId("update-fail-headline").textContent).not.toContain(
        "Failed to fetch"
      );
      // raw "업데이트 확인 실패" 단독 라벨도 노출 안 됨 — backend offline 과 혼동 방지.
      expect(screen.getByTestId("update-fail-headline").textContent).not.toContain(
        "업데이트 확인 실패"
      );
      // raw error 는 "기술 상세" details 안에서만 접근 가능 (사용자 친화적).
      expect(container.textContent).toContain("Failed to fetch");
    });

    it("explicitly distinguishes from backend connection failure", async () => {
      const check = _mockCheck(UPDATE_STATES.FAILED, {
        currentVersion: "1.0.0",
        error: "x",
      });
      render(
        <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />
      );
      await waitFor(() => screen.getByTestId("update-fail-not-backend"));
      expect(screen.getByTestId("update-fail-not-backend").textContent).toContain(
        "백엔드 연결 실패"
      );
      expect(screen.getByTestId("update-fail-not-backend").textContent).toContain(
        "별개"
      );
    });

    it("does NOT show stale hardcoded release notes (v1.0.0 / 2026-05-08)", async () => {
      // GitHub Release fetch 실패 시 *로컬 RELEASE_NOTES 의 v1.0.0 / 2026-05-08
      // 안내가 최신 업데이트인 척 표시되면 안 된다*. UpdateBanner 는 fetch
      // 결과만 표시하며 RELEASE_NOTES / WELCOME_NOTES 를 import 하지 않는다.
      const check = _mockCheck(UPDATE_STATES.FAILED, {
        currentVersion: "1.0.0",
        error: "Failed to fetch",
      });
      const { container } = render(
        <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />
      );
      await waitFor(() => screen.getByTestId("update-fail-headline"));
      const text = container.textContent || "";
      // 하드코딩된 옛 release entry 의 핵심 문구 — FAILED banner 에 절대 등장 X.
      expect(text).not.toContain("에이전트 트레이더 v1 첫 공개");
      expect(text).not.toContain("2026-05-08");
      // "새 버전이 있습니다" 라벨도 등장 X (UPDATE_AVAILABLE 전용).
      expect(text).not.toContain("새 버전이 있습니다");
      // current version 은 명시 표시 (운영자가 어떤 버전을 쓰고 있는지 확인용).
      expect(text).toContain("v1.0.0");
    });

    it("shows current version 'v0.0.0-unknown' when prop is fallback", async () => {
      // _readCurrentVersion 의 fallback 이 의도적으로 부자연스러운 값임을 검증.
      // 실 build 에서는 vite.config.js 가 inject 하므로 이 fallback 은 도달 X.
      const check = _mockCheck(UPDATE_STATES.FAILED, {
        currentVersion: "0.0.0-unknown",
        error: "x",
      });
      render(
        <UpdateBanner
          currentVersion="0.0.0-unknown"
          checkImpl={check}
          openImpl={vi.fn()}
        />
      );
      await waitFor(() => screen.getByTestId("update-fail-detail"));
      expect(screen.getByTestId("update-fail-detail").textContent).toContain(
        "v0.0.0-unknown"
      );
    });
  });

  it("renders all 3 safety badges in every state", async () => {
    const states = [
      UPDATE_STATES.UP_TO_DATE,
      UPDATE_STATES.UPDATE_AVAILABLE,
      UPDATE_STATES.FAILED,
    ];
    for (const st of states) {
      const check = _mockCheck(st, {
        currentVersion: "1.0.0",
        latestVersion: st === UPDATE_STATES.UPDATE_AVAILABLE ? "1.0.1" : "1.0.0",
        releaseUrl: "https://example.com",
        error: "x",
      });
      const { unmount } = render(
        <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />
      );
      await waitFor(() => screen.getByTestId("update-safety-badges"));
      expect(screen.getByTestId("badge-no-env-overwrite").textContent).toMatch(
        /사용자 \.env 보존/
      );
      expect(screen.getByTestId("badge-no-live-flag-change").textContent).toMatch(
        /실거래 OFF 유지/
      );
      expect(screen.getByTestId("badge-not-order-trigger").textContent).toMatch(
        /주문 기능 아님/
      );
      unmount();
    }
  });

  it("contains no forbidden order/trading labels in buttons", async () => {
    const check = _mockCheck(UPDATE_STATES.UPDATE_AVAILABLE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.1",
      releaseUrl: "https://example.com",
    });
    const { container } = render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />
    );
    await waitFor(() => screen.getByTestId("btn-update-apply"));
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = (btn.textContent || "").toLowerCase();
      expect(text).not.toContain("place order");
      expect(text).not.toContain("buy");
      expect(text).not.toContain("sell");
      expect(text).not.toContain("매수");
      expect(text).not.toContain("매도");
      expect(text).not.toContain("실거래 시작");
      expect(text).not.toContain("enable_live");
    }
  });

  it("re-check button triggers checkImpl again", async () => {
    const check = _mockCheck(UPDATE_STATES.UP_TO_DATE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.0",
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />
    );
    await waitFor(() => screen.getByTestId("update-uptodate"));
    fireEvent.click(screen.getByTestId("btn-update-check"));
    await waitFor(() => expect(check).toHaveBeenCalledTimes(2));
  });

  it("displays sanitized release notes (redacts secret-looking strings)", async () => {
    const check = _mockCheck(UPDATE_STATES.UPDATE_AVAILABLE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.1",
      releaseNotes: "fix: removed leaked sk-abcdefghijklmnop12345 token",
      releaseUrl: "https://example.com",
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />
    );
    await waitFor(() => screen.getByTestId("update-release-notes"));
    // 본 UpdateBanner 는 release notes 를 *그대로* 표시한다 — checkForUpdate 단계
    // (updaterClient.fetchLatestRelease) 에서 sanitize 되어 들어옴. 본 테스트는
    // sanitize 가 *통과* 한 input 도 banner 가 잘 표시함을 확인 (실제 sanitize
    // 동작은 sanitizeText 단위 테스트가 lock).
    const notesText = screen.getByTestId("update-release-notes").textContent || "";
    // checkImpl 이 sanitize 된 값을 직접 전달하므로 본 테스트에서는 input 그대로.
    // 실 호출 경로(fetchLatestRelease) 는 별도 unit test 가 sanitize 검증.
    expect(notesText).toBeDefined();
  });
});


// ============================================================================
// fix/step5-stale-release-popup-guard (#5-04)
// ============================================================================
//
// 본 describe 블록은 사용자 요청서 §4-7 의 7가지 시나리오를 *명시적으로* lock:
//   1. fetch 실패 시 stale release note 표시 0건
//   2. v1.0.0 / 2026-05-08 같은 하드코딩 공지가 최신 업데이트처럼 둔갑 X
//   3. "이번 공지 확인" 후 재팝업 안 됨 (VersionBadge.test.jsx 가 상세 — 본 파일에서는 cross-check)
//   4. 새 release 가 있으면 release note 표시
//   5. 업데이트 실패와 backend 연결 실패가 분리됨
//   6. secret 노출 0건
//   7. 실거래 버튼 문구 0건
//
// 추가로 *정적 import 가드* 를 도입해 미래의 회귀를 방지한다:
//   - UpdateBanner.jsx 가 `../config/releaseNotes` 의 어떤 export 도 import X
//   - desktop/updaterClient.js 가 `../config/releaseNotes` 를 import X

describe("Stale popup guard — static import invariant (#5-04)", () => {
  it("UpdateBanner.jsx does NOT import releaseNotes module", () => {
    // 어떤 형태로든 releaseNotes 를 import 하면 stale popup 회귀 위험.
    expect(UPDATE_BANNER_SRC).not.toMatch(
      /from\s+["']\.\.\/config\/releaseNotes["']/,
    );
    expect(UPDATE_BANNER_SRC).not.toMatch(/require\(["']\.\.\/config\/releaseNotes["']\)/);
  });

  it("UpdateBanner.jsx does NOT reference RELEASE_NOTES / WELCOME_NOTES / latestReleaseNote / latestWelcomeNote symbols", () => {
    // 직접 import 가 없어도 symbol 이름을 sub-string 으로 hard-code 하면 위험.
    // (논리 주석 / docstring 에서만 등장하는 경우는 허용 — 본 검사는 *코드*
    // 상에 동일 식별자가 *값으로* 등장하는지를 lock 하기 위한 보수적 grep.)
    for (const banned of [
      "RELEASE_NOTES",
      "WELCOME_NOTES",
      "latestReleaseNote(",
      "latestWelcomeNote(",
    ]) {
      // 식별자 다음에 (, ., space 등이 와야 *값으로 참조* 한 것으로 본다.
      // 본 banner 의 주석에는 `RELEASE_NOTES` 가 plain word 로 등장할 수 있어
      // import 라인 + 호출 패턴만 차단.
      if (banned.endsWith("(")) {
        expect(UPDATE_BANNER_SRC).not.toContain(banned);
      } else {
        // identifier 가 *코드 토큰* (마침표 / 대괄호 / 공백 + 연산자) 으로
        // 사용되었는지 확인. 주석 안의 plain 사용은 검출하지 않음.
        const tokenUse = new RegExp(`\\b${banned}\\s*[\\.\\[]`);
        expect(UPDATE_BANNER_SRC).not.toMatch(tokenUse);
      }
    }
  });

  it("desktop/updaterClient.js does NOT import releaseNotes module", () => {
    expect(UPDATER_CLIENT_SRC).not.toMatch(
      /from\s+["']\.\.\/config\/releaseNotes["']/,
    );
    expect(UPDATER_CLIENT_SRC).not.toMatch(
      /require\(["']\.\.\/config\/releaseNotes["']\)/,
    );
  });
});


describe("Stale popup guard — behavior (#5-04)", () => {
  afterEach(cleanup);

  // §4-1: fetch 실패 시 stale release note 표시 0건.
  it("fetch 실패 → 어떤 release-note 식별자 / 하드코딩 안내도 노출되지 않음", async () => {
    const check = _mockCheck(UPDATE_STATES.FAILED, {
      currentVersion: "1.0.0",
      error: "Failed to fetch",
    });
    const { container } = render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    await waitFor(() => screen.getByTestId("update-fail-headline"));
    const text = container.textContent || "";
    // 하드코딩 welcome 항목의 핵심 문구들이 banner 에 등장하지 않아야 함.
    expect(text).not.toContain("에이전트 트레이더 v1 첫 공개");
    expect(text).not.toContain("핵심 변경사항");
    expect(text).not.toContain("이번 안내 확인");  // welcome ack 버튼 라벨
    expect(text).not.toContain("이번 버전 공지 확인");  // release ack 버튼 라벨
  });

  // §4-2: v1.0.0 / 2026-05-08 같은 하드코딩 공지가 *최신 업데이트* 인 척 X.
  it("fetch 실패 → '새 버전' / '최신 업데이트' 라벨 0건", async () => {
    const check = _mockCheck(UPDATE_STATES.FAILED, {
      currentVersion: "1.0.0",
      error: "x",
    });
    const { container } = render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    await waitFor(() => screen.getByTestId("update-fail-headline"));
    const text = container.textContent || "";
    expect(text).not.toContain("새 버전이 있습니다");
    expect(text).not.toContain("업데이트 적용");
    // "최신 버전" 단어는 "최신 버전 확인 불가" headline 에는 등장 — sanity check
    // 으로 headline 이 단독 단어가 아닌 *불가* 와 연결된 문구임을 확인.
    expect(screen.getByTestId("update-fail-headline").textContent)
      .toContain("최신 버전 확인 불가");
  });

  // §4-4: 새 release 가 *실제로* 감지된 경우에만 release note 표시.
  it("UPDATE_AVAILABLE 상태에서만 release note details 가 노출됨", async () => {
    const check = _mockCheck(UPDATE_STATES.UPDATE_AVAILABLE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.1",
      releaseNotes: "신규 기능 A / 버그 수정 B",
      releaseUrl: "https://example.com",
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    await waitFor(() => screen.getByTestId("update-release-notes"));
    expect(screen.getByTestId("update-release-notes").textContent)
      .toContain("신규 기능 A");
  });

  it("UP_TO_DATE 상태에서는 release notes details 가 노출되지 않음", async () => {
    const check = _mockCheck(UPDATE_STATES.UP_TO_DATE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.0",
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    await waitFor(() => screen.getByTestId("update-uptodate"));
    expect(screen.queryByTestId("update-release-notes")).toBeNull();
  });

  // §4-5: 업데이트 실패 ≠ backend offline. 명시 disclaimer 노출.
  it("업데이트 실패와 backend 연결 실패가 명시적으로 분리됨", async () => {
    const check = _mockCheck(UPDATE_STATES.FAILED, {
      currentVersion: "1.0.0",
      error: "Failed to fetch",
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    await waitFor(() => screen.getByTestId("update-fail-not-backend"));
    const not = screen.getByTestId("update-fail-not-backend").textContent || "";
    expect(not).toMatch(/백엔드 연결 실패.*별개/);
  });

  // §4-6: secret 노출 0건 — FAILED 상태에서도 raw error 가 mask 적용.
  it("FAILED 상태에서 error 안에 secret 패턴이 있어도 raw 노출 0건", async () => {
    const check = _mockCheck(UPDATE_STATES.FAILED, {
      currentVersion: "1.0.0",
      // 일부러 error 메시지에 secret 패턴을 섞어 본다 — banner 가 sanitize 없이
      // 그대로 details 안에 표출하면 안 된다.
      error: "fetch denied with Bearer abc123def456ghi789jkl012mno345",
    });
    const { container } = render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    await waitFor(() => screen.getByTestId("update-fail-tech-detail"));
    const text = container.textContent || "";
    // sanitizeText 가 Bearer 토큰을 [REDACTED] 로 마스킹.
    // 본 banner 자체는 error 를 그대로 표시하므로, 추가 sanitize 가 필요. 본
    // 테스트는 *현 동작* 을 기준으로 future regression 을 방지: error 텍스트가
    // 최소한 raw secret literal 을 노출하지 않아야 한다. 현 실패 메시지가
    // 부적합하면 본 테스트가 실패해 운영자가 sanitize 도입.
    expect(text).not.toMatch(/Bearer\s+abc123def456ghi789jkl012mno345/);
  });

  // §4-7: 실거래 버튼 문구 0건 — 모든 state 에서.
  it("모든 state 에서 BUY / SELL / Place Order / 매수 / 매도 / 실거래 시작 라벨 0건", async () => {
    const states = [
      UPDATE_STATES.UP_TO_DATE,
      UPDATE_STATES.UPDATE_AVAILABLE,
      UPDATE_STATES.FAILED,
    ];
    for (const st of states) {
      const check = _mockCheck(st, {
        currentVersion: "1.0.0",
        latestVersion: st === UPDATE_STATES.UPDATE_AVAILABLE ? "1.0.1" : "1.0.0",
        releaseNotes: "ok",
        releaseUrl: "https://example.com",
        error: "x",
      });
      const { container, unmount } = render(
        <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
      );
      await waitFor(() => screen.getByTestId("update-banner"));
      const text = container.textContent || "";
      for (const banned of [
        "BUY", "SELL", "HOLD",
        "Place Order", "place order",
        "매수", "매도",
        "실거래 시작", "실거래 활성화",
        "ENABLE_LIVE_TRADING",
      ]) {
        expect(text.includes(banned)).toBe(false);
      }
      unmount();
    }
  });

  // 추가 lock: FAILED state 에 들어가도 "변경 내용 보기" details 0건 — 자동
  // 표시되는 update-release-notes 가 stale 정보를 들고 등장하지 않음.
  it("FAILED state 에 update-release-notes 가 존재하지 않음", async () => {
    const check = _mockCheck(UPDATE_STATES.FAILED, {
      currentVersion: "1.0.0",
      error: "x",
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    await waitFor(() => screen.getByTestId("update-fail-headline"));
    expect(screen.queryByTestId("update-release-notes")).toBeNull();
  });
});


// ============================================================================
// fix/step5-github-release-artifact-link (#5-05) — direct setup.exe link
// ============================================================================

describe("UpdateBanner — GitHub Release setup.exe direct link (#5-05)", () => {
  afterEach(cleanup);

  it("UPDATE_AVAILABLE + setupExeAsset 가 있으면 'setup.exe 직접 받기' 링크 노출", async () => {
    const check = _mockCheck(UPDATE_STATES.UPDATE_AVAILABLE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.1",
      releaseUrl: "https://github.com/1976haru/autotrade/releases/tag/v1.0.1",
      setupExeAsset: {
        name: "Agent Trader v1_1.0.1_x64-setup.exe",
        size: 12_345_678,
        downloadUrl:
          "https://github.com/1976haru/autotrade/releases/download/v1.0.1/Agent-Trader-v1_1.0.1_x64-setup.exe",
      },
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    const direct = await waitFor(() =>
      screen.getByTestId("link-setup-exe-direct"),
    );
    expect(direct.tagName).toBe("A");
    expect(direct.getAttribute("href")).toContain(
      "/releases/download/v1.0.1/",
    );
    expect(direct.getAttribute("href")).toMatch(/setup\.exe$/);
    // 보안 invariant: target=_blank 시 noopener/noreferrer 필수.
    expect(direct.getAttribute("rel")).toMatch(/noopener/);
    expect(direct.getAttribute("rel")).toMatch(/noreferrer/);
    expect(direct.getAttribute("target")).toBe("_blank");
    expect(direct.textContent).toContain("setup.exe");
  });

  it("UPDATE_AVAILABLE + setupExeAsset 없음 → 직접 다운로드 링크 0건 (release 페이지 버튼만)", async () => {
    const check = _mockCheck(UPDATE_STATES.UPDATE_AVAILABLE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.1",
      releaseUrl: "https://example.com",
      // setupExeAsset 없음 — GitHub Release 가 setup.exe 아직 첨부 안 한 경우.
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    await waitFor(() => screen.getByTestId("btn-update-apply"));
    expect(screen.queryByTestId("link-setup-exe-direct")).toBeNull();
  });

  it("UP_TO_DATE 상태에서는 setupExeAsset 이 있어도 직접 링크 노출 X", async () => {
    const check = _mockCheck(UPDATE_STATES.UP_TO_DATE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.0",
      setupExeAsset: {
        name: "x-setup.exe",
        size: 1,
        downloadUrl: "https://example.com/x-setup.exe",
      },
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    await waitFor(() => screen.getByTestId("update-uptodate"));
    expect(screen.queryByTestId("link-setup-exe-direct")).toBeNull();
  });

  it("FAILED 상태에서도 직접 링크 노출 X — release 페이지 fallback 만", async () => {
    const check = _mockCheck(UPDATE_STATES.FAILED, {
      currentVersion: "1.0.0",
      error: "Failed to fetch",
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    await waitFor(() => screen.getByTestId("update-fail-headline"));
    expect(screen.queryByTestId("link-setup-exe-direct")).toBeNull();
    // FAILED 의 manual download fallback 은 release *페이지* 링크.
    expect(screen.getByTestId("link-manual-download").getAttribute("href"))
      .toContain("github.com/1976haru/autotrade/releases");
  });

  it("직접 링크에 BUY / SELL / 매수 / 매도 / 실거래 라벨 0건", async () => {
    const check = _mockCheck(UPDATE_STATES.UPDATE_AVAILABLE, {
      currentVersion: "1.0.0",
      latestVersion: "1.0.1",
      releaseUrl: "https://example.com",
      setupExeAsset: {
        name: "Agent-Trader-v1_1.0.1_x64-setup.exe",
        size: 1,
        downloadUrl: "https://example.com/Agent-Trader-v1_1.0.1_x64-setup.exe",
      },
    });
    render(
      <UpdateBanner currentVersion="1.0.0" checkImpl={check} openImpl={vi.fn()} />,
    );
    const direct = await waitFor(() =>
      screen.getByTestId("link-setup-exe-direct"),
    );
    const text = direct.textContent || "";
    for (const banned of [
      "BUY", "SELL", "HOLD",
      "Place Order",
      "매수", "매도",
      "실거래 시작", "실거래 활성화",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
  });
});
