import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { UserGuideCard, UserGuideModal } from "./UserGuideModal";


afterEach(cleanup);


describe("<UserGuideModal>", () => {
  it("does not render when open=false", () => {
    const { queryByTestId } = render(
      <UserGuideModal open={false} onClose={() => {}} />,
    );
    expect(queryByTestId("user-guide-modal")).toBeNull();
  });

  it("renders all 7 sections when open", () => {
    const { getByTestId } = render(
      <UserGuideModal open={true} onClose={() => {}} />,
    );
    for (let i = 1; i <= 7; i++) {
      const sec = getByTestId(`ug-section-${i}`);
      expect(sec).toBeTruthy();
    }
  });

  it("section 5 (caution) contains required safety phrases", () => {
    const { getByTestId } = render(
      <UserGuideModal open={true} onClose={() => {}} />,
    );
    const c = getByTestId("ug-section-5").textContent;
    expect(c).toMatch(/수익 보장 도구가 아닙니다/);
    expect(c).toMatch(/Paper.*Shadow.*Manual Approval 검증/);
    expect(c).toMatch(/AI 판단.*참고자료/);
    expect(c).toMatch(/긴급중단 버튼/);
    expect(c).toMatch(/API key.*Secret.*계좌번호/);
  });

  it("section 6 lists all 6 modes", () => {
    const { getByTestId } = render(
      <UserGuideModal open={true} onClose={() => {}} />,
    );
    const modes = getByTestId("ug-section-6").textContent;
    for (const mode of ["SIMULATION", "PAPER", "LIVE_SHADOW",
                          "LIVE_MANUAL_APPROVAL", "LIVE_AI_ASSIST",
                          "LIVE_AI_EXECUTION"]) {
      expect(modes).toContain(mode);
    }
  });

  it("does NOT contain BUY/SELL/HOLD or 주문 실행 buttons", () => {
    const { container } = render(
      <UserGuideModal open={true} onClose={() => {}} />,
    );
    const buttons = container.querySelectorAll("button");
    for (const b of buttons) {
      const t = b.textContent || "";
      expect(t).not.toMatch(/BUY|SELL|HOLD|매수 실행|매도 실행|즉시 주문/);
    }
  });

  it("contains '투자 조언이 아닙니다' disclaimer", () => {
    const { container } = render(
      <UserGuideModal open={true} onClose={() => {}} />,
    );
    expect(container.textContent).toMatch(/투자 조언이 아닙니다/);
  });

  it("close button triggers onClose", () => {
    let closed = false;
    const { getByTestId } = render(
      <UserGuideModal open={true} onClose={() => { closed = true; }} />,
    );
    fireEvent.click(getByTestId("user-guide-close"));
    expect(closed).toBe(true);
  });

  it("backdrop click triggers onClose", () => {
    let closed = false;
    const { getByTestId } = render(
      <UserGuideModal open={true} onClose={() => { closed = true; }} />,
    );
    fireEvent.click(getByTestId("user-guide-backdrop"));
    expect(closed).toBe(true);
  });
});


describe("<UserGuideCard>", () => {
  it("renders the card with disclaimer that it's not investment advice", () => {
    const { container } = render(<UserGuideCard />);
    // Card primitive doesn't forward data-testid; check via container content.
    expect(container.textContent).toMatch(/투자 조언이 아닙니다/);
    expect(container.textContent).toMatch(/처음 사용자도/);
  });

  it("opens modal when button is clicked", () => {
    const { getByTestId, queryByTestId } = render(<UserGuideCard />);
    expect(queryByTestId("user-guide-modal")).toBeNull();
    fireEvent.click(getByTestId("user-guide-open"));
    expect(queryByTestId("user-guide-modal")).not.toBeNull();
  });

  it("modal can be closed and re-opened", () => {
    const { getByTestId, queryByTestId } = render(<UserGuideCard />);
    fireEvent.click(getByTestId("user-guide-open"));
    fireEvent.click(getByTestId("user-guide-close"));
    expect(queryByTestId("user-guide-modal")).toBeNull();
    fireEvent.click(getByTestId("user-guide-open"));
    expect(queryByTestId("user-guide-modal")).not.toBeNull();
  });
});
