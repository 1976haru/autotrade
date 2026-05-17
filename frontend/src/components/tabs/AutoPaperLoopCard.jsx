/* eslint-disable react/prop-types */
import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

// AI Paper Auto Loop card — EXE 원클릭 시작/정지/긴급정지.
//
// **주문이 아닙니다. 실거래는 절대 켜지지 않습니다.** PAPER/SIMULATION 한정.
// "매수" / "매도" / "Place Order" / "ENABLE_*" 라벨 버튼 0개 (테스트로 lock).
//
// 시작 버튼  → POST /api/auto-paper/start
// 정지 버튼  → POST /api/auto-paper/stop
// 긴급정지   → POST /api/auto-paper/emergency-stop

// feat/step2-01-auto-paper-states: 체크리스트 표준 4 상태 (PAUSED / RUNNING /
// STOPPED / EMERGENCY_STOP). 레거시 IDLE / EMERGENCY 도 동일 라벨로 매핑 —
// 옛 backend 가 IDLE / EMERGENCY 를 emit 해도 UI 가 깨지지 않도록.
//
// feat/step2-market-waiting-mode: 한국장 시작 전 대기 (WAITING_MARKET) /
// 장 종료 후 또는 주말 (MARKET_CLOSED) 두 상태 추가. 09:00 KST 가 되면
// backend 가 lazy 로 WAITING_MARKET → RUNNING 으로 promote (polling 갱신).
const _STATE_COLOR = {
  PAUSED:         "#94a3b8",
  IDLE:           "#94a3b8",   // legacy alias
  WAITING_MARKET: "#3b82f6",   // 신규: 장 시작 대기 (파랑)
  RUNNING:        "#22c55e",
  STOPPED:        "#fbbf24",
  EMERGENCY_STOP: "#ef4444",
  EMERGENCY:      "#ef4444",   // legacy alias
  MARKET_CLOSED:  "#64748b",   // 신규: 장 종료 / 휴장 (회색)
};

const _STATE_LABEL = {
  PAUSED:         "대기 (일시정지)",
  IDLE:           "대기 (일시정지)",
  WAITING_MARKET: "장 시작 대기 중",
  RUNNING:        "AI Paper Auto Loop 진행 중",
  STOPPED:        "정지됨",
  EMERGENCY_STOP: "긴급정지됨",
  EMERGENCY:      "긴급정지됨",
  MARKET_CLOSED:  "장 종료 · 휴장 (다음 영업일 09:00 KST 부터 시작 가능)",
};

const POLL_INTERVAL_MS = 5_000;

function _Pill({ label, value, color, testid }) {
  return (
    <span
      data-testid={testid}
      style={{
        display: "inline-block",
        padding: "2px 10px",
        borderRadius: 999,
        fontSize: "var(--fs-xs)",
        fontWeight: "var(--fw-bold)",
        background: color || "var(--c-surface-2)",
        color: "#fff",
        marginRight: 6,
      }}
    >
      {label}: {value}
    </span>
  );
}

export function AutoPaperLoopCard({
  apiClient = backendApi,
  pollIntervalMs = POLL_INTERVAL_MS,
  // feat/step2-05-pre-market-gate: pre-market checklist 결과 carry.
  // `start_allowed === false` 면 시작 버튼 비활성화 + 차단 배너 노출 +
  // start() 호출 시 backend 에도 동일 payload 동봉 (서버 단 거절).
  preMarketCheckResult = null,
} = {}) {
  const [status, setStatus] = useState(null);
  const [safety, setSafety] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  // #2-09: Paper Loop advisory ledger — 최근 AI 판단 / 가상 체결 noise-low 표시.
  const [ledgerEvents, setLedgerEvents] = useState([]);

  const refresh = useCallback(async () => {
    try {
      const [s, h] = await Promise.all([
        apiClient.autoPaperStatus(),
        apiClient.desktopHealth(),
      ]);
      setStatus(s);
      setSafety(h?.safety_flags || null);
      setError(null);
    } catch (err) {
      setError(err?.message || String(err));
    }
    // ledger 는 *별도* — 실패해도 main status 표시는 살아있어야 함.
    if (typeof apiClient.autoPaperLedger === "function") {
      try {
        const r = await apiClient.autoPaperLedger({ limit: 10 });
        if (r && Array.isArray(r.events)) {
          // 최신이 위로 오도록 reverse.
          setLedgerEvents([...r.events].reverse());
        }
      } catch {
        // ledger 가 없는 환경 (테스트 mock 등) — 조용히 무시.
      }
    }
  }, [apiClient]);

  useEffect(() => {
    refresh();
    if (!pollIntervalMs || pollIntervalMs <= 0) return undefined;
    const t = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(t);
  }, [refresh, pollIntervalMs]);

  // feat/step2-05-pre-market-gate: Pre-market BLOCK 판정.
  // `start_allowed === false` 가 명시적일 때만 차단 — null / undefined 는 미평가.
  const preMarketBlocked = preMarketCheckResult != null
    && preMarketCheckResult.start_allowed === false;
  const preMarketReasons = preMarketBlocked
    ? (preMarketCheckResult.blocking_reasons || [])
    : [];

  const wrap = (fn) => async () => {
    setBusy(true);
    try {
      await fn();
      await refresh();
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  // 시작 버튼: pre-market 결과를 backend 에 동봉. 서버가 최종 거절 권한.
  const onStart = useCallback(wrap(async () => {
    const body = preMarketCheckResult != null
      ? {
          pre_market: {
            start_allowed:    preMarketCheckResult.start_allowed === true,
            verdict:          preMarketCheckResult.verdict || "",
            blocking_reasons: preMarketCheckResult.blocking_reasons || [],
            warnings:         preMarketCheckResult.warnings || [],
          },
        }
      : null;
    // apiClient.autoPaperStart 는 (body=null) 시 body 미전송 (기존 호환).
    return apiClient.autoPaperStart(body);
  }), [apiClient, refresh, preMarketCheckResult]);
  const onStop = useCallback(wrap(apiClient.autoPaperStop), [apiClient, refresh]);
  const onEmergencyStop = useCallback(
    wrap(apiClient.autoPaperEmergencyStop),
    [apiClient, refresh]
  );

  // feat/step2-01-auto-paper-states: 초기 default = PAUSED (canonical).
  const state = status?.state || "PAUSED";
  const stateColor = _STATE_COLOR[state] || "#94a3b8";
  const stateLabel = _STATE_LABEL[state] || state;
  const liveOff = safety?.enable_live_trading === false;
  const kisPaperOn = safety?.kis_is_paper !== false;

  return (
    <Card data-testid="auto-paper-loop-card">
      <SectionLabel>AI Paper Auto Loop</SectionLabel>

      <div style={{ marginBottom: 12 }} data-testid="safety-badges">
        <span
          data-testid="badge-not-order-signal"
          style={{
            display: "inline-block",
            padding: "3px 10px",
            borderRadius: 6,
            fontSize: "var(--fs-xs)",
            fontWeight: "var(--fw-bold)",
            background: "#1e3a8a",
            color: "#fff",
            marginRight: 6,
          }}
        >
          모의 전용 · 실거래 OFF
        </span>
        <span
          data-testid="badge-paper-mode"
          style={{
            display: "inline-block",
            padding: "3px 10px",
            borderRadius: 6,
            fontSize: "var(--fs-xs)",
            background: "#0ea5e9",
            color: "#fff",
            marginRight: 6,
          }}
        >
          KIS Paper ON
        </span>
        <span
          data-testid="badge-no-auto-apply"
          style={{
            display: "inline-block",
            padding: "3px 10px",
            borderRadius: 6,
            fontSize: "var(--fs-xs)",
            background: "#6b7280",
            color: "#fff",
          }}
        >
          주문 신호 아님
        </span>
      </div>

      <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
        <div
          data-testid="state-pill"
          style={{
            display: "inline-block",
            padding: "3px 12px",
            borderRadius: 999,
            fontSize: "var(--fs-sm)",
            fontWeight: "var(--fw-bold)",
            background: stateColor,
            color: "#fff",
          }}
        >
          {stateLabel}
        </div>
        <span data-testid="cycle-count" style={{ fontSize: "var(--fs-sm)", color: "var(--c-text-2)" }}>
          cycle {status?.cycle_count ?? 0}
        </span>
      </div>

      <div style={{ marginBottom: 12 }} data-testid="safety-matrix">
        <_Pill
          label="실거래"
          value={liveOff ? "OFF" : "ON ⚠"}
          color={liveOff ? "#22c55e" : "#ef4444"}
          testid="flag-live-off"
        />
        <_Pill
          label="KIS 모의"
          value={kisPaperOn ? "ON" : "OFF ⚠"}
          color={kisPaperOn ? "#22c55e" : "#ef4444"}
          testid="flag-kis-paper"
        />
        <_Pill
          label="AI 자동주문"
          value={safety?.enable_ai_execution === false ? "OFF" : "ON ⚠"}
          color={safety?.enable_ai_execution === false ? "#22c55e" : "#ef4444"}
          testid="flag-ai-exec"
        />
      </div>

      {/* feat/step2-market-waiting-mode: WAITING_MARKET 안내 배너. */}
      {state === "WAITING_MARKET" && (
        <div
          data-testid="auto-paper-market-waiting-banner"
          style={{
            padding: "8px 12px",
            marginBottom: 10,
            background: "#eff6ff",
            border: "1px solid #bfdbfe",
            borderRadius: "var(--r-md)",
            color: "#1e3a8a",
            fontSize: "var(--fs-sm)",
          }}
        >
          <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
            ⏳ 장 시작 대기 중
          </div>
          <div style={{ fontSize: "var(--fs-xs)" }}>
            한국 주식시장 정규장(09:00 KST)이 시작되면 자동으로 AI Paper
            Auto Loop가 RUNNING 상태로 전환됩니다. 그 전까지는 신규 가상
            매매 후보를 생성하지 않습니다.
          </div>
        </div>
      )}

      {/* feat/step2-market-waiting-mode: MARKET_CLOSED 안내 배너. */}
      {state === "MARKET_CLOSED" && (
        <div
          data-testid="auto-paper-market-closed-banner"
          style={{
            padding: "8px 12px",
            marginBottom: 10,
            background: "#f1f5f9",
            border: "1px solid #cbd5e1",
            borderRadius: "var(--r-md)",
            color: "#334155",
            fontSize: "var(--fs-sm)",
          }}
        >
          <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
            🌙 한국장 종료 / 휴장
          </div>
          <div style={{ fontSize: "var(--fs-xs)" }}>
            다음 영업일 09:00 KST 부터 AI Paper Auto Loop를 다시 시작할 수
            있습니다. 신규 가상 매매 후보 생성은 정지된 상태입니다.
          </div>
        </div>
      )}

      {/* feat/step2-05-pre-market-gate: Pre-market BLOCK 차단 배너. */}
      {preMarketBlocked && (
        <div
          data-testid="auto-paper-premarket-blocked-banner"
          style={{
            padding: "8px 12px",
            marginBottom: 10,
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: "var(--r-md)",
            color: "#7f1d1d",
            fontSize: "var(--fs-sm)",
          }}
        >
          <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
            ⚠ Pre-market 점검 미통과 — 자동 시작 불가
          </div>
          {preMarketReasons.length > 0 && (
            <ul
              data-testid="auto-paper-premarket-block-reasons"
              style={{ margin: 0, paddingLeft: 18, fontSize: "var(--fs-xs)" }}
            >
              {preMarketReasons.slice(0, 5).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 4 }}>
            Pre-market 카드에서 사유를 해결한 뒤 다시 점검 → 시작 시도하세요.
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }} data-testid="control-buttons">
        <button
          data-testid="btn-start-auto-paper"
          onClick={onStart}
          disabled={
            busy
            || state === "RUNNING"
            || state === "WAITING_MARKET"  // feat/step2-market-waiting-mode: 이미 대기 중
            || preMarketBlocked
          }
          style={{
            padding: "8px 16px",
            borderRadius: "var(--r-md)",
            background:
              (state === "RUNNING" || state === "WAITING_MARKET" || preMarketBlocked)
                ? "#94a3b8"
                : "#22c55e",
            color: "#fff",
            border: "none",
            cursor:
              (state === "RUNNING" || state === "WAITING_MARKET" || preMarketBlocked)
                ? "not-allowed"
                : "pointer",
            fontWeight: "var(--fw-bold)",
          }}
        >
          시작 (AI Paper Auto Loop)
        </button>
        <button
          data-testid="btn-stop-auto-paper"
          onClick={onStop}
          disabled={busy || state !== "RUNNING"}
          style={{
            padding: "8px 16px",
            borderRadius: "var(--r-md)",
            background: state !== "RUNNING" ? "#94a3b8" : "#fbbf24",
            color: "#fff",
            border: "none",
            cursor: state !== "RUNNING" ? "not-allowed" : "pointer",
          }}
        >
          정지 (신규 판단 중단)
        </button>
        <button
          data-testid="btn-emergency-stop"
          onClick={onEmergencyStop}
          disabled={busy}
          style={{
            padding: "8px 16px",
            borderRadius: "var(--r-md)",
            background: "#ef4444",
            color: "#fff",
            border: "none",
            cursor: "pointer",
          }}
        >
          긴급정지 (모든 루프 즉시 중단)
        </button>
      </div>

      {error && (
        <div
          data-testid="auto-paper-error"
          style={{
            marginTop: 10,
            padding: "6px 10px",
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: "var(--r-md)",
            color: "#7f1d1d",
            fontSize: "var(--fs-xs)",
          }}
        >
          {error}
        </div>
      )}

      {/* #2-09 + #2-10: 최근 AI 판단 / Paper 가상 체결 ledger — read-only advisory */}
      {ledgerEvents.length > 0 && (
        <div
          data-testid="paper-ledger-panel"
          style={{
            marginTop: 12,
            padding: 10,
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: "var(--r-md)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 6,
            }}
          >
            <div
              style={{
                fontSize: "var(--fs-xs)",
                fontWeight: "var(--fw-bold)",
                color: "var(--c-text-2)",
              }}
            >
              최근 AI Paper 판단 (advisory — 주문 신호 아님)
            </div>
            <span
              data-testid="paper-ledger-event-count"
              style={{
                fontSize: "var(--fs-xs)",
                color: "var(--c-text-3)",
              }}
            >
              총 {ledgerEvents.length}건 표시
            </span>
          </div>

          {/* #2-10 신규: 최신 결정 highlight (confidence + risk_flags 강조) */}
          {ledgerEvents[0] && (
            <div
              data-testid="paper-latest-decision"
              data-decision={ledgerEvents[0].decision_action}
              style={{
                marginBottom: 8,
                padding: "8px 10px",
                background: "#ffffff",
                border: "1px solid #cbd5e1",
                borderRadius: "var(--r-sm)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <span
                  data-testid="badge-paper-only"
                  style={{
                    display: "inline-block",
                    padding: "1px 8px",
                    borderRadius: 4,
                    fontWeight: "var(--fw-bold)",
                    fontSize: "var(--fs-xs)",
                    background: "#1e3a8a",
                    color: "#fff",
                  }}
                >
                  Paper 전용 · 실제 주문 아님
                </span>
                <span
                  data-testid="paper-latest-action"
                  style={{
                    display: "inline-block",
                    padding: "1px 8px",
                    borderRadius: 4,
                    fontWeight: "var(--fw-bold)",
                    fontSize: "var(--fs-xs)",
                    background:
                      ledgerEvents[0].decision_action === "HOLD" ? "#94a3b8"
                      : ledgerEvents[0].decision_action === "BUY" ? "#22c55e"
                      : ledgerEvents[0].decision_action === "SELL" ? "#fbbf24"
                      : ledgerEvents[0].decision_action === "EXIT" ? "#6b7280"
                      : "#e2e8f0",
                    color: "#fff",
                  }}
                >
                  {ledgerEvents[0].decision_action}
                </span>
                <span data-testid="paper-latest-strategy"
                       style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)" }}>
                  {ledgerEvents[0].strategy}
                </span>
                <span data-testid="paper-latest-symbol"
                       style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                  {ledgerEvents[0].symbol}
                </span>
                {ledgerEvents[0].confidence != null && (
                  <span data-testid="paper-latest-confidence"
                         style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
                    conf {Math.round(ledgerEvents[0].confidence * 100)}%
                  </span>
                )}
              </div>
              <div data-testid="paper-latest-reason"
                    style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "var(--c-text)" }}>
                {ledgerEvents[0].reason || "(no reason)"}
              </div>
              {Array.isArray(ledgerEvents[0].risk_flags) && ledgerEvents[0].risk_flags.length > 0 && (
                <div data-testid="paper-latest-risk-flags"
                      style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "#b45309" }}>
                  ⚠ {ledgerEvents[0].risk_flags.join(", ")}
                </div>
              )}
            </div>
          )}

          <div data-testid="paper-ledger-list">
            {ledgerEvents.map((ev) => (
              <div
                key={ev.event_id}
                data-testid={`paper-ledger-event-${ev.event_id}`}
                data-decision={ev.decision_action}
                data-loop-state={ev.loop_state}
                style={{
                  fontSize: "var(--fs-xs)",
                  color: "var(--c-text)",
                  padding: "4px 0",
                  borderBottom: "1px dashed #e2e8f0",
                }}
              >
                <code style={{ color: "var(--c-text-3)" }}>
                  {ev.timestamp?.slice(11, 19) || ""}
                </code>{" "}
                <span
                  data-testid={`paper-ledger-action-${ev.event_id}`}
                  style={{
                    display: "inline-block",
                    padding: "1px 6px",
                    borderRadius: 4,
                    fontWeight: "var(--fw-bold)",
                    background:
                      ev.decision_action === "HOLD" ? "#94a3b8"
                      : ev.decision_action === "BUY" ? "#22c55e"
                      : ev.decision_action === "SELL" ? "#fbbf24"
                      : ev.decision_action === "EXIT" ? "#6b7280"
                      : "#e2e8f0",
                    color: "#fff",
                    marginRight: 4,
                  }}
                >
                  {ev.decision_action}
                </span>
                <b>{ev.strategy}</b>
                {" · "}
                {ev.symbol}
                {ev.paper_fill_status && ev.paper_fill_status !== "NA"
                  ? ` · 가상체결: ${ev.paper_fill_status}`
                  : ""}
                {ev.reason ? ` — ${ev.reason}` : ""}
              </div>
            ))}
          </div>
          <div
            data-testid="paper-ledger-disclaimer"
            style={{ marginTop: 6, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}
          >
            본 ledger 는 *advisory* — Paper 가상 체결 / AI 판단만 기록.
            실 broker 호출 0건, is_order_signal=false.
          </div>
        </div>
      )}

      <div
        data-testid="card-disclaimer"
        style={{ marginTop: 10, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}
      >
        본 카드는 *Paper 모의 자동 루프* 만 제어합니다. 시작 버튼이 broker 에
        직접 주문을 보내지 않으며, 실거래는 어떤 경로로도 진행되지 않습니다.
      </div>
    </Card>
  );
}

export default AutoPaperLoopCard;
