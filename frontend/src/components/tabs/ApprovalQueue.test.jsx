/**
 * 체크리스트 #61: ApprovalQueue sub-component 테스트.
 *
 * 검증 invariant:
 *   - "주문은 아직 실행되지 않았습니다" 안내 항상 노출 (ApprovalProposalSummary)
 *   - reasons 비면 "표시 가능한 리스크 사유 없음" + 재검증 안내 (ApprovalRiskSummary)
 *   - TTL 우선, 없으면 age stale fallback (ApprovalFreshnessBadge)
 *   - stale + approve일 때만 stale 경고 (ApproveConfirmSummary)
 *   - LIVE 주문 발주 / "Place Order" / "지금 매수" 같은 라벨 0개
 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  ApprovalFreshnessBadge,
  ApprovalProposalSummary,
  ApprovalRiskSummary,
  ApprovalActionBar,
  ApprovalQueueEmptyState,
  ApproveConfirmSummary,
  categorizeRiskReasons,
} from "./ApprovalQueue";


afterEach(cleanup);


function _approval(overrides = {}) {
  return {
    id: 17,
    symbol: "005930",
    side: "BUY",
    quantity: 5,
    order_type: "MARKET",
    limit_price: null,
    mode: "LIVE_MANUAL_APPROVAL",
    created_at: new Date(Date.now() - 60_000).toISOString(),  // 1분 전
    request_source: "MANUAL",
    request_source_label: "수동 주문",
    reasons: [],
    attempts: [],
    ...overrides,
  };
}


// ====================================================================
// ApprovalFreshnessBadge
// ====================================================================


describe("<ApprovalFreshnessBadge>", () => {
  it("uses TTL countdown when backend provides expires_at + seconds_until_expiry", () => {
    const a = _approval({
      expires_at:           new Date(Date.now() + 120_000).toISOString(),
      seconds_until_expiry: 120,
      is_expired:           false,
    });
    const { getByTestId } = render(<ApprovalFreshnessBadge approval={a} />);
    const badge = getByTestId(`approval-freshness-badge-${a.id}`);
    expect(badge.getAttribute("data-state")).toBe("fresh");
    expect(badge.textContent).toMatch(/2분 후 만료/);
  });

  it("marks nearing expiry (< 60s)", () => {
    const a = _approval({
      expires_at:           new Date(Date.now() + 30_000).toISOString(),
      seconds_until_expiry: 30,
      is_expired:           false,
    });
    const { getByTestId } = render(<ApprovalFreshnessBadge approval={a} />);
    const badge = getByTestId(`approval-freshness-badge-${a.id}`);
    expect(badge.getAttribute("data-state")).toBe("nearing");
    expect(badge.textContent).toMatch(/30초 후 만료/);
  });

  it("marks expired when seconds_until_expiry <= 0 or is_expired true", () => {
    const a = _approval({
      expires_at:           new Date(Date.now() - 60_000).toISOString(),
      seconds_until_expiry: 0,
      is_expired:           true,
    });
    const { getByTestId } = render(<ApprovalFreshnessBadge approval={a} />);
    const badge = getByTestId(`approval-freshness-badge-${a.id}`);
    expect(badge.getAttribute("data-state")).toBe("expired");
    expect(badge.textContent).toMatch(/만료됨/);
  });

  it("falls back to age-based display when no TTL", () => {
    const a = _approval({ created_at: new Date(Date.now() - 60_000).toISOString() });
    const { getByTestId } = render(<ApprovalFreshnessBadge approval={a} />);
    const badge = getByTestId(`approval-freshness-badge-${a.id}`);
    expect(badge.getAttribute("data-state")).toBe("fresh");
    expect(badge.textContent).toMatch(/생성 후/);
    expect(badge.textContent).not.toMatch(/만료/);
  });

  it("flags stale age (10분+) when no TTL", () => {
    const a = _approval({
      created_at: new Date(Date.now() - 15 * 60_000).toISOString(),  // 15분 전
    });
    const { getByTestId } = render(<ApprovalFreshnessBadge approval={a} />);
    const badge = getByTestId(`approval-freshness-badge-${a.id}`);
    expect(badge.getAttribute("data-state")).toBe("stale");
    expect(badge.textContent).toMatch(/신호 노후/);
  });
});


// ====================================================================
// ApprovalProposalSummary
// ====================================================================


describe("<ApprovalProposalSummary>", () => {
  it("always renders 'order not executed' note (invariant)", () => {
    const { getByTestId } = render(
      <ApprovalProposalSummary approval={_approval()} />
    );
    expect(getByTestId("proposal-not-order-note").textContent)
      .toMatch(/주문은 아직 실행되지 않았습니다/);
  });

  it("renders AI source badge for AI proposals", () => {
    const a = _approval({
      request_source: "AI",
      request_source_label: "AI 제안",
      strategy: "ai_assist:execution_recommender",
      signal_confidence: 75,
    });
    const { getByTestId, getByText } = render(
      <ApprovalProposalSummary approval={a} />
    );
    const wrapper = getByTestId(`approval-proposal-summary-${a.id}`);
    expect(wrapper.getAttribute("data-source")).toBe("AI");
    expect(getByText(/🤖.*AI 제안/)).toBeTruthy();
    expect(getByText("ai_assist:execution_recommender")).toBeTruthy();
    expect(getByText("conf 75")).toBeTruthy();
  });

  it("renders strategy source badge for STRATEGY proposals", () => {
    const a = _approval({
      request_source: "STRATEGY",
      request_source_label: "전략 신호",
      strategy: "sma_crossover",
    });
    const { getByTestId, getByText } = render(
      <ApprovalProposalSummary approval={a} />
    );
    const wrapper = getByTestId(`approval-proposal-summary-${a.id}`);
    expect(wrapper.getAttribute("data-source")).toBe("STRATEGY");
    expect(getByText(/📊.*전략 신호/)).toBeTruthy();
  });

  it("renders supporting / opposing / risk_note from ai_decision_meta", () => {
    const a = _approval({
      request_source: "AI",
      ai_decision_meta: {
        supporting_reasons: ["earnings_beat", "regime_match"],
        opposing_reasons:   ["high_volatility"],
        risk_note:          "stop_loss_only",
      },
    });
    const { getByTestId } = render(<ApprovalProposalSummary approval={a} />);
    expect(getByTestId("proposal-supporting-reasons").textContent)
      .toMatch(/earnings_beat/);
    expect(getByTestId("proposal-opposing-reasons").textContent)
      .toMatch(/high_volatility/);
    expect(getByTestId("proposal-risk-note").textContent)
      .toMatch(/stop_loss_only/);
  });

  it("renders expected reward / risk / R:R when ai_decision_meta has them", () => {
    const a = _approval({
      ai_decision_meta: {
        expected_reward:   200_000,
        expected_risk:     100_000,
        risk_reward_ratio: 2.0,
      },
    });
    const { getByTestId } = render(<ApprovalProposalSummary approval={a} />);
    const rr = getByTestId("proposal-rr");
    expect(rr.textContent).toMatch(/예상 수익/);
    expect(rr.textContent).toMatch(/예상 손실/);
    expect(rr.textContent).toMatch(/R:R/);
    expect(rr.textContent).toMatch(/2.00/);
  });

  it("renders nothing for null approval", () => {
    const { container } = render(<ApprovalProposalSummary approval={null} />);
    expect(container.firstChild).toBeNull();
  });
});


// ====================================================================
// ApprovalRiskSummary
// ====================================================================


describe("<ApprovalRiskSummary>", () => {
  it("renders 'no reasons + revalidate notice' when reasons empty (no 'risk free' assertion)", () => {
    const { getByTestId } = render(
      <ApprovalRiskSummary approval={_approval({ reasons: [] })} />
    );
    const wrapper = getByTestId("approval-risk-summary-17");
    expect(wrapper.getAttribute("data-empty")).toBe("true");
    expect(wrapper.textContent).toMatch(/표시 가능한 리스크 사유 없음/);
    expect(wrapper.textContent).toMatch(/검증/);   // "승인 시점에 백엔드가 다시 … 검증"
    // 위험 없음으로 단언 금지
    expect(wrapper.textContent).not.toMatch(/위험 없음/);
    expect(wrapper.textContent).not.toMatch(/안전함/);
  });

  it("categorizes freshness reasons", () => {
    const a = _approval({
      reasons: ["stale price exceeded 60s", "market is closed"],
    });
    const { getByTestId, queryByTestId } = render(
      <ApprovalRiskSummary approval={a} />
    );
    expect(getByTestId("risk-category-freshness").textContent)
      .toMatch(/stale price/);
    expect(queryByTestId("risk-category-position")).toBeNull();
  });

  it("categorizes position reasons", () => {
    const a = _approval({
      reasons: ["max_positions exceeded", "max_symbol_exposure 한도 초과"],
    });
    const { getByTestId } = render(<ApprovalRiskSummary approval={a} />);
    expect(getByTestId("risk-category-position").textContent)
      .toMatch(/max_positions/);
  });

  it("categorizes loss reasons", () => {
    const a = _approval({ reasons: ["daily_loss limit breached"] });
    const { getByTestId } = render(<ApprovalRiskSummary approval={a} />);
    expect(getByTestId("risk-category-loss").textContent)
      .toMatch(/daily_loss/);
  });

  it("categorizes AI reasons", () => {
    const a = _approval({
      reasons: ["AI rate_limit exceeded", "ai_reasoning missing"],
    });
    const { getByTestId } = render(<ApprovalRiskSummary approval={a} />);
    expect(getByTestId("risk-category-ai")).toBeTruthy();
  });

  it("categorizes guard reasons", () => {
    const a = _approval({
      reasons: ["duplicate fingerprint detected"],
    });
    const { getByTestId } = render(<ApprovalRiskSummary approval={a} />);
    expect(getByTestId("risk-category-guard").textContent)
      .toMatch(/duplicate/);
  });

  it("falls into 'other' bucket for unmatched reasons", () => {
    const a = _approval({ reasons: ["some random unmatched reason"] });
    const { getByTestId } = render(<ApprovalRiskSummary approval={a} />);
    expect(getByTestId("risk-category-other").textContent)
      .toMatch(/random unmatched/);
  });

  it("categorizeRiskReasons pure function correctly buckets", () => {
    const out = categorizeRiskReasons([
      "stale price", "max_positions 한도", "daily_loss 한도",
      "ai_rate_limit", "duplicate fingerprint", "그 외 사유",
    ]);
    expect(out.freshness.length).toBe(1);
    expect(out.position.length).toBe(1);
    expect(out.loss.length).toBe(1);
    expect(out.ai.length).toBe(1);
    expect(out.guard.length).toBe(1);
    expect(out.other.length).toBe(1);
  });
});


// ====================================================================
// ApproveConfirmSummary
// ====================================================================


describe("<ApproveConfirmSummary>", () => {
  it("renders symbol / side / qty / mode / strategy / confidence", () => {
    const a = _approval({
      strategy: "sma_crossover",
      signal_confidence: 82,
    });
    const { getByTestId } = render(
      <ApproveConfirmSummary approval={a} action="approve" />
    );
    const summary = getByTestId("approve-confirm-summary");
    expect(summary.textContent).toMatch(/005930/);
    expect(summary.textContent).toMatch(/BUY/);
    expect(summary.textContent).toMatch(/5주/);
    expect(summary.textContent).toMatch(/LIVE_MANUAL_APPROVAL/);
    expect(summary.textContent).toMatch(/sma_crossover/);
    expect(summary.textContent).toMatch(/conf 82/);
  });

  it("renders stale warning only for approve + stale (>10분)", () => {
    const a = _approval({
      created_at: new Date(Date.now() - 15 * 60_000).toISOString(),
    });
    const { getByTestId } = render(
      <ApproveConfirmSummary approval={a} action="approve" />
    );
    const warn = getByTestId("approve-confirm-stale-warning");
    expect(warn.textContent).toMatch(/이 신호는 오래되었습니다/);
  });

  it("does NOT render stale warning for reject action even if stale", () => {
    const a = _approval({
      created_at: new Date(Date.now() - 15 * 60_000).toISOString(),
    });
    const { queryByTestId } = render(
      <ApproveConfirmSummary approval={a} action="reject" />
    );
    expect(queryByTestId("approve-confirm-stale-warning")).toBeNull();
  });

  it("does NOT render stale warning for cancel action even if stale", () => {
    const a = _approval({
      created_at: new Date(Date.now() - 15 * 60_000).toISOString(),
    });
    const { queryByTestId } = render(
      <ApproveConfirmSummary approval={a} action="cancel" />
    );
    expect(queryByTestId("approve-confirm-stale-warning")).toBeNull();
  });

  it("does NOT render stale warning for fresh approve", () => {
    const a = _approval({
      created_at: new Date(Date.now() - 30_000).toISOString(),
    });
    const { queryByTestId } = render(
      <ApproveConfirmSummary approval={a} action="approve" />
    );
    expect(queryByTestId("approve-confirm-stale-warning")).toBeNull();
  });

  it("shows top 3 reasons + overflow count", () => {
    const a = _approval({
      reasons: ["r1", "r2", "r3", "r4", "r5"],
    });
    const { getByTestId } = render(
      <ApproveConfirmSummary approval={a} action="approve" />
    );
    const reasons = getByTestId("approve-confirm-reasons");
    expect(reasons.textContent).toMatch(/r1/);
    expect(reasons.textContent).toMatch(/r2/);
    expect(reasons.textContent).toMatch(/r3/);
    expect(reasons.textContent).toMatch(/외 2건/);
    // 4번째와 5번째는 합쳐서 "외 2건"으로 표시
    expect(reasons.textContent).not.toMatch(/r4/);
  });
});


// ====================================================================
// ApprovalActionBar — invariant: no LIVE order labels
// ====================================================================


describe("<ApprovalActionBar>", () => {
  it("renders only 승인/거부/취소 buttons — no LIVE order trigger labels", () => {
    const { queryByText, getByText } = render(
      <ApprovalActionBar
        onApprove={() => {}} onReject={() => {}} onCancel={() => {}}
      />
    );
    // 정상 라벨 존재
    expect(getByText(/✓ 승인/)).toBeTruthy();
    expect(getByText(/✗ 거부/)).toBeTruthy();
    expect(getByText(/⊘ 취소/)).toBeTruthy();
    // 금지 라벨 부재 (invariant lock)
    expect(queryByText(/즉시 매수/)).toBeNull();
    expect(queryByText(/지금 주문/)).toBeNull();
    expect(queryByText(/Place Order/i)).toBeNull();
    expect(queryByText(/실거래/)).toBeNull();
    expect(queryByText(/LIVE 활성화/)).toBeNull();
  });
});


// ====================================================================
// ApprovalQueueEmptyState
// ====================================================================


describe("<ApprovalQueueEmptyState>", () => {
  it("renders friendly empty message — no raw 'Failed to fetch'", () => {
    const { getByTestId } = render(<ApprovalQueueEmptyState kind="empty" />);
    const body = getByTestId("approval-queue-empty");
    expect(body.textContent).toMatch(/승인 대기 항목이 없습니다/);
    expect(body.textContent).not.toMatch(/Failed to fetch/);
    expect(body.textContent).not.toMatch(/undefined/);
    expect(body.textContent).not.toMatch(/null/);
  });

  it("renders demo hint for kind=demo", () => {
    const { getByTestId } = render(<ApprovalQueueEmptyState kind="demo" />);
    expect(getByTestId("approval-queue-empty-demo").textContent)
      .toMatch(/GitHub Pages 데모/);
  });

  it("renders loading state for kind=loading", () => {
    const { getByTestId } = render(<ApprovalQueueEmptyState kind="loading" />);
    expect(getByTestId("approval-queue-empty-loading").textContent)
      .toMatch(/불러오는 중/);
  });
});
