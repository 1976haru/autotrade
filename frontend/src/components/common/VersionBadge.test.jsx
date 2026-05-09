import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ReleaseNotesModal,
  VersionBadge,
  _readLastSeenVersion,
  _writeLastSeenVersion,
  useReleaseNotesAutoPopup,
} from "./VersionBadge";
import { APP_INFO } from "../../config/appInfo";


// localStorage 정리 — 테스트 간 자동 팝업 상태가 누적되지 않도록.
beforeEach(() => {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem("agent-trader-last-seen-version");
  }
});
afterEach(cleanup);


describe("<VersionBadge>", () => {
  it("renders APP_INFO version + label", () => {
    const { getByTestId } = render(<VersionBadge onClick={() => {}} />);
    const btn = getByTestId("version-badge");
    expect(btn.textContent).toContain(APP_INFO.releaseLabel);
    expect(btn.textContent).toContain(APP_INFO.version);
  });

  it("calls onClick when clicked", () => {
    const onClick = vi.fn();
    const { getByTestId } = render(<VersionBadge onClick={onClick} />);
    fireEvent.click(getByTestId("version-badge"));
    expect(onClick).toHaveBeenCalled();
  });
});


describe("<ReleaseNotesModal>", () => {
  it("does NOT render when open=false", () => {
    const { queryByTestId } = render(
      <ReleaseNotesModal open={false} onClose={() => {}} />,
    );
    expect(queryByTestId("release-notes-modal")).toBeNull();
  });

  it("renders the latest release note when open", () => {
    const { getByTestId } = render(
      <ReleaseNotesModal open={true} onClose={() => {}} />,
    );
    const modal = getByTestId("release-notes-modal");
    expect(modal.textContent).toContain("Agent Trader v1");
    expect(modal.textContent).toContain("v1.0.0");
    expect(modal.textContent).toContain("에이전트 트레이더 v1 첫 공개");
  });

  it("renders highlights and safety notes", () => {
    const { getByTestId } = render(
      <ReleaseNotesModal open={true} onClose={() => {}} />,
    );
    expect(getByTestId("release-notes-highlights").textContent)
      .toMatch(/AI 에이전트|RiskManager/);
    const safety = getByTestId("release-notes-safety");
    expect(safety.textContent).toMatch(/실거래 자동매매 허가 버전이 아닙니다/);
    expect(safety.textContent).toMatch(/API key|Secret|계좌번호/);
  });

  it("ack button calls onClose AND writes lastSeenVersion to localStorage", () => {
    const onClose = vi.fn();
    const { getByTestId } = render(
      <ReleaseNotesModal open={true} onClose={onClose} />,
    );
    fireEvent.click(getByTestId("release-notes-ack"));
    expect(onClose).toHaveBeenCalled();
    expect(_readLastSeenVersion()).toBe(APP_INFO.version);
  });

  it("close button calls onClose without writing localStorage", () => {
    const onClose = vi.fn();
    const { getByTestId } = render(
      <ReleaseNotesModal open={true} onClose={onClose} />,
    );
    fireEvent.click(getByTestId("release-notes-close"));
    expect(onClose).toHaveBeenCalled();
    // 닫기는 lastSeen 저장 X — 다음 접속 시 자동 재팝업
    expect(_readLastSeenVersion()).toBeNull();
  });

  it("backdrop click also closes modal", () => {
    const onClose = vi.fn();
    const { getByTestId } = render(
      <ReleaseNotesModal open={true} onClose={onClose} />,
    );
    fireEvent.click(getByTestId("release-notes-backdrop"));
    expect(onClose).toHaveBeenCalled();
  });

  it("does NOT contain BUY/SELL/HOLD/즉시 주문 buttons", () => {
    const { container } = render(
      <ReleaseNotesModal open={true} onClose={() => {}} />,
    );
    const buttons = container.querySelectorAll("button");
    for (const b of buttons) {
      const t = b.textContent || "";
      expect(t).not.toMatch(/BUY|SELL|HOLD|매수|매도|즉시 주문/);
    }
  });
});


describe("useReleaseNotesAutoPopup", () => {
  function _Probe() {
    const { open } = useReleaseNotesAutoPopup();
    return <div data-testid="auto-popup-state">{open ? "OPEN" : "CLOSED"}</div>;
  }

  it("opens on first visit (no lastSeenVersion)", async () => {
    const { findByTestId } = render(<_Probe />);
    const el = await findByTestId("auto-popup-state");
    await waitFor(() => expect(el.textContent).toBe("OPEN"));
  });

  it("opens when lastSeenVersion is older than current", async () => {
    _writeLastSeenVersion("0.9.0");
    const { findByTestId } = render(<_Probe />);
    const el = await findByTestId("auto-popup-state");
    await waitFor(() => expect(el.textContent).toBe("OPEN"));
  });

  it("does NOT open when lastSeenVersion matches current", async () => {
    _writeLastSeenVersion(APP_INFO.version);
    const { findByTestId } = render(<_Probe />);
    const el = await findByTestId("auto-popup-state");
    // hook은 mount 시 1회 평가 — 짧게 기다린 후 여전히 CLOSED여야 함.
    await new Promise((r) => setTimeout(r, 50));
    expect(el.textContent).toBe("CLOSED");
  });
});
