import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";


// 213: ErrorBoundary 회귀 — 렌더 도중 예외가 던져져도 fallback UI가 보이고
// 기본 children 트리는 unmount되도록.
function Boom({ message = "boom!" }) {
  throw new Error(message);
}


describe("<ErrorBoundary>", () => {
  // componentDidCatch가 console.error를 직접 호출 — 테스트 출력 노이즈 방지.
  let _err;
  beforeEach(() => { _err = vi.spyOn(console, "error").mockImplementation(() => {}); });
  afterEach(() => { _err.mockRestore(); cleanup(); });

  it("renders children when no error is thrown", () => {
    const { getByText, queryByTestId } = render(
      <ErrorBoundary><div>hello</div></ErrorBoundary>,
    );
    expect(getByText("hello")).toBeTruthy();
    expect(queryByTestId("error-boundary")).toBeNull();
  });

  it("renders fallback UI when a child throws", () => {
    const { getByTestId } = render(
      <ErrorBoundary label="테스트"><Boom /></ErrorBoundary>,
    );
    const box = getByTestId("error-boundary");
    expect(box.textContent).toContain("테스트");
    expect(box.textContent).toContain("오류");
  });

  it("clicking 다시 시도 clears the error state", () => {
    function Toggle({ shouldThrow }) {
      if (shouldThrow) throw new Error("kaboom");
      return <div data-testid="child-ok">ok</div>;
    }
    const { getByTestId, queryByTestId, rerender } = render(
      <ErrorBoundary><Toggle shouldThrow={true} /></ErrorBoundary>,
    );
    expect(getByTestId("error-boundary")).toBeTruthy();
    // 먼저 throwing children을 정상 children으로 갱신한 뒤 reset 버튼을 눌러야
    // 한다. 그렇지 않으면 reset 직후 같은 children이 다시 throw 해서 boundary가
    // 즉시 fallback으로 복귀.
    rerender(<ErrorBoundary><Toggle shouldThrow={false} /></ErrorBoundary>);
    fireEvent.click(getByTestId("error-boundary-reset"));
    expect(queryByTestId("error-boundary")).toBeNull();
    expect(getByTestId("child-ok")).toBeTruthy();
  });
});
