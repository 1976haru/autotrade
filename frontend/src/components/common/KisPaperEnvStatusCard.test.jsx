/**
 * 0-04: KisPaperEnvStatusCard 단위 테스트.
 *
 * invariant:
 *  - PAPER / 실거래 OFF / KIS Paper ON / Auto / dry-run / fill-polling /
 *    자격 구성됨·미구성 / broker_order_type / is_live_authorization 표시.
 *  - API key / Secret / 계좌번호 값 노출 0건.
 *  - LIVE 활성화 / ENABLE_* 토글 버튼 0개, 입력 form 0개.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { KisPaperEnvStatusCard } from "./KisPaperEnvStatusCard";

const _SAFE = {
  enable_kis_paper_auto_trading: true,
  dry_run: false,
  fill_polling: true,
  kis_is_paper: true,
  enable_live_trading: false,
  credentials_present: true,
  // 4-01: per-credential present (값 원문 0건 — boolean 만).
  kis_app_key_present: true,
  kis_app_secret_present: true,
  kis_account_no_present: true,
  kis_product_code_present: true,
  product_code_present: true,
  missing_credentials: [],
  is_live_authorization: false,
  broker_order_type: "KIS_PAPER",
  default_mode: "PAPER",
  paper_broker_kind: "KIS_PAPER",
};

function _api(payload = _SAFE) {
  return { kisPaperAutoStatus: vi.fn(async () => payload) };
}

describe("<KisPaperEnvStatusCard>", () => {
  afterEach(cleanup);

  it("PAPER / 실거래 OFF / KIS Paper ON 표시", async () => {
    render(<KisPaperEnvStatusCard apiClient={_api()} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("kis-env-mode").textContent).toMatch(/PAPER/));
    expect(screen.getByTestId("kis-env-live").textContent).toMatch(/OFF/);
    expect(screen.getByTestId("kis-env-kis-paper").textContent).toMatch(/ON/);
  });

  it("Paper broker / Auto / dry-run / fill-polling 표시", async () => {
    render(<KisPaperEnvStatusCard apiClient={_api()} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("kis-env-broker-kind").textContent).toMatch(/KIS_PAPER/));
    expect(screen.getByTestId("kis-env-auto").textContent).toMatch(/ON/);
    expect(screen.getByTestId("kis-env-dry-run").textContent).toMatch(/OFF/);
    expect(screen.getByTestId("kis-env-fill-polling").textContent).toMatch(/ON/);
  });

  it("KIS 자격 구성됨 표시", async () => {
    render(<KisPaperEnvStatusCard apiClient={_api()} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("kis-env-credentials").textContent).toMatch(/구성됨/));
  });

  it("미구성 시 미구성 표시", async () => {
    render(<KisPaperEnvStatusCard
      apiClient={_api({ ..._SAFE, credentials_present: false })} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("kis-env-credentials").textContent).toMatch(/미구성/));
  });

  it("4종 per-credential 구성됨 표시 (APP KEY/SECRET/ACCOUNT/PRODUCT)", async () => {
    render(<KisPaperEnvStatusCard apiClient={_api()} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("kis-env-app-key").textContent).toMatch(/구성됨/));
    expect(screen.getByTestId("kis-env-app-secret").textContent).toMatch(/구성됨/);
    expect(screen.getByTestId("kis-env-account-no").textContent).toMatch(/구성됨/);
    expect(screen.getByTestId("kis-env-product-code").textContent).toMatch(/구성됨/);
  });

  it("자격 미구성 시 missing_credentials 표시", async () => {
    render(<KisPaperEnvStatusCard apiClient={_api({
      ..._SAFE, credentials_present: false,
      kis_app_secret_present: false, kis_account_no_present: false,
      missing_credentials: ["KIS_APP_SECRET", "KIS_ACCOUNT_NO"],
    })} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("kis-env-missing-credentials")).toBeTruthy());
    const txt = screen.getByTestId("kis-env-missing-credentials").textContent;
    expect(txt).toMatch(/KIS_APP_SECRET/);
    expect(txt).toMatch(/KIS_ACCOUNT_NO/);
    expect(screen.getByTestId("kis-env-app-secret").textContent).toMatch(/미구성/);
  });

  it("broker_order_type=KIS_PAPER / is_live_authorization=false 표시", async () => {
    render(<KisPaperEnvStatusCard apiClient={_api()} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("kis-env-broker-order-type").textContent)
        .toMatch(/KIS_PAPER/));
    expect(screen.getByTestId("kis-env-live-auth").textContent).toMatch(/false/);
  });

  it("자격정보 backend/.env 저장 안내 + 화면 미표시 문구", async () => {
    render(<KisPaperEnvStatusCard apiClient={_api()} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("kis-env-footer")).toBeTruthy());
    const txt = screen.getByTestId("kis-env-footer").textContent;
    expect(txt).toMatch(/자격정보 원문은 표시하지 않습니다/);
    expect(txt).toMatch(/backend\/\.env에만 입력하세요/);
    expect(txt).toMatch(/\.env\.example에는 실제 값을 넣지 마세요/);
    expect(txt).toMatch(/KIS 모의투자 설정\s*확인용/);
    expect(txt).toMatch(/API key \/ Secret \/ 계좌번호가 표시되지 않/);
    expect(txt).toMatch(/Paper \/ KIS 모의투자 전용/);
  });

  it("secret/계좌번호 값 노출 0개 (invariant)", async () => {
    // 응답에 secret-like 값이 섞여 와도 카드는 boolean/enum 만 렌더 — 값 미표시.
    const leaky = { ..._SAFE, kis_app_secret: "sk-LEAKED-SECRET-zzzz",
      account_no: "12345678-01" };
    const { container } = render(
      <KisPaperEnvStatusCard apiClient={_api(leaky)} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("kis-env-mode")).toBeTruthy());
    expect(container.textContent).not.toContain("sk-LEAKED-SECRET");
    expect(container.textContent).not.toContain("12345678-01");
  });

  it("LIVE 활성화 / ENABLE_ 토글 버튼 0개 + 입력 form 0개 (invariant)", async () => {
    const { container } = render(
      <KisPaperEnvStatusCard apiClient={_api()} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("kis-env-mode")).toBeTruthy());
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
    expect(container.querySelectorAll("select").length).toBe(0);
    for (const banned of ["실거래 활성화", "ENABLE_LIVE_TRADING 켜기",
                          "Live 자금 승인", "Place Order"]) {
      expect(container.textContent).not.toContain(banned);
    }
  });

  it("API 실패 시 에러 표시(크래시 없음)", async () => {
    const api = { kisPaperAutoStatus: vi.fn(async () => { throw new Error("down"); }) };
    render(<KisPaperEnvStatusCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("kis-env-error").textContent)
      .toMatch(/down/));
  });
});
