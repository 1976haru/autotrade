/**
 * 체크리스트 #62: Risk Control Panel — 3단계 Kill Switch + 제한값 + 후보 표시.
 *
 * 본 카드는 기존 `KillSwitchPanel` (read-only status)와 별개로 *액션 가능한*
 * 3단계 버튼을 한 자리에 모은다. 모든 위험 버튼은 ConfirmModal 필수.
 *
 * 절대 원칙 (CLAUDE.md):
 *   1. 본 컴포넌트는 broker.place_order / cancel_order 어떤 호출도 만들지 *않는다*.
 *   2. 자동 전량청산 / 자동 취소 버튼 생성 *금지* — 모든 LEVEL 2/3 행동은
 *      candidate 표시까지만.
 *   3. LEVEL 1은 신규 매수 차단(emergency stop ON)만 적용 — broker.cancel/
 *      place 호출 없음. 백엔드 `RiskManager.emergency_stop=True`로만 발현.
 *   4. LIVE_AI_EXECUTION / FUTURES_LIVE flag 토글 노출 *금지*.
 *   5. AI Key / Secret / 계좌번호 입력 필드 *없음*.
 *   6. 본 컴포넌트는 기존 endpoint만 호출:
 *      - GET /api/risk/policy
 *      - GET /api/risk/emergency-stop/status
 *      - POST /api/risk/emergency-stop  (enabled + level + decided_by/note)
 *      - GET /api/risk/emergency-stop/cancel-candidates    (read-only)
 *      - GET /api/risk/emergency-stop/liquidation-candidates (read-only)
 *
 * UX 구조:
 *   1. 현재 안전 상태 헤더 (level badge + 모드 / LIVE flag 상태)
 *   2. 3단계 Kill Switch 버튼 (각 버튼은 모달 필수)
 *   3. 현재 리스크 제한값 (5개 핵심)
 *   4. 미체결 취소 후보 (LEVEL 2 클릭 시 펼침)
 *   5. 청산 후보 (LEVEL 3 클릭 시 펼침)
 *   6. 안전 invariant 안내 문구
 */

import { useCallback, useEffect, useState } from "react";

import { Btn, Card, SectionLabel } from "../common";
import { DecisionDialog } from "../common/DecisionDialog";
import { ErrorState, LoadingState, StatusBadge } from "../common/primitives";
import { backendApi } from "../../services/backend/client";
import { friendlyErrorMessage } from "../../utils/errorMessage";
import { fmtKRW } from "../../utils/format";


// ====================================================================
// 1. Level metadata
// ====================================================================


const LEVEL_BADGE = {
  OFF:     { status: "success", label: "OFF" },
  LEVEL_1: { status: "warning", label: "LEVEL 1" },
  LEVEL_2: { status: "danger",  label: "LEVEL 2" },
  LEVEL_3: { status: "danger",  label: "LEVEL 3" },
};


// 키는 actionType 문자열과 1:1. 운영자 의도가 명확해지도록 ENABLE_/VIEW_/
// RESUME_ prefix를 유지.
const LEVEL_BUTTON_META = {
  ENABLE_LEVEL_1: {
    title:       "신규매수 중단 (LEVEL 1)",
    description: "새로운 BUY 주문만 차단합니다. 보유 포지션은 *자동 청산하지 않습니다*.",
    helperText:  "RiskManager.emergency_stop=True로 설정되어 모든 신규 주문이 즉시 REJECTED 됩니다.",
    confirmLabel: "✓ 신규매수 중단",
    accent:      "#f59e0b",
  },
  VIEW_CANCEL_CANDIDATES: {
    title:       "미체결 취소 후보 확인 (LEVEL 2)",
    description: "미체결 / 승인 대기 주문 목록을 확인합니다. **실제 취소 주문은 실행하지 않습니다** — 후보만 표시합니다.",
    helperText:  "운영자가 후보를 확인한 뒤 결재 탭에서 수동으로 한 건씩 취소해야 합니다.",
    confirmLabel: "📋 후보 확인",
    accent:      "#fbbf24",
  },
  VIEW_LIQUIDATION_CANDIDATES: {
    title:       "청산 후보 표시 (LEVEL 3)",
    description: "현재 보유 포지션을 청산 후보로 표시합니다. **자동 전량청산은 비활성화되어 있습니다** — 후보만 표시합니다.",
    helperText:  "호가 공백 / 급락 상황에서 자동 시장가 전량청산은 위험합니다. 운영자가 호가를 확인하고 수동 승인해야 합니다.",
    confirmLabel: "📋 청산 후보 보기",
    accent:      "#ef4444",
  },
  RESUME_OFF: {
    title:       "Kill Switch 해제 (OFF)",
    description: "모든 단계의 Kill Switch를 해제합니다. 정상 운영(신규 주문 허용) 상태로 돌아갑니다.",
    helperText:  "해제 후에도 RiskManager의 기본 가드(notional / position / loss / freshness)는 그대로 유지됩니다.",
    confirmLabel: "✓ 해제",
    accent:      "#22c55e",
  },
};


// ====================================================================
// 2. Risk policy limit summary
// ====================================================================


/**
 * 핵심 5개 제한값만 highlight — 운영자가 한눈에 "지금 어떤 한도가 걸려
 * 있는지" 파악. 더 상세한 22개 필드는 기존 `BackendPolicyCard`에서.
 */
const KEY_LIMITS = [
  { key: "max_order_notional",   label: "1회 주문 한도",     kind: "krw"   },
  { key: "max_symbol_exposure",  label: "종목별 노출 한도",  kind: "krw"   },
  { key: "max_total_exposure",   label: "총 노출 한도",      kind: "krw"   },
  { key: "max_positions",        label: "최대 보유 종목",    kind: "count" },
  { key: "max_daily_loss",       label: "일일 손실 한도",    kind: "krw"   },
];


function formatLimitValue(policy, key, kind) {
  const v = policy?.[key];
  if (v == null || v === "") return null;
  if (kind === "krw" && typeof v === "number") {
    if (v === 0) return "비활성";
    return `${fmtKRW(v)}원`;
  }
  if (kind === "count" && typeof v === "number") {
    if (v === 0) return "비활성";
    return `${v}건`;
  }
  return String(v);
}


export function RiskLimitsSummary({ policy }) {
  if (!policy) {
    return (
      <div data-testid="risk-limits-summary-empty"
           style={{ fontSize: 11, color: "#94a3b8",
                    padding: 8, background: "#0c2035",
                    border: "1px solid #1e3a5c", borderRadius: 4 }}>
        설정값 없음 — 백엔드 연결 대기 중 또는 기본 안전값 사용.
      </div>
    );
  }
  return (
    <div data-testid="risk-limits-summary"
         style={{
           display: "grid", gridTemplateColumns: "1fr 1fr",
           gap: 6, fontSize: 11,
         }}>
      {KEY_LIMITS.map((f) => {
        const display = formatLimitValue(policy, f.key, f.kind);
        return (
          <div key={f.key}
               data-testid={`risk-limit-${f.key}`}
               style={{
                 padding: "6px 8px", background: "#0c2035",
                 border: "1px solid #1e3a5c", borderRadius: 4,
               }}>
            <div style={{ fontSize: 9, color: "#64748b", marginBottom: 2 }}>
              {f.label}
            </div>
            <div style={{ fontSize: 11, fontWeight: 700,
                           color: display === "비활성" ? "#94a3b8" : "#7dd3fc" }}>
              {display ?? "설정값 없음"}
            </div>
          </div>
        );
      })}
    </div>
  );
}


// ====================================================================
// 3. Safety flags summary
// ====================================================================


/**
 * `enable_live_trading` / `enable_ai_execution` / `enable_futures_live_trading`
 * 세 LIVE 가드 + emergency_stop 상태를 한 줄로. 모두 false여야 안전.
 */
export function SafetyFlagsRow({ policy, emergencyStop }) {
  const liveOn  = !!policy?.enable_live_trading;
  const aiOn    = !!policy?.enable_ai_execution;
  const futOn   = !!policy?.enable_futures_live_trading;
  return (
    <div data-testid="risk-safety-flags-row"
         style={{
           display: "flex", flexWrap: "wrap", gap: 6, fontSize: 10,
         }}>
      <FlagChip label="실거래"
                ok={!liveOn}
                okText="비활성"
                warnText="활성화됨"
                testId="flag-live" />
      <FlagChip label="AI 실행"
                ok={!aiOn}
                okText="비활성"
                warnText="활성화됨"
                testId="flag-ai" />
      <FlagChip label="선물 실거래"
                ok={!futOn}
                okText="비활성"
                warnText="활성화됨"
                testId="flag-futures" />
      <FlagChip label="긴급 정지"
                ok={!emergencyStop}
                okText="OFF"
                warnText="ON"
                testId="flag-emergency"
                invertColor />
    </div>
  );
}


function FlagChip({ label, ok, okText, warnText, testId, invertColor = false }) {
  // invertColor: 긴급 정지의 경우 "ON"이 red, "OFF"가 green (다른 flag는 OK가 녹색).
  const color = ok
    ? (invertColor ? "#22c55e" : "#22c55e")
    : "#ef4444";
  return (
    <span
      data-testid={testId}
      data-ok={ok ? "true" : "false"}
      style={{
        padding: "2px 8px", borderRadius: 3, fontSize: 10, fontWeight: 700,
        color, background: `${color}15`, border: `1px solid ${color}55`,
      }}>
      {label}: {ok ? okText : warnText}
    </span>
  );
}


// ====================================================================
// 4. Cancel / Liquidation candidate lists
// ====================================================================


export function CancelCandidatesList({ candidates, error }) {
  if (error) {
    return (
      <div data-testid="cancel-candidates-error"
           style={{ fontSize: 11, color: "#f87171",
                    padding: 8, background: "#7f1d1d22",
                    border: "1px solid #ef444466", borderRadius: 4 }}>
        {friendlyErrorMessage(error) || "미체결 취소 후보를 불러올 수 없어요."}
      </div>
    );
  }
  if (!candidates || candidates.length === 0) {
    return (
      <div data-testid="cancel-candidates-empty"
           style={{ fontSize: 11, color: "#94a3b8",
                    padding: 12, textAlign: "center",
                    background: "#0c2035",
                    border: "1px dashed #1e3a5c", borderRadius: 4 }}>
        미체결 취소 후보가 없습니다.
      </div>
    );
  }
  return (
    <div data-testid="cancel-candidates-list"
         style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div data-testid="cancel-candidates-banner"
           style={{ fontSize: 10, color: "#fbbf24",
                    padding: "4px 8px", background: "#fbbf2415",
                    border: "1px solid #fbbf2455", borderRadius: 3 }}>
        ⚠ 실제 취소 아님 · 운영자가 결재 탭에서 수동 취소해야 합니다.
      </div>
      {candidates.map((c, idx) => (
        <div key={c.id ?? `cc-${idx}`}
             data-testid={`cancel-candidate-${c.id ?? idx}`}
             style={{
               padding: "6px 8px", background: "#0c2035",
               border: "1px solid #1e3a5c", borderRadius: 4, fontSize: 11,
             }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                         alignItems: "baseline" }}>
            <div>
              <span style={{ color: "#7dd3fc", fontWeight: 700 }}>{c.symbol}</span>
              <span style={{
                color: c.side === "BUY" ? "#22c55e" : "#ef4444",
                marginLeft: 6, fontSize: 10, fontWeight: 700,
              }}>{c.side}</span>
              <span style={{ marginLeft: 6, color: "#94a3b8" }}>
                {c.quantity}주 · {c.order_type}
              </span>
            </div>
            <span style={{ fontSize: 9, color: "#64748b" }}>
              #{c.id}
            </span>
          </div>
          <div style={{ fontSize: 9, color: "#475569", marginTop: 2 }}>
            {c.created_at ? new Date(c.created_at).toLocaleString("ko-KR") : "—"}
            {c.status && ` · ${c.status}`}
            {c.reason && (
              <span style={{ marginLeft: 4, color: "#fbbf24" }}>· {c.reason}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}


export function LiquidationCandidatesList({ candidates, totalUnrealized, error }) {
  if (error) {
    return (
      <div data-testid="liquidation-candidates-error"
           style={{ fontSize: 11, color: "#f87171",
                    padding: 8, background: "#7f1d1d22",
                    border: "1px solid #ef444466", borderRadius: 4 }}>
        {friendlyErrorMessage(error) || "청산 후보를 불러올 수 없어요."}
      </div>
    );
  }
  if (!candidates || candidates.length === 0) {
    return (
      <div data-testid="liquidation-candidates-empty"
           style={{ fontSize: 11, color: "#94a3b8",
                    padding: 12, textAlign: "center",
                    background: "#0c2035",
                    border: "1px dashed #1e3a5c", borderRadius: 4 }}>
        청산 후보가 없습니다.
      </div>
    );
  }
  return (
    <div data-testid="liquidation-candidates-list"
         style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div data-testid="liquidation-candidates-banner"
           style={{ fontSize: 10, color: "#ef4444",
                    padding: "4px 8px", background: "#ef444415",
                    border: "1px solid #ef444455", borderRadius: 3 }}>
        ⚠ 자동 청산 아님 · 호가/시장 상황 확인 후 수동 승인 필요. 자동 전량청산
        버튼은 비활성화되어 있습니다.
      </div>
      {totalUnrealized != null && (
        <div data-testid="liquidation-total-unrealized"
             style={{ fontSize: 10, color: "#94a3b8", padding: "2px 4px" }}>
          총 미실현 PnL{" "}
          <span style={{
            color: totalUnrealized >= 0 ? "#22c55e" : "#ef4444",
            fontWeight: 700,
          }}>
            {totalUnrealized >= 0 ? "+" : ""}{fmtKRW(totalUnrealized)}원
          </span>
        </div>
      )}
      {candidates.map((c, idx) => {
        const pnl = c.unrealized_pnl ?? 0;
        return (
          <div key={c.symbol ?? `liq-${idx}`}
               data-testid={`liquidation-candidate-${c.symbol ?? idx}`}
               style={{
                 padding: "6px 8px", background: "#0c2035",
                 border: "1px solid #1e3a5c", borderRadius: 4, fontSize: 11,
               }}>
            <div style={{ display: "flex", justifyContent: "space-between",
                           alignItems: "baseline" }}>
              <div>
                <span style={{ color: "#7dd3fc", fontWeight: 700 }}>{c.symbol}</span>
                <span style={{ marginLeft: 6, color: "#94a3b8" }}>
                  {c.quantity}주
                </span>
              </div>
              <span style={{
                fontSize: 11, fontWeight: 700,
                color: pnl >= 0 ? "#22c55e" : "#ef4444",
              }}>
                {pnl >= 0 ? "+" : ""}{fmtKRW(pnl)}원
              </span>
            </div>
            <div style={{ fontSize: 9, color: "#475569", marginTop: 2 }}>
              평단 {fmtKRW(c.avg_price)}원 · 현재 {fmtKRW(c.market_price ?? c.current_price)}원
              {c.risk_reason && (
                <span style={{ marginLeft: 4, color: "#fbbf24" }}>
                  · {c.risk_reason}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}


// ====================================================================
// 5. Confirm modal
// ====================================================================


/**
 * 위험 액션 확인 모달. DecisionDialog 재사용 — 운영자명/사유 입력 유지.
 *
 * actionType별로 안전 문구가 다르다. 모달은 항상:
 *  - 무엇을 실행할지
 *  - 실제 주문 여부 (LEVEL 2/3은 "실제 주문 실행 안 함" 명시)
 *  - 자동 청산 여부 (LEVEL 3은 "자동 전량청산 비활성화" 명시)
 *  - 운영자명 / 사유 입력
 */
export function RiskActionConfirmModal({
  actionType, busy, onConfirm, onCancel, defaultDecidedBy = "",
}) {
  const meta = LEVEL_BUTTON_META[actionType];
  if (!meta) return null;

  // 위험도가 높은 LEVEL 3은 빨강, LEVEL 1/2는 amber. RESUME은 녹색.
  return (
    <DecisionDialog
      title={meta.title}
      ariaLabel={meta.title}
      accent={meta.accent}
      cancelLabel="취소"
      confirmLabel={meta.confirmLabel}
      summary={
        <div
          data-testid={`risk-confirm-summary-${actionType}`}
          style={{
            fontSize: 11, color: "#cbd5e1",
            padding: "8px 10px", marginBottom: 10,
            background: "#010a14",
            border: `1px solid ${meta.accent}55`,
            borderRadius: 4, lineHeight: 1.5,
          }}>
          <div style={{ fontWeight: 700, color: meta.accent, marginBottom: 4 }}>
            {meta.description}
          </div>
          <div style={{ fontSize: 10, color: "#94a3b8" }}>
            {meta.helperText}
          </div>
          {/* 명시적 안전 invariant */}
          <ul style={{
            fontSize: 9, color: "#64748b", margin: "6px 0 0",
            paddingLeft: 16, lineHeight: 1.5,
          }}>
            {actionType === "ENABLE_LEVEL_1" && (
              <>
                <li>이 작업은 신규 매수만 중단합니다.</li>
                <li>보유 포지션을 자동 청산하지 *않습니다*.</li>
                <li>미체결 주문도 자동 취소하지 *않습니다*.</li>
              </>
            )}
            {actionType === "VIEW_CANCEL_CANDIDATES" && (
              <>
                <li>미체결 주문 취소 후보만 *표시*합니다.</li>
                <li>실제 cancel_order 호출은 *발생하지 않습니다*.</li>
                <li>취소는 결재 탭에서 운영자가 수동 진행해야 합니다.</li>
              </>
            )}
            {actionType === "VIEW_LIQUIDATION_CANDIDATES" && (
              <>
                <li>현재 보유 포지션을 청산 후보로 *표시*합니다.</li>
                <li>자동 전량청산은 *비활성화*되어 있습니다.</li>
                <li>실제 청산은 운영자가 호가 확인 후 별도 수동 승인.</li>
              </>
            )}
            {actionType === "RESUME_OFF" && (
              <>
                <li>Kill Switch를 OFF로 되돌립니다.</li>
                <li>RiskManager의 기본 가드(notional / loss / freshness)는 유지됩니다.</li>
              </>
            )}
          </ul>
        </div>
      }
      description="감사 추적을 위해 운영자명 / 사유를 남겨주세요."
      notePlaceholder="예: 변동성 급증, 신호 노후 점검"
      busy={busy}
      defaultDecidedBy={defaultDecidedBy}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}


// ====================================================================
// 6. RiskControlPanel — orchestrator
// ====================================================================


export function RiskControlPanel({ operatorName = "" }) {
  const [policy,           setPolicy]           = useState(null);
  const [status,           setStatus]           = useState(null);
  const [policyError,      setPolicyError]      = useState("");
  const [statusError,      setStatusError]      = useState("");
  const [loadingInitial,   setLoadingInitial]   = useState(true);
  const [busy,             setBusy]             = useState(false);

  // pending modal — actionType | null
  const [pendingAction,    setPendingAction]    = useState(null);

  // candidate lists (lazy load on LEVEL 2/3 confirm)
  const [cancelCandidates,        setCancelCandidates]        = useState(null);
  const [liquidationCandidates,   setLiquidationCandidates]   = useState(null);
  const [liquidationTotal,        setLiquidationTotal]        = useState(null);
  const [cancelError,             setCancelError]             = useState("");
  const [liquidationError,        setLiquidationError]        = useState("");

  // ---------- initial fetch: policy + status ----------
  const refresh = useCallback(async () => {
    try {
      const p = await backendApi.getRiskPolicy();
      setPolicy(p);
      setPolicyError("");
    } catch (e) {
      setPolicyError(e.message || String(e));
    }
    try {
      const s = await backendApi.emergencyStopStatus();
      setStatus(s);
      setStatusError("");
    } catch (e) {
      setStatusError(e.message || String(e));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await refresh();
      if (!cancelled) setLoadingInitial(false);
    })();
    return () => { cancelled = true; };
  }, [refresh]);

  const currentLevel = status?.level || "OFF";
  const isOff = currentLevel === "OFF";

  // ---------- action handlers ----------
  const handleConfirm = useCallback(async (decision) => {
    if (!pendingAction) return { ok: false, message: "no action" };
    setBusy(true);
    try {
      const actionType = pendingAction;
      if (actionType === "ENABLE_LEVEL_1") {
        await backendApi.setEmergencyStop(true, {
          level: "LEVEL_1",
          ...(decision || {}),
        });
        await refresh();
      } else if (actionType === "VIEW_CANCEL_CANDIDATES") {
        // LEVEL 2: level=LEVEL_2 토글 + read-only candidate fetch.
        await backendApi.setEmergencyStop(true, {
          level: "LEVEL_2",
          ...(decision || {}),
        });
        const data = await backendApi.emergencyStopCancelCandidates();
        setCancelCandidates(Array.isArray(data?.candidates) ? data.candidates : []);
        setCancelError("");
        await refresh();
      } else if (actionType === "VIEW_LIQUIDATION_CANDIDATES") {
        // LEVEL 3: level=LEVEL_3 토글 + read-only candidate fetch.
        await backendApi.setEmergencyStop(true, {
          level: "LEVEL_3",
          ...(decision || {}),
        });
        const data = await backendApi.emergencyStopLiquidationCandidates();
        setLiquidationCandidates(Array.isArray(data?.candidates) ? data.candidates : []);
        setLiquidationTotal(data?.total_unrealized_pnl ?? null);
        setLiquidationError("");
        await refresh();
      } else if (actionType === "RESUME_OFF") {
        await backendApi.setEmergencyStop(false, decision || {});
        // 후보 list도 초기화 — OFF면 더 이상 의미 없음
        setCancelCandidates(null);
        setLiquidationCandidates(null);
        setLiquidationTotal(null);
        await refresh();
      }
      setPendingAction(null);
      return { ok: true };
    } catch (e) {
      const msg = e.message || String(e);
      // 모달 안에 inline 에러로 표시 + 컴포넌트 외부 상태도 갱신
      if (pendingAction === "VIEW_CANCEL_CANDIDATES") setCancelError(msg);
      if (pendingAction === "VIEW_LIQUIDATION_CANDIDATES") setLiquidationError(msg);
      return { ok: false, message: friendlyErrorMessage(msg) || msg };
    } finally {
      setBusy(false);
    }
  }, [pendingAction, refresh]);

  // ---------- render ----------
  if (loadingInitial) {
    return (
      <Card>
        <LoadingState testId="risk-control-panel-loading"
          title="리스크 상태 확인 중..." />
      </Card>
    );
  }

  if (policyError && statusError) {
    // 둘 다 실패 — backend 자체가 다운된 경우
    return (
      <Card>
        <ErrorState
          testId="risk-control-panel-error"
          title="리스크 상태 조회 실패"
          hint={friendlyErrorMessage(policyError) || "백엔드 연결을 확인하세요."}
          retryLabel="다시 시도"
          onRetry={refresh}
        />
      </Card>
    );
  }

  const levelBadge = LEVEL_BADGE[currentLevel] || LEVEL_BADGE.OFF;

  return (
    <Card accentColor={isOff ? "#22c55e33" : "#ef444455"}>
      <div data-testid="risk-control-panel"
           data-level={currentLevel}
           style={{ display: "flex", flexDirection: "column", gap: 10 }}>

        {/* 헤더 */}
        <div style={{
          display: "flex", justifyContent: "space-between",
          alignItems: "baseline", gap: 8, flexWrap: "wrap",
        }}>
          <SectionLabel>🛡 리스크 컨트롤 패널</SectionLabel>
          <StatusBadge status={levelBadge.status}
                       testId="risk-control-panel-level-badge">
            {levelBadge.label}
          </StatusBadge>
        </div>

        <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.5 }}>
          사용자가 즉시 위험을 멈출 수 있는 3단계 Kill Switch입니다. 위험한 작업은
          확인 모달을 거치며, 본 패널은 *어떤 broker API도 직접 호출하지 않습니다*.
        </div>

        {/* 안전 flag 상태 */}
        <SafetyFlagsRow policy={policy}
                        emergencyStop={!!status?.emergency_stop} />

        {/* 3단계 Kill Switch 버튼 */}
        <div data-testid="risk-control-buttons"
             style={{
               display: "flex", flexDirection: "column", gap: 6,
             }}>
          <div style={{ fontSize: 10, color: "#64748b" }}>
            3단계 Kill Switch — 각 버튼 클릭 시 확인 모달이 표시됩니다.
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <Btn
              full small color="#f59e0b"
              onClick={() => setPendingAction("ENABLE_LEVEL_1")}
              disabled={busy}
            >
              ⛔ 신규매수 중단 (LEVEL 1)
            </Btn>
            <Btn
              full small color="#fbbf24"
              onClick={() => setPendingAction("VIEW_CANCEL_CANDIDATES")}
              disabled={busy}
            >
              📋 미체결 취소 후보 확인 (LEVEL 2)
            </Btn>
            <Btn
              full small color="#ef4444"
              onClick={() => setPendingAction("VIEW_LIQUIDATION_CANDIDATES")}
              disabled={busy}
            >
              🚨 청산 후보 표시 (LEVEL 3)
            </Btn>
            {!isOff && (
              <Btn
                full small color="#22c55e"
                onClick={() => setPendingAction("RESUME_OFF")}
                disabled={busy}
              >
                ✓ Kill Switch 해제 (OFF로 복귀)
              </Btn>
            )}
          </div>
          <div data-testid="risk-control-auto-liquidation-warning"
               style={{
                 fontSize: 10, color: "#fbbf24",
                 padding: "4px 8px", background: "#fbbf2415",
                 border: "1px solid #fbbf2455", borderRadius: 3,
                 lineHeight: 1.5,
               }}>
            ⚠ 자동 전량청산 버튼은 의도적으로 *생성되지 않았습니다*. 청산은
            운영자가 호가/시장 상황을 확인한 뒤 별도 수동 승인 흐름에서만
            진행됩니다.
          </div>
        </div>

        {/* 제한값 */}
        <div>
          <div style={{ fontSize: 10, color: "#64748b", marginBottom: 4 }}>
            현재 리스크 제한값 (핵심 5개)
          </div>
          <RiskLimitsSummary policy={policy} />
        </div>

        {/* 후보 list — LEVEL 2/3 클릭 후에만 채워짐 */}
        {cancelCandidates !== null && (
          <div>
            <div style={{ fontSize: 10, color: "#64748b", marginBottom: 4 }}>
              미체결 취소 후보
            </div>
            <CancelCandidatesList candidates={cancelCandidates}
                                  error={cancelError} />
          </div>
        )}
        {liquidationCandidates !== null && (
          <div>
            <div style={{ fontSize: 10, color: "#64748b", marginBottom: 4 }}>
              청산 후보 (보유 포지션)
            </div>
            <LiquidationCandidatesList candidates={liquidationCandidates}
                                       totalUnrealized={liquidationTotal}
                                       error={liquidationError} />
          </div>
        )}

        {/* policy 또는 status 단일 실패 시 inline 안내 */}
        {policyError && !statusError && (
          <div data-testid="risk-control-policy-error"
               style={{ fontSize: 10, color: "#fbbf24" }}>
            리스크 정책: {friendlyErrorMessage(policyError) || "조회 실패"}
          </div>
        )}
        {statusError && !policyError && (
          <div data-testid="risk-control-status-error"
               style={{ fontSize: 10, color: "#fbbf24" }}>
            Kill Switch 상태: {friendlyErrorMessage(statusError) || "조회 실패"}
          </div>
        )}

        {/* 새로고침 */}
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <Btn small color="#334155" onClick={refresh} disabled={busy}>
            ↻ 새로고침
          </Btn>
        </div>
      </div>

      {pendingAction && (
        <RiskActionConfirmModal
          actionType={pendingAction}
          busy={busy}
          defaultDecidedBy={operatorName}
          onConfirm={handleConfirm}
          onCancel={() => setPendingAction(null)}
        />
      )}
    </Card>
  );
}


export default RiskControlPanel;
