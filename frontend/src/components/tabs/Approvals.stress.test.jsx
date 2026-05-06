// 133 (MUST): 대량 데이터 렌더 stress test.
//
// 운영 환경에서 audit / approvals history가 수백~수천 row로 누적되면 frontend
// 가 DOM 폭증으로 멈추는 사고가 자주 일어난다. 본 file은 대표 component
// (Approvals)를 큰 데이터셋으로 렌더해 시간/안정성 invariant를 둔다.
//
// SLA는 환경 의존이라 assert는 관대하게 — '터지지 않고 1초 내'를 기준.

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Approvals } from "./Approvals";


function _h(id, overrides = {}) {
  return {
    id, symbol: `S${(id % 100).toString().padStart(2, "0")}`,
    side: id % 2 === 0 ? "BUY" : "SELL", quantity: 1,
    order_type: "MARKET", limit_price: null,
    status: ["APPROVED", "REJECTED", "CANCELLED"][id % 3],
    mode: id % 2 === 0 ? "LIVE_MANUAL_APPROVAL" : "LIVE_AI_ASSIST",
    created_at: new Date(Date.now() - id * 60_000).toISOString(),
    decided_at: new Date(Date.now() - (id - 5) * 60_000).toISOString(),
    decided_by: "ops1",
    note: "",
    audit_id: id,
    reasons: [],
    attempts: [],
    ...overrides,
  };
}

function _approvals({ pending = [], history = [] } = {}) {
  return {
    pending, history,
    loading: false, error: "", busy: false,
    historyHasMore: false, historyLoadingMore: false,
    approve: vi.fn(), reject: vi.fn(), cancel: vi.fn(), cancelMany: vi.fn(),
    refresh: vi.fn(), refreshHistory: vi.fn(), loadMoreHistory: vi.fn(),
  };
}


describe("<Approvals> renders large datasets without crashing (133)", () => {
  afterEach(cleanup);

  it("renders 500 history rows without crashing under 3s", () => {
    const history = Array.from({ length: 500 }, (_, i) => _h(i + 1));
    const t0 = performance.now();
    const { container } = render(<Approvals approvals={_approvals({ history })} operatorName="" />);
    const elapsed = performance.now() - t0;

    // 500 rows must all render to DOM — 첫 invariant.
    const rows = container.querySelectorAll('[data-testid^="approval-history-row-"]');
    expect(rows.length).toBe(500);
    // SLA는 환경 의존(jsdom + Windows powershell + node CI는 브라우저보다 느림)
    // 이라 관대한 3s 임계 — 회귀로 두 자릿수 늘어나면 catch. 운영 브라우저에선
    // sub-second.
    expect(elapsed).toBeLessThan(3000);
  });

  it("renders 200 PENDING rows + 500 history rows together without crashing", () => {
    const pending = Array.from({ length: 200 }, (_, i) => ({
      id: 10_000 + i, symbol: `P${i % 50}`, side: "BUY", quantity: 1,
      order_type: "MARKET", limit_price: null,
      mode: "LIVE_MANUAL_APPROVAL",
      created_at: new Date(Date.now() - i * 30_000).toISOString(),
      reasons: [], attempts: [],
    }));
    const history = Array.from({ length: 500 }, (_, i) => _h(i + 1));

    const { container } = render(
      <Approvals approvals={_approvals({ pending, history })} operatorName="" />,
    );
    expect(container.querySelectorAll('[data-testid^="approval-pending-row-"]').length).toBe(200);
    expect(container.querySelectorAll('[data-testid^="approval-history-row-"]').length).toBe(500);
  });
});
