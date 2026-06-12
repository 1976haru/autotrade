import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react";

import { RuntimeConfigCard } from "./RuntimeConfigCard";

const cfg = (over = {}) => ({
  max_concurrent_positions: { value: 5, min: 1, max: 10 },
  per_stock_budget: { value: 1_000_000, min: 100_000, max: 10_000_000 },
  daily_buy_limit_krw: 3_000_000,
  ...over,
});

afterEach(() => cleanup());

describe("RuntimeConfigCard (R3/R4)", () => {
  it("F2: config=null → 로딩, 저장 버튼 없음(서버값 모른 채 저장 금지)", () => {
    const { getByTestId, queryByTestId } = render(<RuntimeConfigCard config={null} api={{}} />);
    expect(getByTestId("rtcfg-loading")).toBeTruthy();
    expect(queryByTestId("rtcfg-save")).toBeNull();
  });

  it("F2: config._failed → 실패 표시 + 저장 버튼 없음(임의값 금지)", () => {
    const { getByTestId, queryByTestId } = render(<RuntimeConfigCard config={{ _failed: true }} api={{}} />);
    expect(getByTestId("rtcfg-fail").textContent).toContain("불러오지 못했어요");
    expect(queryByTestId("rtcfg-save")).toBeNull();
    expect(queryByTestId("rtcfg-mc-value")).toBeNull();   // 9/300만 같은 임의값 0
  });

  it("스테퍼 초기값 = 서버 실효값", () => {
    const { getByTestId } = render(<RuntimeConfigCard config={cfg()} api={{}} />);
    expect(getByTestId("rtcfg-mc-value").textContent).toContain("5개");
    expect(getByTestId("rtcfg-bud-value").textContent).toContain("1,000,000");
  });

  it("동시진입 스테퍼 범위 클램프(상한 10에서 + 비활성)", () => {
    const { getByTestId } = render(
      <RuntimeConfigCard config={cfg({ max_concurrent_positions: { value: 10, min: 1, max: 10 } })} api={{}} />);
    expect(getByTestId("rtcfg-mc-inc").disabled).toBe(true);
  });

  it("종목당 투자금 하한 10만에서 − 비활성", () => {
    const { getByTestId } = render(
      <RuntimeConfigCard config={cfg({ per_stock_budget: { value: 100_000, min: 100_000, max: 10_000_000 } })} api={{}} />);
    expect(getByTestId("rtcfg-bud-dec").disabled).toBe(true);
  });

  it("저장 버튼: 실효값과 동일하면 비활성", () => {
    const { getByTestId } = render(<RuntimeConfigCard config={cfg()} api={{}} />);
    expect(getByTestId("rtcfg-save").disabled).toBe(true);
  });

  it("값 변경 후 저장 → PUT 호출 + 서버 응답값으로 onSaved(낙관적 갱신 아님)", async () => {
    const put = vi.fn(async () => cfg({ max_concurrent_positions: { value: 3, min: 1, max: 10 } }));
    const onSaved = vi.fn();
    const { getByTestId } = render(<RuntimeConfigCard config={cfg()} api={{ runtimeConfigPut: put }} onSaved={onSaved} />);
    fireEvent.click(getByTestId("rtcfg-mc-dec")); // 5 → 4
    fireEvent.click(getByTestId("rtcfg-mc-dec")); // 4 → 3
    fireEvent.click(getByTestId("rtcfg-save"));
    await waitFor(() => expect(put).toHaveBeenCalledWith({ max_concurrent_positions: 3, per_stock_budget: 1_000_000 }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(getByTestId("rtcfg-note").textContent).toContain("적용됐어요");
    expect(getByTestId("rtcfg-note").textContent).toContain("보유 중인 종목은 그대로");
  });

  it("저장 실패 → 정직한 실패 표시 (가짜 성공 금지)", async () => {
    const put = vi.fn(async () => { throw new Error("HTTP 400"); });
    const { getByTestId } = render(<RuntimeConfigCard config={cfg()} api={{ runtimeConfigPut: put }} />);
    fireEvent.click(getByTestId("rtcfg-mc-inc"));
    fireEvent.click(getByTestId("rtcfg-save"));
    await waitFor(() => expect(getByTestId("rtcfg-note").textContent).toContain("저장 실패"));
    expect(getByTestId("rtcfg-note").textContent).not.toContain("적용됐어요");
  });

  it("정합 표시(충돌): 종목당 × 종목수 > 일일 한도 → 약 N종목 + 한도 모자람 안내", () => {
    // 1,000,000 × 5 = 5,000,000 > 3,000,000 → affordable = floor(3M / 1M) = 3
    const { getByTestId } = render(<RuntimeConfigCard config={cfg()} api={{}} />);
    const w = getByTestId("rtcfg-affordable");
    expect(w.textContent).toContain("3종목");
    expect(w.textContent).toContain("3,000,000");
    expect(w.textContent).toContain("모자라요");
  });

  it("정합 표시(충돌 없음): 한도 이내면 약 N종목만 안내(경고 문구 없음)", () => {
    // 500,000 × 5 = 2,500,000 ≤ 3,000,000 → affordable = floor(3M / 500k) = 6, 모자람 문구 없음
    const { getByTestId } = render(
      <RuntimeConfigCard config={cfg({ per_stock_budget: { value: 500_000, min: 100_000, max: 10_000_000 } })} api={{}} />);
    const w = getByTestId("rtcfg-affordable");
    expect(w.textContent).toContain("6종목");
    expect(w.textContent).not.toContain("모자라요");
  });

  // ── T2: 일일 매수 한도 스테퍼 ───────────────────────────────────────────────
  const cfgDl = (over = {}) => cfg({
    daily_buy_limit_krw: { value: 3_000_000, min: 3_000_000, max: 300_000_000 },
    ...over,
  });

  it("T2: 일일 매수 한도 meta(객체) 있으면 스테퍼 표시 + 값", () => {
    const { getByTestId } = render(<RuntimeConfigCard config={cfgDl()} api={{}} />);
    expect(getByTestId("rtcfg-dl-value").textContent).toContain("3,000,000");
  });

  it("T2: 일일 한도는 평수(구 백엔드)면 스테퍼 미표시(호환)", () => {
    const { queryByTestId } = render(<RuntimeConfigCard config={cfg()} api={{}} />);
    expect(queryByTestId("rtcfg-dl-value")).toBeNull();   // cfg()는 plain int
  });

  it("T2: 일일 한도 변경 저장 → PUT 에 daily_buy_limit_krw 포함", async () => {
    const put = vi.fn(async () => cfgDl({ daily_buy_limit_krw: { value: 4_000_000, min: 3_000_000, max: 300_000_000 } }));
    const { getByTestId } = render(<RuntimeConfigCard config={cfgDl()} api={{ runtimeConfigPut: put }} />);
    fireEvent.click(getByTestId("rtcfg-dl-inc")); // 3,000,000 → 4,000,000 (100만 단위)
    fireEvent.click(getByTestId("rtcfg-save"));
    await waitFor(() => expect(put).toHaveBeenCalledWith(expect.objectContaining({ daily_buy_limit_krw: 4_000_000 })));
  });

  // ── C1: 손절/익절 스테퍼 ──────────────────────────────────────────────────
  const cfgSt = (over = {}) => cfg({
    stop_loss_pct: { value: 2, min: 0.5, max: 10 },
    take_profit_pct: { value: 3.5, min: 0.5, max: 20 },
    ...over,
  });

  it("V1: 손절/익절 스테퍼 값은 양수 %(스테퍼 +/− 와 부호 겹침 방지)", () => {
    const { getByTestId } = render(<RuntimeConfigCard config={cfgSt()} api={{}} />);
    expect(getByTestId("rtcfg-sl-value").textContent).toBe("2%");
    expect(getByTestId("rtcfg-tp-value").textContent).toBe("3.5%");
  });

  it("C1: 손절 변경 저장 → PUT 에 stop_loss_pct 포함 + 보유 적용 안내", async () => {
    const put = vi.fn(async () => cfgSt({ stop_loss_pct: { value: 2.5, min: 0.5, max: 10 } }));
    const { getByTestId } = render(<RuntimeConfigCard config={cfgSt()} api={{ runtimeConfigPut: put }} />);
    fireEvent.click(getByTestId("rtcfg-sl-inc")); // 2 → 2.5
    fireEvent.click(getByTestId("rtcfg-save"));
    await waitFor(() => expect(put).toHaveBeenCalledWith({
      max_concurrent_positions: 5, per_stock_budget: 1_000_000,
      stop_loss_pct: 2.5, take_profit_pct: 3.5,
    }));
    await waitFor(() => expect(getByTestId("rtcfg-note").textContent).toContain("보유 종목에도 적용"));
  });

  it("C1: 손절/익절 meta 없으면 스테퍼 미표시(구 백엔드 호환)", () => {
    const { queryByTestId } = render(<RuntimeConfigCard config={cfg()} api={{}} />);
    expect(queryByTestId("rtcfg-sl-value")).toBeNull();
  });

});
