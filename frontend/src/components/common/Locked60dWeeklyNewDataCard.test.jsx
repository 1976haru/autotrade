/**
 * Locked60dWeeklyNewDataCard 테스트 — 상태/verdict/rule-hash/insufficient + 주문버튼 0 + 경고.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Locked60dWeeklyNewDataCard } from "./Locked60dWeeklyNewDataCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    available: true,
    final_verdict: "NEW_DATA_INSUFFICIENT",
    locked_rule_name: "FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1",
    rule_hash_match: true, no_parameter_change: true, no_look_ahead: true,
    new_trading_days: 0,
    new_data_quality: { newest_existing: "2026-05-22", newest_extra: "2026-05-22",
                        duplicate_removed_count: 21, quality_status: "FAIL" },
    exe_rebuild_recommendation: "EXE 재빌드 보류 — 추가 forward 데이터 부족(미존재). 새 기간 확보 후 재검증.",
    paper_rehearsal_recommendation: "Paper 리허설 아직 불가 — 추가 forward 데이터 확보 후 재검증 필요",
    live_trading_recommendation: false, real_order_allowed: false, dry_run_required: true,
    new_data_only: { note: "미실행" }, old_vs_new_decay: {},
    conclusions: ["추가 forward 기간 미존재로 새 데이터 검증 보류"],
    next_steps: ["새 거래일 ≥20 쌓이면 동일 룰 재검증"],
    do_not_auto_apply: true, auto_apply_allowed: false, is_live_authorization: false,
    broker_order_sent: false, order_created: false, exe_build_executed: false,
    contains_secret: false, no_profit_guarantee: true,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { locked60dWeeklyNewDataLatest: vi.fn(async () => report) };
}

describe("<Locked60dWeeklyNewDataCard>", () => {
  it("완료: verdict + rule hash + EXE 권고 + 실전금지", async () => {
    render(<Locked60dWeeklyNewDataCard apiClient={_api()} />);
    await screen.findByTestId("nd-verdict");
    expect(screen.getByTestId("nd-final-verdict").textContent).toContain("NEW_DATA_INSUFFICIENT");
    expect(screen.getByTestId("nd-rule").textContent).toContain("rule_hash_match=true");
    expect(screen.getByTestId("nd-exe-rec").textContent).toContain("보류");
    expect(screen.getByTestId("nd-live").textContent).toContain("dry_run 필수");
  });

  it("INSUFFICIENT 안내 + 새 거래일/품질 표시", async () => {
    render(<Locked60dWeeklyNewDataCard apiClient={_api()} />);
    await screen.findByTestId("nd-verdict");
    expect(screen.getByTestId("nd-new-days").textContent).toContain("0");
    expect(screen.getByTestId("nd-data").textContent).toContain("2026-05-22");
    expect(screen.getByTestId("nd-insufficient").textContent).toContain("미존재");
  });

  it("데이터 없음", async () => {
    render(<Locked60dWeeklyNewDataCard apiClient={_api(_report({ available: false }))} />);
    expect(await screen.findByTestId("nd-empty")).toBeTruthy();
    expect(screen.queryByTestId("nd-verdict")).toBeNull();
  });

  it("실패", async () => {
    const api = { locked60dWeeklyNewDataLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<Locked60dWeeklyNewDataCard apiClient={api} />);
    expect(await screen.findByTestId("nd-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 시작 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<Locked60dWeeklyNewDataCard apiClient={_api()} />);
    await screen.findByTestId("nd-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전|주문|적용|자동매매 시작|매수|매도|Place Order|빌드 시작/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<Locked60dWeeklyNewDataCard apiClient={_api()} />);
    await screen.findByTestId("nd-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("한글 위험 경고 문구 노출", async () => {
    const { container } = render(<Locked60dWeeklyNewDataCard apiClient={_api()} />);
    await screen.findByTestId("nd-warning");
    expect(container.textContent).toContain("연구/백테스트 결과이며 실전매매 권고가 아닙니다");
  });

  it("NEW_DATA_PAPER_CANDIDATE verdict + new-only 결과 표시", async () => {
    render(<Locked60dWeeklyNewDataCard apiClient={_api(_report({
      final_verdict: "NEW_DATA_PAPER_CANDIDATE",
      new_trading_days: 40,
      new_data_only: { forward_return_pct: 6.0, median_pf: 1.2, forward_mdd_pct: 10, total_trades: 80 },
      old_vs_new_decay: { decay_pp: 1.0, old_full_return: 8.3, new_only_return: 7.3 } }))} />);
    await screen.findByTestId("nd-verdict");
    expect(screen.getByTestId("nd-final-verdict").textContent).toContain("PAPER_CANDIDATE");
    expect(screen.getByTestId("nd-newonly").textContent).toContain("6");
    expect(screen.getByTestId("nd-decay").textContent).toContain("decay");
    expect(screen.queryByTestId("nd-insufficient")).toBeNull();
  });
});
