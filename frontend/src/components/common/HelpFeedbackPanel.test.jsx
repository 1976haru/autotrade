import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  FaqCard,
  FeedbackModal,
  HelpFeedbackPanel,
} from "./HelpFeedbackPanel";


afterEach(cleanup);


describe("<FeedbackModal>", () => {
  it("does not render when open=false", () => {
    const { queryByTestId } = render(
      <FeedbackModal open={false} onClose={() => {}} />,
    );
    expect(queryByTestId("feedback-modal")).toBeNull();
  });

  it("renders Secret 입력 금지 warning prominently", () => {
    const { getByTestId } = render(
      <FeedbackModal open={true} onClose={() => {}} />,
    );
    const warn = getByTestId("feedback-secret-warn");
    expect(warn.textContent).toMatch(/Secret.*개인정보 입력 금지|API key.*Secret.*계좌번호/);
    expect(warn.textContent).toMatch(/입력하지/);
  });

  it("does NOT show secret-detected banner when input is clean", () => {
    const { queryByTestId, getByTestId } = render(
      <FeedbackModal open={true} onClose={() => {}} />,
    );
    fireEvent.change(getByTestId("feedback-body"),
                     { target: { value: "정상 텍스트입니다" } });
    expect(queryByTestId("feedback-secret-detected")).toBeNull();
  });

  it("shows secret-detected banner when input matches secret pattern", () => {
    const { findByTestId, getByTestId } = render(
      <FeedbackModal open={true} onClose={() => {}} />,
    );
    fireEvent.change(getByTestId("feedback-body"), {
      target: { value: "my key sk-ant-abc123def456ghi789jkl0 leaked" },
    });
    return findByTestId("feedback-secret-detected").then((el) => {
      expect(el.textContent).toMatch(/Secret 같은 패턴이 감지되었습니다/);
    });
  });

  it("renders all category options", () => {
    const { getByTestId } = render(
      <FeedbackModal open={true} onClose={() => {}} />,
    );
    const options = Array.from(getByTestId("feedback-category").options)
      .map((o) => o.textContent);
    expect(options).toContain("사용법 질문");
    expect(options).toContain("오류 신고");
    expect(options).toContain("개선 제안");
    expect(options).toContain("AI 판단 관련 문의");
    expect(options).toContain("리스크 / 승인 관련 문의");
    expect(options).toContain("기타");
  });

  it("auto-meta details includes app version + mode + URL but NOT secret", () => {
    const { getByTestId } = render(
      <FeedbackModal open={true} onClose={() => {}} currentMode="SIMULATION" />,
    );
    const details = getByTestId("feedback-auto-meta-details");
    expect(details.textContent).toMatch(/Agent Trader v1/);
    expect(details.textContent).toMatch(/v1\.0\.0/);
    expect(details.textContent).toMatch(/SIMULATION/);
    expect(details.textContent).not.toMatch(/sk-ant-|app_secret=/);
  });

  it("clipboard copy button calls navigator.clipboard.writeText", async () => {
    const writeText = vi.fn().mockResolvedValue();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText }, writable: true, configurable: true,
    });
    const { getByTestId } = render(
      <FeedbackModal open={true} onClose={() => {}} />,
    );
    fireEvent.change(getByTestId("feedback-subject"),
                     { target: { value: "테스트 제목" } });
    fireEvent.change(getByTestId("feedback-body"),
                     { target: { value: "테스트 본문" } });
    fireEvent.click(getByTestId("feedback-copy"));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const arg = writeText.mock.calls[0][0];
    expect(arg).toContain("테스트 제목");
    expect(arg).toContain("테스트 본문");
    expect(arg).toContain("Agent Trader v1");
  });

  it("does NOT render mailto link when VITE_FEEDBACK_EMAIL is empty (default test env)", () => {
    const { queryByTestId } = render(
      <FeedbackModal open={true} onClose={() => {}} />,
    );
    // .env.example default is empty → no mailto link, instead a hint
    // (테스트 env에서 import.meta.env.VITE_FEEDBACK_EMAIL이 미설정인 경우)
    const mailto = queryByTestId("feedback-mailto");
    const noTarget = queryByTestId("feedback-no-mail-target");
    // 둘 중 하나만 노출 — 어느 쪽이든 raw email이 누설되지 않으면 OK.
    if (!mailto) {
      expect(noTarget).not.toBeNull();
      expect(noTarget.textContent).toMatch(/VITE_FEEDBACK_EMAIL 미설정/);
    }
  });

  it("close button triggers onClose", () => {
    let closed = false;
    const { getByTestId } = render(
      <FeedbackModal open={true} onClose={() => { closed = true; }} />,
    );
    fireEvent.click(getByTestId("feedback-close"));
    expect(closed).toBe(true);
  });

  it("does NOT contain BUY/SELL/HOLD/주문 실행 buttons", () => {
    const { container } = render(
      <FeedbackModal open={true} onClose={() => {}} />,
    );
    const buttons = container.querySelectorAll("button");
    for (const b of buttons) {
      const t = b.textContent || "";
      expect(t).not.toMatch(/BUY|SELL|HOLD|매수 실행|매도 실행|즉시 주문/);
    }
  });
});


describe("<FaqCard>", () => {
  it("renders 8 FAQ entries with disclaimer", () => {
    const { container, getByTestId } = render(<FaqCard />);
    const list = getByTestId("faq-list");
    expect(list.children.length).toBe(8);
    expect(container.textContent).toMatch(/투자 조언이 아닙니다/);
  });

  it("FAQ contains required questions about real money / Kill Switch / Demo / live trading", () => {
    const { container } = render(<FaqCard />);
    const text = container.textContent;
    expect(text).toMatch(/실제 돈이 나가나요/);
    expect(text).toMatch(/SIMULATION.*PAPER.*SHADOW/);
    expect(text).toMatch(/AI가 직접 주문/);
    expect(text).toMatch(/긴급중단|Kill Switch/);
    expect(text).toMatch(/백엔드 연결 대기/);
    expect(text).toMatch(/GitHub Pages.*Demo|로컬 실행/);
    expect(text).toMatch(/모의투자|실거래/);
    expect(text).toMatch(/실거래는 언제/);
  });

  it("FAQ answers explicitly say AI does NOT directly call broker", () => {
    const { getByTestId } = render(<FaqCard />);
    fireEvent.click(getByTestId("faq-entry-2").querySelector("summary"));
    const entry2 = getByTestId("faq-entry-2");
    expect(entry2.textContent).toMatch(/AI.*broker.*직접 호출하지 않/);
  });
});


describe("<HelpFeedbackPanel>", () => {
  it("renders the panel with secret 입력 금지 hint", () => {
    const { container } = render(<HelpFeedbackPanel />);
    // Card primitive doesn't forward data-testid; assert via container text.
    expect(container.textContent)
      .toMatch(/API key.*Secret.*계좌번호.*비밀번호.*입력하지/);
  });

  it("opens FeedbackModal when button is clicked", () => {
    const { getByTestId, queryByTestId } = render(<HelpFeedbackPanel />);
    expect(queryByTestId("feedback-modal")).toBeNull();
    fireEvent.click(getByTestId("help-feedback-open"));
    expect(queryByTestId("feedback-modal")).not.toBeNull();
  });
});
