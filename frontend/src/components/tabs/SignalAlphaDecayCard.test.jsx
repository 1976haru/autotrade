/**
 * #94 Signal Alpha Decay Card 테스트.
 *
 * 요구 사항:
 * - verdict 별 헤드라인 (FRESH / DECAYING / STALE / EXPIRED / UNKNOWN)
 * - EXPIRED 시 "이 신호는 오래되어 진입 근거로 사용 금지" 배지
 * - 4 invariant 배지 영구 노출
 * - secret 입력 form (input / textarea) 0개
 * - 매수 / 매도 / Place Order / 실거래 라벨 button 0개
 * - secret 패턴 노출 0건
 * - 세부 항목 펼치기 토글
 * - bucket 표 렌더링
 * - API 호출 + 에러 처리
 */

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    signalAlphaDecayEvaluate: vi.fn(),
    alphaDecayFreshness: vi.fn(),
  },
}));


import { backendApi } from "../../services/backend/client";
import { SignalAlphaDecayCard } from "./SignalAlphaDecayCard";


const _FRESH_RESULT = {
  strategy_name: "sma_crossover",
  buckets: [
    { label: "0m",  age_minutes: 0,  mean_return_bps: 20.0, sample_count: 100, relative_to_t0_pct: 100.0, severity: "PASS", note: "" },
    { label: "1m",  age_minutes: 1,  mean_return_bps: 19.0, sample_count: 100, relative_to_t0_pct: 95.0,  severity: "PASS", note: "" },
    { label: "5m",  age_minutes: 5,  mean_return_bps: 18.0, sample_count: 100, relative_to_t0_pct: 90.0,  severity: "PASS", note: "" },
    { label: "30m", age_minutes: 30, mean_return_bps: 16.0, sample_count: 100, relative_to_t0_pct: 80.0,  severity: "PASS", note: "" },
  ],
  decay_score: 91.3,
  max_actionable_age_minutes: 30,
  verdict_overall: "FRESH",
  warnings: [],
  advice: [],
  insufficient_data: false,
  is_order_signal: false,
  auto_apply_allowed: false,
  is_live_authorization: false,
  generated_at: new Date().toISOString(),
};


const _DECAYING_RESULT = {
  ..._FRESH_RESULT,
  verdict_overall: "DECAYING",
  decay_score: 65.0,
  warnings: ["1개 bucket 에서 WARN — t=0 대비 70% 미만"],
  advice: ["신호 평균 decay_score=65.0 — 진입 시 보수적 사이즈 권장"],
};


const _EXPIRED_RESULT = {
  ..._FRESH_RESULT,
  buckets: [
    { label: "0m",  age_minutes: 0,  mean_return_bps: 30.0, sample_count: 100, relative_to_t0_pct: 100.0, severity: "PASS", note: "" },
    { label: "5m",  age_minutes: 5,  mean_return_bps: 3.0,  sample_count: 100, relative_to_t0_pct: 10.0,  severity: "FAIL", note: "" },
    { label: "30m", age_minutes: 30, mean_return_bps: 1.0,  sample_count: 100, relative_to_t0_pct: 3.3,   severity: "FAIL", note: "" },
  ],
  verdict_overall: "EXPIRED",
  decay_score: 15.0,
  warnings: ["2개 bucket 에서 FAIL — t=0 대비 30% 미만 감소"],
  advice: ["신호 평균 decay_score=15.0 < 30 — *진입 금지*"],
};


const _UNKNOWN_RESULT = {
  ..._FRESH_RESULT,
  buckets: [],
  decay_score: 0.0,
  verdict_overall: "UNKNOWN",
  insufficient_data: true,
  warnings: ["입력 sample 0개 — 평가 불가"],
  advice: ["진입 후 1분 / 3분 / 5분 / 10분 / 30분 / 60분 시점 표본 수집 필요"],
};


beforeEach(() => {
  for (const k of Object.keys(backendApi)) {
    if (typeof backendApi[k]?.mockReset === "function") {
      backendApi[k].mockReset();
    }
  }
  backendApi.signalAlphaDecayEvaluate.mockResolvedValue(_FRESH_RESULT);
  backendApi.alphaDecayFreshness.mockResolvedValue({
    age_minutes: 10, verdict: "DECAYING",
    actionable: true, actionable_strict: true,
    is_order_signal: false,
  });
});

afterEach(cleanup);


describe("SignalAlphaDecayCard — verdict 렌더링", () => {
  it("FRESH 에서 '신호 신선' 헤드라인", () => {
    const { getByTestId } = render(
      <SignalAlphaDecayCard resultOverride={_FRESH_RESULT} />,
    );
    expect(getByTestId("signal-alpha-decay-headline").textContent)
      .toContain("신호 신선");
    expect(getByTestId("signal-alpha-decay-headline").textContent)
      .toContain("FRESH");
  });

  it("DECAYING 에서 '신호 감쇠 진행 중' 헤드라인 + 경고 목록", () => {
    const { getByTestId } = render(
      <SignalAlphaDecayCard resultOverride={_DECAYING_RESULT} />,
    );
    expect(getByTestId("signal-alpha-decay-headline").textContent)
      .toContain("감쇠 진행 중");
    expect(getByTestId("signal-alpha-decay-warnings").textContent)
      .toContain("WARN");
  });

  it("EXPIRED 에서 '신호 만료' 헤드라인 + 차단 배너", () => {
    const { getByTestId } = render(
      <SignalAlphaDecayCard resultOverride={_EXPIRED_RESULT} />,
    );
    expect(getByTestId("signal-alpha-decay-headline").textContent)
      .toContain("만료");
    const banner = getByTestId("signal-alpha-decay-expired-banner");
    expect(banner.textContent).toContain("오래되어 진입 근거로 사용 금지");
    expect(banner.textContent).toContain("AI Agent");
  });

  it("UNKNOWN + insufficient_data 시 안내", () => {
    const { getByTestId } = render(
      <SignalAlphaDecayCard resultOverride={_UNKNOWN_RESULT} />,
    );
    expect(getByTestId("signal-alpha-decay-headline").textContent)
      .toContain("표본 부족");
    expect(getByTestId("signal-alpha-decay-insufficient").textContent)
      .toContain("평가 신뢰성");
  });

  it("FRESH / DECAYING / STALE 에서는 차단 배너 미노출", () => {
    for (const r of [_FRESH_RESULT, _DECAYING_RESULT, _UNKNOWN_RESULT]) {
      const { queryByTestId, unmount } = render(
        <SignalAlphaDecayCard resultOverride={r} />,
      );
      expect(queryByTestId("signal-alpha-decay-expired-banner")).toBeNull();
      unmount();
    }
  });
});


describe("SignalAlphaDecayCard — invariant 영구 노출", () => {
  it("4개 invariant 배지 모두 노출", () => {
    const { getByTestId } = render(
      <SignalAlphaDecayCard resultOverride={_FRESH_RESULT} />,
    );
    const block = getByTestId("signal-alpha-decay-invariants");
    expect(block.textContent).toContain("주문 신호 아님");
    expect(block.textContent).toContain("자동 적용 안 함");
    expect(block.textContent).toContain("실거래 허가 아님");
    expect(block.textContent).toContain("advisory");
  });

  it("disclaimer 가 영구 노출 + 'AI Agent ... 사용하지 않아야' 명시", () => {
    const { getByTestId, rerender } = render(
      <SignalAlphaDecayCard resultOverride={_FRESH_RESULT} />,
    );
    let d = getByTestId("signal-alpha-decay-disclaimer").textContent;
    expect(d).toContain("advisory");
    expect(d).toContain("AI Agent");
    expect(d).toContain("사용하지 않아야");

    rerender(<SignalAlphaDecayCard resultOverride={_EXPIRED_RESULT} />);
    d = getByTestId("signal-alpha-decay-disclaimer").textContent;
    expect(d).toContain("advisory");
  });

  it("#77 과의 구분이 disclaimer 에 명시 (별개 개념)", () => {
    const { getByTestId } = render(
      <SignalAlphaDecayCard resultOverride={_FRESH_RESULT} />,
    );
    const d = getByTestId("signal-alpha-decay-disclaimer").textContent;
    expect(d).toContain("전략 단위");
    expect(d).toContain("별개");
  });
});


describe("SignalAlphaDecayCard — 금지 라벨 button 0개", () => {
  it("어떤 verdict 에서도 매수 / 매도 / Place Order / 실거래 라벨 button 0개", () => {
    for (const r of [_FRESH_RESULT, _DECAYING_RESULT, _EXPIRED_RESULT,
                     _UNKNOWN_RESULT]) {
      const { container, unmount } = render(
        <SignalAlphaDecayCard resultOverride={r} />,
      );
      const buttons = container.querySelectorAll("button");
      for (const b of buttons) {
        const txt = (b.textContent || "").trim();
        for (const banned of [
          "지금 매수", "지금 매도", "매수 실행", "매도 실행",
          "Place Order", "BUY signal", "SELL signal", "HOLD signal",
          "실거래 시작", "실거래 활성화", "ENABLE_LIVE_TRADING 토글",
          "AI 자동 실행 활성화", "전략 비활성화", "Apply Parameters",
        ]) {
          expect(txt.includes(banned)).toBe(false);
        }
      }
      unmount();
    }
  });

  it("BUY / SELL / HOLD 텍스트 0건 (단어 단위)", () => {
    const { container } = render(
      <SignalAlphaDecayCard resultOverride={_EXPIRED_RESULT} />,
    );
    const text = container.textContent || "";
    for (const banned of ["BUY signal", "SELL signal", "HOLD signal",
                          "매수 실행", "매도 실행"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });
});


describe("SignalAlphaDecayCard — secret 노출 0건", () => {
  it("secret 입력 form (input / textarea) 0개", () => {
    const { container } = render(
      <SignalAlphaDecayCard resultOverride={_FRESH_RESULT} />,
    );
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
  });

  it("secret 패턴 노출 0건 (KIS_APP_KEY / sk- / bearer 등)", () => {
    const { container } = render(
      <SignalAlphaDecayCard resultOverride={_EXPIRED_RESULT} />,
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


describe("SignalAlphaDecayCard — 버튼 / 상호작용", () => {
  it("'다시 평가' 버튼 label 정확", () => {
    const { getByTestId } = render(
      <SignalAlphaDecayCard resultOverride={_FRESH_RESULT} />,
    );
    expect(getByTestId("signal-alpha-decay-evaluate-btn").textContent.trim())
      .toBe("다시 평가");
  });

  it("'bucket 상세 펼치기' 토글", () => {
    const { getByTestId, queryByTestId } = render(
      <SignalAlphaDecayCard resultOverride={_FRESH_RESULT} />,
    );
    expect(queryByTestId("signal-alpha-decay-buckets")).toBeNull();
    fireEvent.click(getByTestId("signal-alpha-decay-toggle-detail-btn"));
    const b = getByTestId("signal-alpha-decay-buckets").textContent;
    expect(b).toContain("0m");
    expect(b).toContain("30m");
    expect(b).toContain("samples=100");
  });

  it("대상 전략명 표시", () => {
    const { getByTestId } = render(
      <SignalAlphaDecayCard resultOverride={_FRESH_RESULT} />,
    );
    expect(getByTestId("signal-alpha-decay-target").textContent)
      .toContain("sma_crossover");
  });
});


describe("SignalAlphaDecayCard — API 통합", () => {
  it("'다시 평가' 클릭 시 backendApi 호출", async () => {
    backendApi.signalAlphaDecayEvaluate.mockResolvedValue(_EXPIRED_RESULT);
    const { getByTestId } = render(<SignalAlphaDecayCard />);
    fireEvent.click(getByTestId("signal-alpha-decay-evaluate-btn"));
    await waitFor(() => {
      expect(getByTestId("signal-alpha-decay-headline").textContent)
        .toContain("만료");
    });
    expect(backendApi.signalAlphaDecayEvaluate).toHaveBeenCalled();
  });

  it("API 에러 시 error 메시지 표시", async () => {
    backendApi.signalAlphaDecayEvaluate.mockRejectedValue(new Error("backend down"));
    const { getByTestId } = render(<SignalAlphaDecayCard />);
    fireEvent.click(getByTestId("signal-alpha-decay-evaluate-btn"));
    await waitFor(() => {
      expect(getByTestId("signal-alpha-decay-error").textContent)
        .toContain("backend down");
    });
  });

  it("currentAgeMinutes prop 주어지면 freshness endpoint 호출 + 표시", async () => {
    backendApi.alphaDecayFreshness.mockResolvedValue({
      age_minutes: 5, verdict: "DECAYING",
      actionable: true, actionable_strict: true,
      is_order_signal: false,
    });
    const { getByTestId } = render(
      <SignalAlphaDecayCard
        resultOverride={_FRESH_RESULT}
        currentAgeMinutes={5}
      />,
    );
    await waitFor(() => {
      expect(getByTestId("signal-alpha-decay-realtime-freshness").textContent)
        .toContain("DECAYING");
    });
    expect(backendApi.alphaDecayFreshness).toHaveBeenCalledWith({
      ageMinutes: 5,
    });
  });
});
