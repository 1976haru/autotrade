/**
 * OperatorDiagnosticsCard — 단위 테스트.
 *
 * 사용자 요청서 §6 frontend 필수 테스트:
 *  - 운영 진단 카드 렌더링
 *  - 전체 상태 정상/주의/오류 표시
 *  - 오늘 주문 0건 원인 / primary_reason_code / pipeline stage / next_actions
 *  - 최근 이벤트 로그 + 오류만 보기 필터
 *  - 진단 리포트 복사 버튼 존재 + 민감정보 미포함
 *  - backend offline 안내
 *  - 실거래 활성화 버튼 / ENABLE_LIVE_TRADING UI 0개
 *  - 기존 PaperDiagnosticsCard 와 충돌 없음 (별 testid 사용)
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
  OperatorDiagnosticsCard,
  __test__ as _internals,
} from "./OperatorDiagnosticsCard";


function _mkReport(over = {}) {
  return {
    overall_status:               "HEALTHY",
    conclusion_ko:                "정상: PAPER 모드에서 후보/신호/가상주문 흐름이 작동 중입니다.",
    next_actions_ko:              [
      "정식 관심종목 등록을 권장합니다 (fallback 은 PAPER 테스트용).",
    ],
    default_mode:                 "PAPER",
    enable_live_trading:          false,
    enable_ai_execution:          false,
    enable_futures_live_trading:  false,
    kis_is_paper:                 true,
    market_data_provider:         "yfinance",
    frontend_mode:                null,
    mode_mismatch:                false,
    backend_ready:                true,
    db_ready:                     true,
    migration_state:              "COMPLETED",
    universe_source:              "USER_DEFINED",
    universe_count:               5,
    universe_fallback_used:       false,
    universe_warning_ko:          "",
    market_data_last_fetch_ok:    true,
    market_data_last_fetch_at:    null,
    market_data_stale_symbols:    0,
    auto_bot_state:               "RUNNING",
    auto_bot_running:             true,
    auto_bot_cycle_count:         3,
    auto_bot_last_decision_count: 1,
    auto_bot_last_ledger_events:  1,
    auto_bot_last_error:          null,
    strategy_engine_connected:    true,
    paper_virtual_execution_allowed: true,
    live_execution_blocked:       true,
    permission_last_block_reason: null,
    risk_manager_last_block_reason: null,
    paper_cash_available_krw:     1_000_000,
    paper_cash_insufficient_today: false,
    today_candidate_count:        3,
    today_signal_count:           2,
    today_approved_candidate_count: 1,
    today_rejected_candidate_count: 0,
    today_virtual_order_count:    2,
    today_blocked_order_count:    0,
    today_error_count:            0,
    today_warning_count:          0,
    has_orders_today:             true,
    zero_order_primary_reason:    "NONE",
    zero_order_primary_message:   "오늘 주문이 정상 생성되었습니다.",
    zero_order_secondary_reasons: [],
    zero_order_pipeline_stages:   [
      { stage: "BACKEND",         ok: true,  message: "백엔드 ready" },
      { stage: "UNIVERSE",        ok: true,  message: "universe USER_DEFINED · 5개" },
      { stage: "MARKET_DATA",     ok: true,  message: "provider=yfinance / last_ok=true" },
      { stage: "AUTO_BOT",        ok: true,  message: "loop RUNNING · cycle=3" },
      { stage: "STRATEGY_ENGINE", ok: true,  message: "전략 엔진 연결됨" },
      { stage: "PERMISSION_GATE", ok: true,  message: "PAPER 가상 실행 허용" },
      { stage: "RISK_MANAGER",    ok: true,  message: "RiskManager 통과" },
      { stage: "PAPER_ORDER",     ok: true,  message: "오늘 가상주문 2건" },
    ],
    is_desktop:             false,
    desktop_checks:         [],
    desktop_notes:          [],
    event_summary:          { total: 5, by_level: {}, by_category: {} },
    safe_for_ui:            true,
    contains_secret:        false,
    is_order_signal:        false,
    is_live_authorization:  false,
    advisory_disclaimer:    "본 응답은 PAPER 검증 advisory — broker 호출 0건.",
    ...over,
  };
}


function _mkEvents(over = []) {
  return over.length > 0 ? over : [
    { id: 1, timestamp: "2026-05-21T01:00:00+00:00", level: "INFO",
      category: "SYSTEM", code: "BACKEND_READY", message: "Backend ready",
      details: {}, safe_for_ui: true, contains_secret: false,
      is_order_signal: false, is_live_authorization: false },
    { id: 2, timestamp: "2026-05-21T01:01:00+00:00", level: "WARN",
      category: "MARKET_DATA", code: "STALE_BAR", message: "bar stale 12s",
      details: {}, safe_for_ui: true, contains_secret: false,
      is_order_signal: false, is_live_authorization: false },
    { id: 3, timestamp: "2026-05-21T01:02:00+00:00", level: "ERROR",
      category: "STRATEGY", code: "STRATEGY_ERROR", message: "engine error",
      details: {}, safe_for_ui: true, contains_secret: false,
      is_order_signal: false, is_live_authorization: false },
  ];
}


function _mkApiClient({ report = _mkReport(), events = _mkEvents(), fail = false } = {}) {
  return {
    systemDiagnostics: vi.fn(async () => {
      if (fail) throw new Error("boom");
      return report;
    }),
    systemEventsRecent: vi.fn(async () => ({
      events,
      count: events.length,
      summary: { total: events.length, by_level: {}, by_category: {} },
      safe_for_ui: true, contains_secret: false,
      is_order_signal: false, is_live_authorization: false,
    })),
    systemEventTest:   vi.fn(async () => ({ ok: true })),
  };
}


afterEach(cleanup);


// ────────────────────────────────────────────────────────────────────────────
// A. 상태 요약 카드
// ────────────────────────────────────────────────────────────────────────────


describe("<OperatorDiagnosticsCard> — status summary", () => {
  it("renders the card with section label '운영 진단'", async () => {
    const api = _mkApiClient();
    const { container } = render(
      <OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />,
    );
    await waitFor(() => screen.getByTestId("operator-diagnostics-card"));
    expect(container.textContent).toContain("운영 진단");
  });

  it("shows HEALTHY status label (정상)", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-overall-status"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-overall-status").textContent,
    ).toContain("정상");
  });

  it("shows WARN status label (주의) when overall_status=WARN", async () => {
    const api = _mkApiClient({
      report: _mkReport({ overall_status: "WARN" }),
    });
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-overall-status"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-overall-status").textContent,
    ).toContain("주의");
  });

  it("shows ERROR status label (오류) when overall_status=ERROR", async () => {
    const api = _mkApiClient({
      report: _mkReport({
        overall_status: "ERROR",
        conclusion_ko: "오류: 전략 엔진이 자동봇과 연결되지 않았습니다.",
      }),
    });
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-overall-status"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-overall-status").textContent,
    ).toContain("오류");
  });

  it("shows '백엔드 연결 정상' when backend_ready=true", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-detail-backend"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-detail-backend").textContent,
    ).toContain("백엔드 연결 정상");
  });

  it("shows '전략 엔진 미연동' when strategy_engine_connected=false", async () => {
    const api = _mkApiClient({
      report: _mkReport({
        strategy_engine_connected: false,
        zero_order_primary_reason: "STRATEGY_ENGINE_NOT_CONNECTED",
        zero_order_primary_message: "전략 엔진이 자동봇과 연결되지 않았습니다.",
      }),
    });
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-detail-strategy"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-detail-strategy").textContent,
    ).toContain("전략 엔진 미연동");
  });

  it("shows 'PAPER 가상 실행 차단' when paper_virtual_execution_allowed=false", async () => {
    const api = _mkApiClient({
      report: _mkReport({
        paper_virtual_execution_allowed: false,
        zero_order_primary_reason: "PAPER_EXECUTION_DISABLED",
      }),
    });
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-detail-permission"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-detail-permission").textContent,
    ).toContain("PAPER 가상 실행 차단");
  });

  it("always shows '실거래는 비활성화되어 있습니다' label", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-detail-live-blocked"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-detail-live-blocked").textContent,
    ).toContain("실거래는 비활성화되어 있습니다");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// B. 오늘 주문 0건 원인 카드
// ────────────────────────────────────────────────────────────────────────────


describe("<OperatorDiagnosticsCard> — zero-order analysis", () => {
  it("renders zero-order card with primary_reason_code", async () => {
    const api = _mkApiClient({
      report: _mkReport({
        zero_order_primary_reason: "NO_UNIVERSE",
        zero_order_primary_message: "관심종목이 없고 fallback universe 도 사용할 수 없어 후보 종목 0건.",
        has_orders_today: false,
        overall_status: "WARN",
      }),
    });
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-zero-order-card"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-zero-order-primary-code").textContent,
    ).toBe("NO_UNIVERSE");
    expect(
      screen.getByTestId("operator-diagnostics-card-zero-order-primary-message").textContent,
    ).toContain("관심종목");
  });

  it("renders pipeline stages with PASS/FAIL marks", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-pipeline-stages"),
    );
    for (const stage of [
      "BACKEND", "UNIVERSE", "MARKET_DATA", "AUTO_BOT",
      "STRATEGY_ENGINE", "PERMISSION_GATE", "RISK_MANAGER", "PAPER_ORDER",
    ]) {
      expect(
        screen.getByTestId(`operator-diagnostics-card-pipeline-${stage}`),
      ).toBeTruthy();
    }
  });

  it("renders next_actions list when provided", async () => {
    const api = _mkApiClient({
      report: _mkReport({
        next_actions_ko: [
          "설정 > 관심종목에서 종목을 등록하거나 기본 Universe를 사용하세요.",
          "시장 데이터 provider 연결 상태를 확인하세요.",
        ],
      }),
    });
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-next-actions"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-next-action-0").textContent,
    ).toContain("관심종목");
    expect(
      screen.getByTestId("operator-diagnostics-card-next-action-1").textContent,
    ).toContain("시장 데이터");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// C. 최근 이벤트 로그 + 필터
// ────────────────────────────────────────────────────────────────────────────


describe("<OperatorDiagnosticsCard> — event log + filters", () => {
  it("renders event log section with '최근 이벤트 로그' label", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-event-log-card"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-event-log-card").textContent,
    ).toContain("최근 이벤트 로그");
  });

  it("renders event rows from API", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-event-log-list"),
    );
    // 모든 mock event 가 렌더링.
    expect(
      screen.getByTestId("operator-diagnostics-card-event-row-1"),
    ).toBeTruthy();
    expect(
      screen.getByTestId("operator-diagnostics-card-event-row-2"),
    ).toBeTruthy();
    expect(
      screen.getByTestId("operator-diagnostics-card-event-row-3"),
    ).toBeTruthy();
  });

  it("'오류만 보기' filter passes min_level=ERROR to API", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => screen.getByTestId("operator-diagnostics-card-event-filter-ERROR"));
    // 초기 호출 (ALL).
    expect(api.systemEventsRecent).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("operator-diagnostics-card-event-filter-ERROR"));
    await waitFor(() => {
      const lastCall = api.systemEventsRecent.mock.calls.at(-1);
      expect(lastCall[0]?.minLevel).toBe("ERROR");
    });
  });

  it("'경고 이상 보기' filter passes min_level=WARN", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => screen.getByTestId("operator-diagnostics-card-event-filter-WARN"));
    fireEvent.click(screen.getByTestId("operator-diagnostics-card-event-filter-WARN"));
    await waitFor(() => {
      const lastCall = api.systemEventsRecent.mock.calls.at(-1);
      expect(lastCall[0]?.minLevel).toBe("WARN");
    });
  });

  it("renders empty state when no events", async () => {
    const api = _mkApiClient({ events: [] });
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-event-log-empty"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-event-log-empty").textContent,
    ).toContain("이벤트가 없습니다");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// D. 진단 리포트 복사
// ────────────────────────────────────────────────────────────────────────────


describe("<OperatorDiagnosticsCard> — copy report", () => {
  it("renders '진단 리포트 복사' button", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-report-btn"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-copy-report-btn").textContent,
    ).toBe("진단 리포트 복사");
  });

  it("clicking copy button writes diagnostics JSON to clipboard", async () => {
    const api = _mkApiClient();
    const clipboard = { writeText: vi.fn(async () => {}) };
    render(<OperatorDiagnosticsCard
      apiClient={api} pollIntervalMs={0} clipboard={clipboard}
    />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-report-btn"),
    );
    fireEvent.click(screen.getByTestId("operator-diagnostics-card-copy-report-btn"));
    await waitFor(() => expect(clipboard.writeText).toHaveBeenCalled());
    const text = clipboard.writeText.mock.calls[0][0];
    // 진단 페이로드 안에 핵심 필드가 들어있어야 함.
    expect(text).toContain("HEALTHY");
    expect(text).toContain("conclusion_ko");
    // 민감정보 미포함 검증.
    for (const needle of [
      "sk-ant-", "ghp_", "xoxb-",
      "anthropic_api_key", "openai_api_key", "kis_app_secret",
      "kis_account_no",
    ]) {
      expect(text.toLowerCase()).not.toContain(needle.toLowerCase());
    }
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-ok"),
    );
  });

  it("shows blocked state if report unexpectedly contains secret", async () => {
    // 정상 흐름에서는 backend 가 fail-closed — 본 테스트는 *방어선* 검증.
    const api = _mkApiClient({
      report: _mkReport({
        // 의도적으로 secret-like value 를 message 에 주입.
        zero_order_primary_message: "leak token sk-1234567890ABCDEFghij1234567890",
      }),
    });
    const clipboard = { writeText: vi.fn(async () => {}) };
    render(<OperatorDiagnosticsCard
      apiClient={api} pollIntervalMs={0} clipboard={clipboard}
    />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-report-btn"),
    );
    fireEvent.click(screen.getByTestId("operator-diagnostics-card-copy-report-btn"));
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-blocked"),
    );
    expect(clipboard.writeText).not.toHaveBeenCalled();
  });

  it("renders log path hint", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-log-path-hint"),
    );
    expect(
      screen.getByTestId("operator-diagnostics-card-log-path-hint").textContent,
    ).toContain("로그 파일 위치");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// E. Backend offline / error fallback
// ────────────────────────────────────────────────────────────────────────────


describe("<OperatorDiagnosticsCard> — error fallback", () => {
  it("shows backend offline guidance when API fails", async () => {
    const api = _mkApiClient({ fail: true });
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => screen.getByTestId("operator-diagnostics-card-error"));
    expect(
      screen.getByTestId("operator-diagnostics-card-error").textContent,
    ).toContain("백엔드가 응답하지 않습니다");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// F. Invariants
// ────────────────────────────────────────────────────────────────────────────


describe("<OperatorDiagnosticsCard> — invariants", () => {
  it("displays invariant badges (Paper / 실거래 권한 없음 / 민감정보)", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-invariant-badges"),
    );
    const text = screen.getByTestId("operator-diagnostics-card-invariant-badges").textContent || "";
    expect(text).toContain("Paper");
    expect(text).toContain("실거래 권한 없음");
    expect(text).toContain("민감정보는 진단 리포트에 포함되지 않습니다");
  });

  it("has NO live-trade activation labels / buttons", async () => {
    const api = _mkApiClient();
    const { container } = render(
      <OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />,
    );
    await waitFor(() => screen.getByTestId("operator-diagnostics-card"));
    const text = container.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "place order",
      "매수 실행", "매도 실행",
      "실거래 시작", "실거래 활성화 시작",
      "ENABLE_LIVE_TRADING=true", "ENABLE_AI_EXECUTION=true",
      "실거래 활성화 켜기", "AI 자동매매 활성화",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
    // button 자체에 *활성화* 라벨 0개 검증.
    const buttons = container.querySelectorAll("button");
    for (const b of buttons) {
      const t = (b.textContent || "").trim();
      expect(t).not.toContain("실거래");
      expect(t).not.toContain("매수 실행");
      expect(t).not.toContain("매도 실행");
      expect(t.toLowerCase()).not.toContain("place order");
      expect(t.toLowerCase()).not.toContain("enable_live_trading");
    }
  });

  it("has NO form input elements (no secret entry surface)", async () => {
    const api = _mkApiClient();
    const { container } = render(
      <OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />,
    );
    await waitFor(() => screen.getByTestId("operator-diagnostics-card"));
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
    expect(container.querySelectorAll("select").length).toBe(0);
  });

  it("does not expose secret patterns in DOM", async () => {
    const api = _mkApiClient();
    const { container } = render(
      <OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />,
    );
    await waitFor(() => screen.getByTestId("operator-diagnostics-card"));
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "sk-ant-", "ghp_", "xoxb-",
      "anthropic_api_key", "openai_api_key", "telegram_bot_token",
      "kis_account_no", "kis_app_secret",
    ]) {
      expect(text.includes(needle.toLowerCase())).toBe(false);
    }
  });

  it("uses unique testid that does not collide with PaperDiagnosticsCard", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => screen.getByTestId("operator-diagnostics-card"));
    // operator-diagnostics-card 와 paper-diagnostics-card 는 별도 testid.
    expect(
      screen.queryByTestId("paper-diagnostics-card"),
    ).toBeNull();
  });

  it("disclaimer mentions paper / 민감정보 미포함", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-disclaimer"),
    );
    const text = screen.getByTestId("operator-diagnostics-card-disclaimer").textContent || "";
    expect(text).toContain("PAPER");
    expect(text).toContain("민감정보");
  });

  it("refresh button triggers re-fetch", async () => {
    const api = _mkApiClient();
    render(<OperatorDiagnosticsCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-refresh-btn"),
    );
    api.systemDiagnostics.mockClear();
    fireEvent.click(screen.getByTestId("operator-diagnostics-card-refresh-btn"));
    await waitFor(() => expect(api.systemDiagnostics).toHaveBeenCalled());
  });
});


// ────────────────────────────────────────────────────────────────────────────
// G. fix/operator-diagnostics-copy-secret-guard — 사용자 요청서 정책 B/C 보강
//    secret 감지 범위 + clipboard.writeText 절대 미호출 보장
// ────────────────────────────────────────────────────────────────────────────


describe("OperatorDiagnosticsCard — secret copy guard (hardened)", () => {
  describe("_matchSecretValue / _isSuspiciousKeyName / _findSecret", () => {
    it("matches OpenAI sk- token", () => {
      expect(_internals._matchSecretValue("token sk-ABCDEFGHIJKLMNOPQRST"))
        .not.toBeNull();
    });

    it("matches Anthropic sk-ant- token", () => {
      expect(_internals._matchSecretValue(
        "key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
      )).not.toBeNull();
    });

    it("matches GitHub ghp_ token", () => {
      expect(_internals._matchSecretValue("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZAB"))
        .not.toBeNull();
    });

    it("matches Slack xoxb / xoxp token", () => {
      expect(_internals._matchSecretValue("xoxb-1234567890-token"))
        .not.toBeNull();
      expect(_internals._matchSecretValue("xoxp-1234567890-token"))
        .not.toBeNull();
    });

    it("matches Bearer token", () => {
      expect(_internals._matchSecretValue(
        "Authorization: Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ",
      )).not.toBeNull();
    });

    it("matches JWT token", () => {
      expect(_internals._matchSecretValue(
        "jwt eyJabcdefgh.eyJabcdefgh.SflKxwRJSMeKKF",
      )).not.toBeNull();
    });

    it("matches Korean account number pattern", () => {
      expect(_internals._matchSecretValue("계좌 12345678-01"))
        .not.toBeNull();
    });

    it("matches RRN pattern", () => {
      expect(_internals._matchSecretValue("주민 901020-1234567"))
        .not.toBeNull();
    });

    it("matches credit card pattern", () => {
      expect(_internals._matchSecretValue("카드 1234-5678-9012-3456"))
        .not.toBeNull();
    });

    it("matches telegram bot token line", () => {
      expect(_internals._matchSecretValue(
        "telegram_bot_token = 1234:ABCDEFGH",
      )).not.toBeNull();
    });

    it("does NOT match normal text", () => {
      expect(_internals._matchSecretValue("hello world 005930")).toBeNull();
      expect(_internals._matchSecretValue("Paper / SIMULATION")).toBeNull();
    });

    it.each([
      "api_key", "apiKey", "API_KEY",
      "api_secret", "apiSecret", "app_secret", "app_key",
      "access_token", "refresh_token", "secret_token",
      "password", "Passwd",
      "telegram_bot_token", "bot_token",
      "kis_app_key", "kis_app_secret", "kis_account_no",
      "anthropic_api_key", "openai_api_key",
      "private_key", "client_secret",
      "account_no", "account_number",
    ])("flags suspicious key name '%s'", (key) => {
      expect(_internals._isSuspiciousKeyName(key)).not.toBeNull();
    });

    it("does NOT flag safe boolean-flag keys", () => {
      // contains_secret 같은 *boolean flag* key 는 값이 boolean 이면 차단
      // 대상 아님 (false positive 회피).
      const safe = {
        contains_secret: false,
        safe_for_ui: true,
        is_order_signal: false,
        is_paper_safe_only: true,
        market_data_provider: "yfinance",
        default_mode: "PAPER",
      };
      expect(_internals._findSecret(safe, 0)).toBeNull();
    });

    it("structured walk: api_key with non-empty string value -> BLOCK", () => {
      const hit = _internals._findSecret({
        config: { api_key: "abcdef1234" },
      }, 0);
      expect(hit).toMatch(/key_name:/);
    });

    it("structured walk: password with empty string -> NOT blocked", () => {
      expect(_internals._findSecret({ password: "" }, 0)).toBeNull();
    });

    it("structured walk: nested sk- secret in event details -> BLOCK", () => {
      const hit = _internals._findSecret({
        events: [
          { id: 1, message: "leaked sk-ABCDEFGHIJKLMNOPQRSTUVWX" },
        ],
      }, 0);
      expect(hit).toMatch(/value_pattern:/);
    });

    it("structured walk: deeply nested object", () => {
      const hit = _internals._findSecret({
        a: { b: { c: [{ d: { kis_app_secret: "abc1234567" } }] } },
      }, 0);
      expect(hit).toMatch(/key_name:/);
    });

    it("recursion depth cap does not throw", () => {
      let node = "deep";
      for (let i = 0; i < 50; i++) node = { wrap: node };
      expect(() => _internals._findSecret(node, 0)).not.toThrow();
    });
  });

  // ── 사용자 요청서 §3 필수 테스트 ──
  it("secret in diagnostics -> copy-blocked + writeText NEVER called", async () => {
    const api = _mkApiClient({
      report: _mkReport({
        zero_order_primary_message:
          "leak sk-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh",
      }),
    });
    const clipboard = { writeText: vi.fn(async () => {}) };
    render(<OperatorDiagnosticsCard
      apiClient={api} pollIntervalMs={0} clipboard={clipboard}
    />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-report-btn"),
    );
    fireEvent.click(screen.getByTestId("operator-diagnostics-card-copy-report-btn"));
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-blocked"),
    );
    expect(clipboard.writeText).not.toHaveBeenCalled();
    expect(
      screen.getByTestId("operator-diagnostics-card-copy-blocked").textContent,
    ).toContain("민감정보가 감지되어 복사가 차단되었습니다");
  });

  it("secret in events.message -> copy-blocked + writeText NEVER called", async () => {
    const api = _mkApiClient({
      events: [
        {
          id: 99, timestamp: "2026-05-21T01:05:00+00:00",
          level: "WARN", category: "SYSTEM",
          code: "X", message: "Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ",
          details: {}, safe_for_ui: true, contains_secret: false,
          is_order_signal: false, is_live_authorization: false,
        },
      ],
    });
    const clipboard = { writeText: vi.fn(async () => {}) };
    render(<OperatorDiagnosticsCard
      apiClient={api} pollIntervalMs={0} clipboard={clipboard}
    />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-report-btn"),
    );
    fireEvent.click(screen.getByTestId("operator-diagnostics-card-copy-report-btn"));
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-blocked"),
    );
    expect(clipboard.writeText).not.toHaveBeenCalled();
  });

  it("api_key field in events.details -> copy-blocked", async () => {
    const api = _mkApiClient({
      events: [
        {
          id: 1, timestamp: "2026-05-21T01:05:00+00:00",
          level: "INFO", category: "SYSTEM",
          code: "X", message: "no body",
          details: { api_key: "leaked-value-here" },
          safe_for_ui: true, contains_secret: false,
          is_order_signal: false, is_live_authorization: false,
        },
      ],
    });
    const clipboard = { writeText: vi.fn(async () => {}) };
    render(<OperatorDiagnosticsCard
      apiClient={api} pollIntervalMs={0} clipboard={clipboard}
    />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-report-btn"),
    );
    fireEvent.click(screen.getByTestId("operator-diagnostics-card-copy-report-btn"));
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-blocked"),
    );
    expect(clipboard.writeText).not.toHaveBeenCalled();
  });

  it("clean payload -> writeText called + copy-blocked NOT shown", async () => {
    const api = _mkApiClient();
    const clipboard = { writeText: vi.fn(async () => {}) };
    render(<OperatorDiagnosticsCard
      apiClient={api} pollIntervalMs={0} clipboard={clipboard}
    />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-report-btn"),
    );
    fireEvent.click(screen.getByTestId("operator-diagnostics-card-copy-report-btn"));
    await waitFor(() => expect(clipboard.writeText).toHaveBeenCalled());
    expect(
      screen.queryByTestId("operator-diagnostics-card-copy-blocked"),
    ).toBeNull();
  });

  it("contains_secret=false / safe_for_ui=true safety flags -> NOT blocked", async () => {
    // 회귀 방지 — boolean flag key 가 secret 로 오인되지 않아야 함.
    const api = _mkApiClient();
    const clipboard = { writeText: vi.fn(async () => {}) };
    render(<OperatorDiagnosticsCard
      apiClient={api} pollIntervalMs={0} clipboard={clipboard}
    />);
    await waitFor(() =>
      screen.getByTestId("operator-diagnostics-card-copy-report-btn"),
    );
    fireEvent.click(screen.getByTestId("operator-diagnostics-card-copy-report-btn"));
    await waitFor(() => expect(clipboard.writeText).toHaveBeenCalled());
  });
});
