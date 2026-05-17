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
const _STATE_COLOR = {
  PAUSED:         "#94a3b8",
  IDLE:           "#94a3b8",   // legacy alias
  RUNNING:        "#22c55e",
  STOPPED:        "#fbbf24",
  EMERGENCY_STOP: "#ef4444",
  EMERGENCY:      "#ef4444",   // legacy alias
};

const _STATE_LABEL = {
  PAUSED:         "대기 (일시정지)",
  IDLE:           "대기 (일시정지)",
  RUNNING:        "AI Paper Auto Loop 진행 중",
  STOPPED:        "정지됨",
  EMERGENCY_STOP: "긴급정지됨",
  EMERGENCY:      "긴급정지됨",
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
          disabled={busy || state === "RUNNING" || preMarketBlocked}
          style={{
            padding: "8px 16px",
            borderRadius: "var(--r-md)",
            background: (state === "RUNNING" || preMarketBlocked) ? "#94a3b8" : "#22c55e",
            color: "#fff",
            border: "none",
            cursor: (state === "RUNNING" || preMarketBlocked) ? "not-allowed" : "pointer",
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
