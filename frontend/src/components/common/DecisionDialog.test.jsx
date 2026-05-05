import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DecisionDialog } from "./DecisionDialog";


// 047/049/065 모달이 모두 이 primitive를 thin wrapper로 감싸므로, 공통 a11y/
// 키보드/buy 동작은 여기서 한 번에 검증하고 wrapper 테스트는 자기 컴포넌트만의
// title/summary/labels에 집중한다.

describe("<DecisionDialog>", () => {
  afterEach(cleanup);

  function _render(overrides = {}) {
    return render(
      <DecisionDialog
        title="테스트"
        accent="#7dd3fc"
        confirmLabel="확인"
        busy={false}
        onConfirm={() => {}}
        onCancel={() => {}}
        {...overrides}
      />,
    );
  }

  it("renders role=dialog with aria-label defaulting to title", () => {
    const { getByRole } = _render({ title: "긴급 정지 활성화" });
    expect(getByRole("dialog").getAttribute("aria-label")).toBe("긴급 정지 활성화");
  });

  it("ariaLabel prop overrides title for the aria-label", () => {
    const { getByRole } = _render({
      title: "stale 일괄 취소 (3건)", ariaLabel: "stale 일괄 취소",
    });
    expect(getByRole("dialog").getAttribute("aria-label")).toBe("stale 일괄 취소");
  });

  it("renders summary node between title and description", () => {
    const { container } = _render({
      summary: <div data-testid="custom-summary">SUMMARY</div>,
    });
    expect(container.querySelector('[data-testid="custom-summary"]')).toBeTruthy();
  });

  it("renders description text when provided", () => {
    const { container } = _render({ description: "설명 텍스트" });
    expect(container.textContent).toContain("설명 텍스트");
  });

  it("auto-focuses the decided_by input when defaultDecidedBy is empty", () => {
    const { getByPlaceholderText } = _render();
    expect(document.activeElement).toBe(getByPlaceholderText(/ops1/));
  });

  it("auto-focuses the note input when defaultDecidedBy is pre-filled", () => {
    const { getByPlaceholderText } = _render({
      defaultDecidedBy: "ops-default",
      notePlaceholder: "사유 placeholder",
    });
    expect(document.activeElement).toBe(getByPlaceholderText("사유 placeholder"));
  });

  it("Esc dispatches onCancel", () => {
    const onCancel = vi.fn();
    _render({ onCancel });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("Enter dispatches onConfirm with trimmed values", () => {
    const onConfirm = vi.fn();
    const { getByPlaceholderText } = _render({
      onConfirm, notePlaceholder: "사유",
    });
    fireEvent.change(getByPlaceholderText(/ops1/), { target: { value: " ops1 " } });
    fireEvent.change(getByPlaceholderText("사유"), { target: { value: " note " } });
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops1", note: "note" });
  });

  it("ignores Esc and Enter while busy", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    _render({ busy: true, onCancel, onConfirm });
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onCancel).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("disables both buttons while busy and shows '처리 중…' on confirm", () => {
    const { getByText } = _render({
      busy: true, cancelLabel: "닫기", confirmLabel: "확인",
    });
    expect(getByText("닫기").disabled).toBe(true);
    expect(getByText(/처리 중/).disabled).toBe(true);
  });

  it("confirm button click dispatches onConfirm with trimmed values", () => {
    const onConfirm = vi.fn();
    const { getByText, getByPlaceholderText } = _render({
      onConfirm, confirmLabel: "✓ 승인", notePlaceholder: "사유",
    });
    fireEvent.change(getByPlaceholderText(/ops1/), { target: { value: " ops1 " } });
    fireEvent.click(getByText("✓ 승인"));
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops1", note: "" });
  });

  it("cancel button click dispatches onCancel", () => {
    const onCancel = vi.fn();
    const { getByText } = _render({ onCancel, cancelLabel: "취소" });
    fireEvent.click(getByText("취소"));
    expect(onCancel).toHaveBeenCalled();
  });

  it("title is colored with the accent prop", () => {
    const { getByText } = _render({ title: "긴급 정지", accent: "#ef4444" });
    expect(getByText("긴급 정지").style.color).toBe("rgb(239, 68, 68)");
  });
});
