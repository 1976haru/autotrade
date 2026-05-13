/**
 * 체크리스트 #73: Live Manual Gate readiness card.
 *
 * CLAUDE.md 절대 원칙:
 *   1. *실거래 활성화 버튼 없음* — 본 카드는 readiness 평가만 표시.
 *   2. `ENABLE_LIVE_TRADING` 등 안전 플래그를 *변경하지 않는다*.
 *   3. broker / 주문 / route_order 호출 0건.
 *   4. UI 상단에 "PASS는 실거래 자동 허가가 아니라, 초소액 수동승인 검토
 *      가능 상태입니다." 위험 문구 강제 표시 (테스트로 lock).
 *
 * 표시:
 *   - 현재 verdict (BLOCKED / CAUTION / PASS / UNKNOWN)
 *   - Paper Gate / Promotion Gate / opt-in / approval_required 통과 여부
 *   - max order notional / max daily loss / allowed symbols / open positions
 *   - audit missing count / system error count / approval bypass attempts
 *   - required actions
 *
 * 운영자가 직접 입력하는 *평가용* 카드 — 본 카드 자체로 live flag 변경 X.
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const VERDICT_COLOR = {
  PASS:    "#22c55e",
  CAUTION: "#f59e0b",
  BLOCKED: "#ef4444",
  UNKNOWN: "#94a3b8",
};

const VERDICT_LABEL = {
  PASS:    "검토 가능",
  CAUTION: "주의 / 검토 필요",
  BLOCKED: "차단됨",
  UNKNOWN: "측정 불가",
};


function VerdictBadge({ verdict }) {
  const color = VERDICT_COLOR[verdict] || VERDICT_COLOR.UNKNOWN;
  const label = VERDICT_LABEL[verdict] || verdict || "—";
  return (
    <span
      data-testid={`live-manual-verdict-${verdict}`}
      style={{
        display: "inline-block",
        padding: "4px 12px",
        borderRadius: 4,
        fontSize: 12,
        fontWeight: 700,
        color,
        background: `${color}15`,
        border: `1px solid ${color}55`,
      }}
    >
      {label}
    </span>
  );
}


function FlagRow({ label, value }) {
  const ok = !!value;
  return (
    <div
      data-testid={`live-manual-flag-${label}`}
      style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center",
        padding: "6px 10px",
        borderBottom: "1px solid var(--c-border)",
        fontSize: 12,
      }}
    >
      <span>{label}</span>
      <span
        style={{
          fontWeight: 700,
          color: ok ? "#22c55e" : "#ef4444",
        }}
      >
        {ok ? "✓" : "—"}
      </span>
    </div>
  );
}


const DEFAULT_INPUT = {
  strategy_name:                 "sma_cross",
  paper_gate_passed:             false,
  promotion_gate_passed:         false,
  user_explicit_opt_in:          false,
  approval_required:             true,
  ai_execution_enabled:          false,
  futures_live_enabled:          false,
  enable_live_trading:           false,
  current_max_order_notional_krw: 30_000,
  current_max_daily_loss_krw:     8_000,
  current_max_open_positions:     2,
  allowed_symbols:                [],
  operating_days:                 0,
  total_live_manual_orders:       0,
  approved_orders:                0,
  rejected_orders:                0,
  expired_or_cancelled_orders:    0,
  approval_bypass_attempts:       0,
  audit_missing_count:            0,
  system_errors:                  0,
  emergency_stops_in_period:      0,
};


export function LiveManualGateCard({ inputOverride = null, resultOverride = null }) {
  const [result, setResult] = useState(resultOverride);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [input, setInput] = useState(inputOverride || DEFAULT_INPUT);

  // resultOverride 가 변경되면 즉시 반영 — 테스트 props 갱신용.
  useEffect(() => {
    if (resultOverride) setResult(resultOverride);
  }, [resultOverride]);

  const onEvaluate = async () => {
    if (resultOverride) return;        // 테스트 모드에서는 fetch 안 함.
    setLoading(true);
    setError("");
    try {
      const r = await backendApi.liveManualGateEvaluate(input);
      setResult(r);
    } catch (e) {
      setError(e?.message || "Live Manual Gate 평가 실패");
    } finally {
      setLoading(false);
    }
  };

  const verdict  = result?.verdict || "UNKNOWN";
  const metrics  = result?.metrics || {};
  const blocked  = result?.blocked_criteria || [];
  const cautions = result?.cautions || [];
  const actions  = result?.required_actions || [];

  return (
    <Card style={{ marginBottom: 12 }} accentColor={VERDICT_COLOR[verdict]}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>Live Manual Gate — readiness 평가</SectionLabel>
        <VerdictBadge verdict={verdict} />
      </div>

      <div
        data-testid="live-manual-disclaimer"
        style={{
          padding: "10px 12px",
          background: "#fef3c7",
          border: "1px solid #f59e0b55",
          color: "#92400e",
          borderRadius: 6,
          fontSize: 12,
          fontWeight: 600,
          marginBottom: 12,
        }}
      >
        ⚠️ PASS는 실거래 자동 허가가 아니라, 초소액 수동승인 검토 가능 상태입니다.
      </div>

      {/* 평가 버튼 — "활성화" 아님. */}
      <button
        data-testid="live-manual-evaluate-btn"
        onClick={onEvaluate}
        disabled={loading || !!resultOverride}
        style={{
          padding: "8px 14px",
          borderRadius: 6,
          border: "1px solid var(--c-border-strong)",
          background: "var(--c-surface-2)",
          color: "var(--c-text)",
          cursor: loading ? "wait" : "pointer",
          fontSize: 12,
          marginBottom: 10,
        }}
      >
        {loading ? "평가 중…" : "활성화 가능성 평가"}
      </button>

      {error ? (
        <div
          data-testid="live-manual-error"
          style={{
            padding: 8, color: "#b91c1c", fontSize: 12,
            background: "#fee2e2", borderRadius: 4, marginBottom: 10,
          }}
        >
          {error}
        </div>
      ) : null}

      {result ? (
        <>
          <div style={{
            display: "grid", gridTemplateColumns: "1fr 1fr",
            gap: 8, marginBottom: 10,
          }}>
            <div style={{
              border: "1px solid var(--c-border)", borderRadius: 6,
            }}>
              <div style={{
                padding: "6px 10px",
                background: "var(--c-surface-2)",
                fontSize: 11, fontWeight: 700,
              }}>
                전제 조건
              </div>
              <FlagRow label="paper_gate_passed"     value={metrics.paper_gate_passed} />
              <FlagRow label="promotion_gate_passed" value={metrics.promotion_gate_passed} />
              <FlagRow label="user_explicit_opt_in"  value={metrics.user_explicit_opt_in} />
              <FlagRow label="approval_required"     value={metrics.approval_required} />
            </div>
            <div style={{
              border: "1px solid var(--c-border)", borderRadius: 6,
            }}>
              <div style={{
                padding: "6px 10px",
                background: "var(--c-surface-2)",
                fontSize: 11, fontWeight: 700,
              }}>
                안전 플래그 (현재값)
              </div>
              <FlagRow label="ai_execution_disabled"  value={!metrics.ai_execution_enabled} />
              <FlagRow label="futures_live_disabled"  value={!metrics.futures_live_enabled} />
              <FlagRow label="approval_bypass_zero"   value={(metrics.approval_bypass_attempts || 0) === 0} />
              <FlagRow label="audit_missing_zero"     value={(metrics.audit_missing_count || 0) === 0} />
            </div>
          </div>

          <div data-testid="live-manual-limits" style={{
            border: "1px solid var(--c-border)", borderRadius: 6,
            marginBottom: 10,
          }}>
            <div style={{
              padding: "6px 10px",
              background: "var(--c-surface-2)",
              fontSize: 11, fontWeight: 700,
            }}>
              극소액 정책 (현재 적용 한도)
            </div>
            <div style={{
              padding: "8px 10px", fontSize: 12,
              display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
              gap: 6,
            }}>
              <div>
                <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>1회 주문</div>
                <div style={{ fontWeight: 700 }}>
                  {(metrics.current_max_order_notional_krw || 0).toLocaleString()} 원
                </div>
              </div>
              <div>
                <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>일일 손실</div>
                <div style={{ fontWeight: 700 }}>
                  {(metrics.current_max_daily_loss_krw || 0).toLocaleString()} 원
                </div>
              </div>
              <div>
                <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>보유 종목</div>
                <div style={{ fontWeight: 700 }}>
                  {metrics.current_max_open_positions || 0} 개
                </div>
              </div>
            </div>
          </div>

          {blocked.length > 0 ? (
            <div data-testid="live-manual-blocked-list" style={{
              border: "1px solid #ef444455", borderRadius: 6,
              marginBottom: 10, padding: 10, background: "#fee2e2",
            }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#b91c1c" }}>
                차단 사유 (BLOCKED)
              </div>
              {blocked.map((b, i) => (
                <div key={i} style={{ fontSize: 12, marginTop: 4 }}>🛑 {b}</div>
              ))}
            </div>
          ) : null}

          {cautions.length > 0 ? (
            <div data-testid="live-manual-caution-list" style={{
              border: "1px solid #f59e0b55", borderRadius: 6,
              marginBottom: 10, padding: 10, background: "#fef3c7",
            }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#92400e" }}>
                CAUTION
              </div>
              {cautions.map((c, i) => (
                <div key={i} style={{ fontSize: 12, marginTop: 4 }}>⚠️ {c}</div>
              ))}
            </div>
          ) : null}

          {actions.length > 0 ? (
            <div data-testid="live-manual-actions" style={{
              border: "1px solid var(--c-border)", borderRadius: 6,
              padding: 10,
            }}>
              <div style={{ fontSize: 11, fontWeight: 700 }}>
                필요 조치
              </div>
              {actions.map((a, i) => (
                <div key={i} style={{ fontSize: 12, marginTop: 4 }}>📝 {a}</div>
              ))}
            </div>
          ) : null}
        </>
      ) : null}

      <div style={{
        marginTop: 10, padding: "8px 10px",
        fontSize: 10, color: "var(--c-text-3)",
        background: "var(--c-surface-2)", borderRadius: 4,
      }}>
        * 본 카드는 readiness *평가*만 합니다. LIVE flag 변경 / KIS 실주문 라우팅 활성화는
        별도 옵트인 PR + 사용자 명시 승인이 필요합니다.
      </div>
    </Card>
  );
}

export default LiveManualGateCard;
