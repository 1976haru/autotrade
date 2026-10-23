import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, waitFor, fireEvent, act } from "@testing-library/react";

import { DesktopTrayBridge } from "./DesktopTrayBridge";

afterEach(() => { cleanup(); delete window.__agentTraderDesktop; });

const risky = {
  safe_to_quit: false, needs_running_for_eod: true,
  program_holdings: [{ symbol: "005930", quantity: 1 }], open_order_qty: 0,
  eod_state: "BUY_CUTOFF_ACTIVE",
  message_ko: "프로그램 보유 1종목 / 미체결 0주가 있습니다. 지금 종료하면 15:00~15:30 자동 청산이 실행되지 않습니다.",
};
const safe = { ...risky, safe_to_quit: true, needs_running_for_eod: false, program_holdings: [],
  message_ko: "프로그램 보유·미체결이 없습니다. 안전하게 종료할 수 있습니다." };

describe("DesktopTrayBridge (WINDOWS-ONECLICK-EXE-V1)", () => {
  it("웹 모드(enabled=false)에서는 아무것도 렌더/등록하지 않음", () => {
    const { container } = render(<DesktopTrayBridge enabled={false} api={{}} invoke={vi.fn()} />);
    expect(container.textContent).toBe("");
    expect(window.__agentTraderDesktop).toBeUndefined();
  });

  it("안전 종료: 보유가 있으면 경고 + 트레이 유지(권장) 선택 시 종료 안 함", async () => {
    const invoke = vi.fn(async () => null);
    const api = { kisPaperEodShutdownCheck: vi.fn(async () => risky) };
    const { getByTestId } = render(<DesktopTrayBridge enabled api={api} invoke={invoke} />);
    act(() => { window.__agentTraderDesktop.requestQuit(); });
    await waitFor(() => expect(getByTestId("tray-check").textContent).toContain("자동 청산이 실행되지 않습니다"));
    expect(getByTestId("tray-quit").textContent).toContain("그래도 종료");
    expect(getByTestId("tray-guarantee").textContent).toContain("보장할 수 없습니다");
    fireEvent.click(getByTestId("tray-keep"));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("hide_to_tray"));
    expect(invoke).not.toHaveBeenCalledWith("confirm_quit");
  });

  it("안전 종료: 사용자가 '그래도 종료' 를 누를 때만 confirm_quit", async () => {
    const invoke = vi.fn(async () => null);
    const api = { kisPaperEodShutdownCheck: vi.fn(async () => safe) };
    const { getByTestId } = render(<DesktopTrayBridge enabled api={api} invoke={invoke} />);
    act(() => { window.__agentTraderDesktop.requestQuit(); });
    await waitFor(() => expect(getByTestId("tray-quit").textContent).toBe("종료"));
    fireEvent.click(getByTestId("tray-quit"));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("confirm_quit"));
  });

  it("확인 실패 시에도 위험 경고 (조용히 종료 허용 안 함)", async () => {
    const api = { kisPaperEodShutdownCheck: vi.fn(async () => { throw new Error("down"); }) };
    const { getByTestId } = render(<DesktopTrayBridge enabled api={api} invoke={vi.fn()} />);
    act(() => { window.__agentTraderDesktop.requestQuit(); });
    await waitFor(() => expect(getByTestId("tray-check-failed")).toBeTruthy());
    expect(getByTestId("tray-quit").textContent).toContain("그래도 종료");
  });

  it("상태 확인: 정보만 표시 (종료 버튼 없음)", async () => {
    const api = { kisPaperEodShutdownCheck: vi.fn(async () => risky) };
    const { getByTestId, queryByTestId } = render(<DesktopTrayBridge enabled api={api} invoke={vi.fn()} />);
    act(() => { window.__agentTraderDesktop.showStatus(); });
    await waitFor(() => expect(getByTestId("tray-check").textContent).toContain("보유: 1종목"));
    expect(queryByTestId("tray-quit")).toBeNull();
  });
});
