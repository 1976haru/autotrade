/**
 * #70/#71/#72 — LiveSafetyStatusCard 단위 테스트.
 *
 * lock: live policy / kis endpoint / live capital review 표시 + 안전 문구 +
 * 실전/주문/승인 버튼 0개 + 입력 form 0개 + secret 미표시.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { LiveSafetyStatusCard } from "./LiveSafetyStatusCard";

afterEach(cleanup);

const _DATA = {
  live_policy: {
    enable_live_trading: false, enable_ai_execution: false,
    enable_futures_live_trading: false, kis_is_paper: true,
    default_mode: "SIMULATION", live_path_gated: true, live_order_blocked: true,
    reason_code: "LIVE_ORDER_BLOCKED_BY_DEFAULT", is_live_authorization: false,
  },
  kis_endpoint: {
    selected_mode: "PAPER", paper_live_separated: true, live_gate_required: false,
    reason_code: "KIS_PAPER_PATH_SELECTED",
    message_ko: "KIS 모의(Paper) 경로를 사용합니다.", broker_order_sent: false,
  },
  live_capital_review: {
    approval_status: "MISSING", symbol_whitelist_count: 0,
    max_order_notional_configured: false, daily_live_limit_configured: false,
    order_created: false, broker_order_sent: false, is_live_authorization: false,
  },
  is_live_authorization: false,
};

function _api(data = _DATA) {
  return { liveSafetyStatus: vi.fn(async () => data) };
}

describe("<LiveSafetyStatusCard>", () => {
  it("live policy 블록 + 안전 flag 표시", async () => {
    render(<LiveSafetyStatusCard apiClient={_api()} />);
    await screen.findByTestId("live-policy-block");
    expect(screen.getByTestId("live-flag-live").textContent).toContain("false");
    expect(screen.getByTestId("live-policy-gated").textContent).toContain("차단");
  });

  it("KIS endpoint 분리 표시", async () => {
    render(<LiveSafetyStatusCard apiClient={_api()} />);
    const t = (await screen.findByTestId("kis-endpoint-mode")).textContent;
    expect(t).toContain("PAPER");
    expect(t).toContain("분리됨: true");
  });

  it("Live Capital Review 상태 표시 (order 0)", async () => {
    render(<LiveSafetyStatusCard apiClient={_api()} />);
    expect((await screen.findByTestId("live-review-status")).textContent).toContain("MISSING");
    const order = screen.getByTestId("live-review-order").textContent;
    expect(order).toContain("order_created=false");
    expect(order).toContain("is_live_authorization=false");
  });

  it("안전 문구 노출", async () => {
    render(<LiveSafetyStatusCard apiClient={_api()} />);
    const intro = (await screen.findByTestId("live-safety-intro")).textContent;
    expect(intro).toContain("실전매매 기본 OFF");
    expect(intro).toContain("KIS Paper 와 KIS Live 경로는 분리");
    expect(intro).toContain("Live Capital Review 는 주문 승인이 아닙니다");
    expect(intro).toContain("현재 실전 주문은 차단 상태");
  });

  it("실전/주문/승인 버튼 0개, 입력 form 0개", async () => {
    const { container } = render(<LiveSafetyStatusCard apiClient={_api()} />);
    await screen.findByTestId("live-policy-block");
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input, textarea, select").length).toBe(0);
    for (const f of ["실전 켜기", "LIVE ON", "approve live", "지금 매수", "지금 매도", "주문 시작"]) {
      expect(container.textContent).not.toContain(f);
    }
  });

  it("secret/account 미표시", async () => {
    const { container } = render(<LiveSafetyStatusCard apiClient={_api()} />);
    await screen.findByTestId("live-policy-block");
    for (const f of ["sk-ant-", "Bearer ", "access_token", "kis_app_secret"]) {
      expect(container.textContent).not.toContain(f);
    }
  });

  it("KIS_IS_PAPER=false + no gate → BLOCKED 표시", async () => {
    const blocked = {
      ..._DATA,
      live_policy: { ..._DATA.live_policy, kis_is_paper: false },
      kis_endpoint: { selected_mode: "BLOCKED", paper_live_separated: true,
                      live_gate_required: true, reason_code: "KIS_LIVE_GATE_REQUIRED",
                      message_ko: "explicit live gate 가 없어 차단되었습니다.",
                      broker_order_sent: false },
    };
    render(<LiveSafetyStatusCard apiClient={_api(blocked)} />);
    expect((await screen.findByTestId("kis-endpoint-mode")).textContent).toContain("BLOCKED");
    expect(screen.getByTestId("kis-endpoint-reason").textContent).toContain("차단");
  });
});
