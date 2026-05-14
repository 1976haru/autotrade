/**
 * #92 Release Readiness Report Card 테스트.
 *
 * 요구 사항:
 * - verdict 별 헤드라인 (READY_TO_TAG / READY_WITH_CAVEATS / DO_NOT_TAG /
 *   INSUFFICIENT_DATA)
 * - 4 invariant 배지 영구 노출 ("실거래 허가 아님" / "자동 .env 수정 안 함" /
 *   "release 자동 태깅 안 함" / "주문 신호 아님")
 * - failed / warnings / actions 리스트 렌더링
 * - secret 입력 form (input / textarea) 0개
 * - 실거래 시작 / 릴리스 자동 태깅 / Place Order 라벨 button 0개
 * - secret 패턴 노출 0건 (KIS_APP_KEY / sk- / bearer 등)
 * - 세부 항목 펼치기 토글
 * - markdown 미리보기 토글
 */

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    releaseReadinessEvaluate: vi.fn(),
    releaseReadinessMarkdown: vi.fn(),
  },
}));


import { backendApi } from "../../services/backend/client";
import { ReleaseReadinessCard } from "./ReleaseReadinessCard";


const _READY_RESULT = {
  target_release_tag: "v1.0.0-beta.5",
  release_kind: "BETA",
  verdict: "READY_TO_TAG",
  items: [
    { name: "kis_is_paper_safety", category: "safety_flags",
      severity: "PASS", required: true, message: "KIS_IS_PAPER=true 안전",
      detail: {} },
    { name: "pre_market_check", category: "pre_market",
      severity: "PASS", required: true, message: "READY_TO_START",
      detail: {} },
    { name: "system_audit_recency", category: "system_hygiene",
      severity: "PASS", required: true, message: "2일 전", detail: {} },
  ],
  failed_required: [],
  warnings: [],
  required_actions: [
    "READY 라벨은 *릴리스 자동 허가가 아닙니다* — 운영자가 직접 별도 PR / git tag / GitHub Release 생성으로 진행.",
  ],
  operator_note: "",
  is_live_authorization: false,
  auto_apply_allowed: false,
  is_order_signal: false,
  live_flag_changed: false,
  mode_changed: false,
  generated_at: new Date().toISOString(),
};


const _BLOCKED_RESULT = {
  ..._READY_RESULT,
  verdict: "DO_NOT_TAG",
  items: [
    ..._READY_RESULT.items,
    { name: "enable_live_trading_safety", category: "safety_flags",
      severity: "FAIL", required: true,
      message: "ENABLE_LIVE_TRADING=true — 베타 / RC / Stable 어느 단계에서도 금지",
      detail: {} },
  ],
  failed_required: ["enable_live_trading_safety"],
  required_actions: ["required FAIL 항목을 모두 해결 후 재평가."],
};


const _WARN_RESULT = {
  ..._READY_RESULT,
  verdict: "READY_WITH_CAVEATS",
  items: [
    ..._READY_RESULT.items,
    { name: "desktop_sidecar_build", category: "desktop_build",
      severity: "WARN", required: false,
      message: "backend sidecar 미빌드 — scripts/build_backend_sidecar.ps1 실행 필요",
      detail: {} },
  ],
  warnings: ["desktop_sidecar_build: backend sidecar 미빌드 — ..."],
};


const _INSUFFICIENT_RESULT = {
  ..._READY_RESULT,
  verdict: "INSUFFICIENT_DATA",
  items: [
    { name: "system_audit_recency", category: "system_hygiene",
      severity: "UNKNOWN", required: true,
      message: "마지막 시스템 hygiene audit 일시 입력 없음", detail: {} },
  ],
  failed_required: [],
  required_actions: ["required 항목 데이터를 채워서 재평가."],
};


beforeEach(() => {
  for (const k of Object.keys(backendApi)) {
    if (typeof backendApi[k]?.mockReset === "function") {
      backendApi[k].mockReset();
    }
  }
  backendApi.releaseReadinessEvaluate.mockResolvedValue(_READY_RESULT);
  backendApi.releaseReadinessMarkdown.mockResolvedValue({
    markdown: "# Release Readiness Report — v1.0.0-beta.5\n\n_release_kind: **BETA**_\n",
    verdict: "READY_TO_TAG",
  });
});

afterEach(cleanup);


describe("ReleaseReadinessCard — verdict 렌더링", () => {
  it("READY_TO_TAG 에서 '릴리스 태그 검토 가능' 헤드라인", () => {
    const { getByTestId } = render(
      <ReleaseReadinessCard resultOverride={_READY_RESULT} />,
    );
    const head = getByTestId("release-readiness-headline");
    expect(head.textContent).toContain("릴리스 태그 검토 가능");
    expect(head.textContent).toContain("READY_TO_TAG");
  });

  it("DO_NOT_TAG 에서 '릴리스 금지' 헤드라인 + 실패 항목 표시", () => {
    const { getByTestId } = render(
      <ReleaseReadinessCard resultOverride={_BLOCKED_RESULT} />,
    );
    expect(getByTestId("release-readiness-headline").textContent)
      .toContain("릴리스 금지");
    expect(getByTestId("release-readiness-failed").textContent)
      .toContain("enable_live_trading_safety");
  });

  it("READY_WITH_CAVEATS 에서 '주의' 헤드라인 + 경고 목록", () => {
    const { getByTestId } = render(
      <ReleaseReadinessCard resultOverride={_WARN_RESULT} />,
    );
    expect(getByTestId("release-readiness-headline").textContent)
      .toContain("주의");
    expect(getByTestId("release-readiness-warnings").textContent)
      .toContain("desktop_sidecar_build");
  });

  it("INSUFFICIENT_DATA 에서 '데이터 부족' 헤드라인", () => {
    const { getByTestId } = render(
      <ReleaseReadinessCard resultOverride={_INSUFFICIENT_RESULT} />,
    );
    expect(getByTestId("release-readiness-headline").textContent)
      .toContain("데이터 부족");
  });
});


describe("ReleaseReadinessCard — invariant 영구 노출", () => {
  it("4개 invariant 배지 모두 노출", () => {
    const { getByTestId } = render(
      <ReleaseReadinessCard resultOverride={_READY_RESULT} />,
    );
    const block = getByTestId("release-readiness-invariants");
    expect(block.textContent).toContain("실거래 허가 아님");
    expect(block.textContent).toContain("자동 .env 수정 안 함");
    expect(block.textContent).toContain("release 자동 태깅 안 함");
    expect(block.textContent).toContain("주문 신호 아님");
  });

  it("4개 invariant 배지 testid 모두 존재", () => {
    const { getByTestId } = render(
      <ReleaseReadinessCard resultOverride={_READY_RESULT} />,
    );
    expect(getByTestId("release-readiness-invariant-live")).toBeTruthy();
    expect(getByTestId("release-readiness-invariant-auto")).toBeTruthy();
    expect(getByTestId("release-readiness-invariant-tag")).toBeTruthy();
    expect(getByTestId("release-readiness-invariant-order")).toBeTruthy();
  });

  it("disclaimer 가 영구 노출 + 'READY_TO_TAG 가 실거래 활성화 아님' 명시", () => {
    const { getByTestId, rerender } = render(
      <ReleaseReadinessCard resultOverride={_READY_RESULT} />,
    );
    let d = getByTestId("release-readiness-disclaimer").textContent;
    expect(d).toContain("advisory");
    expect(d).toContain("실거래 활성화");
    expect(d).toContain("자동 promotion");

    rerender(<ReleaseReadinessCard resultOverride={_BLOCKED_RESULT} />);
    d = getByTestId("release-readiness-disclaimer").textContent;
    expect(d).toContain("실거래 활성화");
  });
});


describe("ReleaseReadinessCard — 금지 라벨 버튼 0개", () => {
  it("어떤 verdict 에서도 실거래 시작 / 릴리스 자동 태깅 / Place Order 라벨 button 0개", () => {
    for (const r of [_READY_RESULT, _BLOCKED_RESULT, _WARN_RESULT,
                     _INSUFFICIENT_RESULT]) {
      const { container, unmount } = render(
        <ReleaseReadinessCard resultOverride={r} />,
      );
      const buttons = container.querySelectorAll("button");
      for (const b of buttons) {
        const txt = (b.textContent || "").trim();
        for (const banned of [
          "릴리스 자동 태깅", "git tag 자동 생성", "GitHub Release publish",
          "자동 promotion", "실거래 활성화", "실거래 시작", "지금 매수",
          "지금 매도", "Place Order", "BUY signal", "SELL signal",
          "HOLD signal", "ENABLE_LIVE_TRADING 토글", ".env 자동 수정",
          "settings 자동 변경",
        ]) {
          expect(txt.includes(banned)).toBe(false);
        }
      }
      unmount();
    }
  });

  it("BUY / SELL / HOLD / 매수 실행 / 매도 실행 텍스트 0건", () => {
    const { container } = render(
      <ReleaseReadinessCard resultOverride={_BLOCKED_RESULT} />,
    );
    const text = container.textContent || "";
    for (const banned of ["매수 실행", "매도 실행", "BUY signal", "SELL signal",
                          "HOLD signal"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });
});


describe("ReleaseReadinessCard — secret 노출 0건", () => {
  it("secret 입력 form (input / textarea) 0개", () => {
    const { container } = render(
      <ReleaseReadinessCard resultOverride={_READY_RESULT} />,
    );
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
  });

  it("secret 패턴 노출 0건 (KIS_APP_KEY / sk- / bearer 등)", () => {
    const { container } = render(
      <ReleaseReadinessCard resultOverride={_BLOCKED_RESULT} />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key=", "kis_app_secret=", "anthropic_api_key=",
      "telegram_bot_token=", "sk-", "bearer ",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });
});


describe("ReleaseReadinessCard — 버튼 / 상호작용", () => {
  it("'다시 평가' 버튼 label 정확", () => {
    const { getByTestId } = render(
      <ReleaseReadinessCard resultOverride={_READY_RESULT} />,
    );
    expect(getByTestId("release-readiness-evaluate-btn").textContent.trim())
      .toBe("다시 평가");
  });

  it("'markdown 미리보기' 버튼 label 정확", () => {
    const { getByTestId } = render(
      <ReleaseReadinessCard resultOverride={_READY_RESULT} />,
    );
    expect(getByTestId("release-readiness-markdown-btn").textContent.trim())
      .toBe("markdown 미리보기");
  });

  it("'세부 항목 펼치기' 토글", () => {
    const { getByTestId, queryByTestId } = render(
      <ReleaseReadinessCard resultOverride={_READY_RESULT} />,
    );
    expect(queryByTestId("release-readiness-items")).toBeNull();
    fireEvent.click(getByTestId("release-readiness-toggle-detail-btn"));
    expect(getByTestId("release-readiness-items").textContent)
      .toContain("kis_is_paper_safety");
    // 카테고리는 CATEGORY_LABEL 매핑으로 한국어 표시 ("시스템 hygiene").
    expect(getByTestId("release-readiness-items").textContent)
      .toContain("시스템 hygiene");
  });

  it("markdown 미리보기 토글 — markdownOverride 제공 시", () => {
    const { getByTestId, queryByTestId } = render(
      <ReleaseReadinessCard
        resultOverride={_READY_RESULT}
        markdownOverride={"# Release Readiness Report — v1.0.0-beta.5\nfoo"}
      />,
    );
    expect(queryByTestId("release-readiness-markdown")).toBeNull();
    fireEvent.click(getByTestId("release-readiness-markdown-btn"));
    expect(getByTestId("release-readiness-markdown").textContent)
      .toContain("Release Readiness Report");
  });
});


describe("ReleaseReadinessCard — API 통합", () => {
  it("'다시 평가' 클릭 시 backendApi 호출 + 결과 표시", async () => {
    backendApi.releaseReadinessEvaluate.mockResolvedValue(_BLOCKED_RESULT);
    const { getByTestId } = render(<ReleaseReadinessCard />);
    fireEvent.click(getByTestId("release-readiness-evaluate-btn"));
    await waitFor(() => {
      expect(getByTestId("release-readiness-headline").textContent)
        .toContain("릴리스 금지");
    });
    expect(backendApi.releaseReadinessEvaluate).toHaveBeenCalled();
  });

  it("'markdown 미리보기' 클릭 시 backendApi 호출 + markdown 표시", async () => {
    const { getByTestId } = render(<ReleaseReadinessCard />);
    fireEvent.click(getByTestId("release-readiness-markdown-btn"));
    await waitFor(() => {
      expect(getByTestId("release-readiness-markdown").textContent)
        .toContain("Release Readiness Report");
    });
    expect(backendApi.releaseReadinessMarkdown).toHaveBeenCalled();
  });

  it("API 에러 시 error 메시지 표시", async () => {
    backendApi.releaseReadinessEvaluate.mockRejectedValue(
      new Error("backend down"),
    );
    const { getByTestId } = render(<ReleaseReadinessCard />);
    fireEvent.click(getByTestId("release-readiness-evaluate-btn"));
    await waitFor(() => {
      expect(getByTestId("release-readiness-error").textContent)
        .toContain("backend down");
    });
  });
});


describe("ReleaseReadinessCard — 대상 릴리스 표시", () => {
  it("target_release_tag + release_kind 표시", () => {
    const { getByTestId } = render(
      <ReleaseReadinessCard resultOverride={_READY_RESULT} />,
    );
    const target = getByTestId("release-readiness-target");
    expect(target.textContent).toContain("v1.0.0-beta.5");
    expect(target.textContent).toContain("BETA");
  });
});
