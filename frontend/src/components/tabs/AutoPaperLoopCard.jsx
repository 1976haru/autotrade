import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "../common";
import AgentRiskProfileSelector, {
  getRiskProfileLabel,
  normalizeRiskProfile,
} from "../AgentRiskProfileSelector";
import { backendApi } from "../../services/backend/client";
import { formatBuyBlockReason } from "../../utils/buyBlockReasons";
import {
  buildPaperCapitalSummary,
  fromBackendSettings,
  loadPaperCapitalSettings,
  normalizePaperCapitalSettings,
  savePaperCapitalSettings,
  toStartPayloadCapitalSettings,
} from "../../store/usePaperCapitalSettings";

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


// fix/frontend-ci-operator-and-autopaper:
// Auto Paper Loop 상태 정규화 + canStop 정책을 *순수 함수* 로 분리. UI 라벨,
// 버튼 disabled, onClick guard 가 모두 같은 normalized state 를 사용해야
// CI 환경에서 race / alias mismatch 가 발생하지 않는다.
//
// canonical 상태 (backend `AutoPaperState`):
//   PAUSED / WAITING_MARKET / RUNNING / STOPPED / EMERGENCY_STOP / MARKET_CLOSED
//
// 허용 alias (legacy / 대소문자 / 외부 시스템):
//   IDLE / EMERGENCY → PAUSED / EMERGENCY_STOP
//   running / started / active → RUNNING
//   stopped / halted → STOPPED
//   paused → PAUSED

const _STATE_ALIASES = {
  // canonical → canonical (idempotent)
  PAUSED:          "PAUSED",
  WAITING_MARKET:  "WAITING_MARKET",
  RUNNING:         "RUNNING",
  STOPPED:         "STOPPED",
  EMERGENCY_STOP:  "EMERGENCY_STOP",
  MARKET_CLOSED:   "MARKET_CLOSED",
  // legacy.
  IDLE:            "PAUSED",
  EMERGENCY:       "EMERGENCY_STOP",
  // common alias (다른 시스템 / 외부 호출 / 대소문자 일관성).
  STARTED:         "RUNNING",
  ACTIVE:          "RUNNING",
  HALTED:          "STOPPED",
  WAITING:         "WAITING_MARKET",
  CLOSED:          "MARKET_CLOSED",
};


/**
 * Auto Paper Loop 상태 정규화 — *순수 함수*. null / 빈 문자열 / 알 수 없음 →
 * "PAUSED" (안전 fallback). 대소문자 무시.
 *
 * UI 라벨 / 버튼 disabled / onClick guard 모두 본 함수의 결과만 사용해야 한다.
 *
 * @param {string|null|undefined} raw
 * @returns {string} canonical state
 */
export function normalizeAutoPaperState(raw) {
  if (raw == null) return "PAUSED";
  const key = String(raw).trim().toUpperCase();
  if (!key) return "PAUSED";
  return _STATE_ALIASES[key] || "PAUSED";
}


/**
 * 정지(autoPaperStop) 호출 가능 여부 — *순수 함수*. RUNNING 일 때만 정지 가능.
 *
 * 사용자 요청서 §3 정책:
 *  - RUNNING → canStop=true
 *  - WAITING_MARKET / MARKET_CLOSED / STOPPED / PAUSED / EMERGENCY_STOP →
 *    canStop=false (해당 상태에서는 정지할 *running tick* 자체가 없음)
 *
 * @param {string|null|undefined} rawState
 * @returns {boolean}
 */
export function canStopAutoPaper(rawState) {
  return normalizeAutoPaperState(rawState) === "RUNNING";
}


/**
 * 시작(autoPaperStart) 호출 가능 여부 — *순수 함수*.
 * RUNNING / WAITING_MARKET 이미 진행 중인 상태에서는 시작 차단.
 */
export function canStartAutoPaper(rawState) {
  const s = normalizeAutoPaperState(rawState);
  return s !== "RUNNING" && s !== "WAITING_MARKET";
}

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
  // P-15: Paper 자금 설정 — Settings 카드의 localStorage 값 carry.
  // 미지정 시 localStorage 에서 직접 로드. start payload 에 동봉.
  paperCapitalSettings = null,
} = {}) {
  const [status, setStatus] = useState(null);
  const [safety, setSafety] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  // P-16: backend 영속 Paper 자금 설정 — mount 시 1회 로드해 localStorage 에
  // mirror. Settings 탭을 방문하지 않아도 start payload 가 *재실행 후에도 유지된*
  // 자금 기준을 동봉하도록 보장. backend 미가용 시 localStorage fallback.
  const [backendCapital, setBackendCapital] = useState(null);
  // #2-09: Paper Loop advisory ledger — 최근 AI 판단 / 가상 체결 noise-low 표시.
  const [ledgerEvents, setLedgerEvents] = useState([]);
  // P-17: 오늘 매수 불가 사유 요약 (표시 전용 — 실제 매수 로직 0건).
  const [blockedSummary, setBlockedSummary] = useState(null);
  // #4-RiskProfileUI: 사용자가 선택한 AI 운용 성향 — 기본값 BALANCED.
  // start 시점에 backend POST /api/auto-paper/start 요청 body 에 동봉.
  // 영속: 선택한 성향을 localStorage(Paper 자금 설정) 에 저장 → 재마운트/poll
  // 후에도 유지(이전엔 컴포넌트 state 라 BALANCED 로 되돌아가던 버그).
  const [riskProfile, setRiskProfileState] = useState(() =>
    normalizeRiskProfile(
      (paperCapitalSettings || loadPaperCapitalSettings()).riskProfile,
    ),
  );
  const setRiskProfile = useCallback((next) => {
    const v = normalizeRiskProfile(next);
    setRiskProfileState(v);
    // 동일 source(Paper 자금 설정)에 persist — PaperCapitalSettingsCard 와 공유.
    try {
      const cur = paperCapitalSettings || loadPaperCapitalSettings();
      savePaperCapitalSettings({ ...cur, riskProfile: v });
    } catch {
      // localStorage 미가용 — 메모리 상태만 유지.
    }
  }, [paperCapitalSettings]);
  // 자동매매 실행 점검판: run-readiness + 강제 진단 run-once 결과.
  const [readiness, setReadiness] = useState(null);
  const [runOnceResult, setRunOnceResult] = useState(null);
  const [runOnceBusy, setRunOnceBusy] = useState(false);

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
    // run-readiness 도 *별도* — universe / 시장 세션 / 권한 점검판용.
    if (typeof apiClient.autoPaperRunReadiness === "function") {
      try {
        const rr = await apiClient.autoPaperRunReadiness();
        if (rr) setReadiness(rr);
      } catch {
        // readiness 가 없는 환경 (구버전 backend / 테스트 mock) — 조용히 무시.
      }
    }
    // P-17: 오늘 매수 불가 사유 요약 — *별도*, 실패해도 main 표시 유지.
    if (typeof apiClient.autoPaperBlockedReasonsToday === "function") {
      try {
        const bs = await apiClient.autoPaperBlockedReasonsToday({ limit: 5 });
        if (bs) setBlockedSummary(bs);
      } catch {
        // 구버전 backend / 테스트 mock — 조용히 무시.
      }
    }
  }, [apiClient]);

  // 강제 진단 run-once — 파이프라인 전체를 1회 실행하고 결과를 표시.
  // 실거래 아님 — broker 호출 0건. dry_run 기본값으로 ledger 체결 미반영.
  const onRunOnce = useCallback(async () => {
    if (typeof apiClient.autoPaperRunOnceDiagnostic !== "function") return;
    setRunOnceBusy(true);
    try {
      const r = await apiClient.autoPaperRunOnceDiagnostic({
        force_mock_market_data: true,
        dry_run: true,
      });
      setRunOnceResult(r);
      await refresh();
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setRunOnceBusy(false);
    }
  }, [apiClient, refresh]);

  useEffect(() => {
    refresh();
    if (!pollIntervalMs || pollIntervalMs <= 0) return undefined;
    const t = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(t);
  }, [refresh, pollIntervalMs]);

  // P-16: mount 시 backend 영속 자금 설정 1회 로드 (있으면) → localStorage mirror.
  // 실패해도 조용히 무시 — localStorage 값으로 동작 (구버전 backend / 테스트 mock).
  useEffect(() => {
    if (paperCapitalSettings) return;   // prop 우선 — backend 조회 생략.
    if (typeof apiClient.paperCapitalSettingsGet !== "function") return;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiClient.paperCapitalSettingsGet();
        if (cancelled || !res || !res.settings) return;
        const merged = normalizePaperCapitalSettings(
          fromBackendSettings(res.settings),
        ).settings;
        setBackendCapital(merged);
        savePaperCapitalSettings(merged, undefined);   // localStorage mirror.
        setRiskProfileState(normalizeRiskProfile(merged.riskProfile));
      } catch {
        // backend 미가용 — localStorage fallback.
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiClient]);

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

  // P-15/P-16: capital_settings — prop 우선, 다음 backend 영속(mount 로드),
  // 마지막으로 localStorage. 셋 다 동일 shape (normalize 보장).
  const capitalSettings =
    paperCapitalSettings || backendCapital || loadPaperCapitalSettings();
  const capitalSummary = buildPaperCapitalSummary(capitalSettings);
  const capitalPayload = toStartPayloadCapitalSettings(capitalSettings);

  // 시작 버튼: pre-market 결과 + 선택된 risk_profile 을 backend 에 동봉.
  // 서버가 최종 거절 권한 (pre_market BLOCK / EMERGENCY_STOP 등).
  const onStart = useCallback(wrap(async () => {
    // 항상 body 생성 — risk_profile 은 반드시 carry (기본값 BALANCED).
    const body = {
      risk_profile: riskProfile,
      // P-15: Paper 자금 설정 — 시작 시점의 localStorage / prop 값 동봉.
      capital_settings: capitalPayload,
      ...(preMarketCheckResult != null
        ? {
            pre_market: {
              start_allowed:    preMarketCheckResult.start_allowed === true,
              verdict:          preMarketCheckResult.verdict || "",
              blocking_reasons: preMarketCheckResult.blocking_reasons || [],
              warnings:         preMarketCheckResult.warnings || [],
            },
          }
        : {}),
    };
    return apiClient.autoPaperStart(body);
  }), [apiClient, refresh, preMarketCheckResult, riskProfile, capitalPayload]);
  // fix/frontend-ci-operator-and-autopaper: onStop 은 canStop 가드 *별도*.
  // 버튼 disabled 가 어떤 환경 차이로 우회되더라도 *handler 안에서* 한 번
  // 더 검증 — 호출 안전 보장. arrow function 으로 wrap 하여 apiClient.
  // autoPaperStop 의 this 컨텍스트 손실 위험 차단.
  const onStop = useCallback(wrap(async () => {
    return apiClient.autoPaperStop();
  }), [apiClient, refresh]);
  const onEmergencyStop = useCallback(wrap(async () => {
    return apiClient.autoPaperEmergencyStop();
  }), [apiClient, refresh]);

  // feat/step2-01-auto-paper-states: 초기 default = PAUSED (canonical).
  // fix/frontend-ci-operator-and-autopaper: *모든* UI 조건이 동일한
  // normalized state 를 사용해 race / alias mismatch 방지.
  const state = normalizeAutoPaperState(status?.state);
  const stopAllowed = canStopAutoPaper(state);
  const startAllowed = canStartAutoPaper(state);
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

      {/* #4-Loop-09: 최근 cycle 의 Agent consumer 결과 — read-only carry. */}
      <div
        data-testid="auto-paper-consumer-strip"
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 10,
          alignItems: "center",
          marginBottom: 12,
          fontSize: "var(--fs-xs)",
          color: "var(--c-text-2)",
        }}
      >
        <span data-testid="consumer-last-tick">
          마지막 tick:{" "}
          <code>
            {status?.last_tick_at
              ? String(status.last_tick_at).slice(11, 19)
              : "—"}
          </code>
        </span>
        <span data-testid="consumer-last-decision-action">
          최근 판단:{" "}
          {status?.last_decision_action ? (
            <strong
              data-testid={`consumer-action-${status.last_decision_action}`}
              style={{
                display: "inline-block",
                padding: "1px 6px",
                borderRadius: 3,
                background:
                  status.last_decision_action === "BUY" ? "#22c55e"
                  : status.last_decision_action === "SELL" ? "#fbbf24"
                  : status.last_decision_action === "EXIT" ? "#6b7280"
                  : status.last_decision_action === "HOLD" ? "#94a3b8"
                  : "#cbd5e1",
                color: "#fff",
              }}
            >
              {status.last_decision_action}
            </strong>
          ) : (
            <span>—</span>
          )}
        </span>
        <span data-testid="consumer-decision-count">
          판단 수: <strong>{status?.last_decision_count ?? 0}</strong>
        </span>
        <span data-testid="consumer-ledger-events">
          ledger 기록: <strong>{status?.last_ledger_events ?? 0}</strong>
        </span>
        <span data-testid="consumer-decision-log-count">
          AgentDecisionLog 기록:{" "}
          <strong>{status?.last_decision_log_count ?? 0}</strong>
        </span>
        <span
          data-testid="consumer-paper-only-badge"
          style={{
            padding: "1px 6px",
            borderRadius: 3,
            background: "#bef264",
            color: "#0f172a",
            fontWeight: "var(--fw-bold)",
          }}
        >
          Paper 전용 · 실제 주문 아님
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

      {/* #4-RiskProfileUI / fix(ci-policy): 운용 성향 선택 정책 —
          RUNNING(운용 중)에도 *선택 가능*하다. 단 즉시 현재 tick 에 적용되는
          것이 아니라 *다음 tick(다음 판단)부터* 적용된다는 점을 안내한다.
          성향 선택은 주문 권한 / 실거래 권한과 무관 (AGGRESSIVE 선택해도 실거래
          / 추가매수 자동 허용으로 이어지지 않음). 진행 중 액션(busy)일 때만 잠깐
          비활성. */}
      <div style={{ marginBottom: 12 }}>
        <AgentRiskProfileSelector
          value={riskProfile}
          onChange={setRiskProfile}
          disabled={busy}
        />
        <div
          data-testid="current-risk-profile"
          data-risk-profile={riskProfile}
          style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}
        >
          현재 성향: <strong>{getRiskProfileLabel(riskProfile)}</strong>
        </div>
        {state === "RUNNING" && (
          <div
            data-testid="risk-profile-running-notice"
            style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "#a16207" }}
          >
            변경된 운용 성향은 다음 tick(다음 판단)부터 적용됩니다. 현재 진행
            중인 주문에는 영향을 주지 않습니다.
          </div>
        )}
      </div>

      {/* P-15: 적용 자금 기준 요약 — 시작 *전* 사용자에게 노출. */}
      <div
        data-testid="auto-paper-capital-summary"
        style={{
          marginBottom: 10,
          padding: "8px 10px",
          background: "#f1f5f9",
          border: "1px solid #cbd5e1",
          borderRadius: "var(--r-md)",
          fontSize: "var(--fs-xs)",
          color: "var(--c-text)",
          lineHeight: 1.6,
        }}
      >
        <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 2 }}>
          적용 자금 기준
        </div>
        <div data-testid="auto-paper-capital-summary-text">{capitalSummary}</div>
        <div
          data-testid="auto-paper-allow-additional-buy-label"
          style={{
            marginTop: 4,
            color: capitalSettings.allowAdditionalBuy ? "#7f1d1d" : "var(--c-text-2)",
            fontWeight: capitalSettings.allowAdditionalBuy ? "var(--fw-bold)" : "normal",
          }}
        >
          {capitalSettings.allowAdditionalBuy
            ? "동일 종목 추가매수: 허용 — Paper 검증 전용"
            : "동일 종목 추가매수: 비허용"}
        </div>
      </div>

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
          onClick={() => { if (stopAllowed && !busy) onStop(); }}
          disabled={busy || !stopAllowed}
          style={{
            padding: "8px 16px",
            borderRadius: "var(--r-md)",
            background: stopAllowed ? "#fbbf24" : "#94a3b8",
            color: "#fff",
            border: "none",
            cursor: stopAllowed ? "pointer" : "not-allowed",
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

      {/* 자동매매 실행 점검판 — loop / universe / 시장 / 권한 + 강제 진단 run-once.
          "버튼 눌렀는데 아무 일도 안 일어나는 상태" 를 없애기 위한 단일 점검 영역. */}
      <div
        data-testid="auto-paper-exec-diagnostics"
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
            fontSize: "var(--fs-xs)",
            fontWeight: "var(--fw-bold)",
            color: "var(--c-text-2)",
            marginBottom: 6,
          }}
        >
          자동매매 실행 점검판 (advisory — 실거래 아님)
        </div>

        {/* loop health 한 줄 요약 — RUNNING+cycle0 / NOT_RUNNING 등 명확 메시지. */}
        <div
          data-testid="exec-loop-health"
          data-health-code={readiness?.loop?.health_code || ""}
          style={{
            marginBottom: 8,
            fontSize: "var(--fs-xs)",
            color: "var(--c-text)",
          }}
        >
          {state === "RUNNING" && (status?.cycle_count ?? 0) === 0
            ? "거래는 아직 없지만 루프가 실행 중입니다 — 진단 run-once 로 파이프라인 연결을 확인하세요."
            : (readiness?.loop?.health_message
                || "루프 상태를 확인하려면 진단 run-once 를 실행하세요.")}
        </div>

        {/* 항목 그리드 — universe / 시장 세션 / 시세 provider / 권한 / 현금. */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
            gap: 6,
            fontSize: "var(--fs-xs)",
            color: "var(--c-text-2)",
          }}
        >
          <div data-testid="exec-universe">
            Universe:{" "}
            <strong>
              {readiness?.universe?.source || "—"}
              {readiness?.universe?.count != null
                ? ` · ${readiness.universe.count}개`
                : ""}
            </strong>
          </div>
          <div data-testid="exec-market-session">
            시장 세션: <strong>{readiness?.market_session?.phase || "—"}</strong>
          </div>
          <div data-testid="exec-market-data">
            시세 provider:{" "}
            <strong>{readiness?.market_data?.provider || "—"}</strong>
          </div>
          <div data-testid="exec-permission">
            권한:{" "}
            <strong>
              {readiness?.permission?.live_execution_blocked === false
                ? "⚠ LIVE 열림"
                : "실거래 차단 · Paper 허용"}
            </strong>
          </div>
          <div data-testid="exec-paper-cash">
            Paper 현금:{" "}
            <strong>
              {readiness?.paper_capital?.available_cash_krw != null
                ? `${readiness.paper_capital.available_cash_krw.toLocaleString()}원`
                : "—"}
            </strong>
          </div>
          <div data-testid="exec-cycle">
            cycle: <strong>{status?.cycle_count ?? 0}</strong>
          </div>
        </div>

        {/* 자동 tick driver 상태 — opt-in, Paper 전용. 실거래 토글 없음. */}
        {(() => {
          const bt = readiness?.background_tick;
          const enabled = bt?.enabled === true;
          const running = bt?.running === true;
          let msg;
          if (!bt) {
            msg = "자동 tick driver 상태 미확인 — run-once 진단은 수동 실행 가능합니다.";
          } else if (!enabled) {
            msg = "자동 tick driver 비활성 — 현재는 run-once 진단만 수동 실행됩니다.";
          } else if (state === "EMERGENCY_STOP") {
            msg = "긴급정지 ON — driver tick 차단";
          } else if (state === "WAITING_MARKET") {
            msg = "장 시작 전 — driver는 대기 중입니다.";
          } else if (state !== "RUNNING") {
            msg = "AutoPaperLoop 정지 상태 — driver tick 중단";
          } else if (running) {
            msg = `자동 tick driver 활성 — 장중 ${bt.interval_seconds ?? 30}초마다 AI Paper 판단을 실행합니다.`;
          } else {
            msg = `자동 tick driver 활성(대기) — 장중 ${bt.interval_seconds ?? 30}초마다 실행 예정.`;
          }
          return (
            <div
              data-testid="background-tick-status"
              data-enabled={String(enabled)}
              data-running={String(running)}
              style={{
                marginTop: 10,
                padding: "8px 10px",
                background: enabled ? "#ecfeff" : "#f1f5f9",
                border: `1px solid ${enabled ? "#a5f3fc" : "#cbd5e1"}`,
                borderRadius: "var(--r-sm)",
                fontSize: "var(--fs-xs)",
                color: "var(--c-text)",
              }}
            >
              <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 2 }}>
                자동 tick driver:{" "}
                <span data-testid="bg-tick-state-label">
                  {enabled ? (running ? "활성" : "활성(대기)") : "비활성"}
                </span>
                {bt?.dry_run === true && (
                  <span data-testid="bg-tick-dry-run" style={{ marginLeft: 6, color: "var(--c-text-3)" }}>
                    · dry-run
                  </span>
                )}
                <span data-testid="bg-tick-interval" style={{ marginLeft: 6, color: "var(--c-text-3)" }}>
                  · {bt?.interval_seconds ?? 30}초 간격
                </span>
              </div>
              <div data-testid="bg-tick-message">{msg}</div>

              {/* tick 실행 모드 — 진단 dry-run vs Paper 모의 체결. */}
              {bt && (
                <div
                  data-testid="bg-tick-mode"
                  data-tick-mode={bt.tick_mode || "DIAGNOSTIC_DRY_RUN"}
                  style={{ marginTop: 2, color: "var(--c-text-2)" }}
                >
                  현재 모드:{" "}
                  <strong>
                    {bt.tick_mode === "KIS_PAPER_AUTO"
                      ? "KIS 모의투자 자동주문 — 한투 모의 API로 주문 전송"
                      : bt.tick_mode === "SIMULATED_TRADE"
                      ? "Paper 모의 체결 — VirtualOrder와 가상 포트폴리오에 반영"
                      : "진단 dry-run — 주문/체결 반영 없음"}
                  </strong>
                  {" · "}
                  <span data-testid="bg-tick-simulated-fills">
                    {bt.simulated_fills_enabled
                      ? "Paper 체결 시뮬레이션 반영"
                      : "판단만 기록"}
                  </span>
                </div>
              )}

              {/* KIS 모의 자동주문 상태 — 한투 모의투자 API 전용. 실거래 아님. */}
              {(bt?.kis_paper_auto_enabled || bt?.tick_mode === "KIS_PAPER_AUTO") && (
                <div
                  data-testid="bg-tick-kis-auto"
                  data-kis-enabled={String(!!bt?.kis_paper_auto_enabled)}
                  style={{
                    marginTop: 4, padding: "6px 8px", background: "#fffbeb",
                    border: "1px solid #fde68a", borderRadius: "var(--r-sm)",
                    color: "var(--c-text)",
                  }}
                >
                  <div style={{ fontWeight: "var(--fw-bold)" }}>
                    KIS 모의 자동주문:{" "}
                    <span data-testid="bg-tick-kis-state">
                      {bt?.kis_paper_auto_enabled ? "ON" : "OFF"}
                    </span>
                    {bt?.kis_paper_auto_dry_run ? (
                      <span data-testid="bg-tick-kis-dry-run" style={{ marginLeft: 6, color: "var(--c-text-3)" }}>
                        · dry-run (전송 없음)
                      </span>
                    ) : (
                      <span style={{ marginLeft: 6, color: "var(--c-text-3)" }}>· 주문 전송</span>
                    )}
                  </div>
                  {bt?.last_broker_order_no && (
                    <div data-testid="bg-tick-kis-order-no" style={{ marginTop: 2 }}>
                      마지막 KIS 모의 주문번호: <code>{bt.last_broker_order_no}</code>
                      {bt?.last_order_status ? (
                        <span data-testid="bg-tick-kis-order-status"> · {bt.last_order_status}</span>
                      ) : null}
                    </div>
                  )}
                  <div data-testid="bg-tick-kis-safety" style={{ marginTop: 2, color: "var(--c-text-3)" }}>
                    한투 모의투자 API 주문 · 실제 돈이 나가지 않습니다 · 실거래 OFF ·
                    broker_order_type=KIS_PAPER · is_live_authorization=false
                  </div>
                </div>
              )}

              {/* 최근 모의 체결 결과 — order_id / fill_status / 수량 / 명목 / 현금 변화. */}
              {bt?.last_order_id != null && (
                <div data-testid="bg-tick-last-order" style={{ marginTop: 2, color: "var(--c-text)" }}>
                  최근 모의 체결: <code data-testid="bg-tick-order-id">#{bt.last_order_id}</code>
                  {bt.last_fill_status ? (
                    <span data-testid="bg-tick-fill-status"> · {bt.last_fill_status}</span>
                  ) : null}
                  {bt.last_quantity ? (
                    <span data-testid="bg-tick-quantity"> · {bt.last_quantity}주</span>
                  ) : null}
                  {bt.last_notional_krw ? (
                    <span data-testid="bg-tick-notional"> · {Number(bt.last_notional_krw).toLocaleString()}원</span>
                  ) : null}
                  {bt.last_cash_before != null && bt.last_cash_after != null && (
                    <div data-testid="bg-tick-cash-change" style={{ color: "var(--c-text-2)" }}>
                      현금 변화: {Number(bt.last_cash_before).toLocaleString()}원 →{" "}
                      {Number(bt.last_cash_after).toLocaleString()}원
                    </div>
                  )}
                </div>
              )}

              {bt?.last_reason_code && (
                <div data-testid="bg-tick-last-reason" style={{ marginTop: 2, color: "var(--c-text-2)" }}>
                  마지막 tick 사유: <code>{bt.last_reason_code}</code>
                  {bt.last_tick_at ? ` (${String(bt.last_tick_at).slice(11, 19)})` : ""}
                </div>
              )}
              <div data-testid="bg-tick-safety" style={{ marginTop: 2, color: "var(--c-text-3)" }}>
                실제 주문 아님 · broker_order_sent=false · is_live_authorization=false
              </div>
            </div>
          );
        })()}

        <button
          data-testid="btn-run-once-diagnostic"
          onClick={onRunOnce}
          disabled={runOnceBusy}
          style={{
            marginTop: 10,
            padding: "7px 14px",
            borderRadius: "var(--r-md)",
            background: runOnceBusy ? "#94a3b8" : "#0ea5e9",
            color: "#fff",
            border: "none",
            cursor: runOnceBusy ? "not-allowed" : "pointer",
            fontWeight: "var(--fw-bold)",
          }}
        >
          {runOnceBusy ? "진단 실행 중…" : "🔍 진단 실행 (run-once)"}
        </button>

        {/* run-once 결과 — 성공/차단 모두 reason_code + 사유 표시. */}
        {runOnceResult && (
          <div
            data-testid="run-once-result"
            data-result-code={runOnceResult.result_code}
            data-ok={String(!!runOnceResult.ok)}
            style={{
              marginTop: 8,
              padding: "8px 10px",
              background: "#ffffff",
              border: `1px solid ${runOnceResult.ok ? "#86efac" : "#fca5a5"}`,
              borderRadius: "var(--r-sm)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
              <span
                data-testid="run-once-result-code"
                style={{
                  display: "inline-block",
                  padding: "1px 8px",
                  borderRadius: 4,
                  fontWeight: "var(--fw-bold)",
                  fontSize: "var(--fs-xs)",
                  background: runOnceResult.ok ? "#16a34a" : "#dc2626",
                  color: "#fff",
                }}
              >
                {runOnceResult.result_code}
              </span>
              {runOnceResult.quantity != null && runOnceResult.quantity > 0 && (
                <span data-testid="run-once-qty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                  {runOnceResult.quantity}주 · {Number(runOnceResult.notional_krw || 0).toLocaleString()}원
                </span>
              )}
            </div>
            <div
              data-testid="run-once-reason"
              style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "var(--c-text)" }}
            >
              {runOnceResult.reason_message || ""}
            </div>
            {Array.isArray(runOnceResult.stages) && runOnceResult.stages.length > 0 && (
              <div
                data-testid="run-once-stages"
                style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}
              >
                {runOnceResult.stages.map((s, i) => (
                  <span key={i} data-stage={s.stage} data-ok={String(!!s.ok)}>
                    {s.ok ? "✓" : "✕"} {s.stage}
                    {i < runOnceResult.stages.length - 1 ? " → " : ""}
                  </span>
                ))}
              </div>
            )}
            <div style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
              본 진단은 broker 호출 0건 · 실거래 아님 (broker_order_sent=
              {String(runOnceResult.broker_order_sent === true)}).
            </div>
          </div>
        )}
      </div>

      {/* P-17: 오늘 매수 불가 사유 요약 — 표시 전용 (실제 매수 로직 0건). */}
      {blockedSummary && (blockedSummary.total_blocked ?? 0) > 0 && (
        <div
          data-testid="auto-paper-blocked-summary"
          style={{
            marginTop: 12,
            padding: 10,
            background: "#fff7ed",
            border: "1px solid #fed7aa",
            borderRadius: "var(--r-md)",
            fontSize: "var(--fs-xs)",
            lineHeight: 1.7,
            color: "#7c2d12",
          }}
        >
          <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 2 }}>
            🚫 오늘 매수 차단 {blockedSummary.total_blocked}건
          </div>
          {blockedSummary.last_block && (
            <>
              <div data-testid="auto-paper-last-block">
                마지막 차단: {formatBuyBlockReason(blockedSummary.last_block).title}
                {blockedSummary.last_block.symbol
                  ? ` (${blockedSummary.last_block.symbol})` : ""}
              </div>
              <div data-testid="auto-paper-last-block-code" style={{ color: "#9a3412" }}>
                최근 사유: {formatBuyBlockReason(blockedSummary.last_block).code}
              </div>
            </>
          )}
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
