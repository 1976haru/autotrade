import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ConfigureCard, PositionBlock, RegimeIndicator, ScoreboardCard,
  SignalQualityBadge, StrategyContractPanel,
} from "./LiveEngine";


// 137: backend client 모킹 — 컴포넌트가 fetch하는 endpoint를 spy.
vi.mock("../../services/backend/client", () => ({
  backendApi: {
    engineScoreboard: vi.fn(),
  },
}));
import { backendApi } from "../../services/backend/client";


const _REGISTRY = [
  {
    name: "sma_crossover",
    class_name: "SmaCrossoverStrategy",
    description: "단기/장기 이동평균 교차 전략",
    params: [
      { name: "short", type: "int", default: 5,  required: false },
      { name: "long",  type: "int", default: 20, required: false },
    ],
  },
  {
    name: "needs_threshold",
    class_name: "ThresholdStrategy",
    description: "synthetic test strategy",
    params: [
      { name: "threshold", type: "int", default: null, required: true },
    ],
  },
];


describe("<ConfigureCard>", () => {
  afterEach(cleanup);

  it("shows a loading placeholder while registry is null", () => {
    const { getByText } = render(<ConfigureCard busy={false} registry={null} onConfigure={() => {}} />);
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("shows an empty-registry message when no strategies are registered", () => {
    const { getByText } = render(<ConfigureCard busy={false} registry={[]} onConfigure={() => {}} />);
    expect(getByText(/등록된 전략이 없습니다/)).toBeTruthy();
  });

  it("renders a dropdown with one option per registered strategy", async () => {
    const { container } = render(
      <ConfigureCard busy={false} registry={_REGISTRY} onConfigure={() => {}} />,
    );
    const select = container.querySelector("select");
    expect(select).toBeTruthy();
    expect(select.options).toHaveLength(2);
    expect(select.options[0].value).toBe("sma_crossover");
    expect(select.options[1].value).toBe("needs_threshold");
  });

  it("renders param inputs prefilled from defaults when a strategy is selected", async () => {
    const { container } = render(
      <ConfigureCard busy={false} registry={_REGISTRY} onConfigure={() => {}} />,
    );
    // sma_crossover is the default-selected strategy; both params should appear
    // with their defaults populated as strings.
    await waitFor(() => {
      const inputs = container.querySelectorAll('input[type="number"]');
      // 2 strategy params + quantity input = 3
      expect(inputs.length).toBe(3);
    });
    const numberInputs = container.querySelectorAll('input[type="number"]');
    expect(numberInputs[0].value).toBe("5");   // short
    expect(numberInputs[1].value).toBe("20");  // long
    expect(numberInputs[2].value).toBe("1");   // quantity default
  });

  it("submits typed param values via onConfigure", async () => {
    const onConfigure = vi.fn();
    const { container, getByRole } = render(
      <ConfigureCard busy={false} registry={_REGISTRY} onConfigure={onConfigure} />,
    );

    await waitFor(() => {
      expect(container.querySelectorAll('input[type="number"]').length).toBe(3);
    });

    fireEvent.click(getByRole("button"));
    expect(onConfigure).toHaveBeenCalledTimes(1);
    expect(onConfigure).toHaveBeenCalledWith({
      strategy: "sma_crossover",
      params:   { short: 5, long: 20 },
      quantity: 1,
    });
  });

  it("resets param values to the new strategy's schema when selection changes", async () => {
    const onConfigure = vi.fn();
    const { container, getByRole } = render(
      <ConfigureCard busy={false} registry={_REGISTRY} onConfigure={onConfigure} />,
    );

    const select = container.querySelector("select");
    await act(async () => {
      fireEvent.change(select, { target: { value: "needs_threshold" } });
    });

    await waitFor(() => {
      // sma's two params replaced by threshold's single param + quantity = 2
      expect(container.querySelectorAll('input[type="number"]').length).toBe(2);
    });

    // threshold has default=null/required so its input is empty.
    const inputs = container.querySelectorAll('input[type="number"]');
    expect(inputs[0].value).toBe("");
    // submit it; empty cast yields undefined, so params should be {} — backend
    // will then surface a validation error rather than the frontend silently
    // sending threshold=NaN.
    fireEvent.click(getByRole("button"));
    expect(onConfigure).toHaveBeenCalledWith({
      strategy: "needs_threshold",
      params:   {},
      quantity: 1,
    });
  });

  it("disables the submit button while busy", () => {
    const { getByRole } = render(
      <ConfigureCard busy={true} registry={_REGISTRY} onConfigure={() => {}} />,
    );
    expect(getByRole("button").disabled).toBe(true);
  });
});


describe("<PositionBlock>", () => {
  afterEach(cleanup);

  function _status(overrides = {}) {
    return {
      entry_price: 75_000, last_price: 76_000,
      unrealized_pnl: 10_000, unrealized_pnl_pct: 0.0133,
      ...overrides,
    };
  }

  it("renders entry / current / pnl values with green when profitable", () => {
    const { container, getByText } = render(<PositionBlock status={_status()} />);
    expect(getByText("75,000원")).toBeTruthy();   // entry
    expect(getByText("76,000원")).toBeTruthy();   // current
    const pnlBlock = container.querySelector('[data-testid="position-block"]');
    expect(pnlBlock.textContent).toContain("+10,000");
    expect(pnlBlock.textContent).toContain("+1.33%");
  });

  it("uses red color and minus sign when pnl is negative", () => {
    const { container } = render(
      <PositionBlock status={_status({ unrealized_pnl: -5_000, unrealized_pnl_pct: -0.0667 })} />,
    );
    const block = container.querySelector('[data-testid="position-block"]');
    expect(block.textContent).toContain("-5,000");
    expect(block.textContent).toContain("-6.67%");
    // At least one descendant carries the red color inline.
    const reds = Array.from(block.querySelectorAll("*"))
      .filter((el) => el.style?.color === "rgb(239, 68, 68)"); // #ef4444
    expect(reds.length).toBeGreaterThan(0);
  });

  it("falls back to em-dashes when fields are null", () => {
    const { container } = render(
      <PositionBlock status={{
        entry_price: 75_000, last_price: null,
        unrealized_pnl: null, unrealized_pnl_pct: null,
      }} />,
    );
    const block = container.querySelector('[data-testid="position-block"]');
    expect(block.textContent).toContain("75,000원");
    expect(block.textContent).toContain("—"); // last_price + pnl
  });
});


describe("<StrategyContractPanel> (131)", () => {
  afterEach(cleanup);

  it("renders nothing for null/undefined strategy", () => {
    const { container } = render(<StrategyContractPanel strategy={null} />);
    expect(container.querySelector('[data-testid="strategy-contract-panel"]')).toBeNull();
  });

  it("surfaces all 5 contract fields when populated", () => {
    const strategy = {
      name: "sma_crossover",
      entry: "단기 SMA 상향 돌파",
      exit:  "단기 SMA 하향 돌파",
      invalidation: "추세 전환",
      required_regime: "trending",
      risk_profile: { position_size_pct: 5, stop_loss_pct: 2 },
    };
    const { getByTestId } = render(<StrategyContractPanel strategy={strategy} />);
    const panel = getByTestId("strategy-contract-panel");
    expect(panel.textContent).toContain("진입");
    expect(panel.textContent).toContain("단기 SMA 상향 돌파");
    expect(panel.textContent).toContain("청산");
    expect(panel.textContent).toContain("단기 SMA 하향 돌파");
    expect(panel.textContent).toContain("무효화");
    expect(panel.textContent).toContain("추세 전환");
    expect(panel.textContent).toContain("trending");
    expect(panel.textContent).toContain("position_size_pct=5");
    expect(panel.textContent).toContain("stop_loss_pct=2");
  });

  it("flags missing entry/exit/invalidation as '(미작성)'", () => {
    const { getByTestId } = render(
      <StrategyContractPanel strategy={{
        name: "bare", entry: "", exit: "", invalidation: "",
        required_regime: "any", risk_profile: {},
      }} />,
    );
    const panel = getByTestId("strategy-contract-panel");
    // "(미작성)" appears 4 times: entry, exit, invalidation, risk_profile.
    // required_regime="any" also flags missing.
    const occurrences = panel.textContent.match(/미작성/g) || [];
    expect(occurrences.length).toBeGreaterThanOrEqual(4);
  });

  it("treats required_regime='any' as missing (regime hint not declared)", () => {
    const { getByTestId } = render(
      <StrategyContractPanel strategy={{
        name: "bare", entry: "x", exit: "x", invalidation: "x",
        required_regime: "any", risk_profile: { x: 1 },
      }} />,
    );
    expect(getByTestId("strategy-contract-panel").textContent).toContain("(미작성)");
  });

  it("renders risk_profile as 'k=v · k=v' inline", () => {
    const { getByTestId } = render(
      <StrategyContractPanel strategy={{
        name: "x", entry: "x", exit: "x", invalidation: "x",
        required_regime: "trending",
        risk_profile: { position_size_pct: 5, stop_loss_pct: 2, max_concurrent: 1 },
      }} />,
    );
    const text = getByTestId("strategy-contract-panel").textContent;
    expect(text).toContain("position_size_pct=5");
    expect(text).toContain("stop_loss_pct=2");
    expect(text).toContain("max_concurrent=1");
  });
});


describe("<RegimeIndicator> (135)", () => {
  afterEach(cleanup);

  it("renders the regime label with the canonical color when matching", () => {
    const { getByTestId } = render(
      <RegimeIndicator status={{
        current_regime: "trending_up", regime_matches_strategy: true,
      }} />,
    );
    const ind = getByTestId("regime-indicator");
    expect(ind.dataset.regime).toBe("trending_up");
    expect(ind.dataset.matches).toBe("true");
    expect(ind.textContent).toContain("상승 추세");
  });

  it("flags mismatch with amber background and warning text", () => {
    const { getByTestId } = render(
      <RegimeIndicator status={{
        current_regime: "ranging", regime_matches_strategy: false,
      }} />,
    );
    const ind = getByTestId("regime-indicator");
    expect(ind.dataset.matches).toBe("false");
    expect(getByTestId("regime-mismatch-warning")).toBeTruthy();
  });

  it("renders 'any' label when bars are insufficient", () => {
    const { getByTestId } = render(
      <RegimeIndicator status={{
        current_regime: "any", regime_matches_strategy: true,
      }} />,
    );
    expect(getByTestId("regime-indicator").textContent).toContain("분류 전");
  });

  it("falls back to raw label for unknown regimes (forward-compat)", () => {
    const { getByTestId } = render(
      <RegimeIndicator status={{
        current_regime: "future_regime_xyz", regime_matches_strategy: true,
      }} />,
    );
    expect(getByTestId("regime-indicator").textContent).toContain("future_regime_xyz");
  });

  it("treats missing status fields as 'any' / matching", () => {
    const { getByTestId } = render(<RegimeIndicator status={{}} />);
    const ind = getByTestId("regime-indicator");
    expect(ind.dataset.regime).toBe("any");
    expect(ind.dataset.matches).toBe("true");
  });
});


describe("<ScoreboardCard> (137)", () => {
  beforeEach(() => { backendApi.engineScoreboard.mockReset(); });
  afterEach(cleanup);

  it("shows '아직 backtest 기록이 없습니다' when API returns empty", async () => {
    backendApi.engineScoreboard.mockResolvedValue([]);
    const { findByText } = render(<ScoreboardCard />);
    expect(await findByText(/아직 backtest 기록이 없습니다/)).toBeTruthy();
  });

  it("renders a row per strategy with aggregated columns", async () => {
    backendApi.engineScoreboard.mockResolvedValue([
      { strategy: "sma_crossover", runs: 3, total_pnl: 500_000,
        avg_pnl: 166_667, best_pnl: 300_000, worst_pnl: -100_000,
        wins: 12, losses: 8, win_rate: 0.6,
        live_trades: 0, live_pnl: 0, live_wins: 0, live_losses: 0, live_win_rate: 0 },
      { strategy: "rsi_revert",   runs: 1, total_pnl: -50_000,
        avg_pnl: -50_000, best_pnl: -50_000, worst_pnl: -50_000,
        wins: 2, losses: 8, win_rate: 0.2,
        live_trades: 0, live_pnl: 0, live_wins: 0, live_losses: 0, live_win_rate: 0 },
    ]);
    const { findByTestId } = render(<ScoreboardCard />);
    const sma = await findByTestId("scoreboard-row-sma_crossover");
    expect(sma.textContent).toContain("sma_crossover");
    expect(sma.textContent).toContain("3");          // runs
    expect(sma.textContent).toContain("+500,000");
    expect(sma.textContent).toContain("60%");        // win rate
    const rsi = await findByTestId("scoreboard-row-rsi_revert");
    expect(rsi.textContent).toContain("-50,000");
    expect(rsi.textContent).toContain("20%");
  });

  // 144: live PnL columns surface
  it("renders live trade/PnL columns when scoreboard returns live aggregates", async () => {
    backendApi.engineScoreboard.mockResolvedValue([
      { strategy: "sma_crossover", runs: 1, total_pnl: 100_000,
        avg_pnl: 100_000, best_pnl: 100_000, worst_pnl: 100_000,
        wins: 5, losses: 5, win_rate: 0.5,
        live_trades: 4, live_pnl: 25_000,
        live_wins: 3, live_losses: 1, live_win_rate: 0.75 },
    ]);
    const { findByTestId } = render(<ScoreboardCard />);
    const trades = await findByTestId("scoreboard-live-trades-sma_crossover");
    expect(trades.textContent).toContain("4");
    const pnl = await findByTestId("scoreboard-live-pnl-sma_crossover");
    expect(pnl.textContent).toContain("+25,000");
    const row = await findByTestId("scoreboard-row-sma_crossover");
    expect(row.textContent).toContain("75%"); // live_win_rate
  });

  it("defaults live columns to 0 when backend omits them (back-compat)", async () => {
    // Older backend (pre-144) doesn't include live_* fields — FE should not NaN.
    backendApi.engineScoreboard.mockResolvedValue([
      { strategy: "old_strat", runs: 2, total_pnl: 1000,
        avg_pnl: 500, best_pnl: 1000, worst_pnl: 0,
        wins: 1, losses: 1, win_rate: 0.5 },
    ]);
    const { findByTestId } = render(<ScoreboardCard />);
    const trades = await findByTestId("scoreboard-live-trades-old_strat");
    expect(trades.textContent).toContain("0");
    const pnl = await findByTestId("scoreboard-live-pnl-old_strat");
    // "+0" formatted — fmtKRW(0) might output "0" or "+0"; just verify no NaN/undefined.
    expect(pnl.textContent).not.toContain("NaN");
    expect(pnl.textContent).not.toContain("undefined");
  });

  it("surfaces fetch error inline", async () => {
    backendApi.engineScoreboard.mockRejectedValue(new Error("scoreboard down"));
    const { findByText } = render(<ScoreboardCard />);
    expect(await findByText(/scoreboard down/)).toBeTruthy();
  });

  it("clicking 새로고침 re-fetches", async () => {
    backendApi.engineScoreboard.mockResolvedValue([]);
    const { getByText, findByText } = render(<ScoreboardCard />);
    await findByText(/아직/);
    fireEvent.click(getByText("새로고침"));
    // 1 mount + 1 click
    expect(backendApi.engineScoreboard).toHaveBeenCalledTimes(2);
  });
});


describe("<SignalQualityBadge> (136)", () => {
  afterEach(cleanup);

  it("renders nothing when quality is null", () => {
    const { container } = render(<SignalQualityBadge quality={null} signal="BUY" />);
    expect(container.querySelector('[data-testid="signal-quality-badge"]')).toBeNull();
  });

  it("renders nothing for HOLD signal even if quality is provided", () => {
    const { container } = render(
      <SignalQualityBadge quality={{ strength: 80, confidence: 90 }} signal="HOLD" />,
    );
    expect(container.querySelector('[data-testid="signal-quality-badge"]')).toBeNull();
  });

  it("renders strength + confidence mini-bars for BUY/SELL", () => {
    const { getByTestId } = render(
      <SignalQualityBadge quality={{ strength: 80, confidence: 60 }} signal="BUY" />,
    );
    expect(getByTestId("signal-quality-badge")).toBeTruthy();
    expect(getByTestId("signal-quality-strength").textContent).toContain("80");
    expect(getByTestId("signal-quality-confidence").textContent).toContain("60");
  });

  it("low strength rendered with red color (advisory severity)", () => {
    const { getByTestId } = render(
      <SignalQualityBadge quality={{ strength: 20, confidence: 90 }} signal="BUY" />,
    );
    const numCell = getByTestId("signal-quality-strength").lastElementChild;
    expect(numCell.style.color).toBe("rgb(239, 68, 68)"); // #ef4444
  });

  it("high values rendered green", () => {
    const { getByTestId } = render(
      <SignalQualityBadge quality={{ strength: 85, confidence: 75 }} signal="SELL" />,
    );
    const numCell = getByTestId("signal-quality-strength").lastElementChild;
    expect(numCell.style.color).toBe("rgb(34, 197, 94)"); // #22c55e
  });
});
