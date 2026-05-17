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
import { latestWelcomeNote } from "../../config/releaseNotes";


// localStorage 정리 — 신규/legacy 둘 다 비워야 자동 팝업 상태가 누적되지 않음.
beforeEach(() => {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem("agent-trader-last-seen-version");
    window.localStorage.removeItem("agent-trader-welcome-ack");
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

  it("renders the welcome note when open (RELEASE_NOTES empty)", () => {
    const { getByTestId } = render(
      <ReleaseNotesModal open={true} onClose={() => {}} />,
    );
    const modal = getByTestId("release-notes-modal");
    // 본 PR 시점 RELEASE_NOTES 비어 있음 → welcome 노출.
    expect(modal.textContent).toContain("Agent Trader v1");
    expect(modal.textContent).toContain("초기 안내");
    // welcome kind 명시.
    expect(modal.getAttribute("data-note-kind")).toBe("welcome");
  });

  it("shows '초기 안내' badge + disclaimer when kind=welcome", () => {
    const { getByTestId } = render(
      <ReleaseNotesModal open={true} onClose={() => {}} />,
    );
    expect(getByTestId("release-notes-welcome-badge").textContent)
      .toContain("초기 안내");
    expect(getByTestId("release-notes-welcome-disclaimer").textContent)
      .toContain("프로그램 소개");
    expect(getByTestId("release-notes-welcome-disclaimer").textContent)
      .toContain("UpdateBanner");
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

  it("ack button label is '이번 안내 확인' for welcome note", () => {
    const { getByTestId } = render(
      <ReleaseNotesModal open={true} onClose={() => {}} />,
    );
    expect(getByTestId("release-notes-ack").textContent).toBe("이번 안내 확인");
  });

  it("ack button calls onClose AND writes welcome-ack to localStorage", () => {
    const onClose = vi.fn();
    const { getByTestId } = render(
      <ReleaseNotesModal open={true} onClose={onClose} />,
    );
    fireEvent.click(getByTestId("release-notes-ack"));
    expect(onClose).toHaveBeenCalled();
    // ack 가 welcome note 의 version 으로 저장됨.
    expect(_readLastSeenVersion()).toBe(latestWelcomeNote().version);
  });

  it("close button calls onClose without writing localStorage", () => {
    const onClose = vi.fn();
    const { getByTestId } = render(
      <ReleaseNotesModal open={true} onClose={onClose} />,
    );
    fireEvent.click(getByTestId("release-notes-close"));
    expect(onClose).toHaveBeenCalled();
    // 닫기는 ack 저장 X — 다음 접속 시 자동 재팝업.
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

  // fix/update-banner-stale-release-notes: 새 invariant —
  // GitHub Release fetch 실패와 welcome 노트는 *완전히 별개*. welcome 모달이
  // "최신 업데이트"라는 표현으로 둔갑하지 않아야 한다.
  it("welcome modal does NOT claim to be '최신 릴리스' / '최신 업데이트'", () => {
    const { container } = render(
      <ReleaseNotesModal open={true} onClose={() => {}} />,
    );
    const text = container.textContent || "";
    expect(text).toContain("초기 안내");
    expect(text).toContain("최신 릴리스 변경 내역 아님");
  });
});


describe("useReleaseNotesAutoPopup", () => {
  function _Probe() {
    const { open } = useReleaseNotesAutoPopup();
    return <div data-testid="auto-popup-state">{open ? "OPEN" : "CLOSED"}</div>;
  }

  it("opens on first visit (no ack)", async () => {
    const { findByTestId } = render(<_Probe />);
    const el = await findByTestId("auto-popup-state");
    await waitFor(() => expect(el.textContent).toBe("OPEN"));
  });

  it("opens when ack version differs from welcome note version", async () => {
    _writeLastSeenVersion("0.9.0");
    const { findByTestId } = render(<_Probe />);
    const el = await findByTestId("auto-popup-state");
    await waitFor(() => expect(el.textContent).toBe("OPEN"));
  });

  it("does NOT open when ack matches welcome note version", async () => {
    _writeLastSeenVersion(latestWelcomeNote().version);
    const { findByTestId } = render(<_Probe />);
    const el = await findByTestId("auto-popup-state");
    // hook은 mount 시 1회 평가 — 짧게 기다린 후 여전히 CLOSED여야 함.
    await new Promise((r) => setTimeout(r, 50));
    expect(el.textContent).toBe("CLOSED");
  });

  // fix/update-banner-stale-release-notes: backwards compat — legacy key
  // "agent-trader-last-seen-version" 에 이미 ack 가 있으면 재팝업 안 함.
  it("respects legacy storage key for ack (backwards compat)", async () => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(
        "agent-trader-last-seen-version",
        latestWelcomeNote().version,
      );
    }
    const { findByTestId } = render(<_Probe />);
    const el = await findByTestId("auto-popup-state");
    await new Promise((r) => setTimeout(r, 50));
    expect(el.textContent).toBe("CLOSED");
  });
});
