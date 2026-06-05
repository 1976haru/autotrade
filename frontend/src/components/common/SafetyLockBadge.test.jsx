import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";

import SafetyLockBadge from "./SafetyLockBadge";

describe("SafetyLockBadge (PART4-4 항상 보이는 안전 배지)", () => {
  afterEach(() => cleanup());

  it("항상 '실거래 차단' 배지를 렌더한다", () => {
    const { getByTestId } = render(<SafetyLockBadge />);
    const badge = getByTestId("safety-lock-badge");
    expect(badge).toBeTruthy();
    expect(badge.textContent).toContain("실거래 차단");
    expect(badge.textContent).toContain("모의");
  });

  it("compact 모드에서도 같은 안전 텍스트를 유지", () => {
    const { getByTestId } = render(<SafetyLockBadge compact />);
    const badge = getByTestId("safety-lock-badge");
    expect(badge.textContent).toContain("실거래 차단");
  });

  it("끌 수 있는 prop 이 없다 — 어떤 props 로도 사라지지 않는다", () => {
    // hidden/show 같은 prop 을 줘도 무시되고 항상 렌더된다.
    const { getByTestId } = render(<SafetyLockBadge hidden show={false} />);
    expect(getByTestId("safety-lock-badge")).toBeTruthy();
  });

  it("실거래 활성화 버튼/입력을 만들지 않는다 (표시 전용)", () => {
    const { container, queryByRole } = render(<SafetyLockBadge />);
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(queryByRole("button")).toBeNull();
  });

  it("접근성 라벨이 안전 의미를 전달", () => {
    const { getByTestId } = render(<SafetyLockBadge />);
    const badge = getByTestId("safety-lock-badge");
    expect(badge.getAttribute("aria-label")).toContain("실거래 차단");
  });
});
