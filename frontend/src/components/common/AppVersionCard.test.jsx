/**
 * #57 / 7-05 — AppVersionCard 단위 테스트.
 *
 * lock 하는 invariant:
 *  - app version / channel / commit / branch+source / build time / dirty 표시
 *  - unknown fallback 표시
 *  - dirty build 안내 표시
 *  - frontend ↔ backend commit mismatch 안내
 *  - secret / 계좌번호 원문 표시 0건
 *  - 매수/매도/실거래/Place Order 버튼 0개, 입력 form 0개
 *  - build metadata missing 이어도 crash 없음
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { AppVersionCard } from "./AppVersionCard";


afterEach(cleanup);


const _FE_OK = {
  version: "1.2.3",
  channel: "paper-beta",
  commit: "abc1234",
  commit_full: "abc1234def567890",
  branch: "main",
  build_time: "2026-05-24T10:30:00+09:00",
  source: "github-actions",
  is_dirty: false,
};

function _api(backend = { ..._FE_OK }) {
  return { buildInfo: vi.fn(async () => backend) };
}

function _failApi() {
  return { buildInfo: vi.fn(async () => { throw new Error("offline"); }) };
}


describe("<AppVersionCard> — frontend build info", () => {
  it("app version 표시", async () => {
    render(<AppVersionCard apiClient={_api()} frontendInfo={_FE_OK} pollIntervalMs={0} />);
    expect((await screen.findByTestId("app-version-version")).textContent)
      .toContain("1.2.3");
  });

  it("channel 표시", async () => {
    render(<AppVersionCard apiClient={_api()} frontendInfo={_FE_OK} pollIntervalMs={0} />);
    expect(screen.getByTestId("app-version-channel").textContent).toContain("paper-beta");
  });

  it("commit (short) 표시", async () => {
    render(<AppVersionCard apiClient={_api()} frontendInfo={_FE_OK} pollIntervalMs={0} />);
    expect(screen.getByTestId("app-version-commit").textContent).toContain("abc1234");
  });

  it("branch / source 표시", async () => {
    render(<AppVersionCard apiClient={_api()} frontendInfo={_FE_OK} pollIntervalMs={0} />);
    const t = screen.getByTestId("app-version-branch").textContent;
    expect(t).toContain("main");
    expect(t).toContain("github-actions");
  });

  it("build time 포맷 표시", async () => {
    render(<AppVersionCard apiClient={_api()} frontendInfo={_FE_OK} pollIntervalMs={0} />);
    expect(screen.getByTestId("app-version-build-time").textContent)
      .toMatch(/2026-05-24 \d{2}:\d{2}/);
  });

  it("dirty=false → '아니오'", async () => {
    render(<AppVersionCard apiClient={_api()} frontendInfo={_FE_OK} pollIntervalMs={0} />);
    expect(screen.getByTestId("app-version-dirty").textContent).toContain("아니오");
    expect(screen.queryByTestId("app-version-dirty-warning")).toBeNull();
  });

  it("dirty=true → 경고 표시", async () => {
    render(<AppVersionCard apiClient={_api()}
      frontendInfo={{ ..._FE_OK, is_dirty: true }} pollIntervalMs={0} />);
    expect((await screen.findByTestId("app-version-dirty-warning")).textContent)
      .toContain("로컬 변경");
  });

  it("footer 에 GitHub main 비교 안내", async () => {
    render(<AppVersionCard apiClient={_api()} frontendInfo={_FE_OK} pollIntervalMs={0} />);
    expect(screen.getByTestId("app-version-footer").textContent)
      .toContain("GitHub main");
  });
});


describe("<AppVersionCard> — unknown / fallback", () => {
  it("commit unknown → unknown 경고", async () => {
    render(<AppVersionCard apiClient={_failApi()}
      frontendInfo={{ version: "1.0.0" }} pollIntervalMs={0} />);
    expect(await screen.findByTestId("app-version-unknown-warning")).toBeTruthy();
  });

  it("metadata 전부 없음이어도 crash 없이 unknown 표시", async () => {
    render(<AppVersionCard apiClient={_failApi()}
      frontendInfo={null} pollIntervalMs={0} />);
    // frontendInfo=null → Vite 주입값(getBuildInfo) 사용 → crash 없이 렌더.
    expect(await screen.findByTestId("app-version-card")).toBeTruthy();
    expect(screen.getByTestId("app-version-version")).toBeTruthy();
  });

  it("build time unknown → '확인 불가'", async () => {
    render(<AppVersionCard apiClient={_failApi()}
      frontendInfo={{ ..._FE_OK, build_time: "unknown" }} pollIntervalMs={0} />);
    expect((await screen.findByTestId("app-version-build-time")).textContent)
      .toContain("확인 불가");
  });
});


describe("<AppVersionCard> — backend sidecar", () => {
  it("backend commit 표시", async () => {
    render(<AppVersionCard
      apiClient={_api({ ..._FE_OK, commit: "abc1234" })}
      frontendInfo={_FE_OK} pollIntervalMs={0} />);
    expect((await screen.findByTestId("app-version-backend-commit")).textContent)
      .toContain("abc1234");
  });

  it("frontend ↔ backend commit mismatch 안내", async () => {
    render(<AppVersionCard
      apiClient={_api({ ..._FE_OK, commit: "zzz9999" })}
      frontendInfo={_FE_OK} pollIntervalMs={0} />);
    expect((await screen.findByTestId("app-version-mismatch-warning")).textContent)
      .toContain("commit 이 다릅니다");
  });

  it("backend 조회 실패 → 안전 안내 (crash 없음)", async () => {
    render(<AppVersionCard apiClient={_failApi()} frontendInfo={_FE_OK} pollIntervalMs={0} />);
    expect(await screen.findByTestId("app-version-backend-error")).toBeTruthy();
  });
});


describe("<AppVersionCard> — safety invariants", () => {
  it("매수/매도/실거래/Place Order 버튼 0개", async () => {
    const { container } = render(
      <AppVersionCard apiClient={_api()} frontendInfo={_FE_OK} pollIntervalMs={0} />);
    await screen.findByTestId("app-version-version");
    expect(container.querySelectorAll("button").length).toBe(0);
    const text = container.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "매수 실행", "매도 실행",
      "실거래 시작", "실거래 활성화", "ENABLE_LIVE_TRADING",
    ]) {
      expect(text).not.toContain(banned);
    }
  });

  it("입력 form(input/textarea/select) 0개", async () => {
    const { container } = render(
      <AppVersionCard apiClient={_api()} frontendInfo={_FE_OK} pollIntervalMs={0} />);
    await screen.findByTestId("app-version-version");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("secret / 계좌번호 원문이 응답에 있어도 표시되지 않음", async () => {
    const { container } = render(
      <AppVersionCard
        apiClient={_api({
          ..._FE_OK, kis_app_secret: "LEAKSECRET999", kis_account_no: "98765432-11",
        })}
        frontendInfo={{
          ..._FE_OK, kis_app_secret: "FELEAK888", access_token: "TOKENLEAK",
        }}
        pollIntervalMs={0} />);
    await screen.findByTestId("app-version-version");
    const text = container.textContent || "";
    expect(text).not.toContain("LEAKSECRET999");
    expect(text).not.toContain("FELEAK888");
    expect(text).not.toContain("TOKENLEAK");
    expect(text).not.toContain("98765432");
  });
});
