/**
 * #4-RiskProfileUI: AgentRiskProfileSelector 단위 테스트.
 *
 * Invariants (테스트로 lock):
 * - 3 라디오 카드 (보수적 / 안정적 / 공격적) 모두 노출.
 * - 기본값은 BALANCED — value 미주입 시 BALANCED 가 selected.
 * - 클릭 시 onChange 콜백 호출.
 * - AGGRESSIVE 선택 시 "실거래 안전장치를 우회하지 않습니다" 경고 노출.
 * - Paper 전용 · 실거래 아님 영구 배지.
 * - "지금 매수" / "지금 매도" / "Place Order" / "실거래 시작" /
 *   "ENABLE_LIVE_TRADING" / "ENABLE_AI_EXECUTION" 라벨 button 0개.
 * - secret 입력 form (input/textarea) 0건.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import AgentRiskProfileSelector, {
  DEFAULT_RISK_PROFILE,
  RISK_PROFILES,
} from "./AgentRiskProfileSelector";


afterEach(cleanup);


describe("AgentRiskProfileSelector — base UI", () => {
  it("renders the section label + Paper-only badge + default note", () => {
    render(<AgentRiskProfileSelector value="BALANCED" />);
    expect(screen.getByTestId("agent-risk-profile-selector")).toBeTruthy();
    expect(screen.getByTestId("risk-profile-section-label").textContent)
      .toContain("AI 운용 성향");
    const badge = screen.getByTestId("risk-profile-paper-only-badge");
    expect(badge.textContent).toContain("Paper 전용");
    expect(badge.textContent).toContain("실거래 아님");
    expect(screen.getByTestId("risk-profile-default-note").textContent)
      .toContain("안정적");
  });

  it("renders all three profile cards", () => {
    render(<AgentRiskProfileSelector value="BALANCED" />);
    expect(screen.getByTestId("risk-profile-card-CONSERVATIVE")).toBeTruthy();
    expect(screen.getByTestId("risk-profile-card-BALANCED")).toBeTruthy();
    expect(screen.getByTestId("risk-profile-card-AGGRESSIVE")).toBeTruthy();
  });

  it("each card carries Korean label + summary + detail", () => {
    render(<AgentRiskProfileSelector value="BALANCED" />);
    const cons = screen.getByTestId("risk-profile-card-CONSERVATIVE");
    expect(cons.textContent).toContain("보수적");
    expect(cons.textContent).toContain("진입 적게");
    const bal = screen.getByTestId("risk-profile-card-BALANCED");
    expect(bal.textContent).toContain("안정적");
    expect(bal.textContent).toContain("균형");
    const agg = screen.getByTestId("risk-profile-card-AGGRESSIVE");
    expect(agg.textContent).toContain("공격적");
    expect(agg.textContent).toContain("후보 더 많이");
  });

  it("RISK_PROFILES catalog has 3 entries with expected values", () => {
    expect(RISK_PROFILES).toHaveLength(3);
    expect(RISK_PROFILES.map((p) => p.value)).toEqual([
      "CONSERVATIVE", "BALANCED", "AGGRESSIVE",
    ]);
  });

  it("DEFAULT_RISK_PROFILE exports as BALANCED", () => {
    expect(DEFAULT_RISK_PROFILE).toBe("BALANCED");
  });

  it("default selection is BALANCED when value missing/invalid", () => {
    render(<AgentRiskProfileSelector value={undefined} />);
    const group = screen.getByTestId("risk-profile-radiogroup");
    expect(group.getAttribute("data-selected")).toBe("BALANCED");
  });

  it("default selection falls back to BALANCED for unknown value", () => {
    render(<AgentRiskProfileSelector value="EXTREME" />);
    const group = screen.getByTestId("risk-profile-radiogroup");
    expect(group.getAttribute("data-selected")).toBe("BALANCED");
  });
});


describe("AgentRiskProfileSelector — selection state", () => {
  it("marks the selected card with data-selected=true", () => {
    render(<AgentRiskProfileSelector value="AGGRESSIVE" />);
    const agg = screen.getByTestId("risk-profile-card-AGGRESSIVE");
    expect(agg.getAttribute("data-selected")).toBe("true");
    const cons = screen.getByTestId("risk-profile-card-CONSERVATIVE");
    expect(cons.getAttribute("data-selected")).toBe("false");
  });

  it("radio sub-element reflects selection via aria-checked", () => {
    render(<AgentRiskProfileSelector value="CONSERVATIVE" />);
    const radio = screen.getByTestId("risk-profile-radio-CONSERVATIVE");
    expect(radio.getAttribute("aria-checked")).toBe("true");
  });
});


describe("AgentRiskProfileSelector — onChange callback", () => {
  it("clicking CONSERVATIVE card calls onChange with 'CONSERVATIVE'", () => {
    const onChange = vi.fn();
    render(<AgentRiskProfileSelector value="BALANCED" onChange={onChange} />);
    fireEvent.click(screen.getByTestId("risk-profile-card-CONSERVATIVE"));
    expect(onChange).toHaveBeenCalledWith("CONSERVATIVE");
  });

  it("clicking BALANCED card calls onChange with 'BALANCED'", () => {
    const onChange = vi.fn();
    render(<AgentRiskProfileSelector value="CONSERVATIVE" onChange={onChange} />);
    fireEvent.click(screen.getByTestId("risk-profile-card-BALANCED"));
    expect(onChange).toHaveBeenCalledWith("BALANCED");
  });

  it("clicking AGGRESSIVE card calls onChange with 'AGGRESSIVE'", () => {
    const onChange = vi.fn();
    render(<AgentRiskProfileSelector value="BALANCED" onChange={onChange} />);
    fireEvent.click(screen.getByTestId("risk-profile-card-AGGRESSIVE"));
    expect(onChange).toHaveBeenCalledWith("AGGRESSIVE");
  });

  it("clicking the already-selected card does NOT fire onChange", () => {
    const onChange = vi.fn();
    render(<AgentRiskProfileSelector value="BALANCED" onChange={onChange} />);
    fireEvent.click(screen.getByTestId("risk-profile-card-BALANCED"));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("disabled cards do not fire onChange", () => {
    const onChange = vi.fn();
    render(
      <AgentRiskProfileSelector
        value="BALANCED" onChange={onChange} disabled={true}
      />,
    );
    fireEvent.click(screen.getByTestId("risk-profile-card-AGGRESSIVE"));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("missing onChange does not throw", () => {
    render(<AgentRiskProfileSelector value="BALANCED" />);
    // Should not throw.
    fireEvent.click(screen.getByTestId("risk-profile-card-CONSERVATIVE"));
  });
});


describe("AgentRiskProfileSelector — AGGRESSIVE safety warning", () => {
  it("shows the warning only when AGGRESSIVE is selected", () => {
    const { rerender } = render(<AgentRiskProfileSelector value="BALANCED" />);
    expect(screen.queryByTestId("risk-profile-aggressive-warning")).toBeNull();
    rerender(<AgentRiskProfileSelector value="AGGRESSIVE" />);
    const warn = screen.getByTestId("risk-profile-aggressive-warning");
    expect(warn.textContent).toContain("공격적");
    expect(warn.textContent).toContain("실거래 안전장치를 우회하지 않습니다");
    expect(warn.textContent).toContain("ENABLE_LIVE_TRADING=false");
  });

  it("AGGRESSIVE warning text does NOT promote live trading", () => {
    render(<AgentRiskProfileSelector value="AGGRESSIVE" />);
    const warn = screen.getByTestId("risk-profile-aggressive-warning");
    // 우회 불가 안내문이지 실거래 활성화 안내가 아님.
    expect(warn.textContent).not.toContain("지금 매수");
    expect(warn.textContent).not.toContain("Place Order");
    // "실거래 활성화 시작" 등 enabling 문구 부재.
    expect(warn.textContent).not.toContain("실거래 시작");
    expect(warn.textContent).not.toContain("ENABLE_LIVE_TRADING=true");
  });
});


describe("AgentRiskProfileSelector — invariants", () => {
  it("contains zero forbidden order labels", () => {
    const { container } = render(<AgentRiskProfileSelector value="AGGRESSIVE" />);
    const text = container.textContent || "";
    const forbidden = [
      "지금 매수", "지금 매도", "Place Order",
      "실거래 시작", "AI 자동매매 켜기",
      "ENABLE_LIVE_TRADING=true", "ENABLE_AI_EXECUTION=true",
      "ENABLE_FUTURES_LIVE_TRADING=true",
    ];
    for (const f of forbidden) {
      expect(text).not.toContain(f);
    }
  });

  it("renders zero text inputs / textareas (no secret entry surface)", () => {
    render(<AgentRiskProfileSelector value="AGGRESSIVE" />);
    expect(screen.queryAllByRole("textbox").length).toBe(0);
    // Selector profile cards ARE buttons (3 radio cards) — that's expected.
    // The invariant is that none of them carry order/live labels.
  });

  it("only the 3 profile cards are buttons; no order/Live buttons present", () => {
    render(<AgentRiskProfileSelector value="AGGRESSIVE" />);
    const buttons = screen.queryAllByRole("button");
    expect(buttons).toHaveLength(3);
    for (const btn of buttons) {
      // 각 카드는 "보수적" / "안정적" / "공격적" 라벨 — 주문 라벨 없음.
      const t = btn.textContent || "";
      expect(t).not.toContain("Place Order");
      expect(t).not.toContain("지금 매수");
      expect(t).not.toContain("실거래 시작");
    }
  });

  it("footer note describes Paper-only invariants", () => {
    render(<AgentRiskProfileSelector value="BALANCED" />);
    const note = screen.getByTestId("risk-profile-footer-note");
    expect(note.textContent).toContain("is_order_signal=false");
    expect(note.textContent).toContain("auto_apply_allowed=false");
    expect(note.textContent).toContain("is_live_authorization=false");
  });
});
