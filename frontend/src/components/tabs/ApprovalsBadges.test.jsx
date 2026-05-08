import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ApprovalExpiryBadge, RequestSourceBadge } from "./Approvals";


describe("<RequestSourceBadge>", () => {
  afterEach(cleanup);

  it("renders nothing when request_source is null", () => {
    const { container } = render(<RequestSourceBadge approval={{ id: 1 }} />);
    expect(container.textContent).toBe("");
  });

  it("renders AI label", () => {
    const { getByTestId } = render(
      <RequestSourceBadge approval={{
        id: 1, request_source: "AI", request_source_label: "AI 제안",
      }} />,
    );
    const el = getByTestId("request-source-badge-1");
    expect(el.getAttribute("data-source")).toBe("AI");
    expect(el.textContent).toBe("AI 제안");
  });

  it("renders STRATEGY label", () => {
    const { getByTestId } = render(
      <RequestSourceBadge approval={{
        id: 2, request_source: "STRATEGY", request_source_label: "전략 신호",
      }} />,
    );
    const el = getByTestId("request-source-badge-2");
    expect(el.getAttribute("data-source")).toBe("STRATEGY");
    expect(el.textContent).toBe("전략 신호");
  });

  it("renders LIQUIDATION label", () => {
    const { getByTestId } = render(
      <RequestSourceBadge approval={{
        id: 3, request_source: "LIQUIDATION", request_source_label: "청산 후보",
      }} />,
    );
    expect(getByTestId("request-source-badge-3").textContent).toBe("청산 후보");
  });

  it("falls back to source string when label is missing", () => {
    const { getByTestId } = render(
      <RequestSourceBadge approval={{ id: 4, request_source: "MANUAL" }} />,
    );
    expect(getByTestId("request-source-badge-4").textContent).toBe("MANUAL");
  });
});


describe("<ApprovalExpiryBadge>", () => {
  afterEach(cleanup);

  it("renders nothing when expires_at is null (TTL disabled)", () => {
    const { container } = render(
      <ApprovalExpiryBadge approval={{ id: 1, expires_at: null }} />,
    );
    expect(container.textContent).toBe("");
  });

  it("renders 'X후 만료' when more than 60s remains (minutes scale)", () => {
    const { getByTestId } = render(
      <ApprovalExpiryBadge approval={{
        id: 1, expires_at: "2099-01-01T00:00:00Z",
        seconds_until_expiry: 600, is_expired: false,
      }} />,
    );
    const el = getByTestId("approval-expiry-badge-1");
    expect(el.textContent).toContain("10m");
    expect(el.getAttribute("data-expired")).toBe("false");
  });

  it("renders seconds when < 60s remaining (warning color via amber)", () => {
    const { getByTestId } = render(
      <ApprovalExpiryBadge approval={{
        id: 2, expires_at: "2099-01-01T00:00:00Z",
        seconds_until_expiry: 30, is_expired: false,
      }} />,
    );
    expect(getByTestId("approval-expiry-badge-2").textContent).toContain("30s");
  });

  it("renders 만료됨 with data-expired=true when is_expired", () => {
    const { getByTestId } = render(
      <ApprovalExpiryBadge approval={{
        id: 3, expires_at: "2020-01-01T00:00:00Z",
        seconds_until_expiry: 0, is_expired: true,
      }} />,
    );
    const el = getByTestId("approval-expiry-badge-3");
    expect(el.textContent).toContain("만료됨");
    expect(el.getAttribute("data-expired")).toBe("true");
  });

  it("hours scale rendering when more than 1h remains", () => {
    const { getByTestId } = render(
      <ApprovalExpiryBadge approval={{
        id: 4, expires_at: "2099-01-01T00:00:00Z",
        seconds_until_expiry: 7200, is_expired: false,
      }} />,
    );
    expect(getByTestId("approval-expiry-badge-4").textContent).toContain("2h");
  });
});
