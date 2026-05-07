import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DemoModeBanner,
  EmptyState,
  ErrorState,
  LoadingState,
  MetricCard,
  PageHeader,
  SectionHeader,
  StatusBadge,
  StatusPill,
} from "./primitives";


describe("UI-001 primitives", () => {
  afterEach(cleanup);

  it("PageHeader renders title and subtitle", () => {
    const { getByTestId, getByText } = render(
      <PageHeader title="대시보드" subtitle="2025-05-07 운용 현황" />
    );
    const header = getByTestId("ui-page-header");
    expect(header.textContent).toContain("대시보드");
    expect(getByText(/2025-05-07/)).toBeTruthy();
  });

  it("PageHeader supports right slot for badges", () => {
    const { getByText } = render(
      <PageHeader title="X" right={<span>SIM</span>} />
    );
    expect(getByText("SIM")).toBeTruthy();
  });

  it("SectionHeader includes sub-label when provided", () => {
    const { container, getByText } = render(
      <SectionHeader sub="(advisory)">긴급 정지</SectionHeader>
    );
    expect(getByText("긴급 정지")).toBeTruthy();
    expect(container.textContent).toContain("(advisory)");
  });

  it("MetricCard displays label/value/sub with optional color", () => {
    const { getByTestId } = render(
      <MetricCard label="총자산" value="1,234,567원" sub="현금 200,000원"
                  color="#22c55e" testId="m1" />
    );
    const m = getByTestId("m1");
    expect(m.textContent).toContain("총자산");
    expect(m.textContent).toContain("1,234,567원");
    expect(m.textContent).toContain("현금 200,000원");
  });

  it("StatusBadge applies status color via CSS var", () => {
    const { getByTestId } = render(
      <StatusBadge status="success" testId="b1">READY</StatusBadge>
    );
    const b = getByTestId("b1");
    expect(b.textContent).toContain("READY");
    // CSS variable is in style; jsdom resolves to literal in style attr.
    expect(b.style.color).toContain("var(--c-success)");
  });

  it("StatusPill renders dot + label", () => {
    const { getByTestId } = render(
      <StatusPill status="warning" testId="p1">CAUTION</StatusPill>
    );
    const p = getByTestId("p1");
    expect(p.textContent).toContain("CAUTION");
    expect(p.querySelector(".ui-status-pill__dot")).toBeTruthy();
  });

  it("EmptyState shows icon/title/hint", () => {
    const { getByTestId } = render(
      <EmptyState icon="📭" title="없음" hint="아직 데이터가 없습니다" testId="e1" />
    );
    const e = getByTestId("e1");
    expect(e.textContent).toContain("📭");
    expect(e.textContent).toContain("없음");
    expect(e.textContent).toContain("아직 데이터가 없습니다");
  });

  it("ErrorState shows default title and optional retry button", () => {
    const onRetry = vi.fn();
    const { getByTestId, getByText } = render(
      <ErrorState hint="잠시 후 다시 시도하세요" onRetry={onRetry} testId="er" />
    );
    const e = getByTestId("er");
    expect(e.textContent).toContain("데이터 조회 실패");
    fireEvent.click(getByText("다시 시도"));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("LoadingState renders default title", () => {
    const { getByTestId } = render(<LoadingState testId="ld" />);
    expect(getByTestId("ld").textContent).toContain("로딩 중");
  });

  it("DemoModeBanner shows title and body", () => {
    const { getByTestId } = render(
      <DemoModeBanner body="mock 데이터로 동작합니다" hint="hint here" />
    );
    const b = getByTestId("ui-demo-banner");
    expect(b.textContent).toContain("Demo Mode");
    expect(b.textContent).toContain("mock 데이터로 동작합니다");
    expect(b.textContent).toContain("hint here");
  });
});
