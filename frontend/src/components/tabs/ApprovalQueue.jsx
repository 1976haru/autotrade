/**
 * 체크리스트 #61: Approval Queue 구조화 sub-components.
 *
 * 기존 Approvals.jsx의 PENDING row 안에 직접 inlined 되어 있던 표시 로직
 * (제안 요약 / 리스크 사유 / 만료 배지)을 *재사용 가능한* 컴포넌트로 분리.
 * 본 모듈은 backend API contract를 *변경하지 않는다* — 이미 backend가 보내는
 * 필드(`request_source`, `expires_at`, `seconds_until_expiry`, `reasons`,
 * `ai_decision_meta`, `created_at`, `is_expired`)를 더 명확하게 시각화한다.
 *
 * 절대 원칙:
 *   1. 본 컴포넌트는 broker API 호출을 만들지 *않는다*.
 *   2. 본 컴포넌트는 approve/reject/cancel을 *직접 호출하지 않는다* —
 *      props로 받은 핸들러만 부른다.
 *   3. 본 컴포넌트는 LIVE flag / Secret 어떤 것도 변경하지 않는다.
 *   4. 본 컴포넌트는 *제안이 주문이 아님*을 명시적으로 표시한다.
 */

import { Btn } from "../common";
import {
  PENDING_STALE_THRESHOLD_MS,
  fmtKRW,
  formatPendingAge,
  isPendingStale,
} from "../../utils/format";
// 83: strategy displayName 매핑.
import {
  strategyDisplayShort,
  useStrategyDisplayNames,
} from "../../utils/strategyNames";


// ---------- 1. Freshness badge — TTL + age stale 통합 ----------

/**
 * 만료/노후 상태를 단일 배지로 표시.
 *
 * 우선순위:
 *   1. backend가 `expires_at` + `seconds_until_expiry`를 보내면 그것을 사용
 *      (만료까지 카운트다운).
 *   2. 없으면 `created_at` 기반 age 표시 (생성 후 N분 / 시간 전).
 *   3. PENDING_STALE_THRESHOLD_MS(10분) 넘으면 "신호 노후" 경고.
 *
 * 색상:
 *   - fresh:    파랑/회색 (안전)
 *   - nearing:  amber (1분 안에 만료)
 *   - expired:  빨강 (만료됨 / 신호 노후)
 */
export function ApprovalFreshnessBadge({ approval, now }) {
  const hasTTL = approval?.expires_at != null
              && approval?.seconds_until_expiry != null;

  if (hasTTL) {
    const secs = approval.seconds_until_expiry;
    const isExpired = approval.is_expired || secs <= 0;
    const color = isExpired ? "#ef4444"
                : secs < 60 ? "#f59e0b"
                : "#64748b";
    let text;
    if (isExpired) {
      text = "⏰ 만료됨";
    } else if (secs < 60) {
      text = `⏰ ${secs}초 후 만료`;
    } else if (secs < 3600) {
      text = `⏰ ${Math.floor(secs / 60)}분 후 만료`;
    } else {
      text = `⏰ ${Math.floor(secs / 3600)}시간 후 만료`;
    }
    return (
      <span
        data-testid={`approval-freshness-badge-${approval.id ?? "x"}`}
        data-state={isExpired ? "expired" : (secs < 60 ? "nearing" : "fresh")}
        style={{
          fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
          color, border: `1px solid ${color}55`, background: `${color}15`,
        }}>
        {text}
      </span>
    );
  }

  // No TTL — fall back to age-based.
  const stale = isPendingStale(approval?.created_at, now);
  const color = stale ? "#f59e0b" : "#64748b";
  const ageText = formatPendingAge(approval?.created_at, now);
  return (
    <span
      data-testid={`approval-freshness-badge-${approval?.id ?? "x"}`}
      data-state={stale ? "stale" : "fresh"}
      style={{
        fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
        color, border: `1px solid ${color}55`,
        background: stale ? `${color}15` : "transparent",
      }}>
      {stale ? "⚠ 신호 노후 · " : ""}생성 후 {ageText}
    </span>
  );
}


// ---------- 2. Proposal Summary panel ----------

/**
 * 승인 후보의 *근거*를 한 곳에 모은 panel.
 *
 * 표시:
 *   - "🤖 AI 제안" / "📊 전략 신호" / "✋ 수동" 배지
 *   - 전략 이름
 *   - confidence / strength
 *   - ai_decision_meta.supporting_reasons / opposing_reasons
 *   - expected_reward / expected_risk (있을 때만)
 *   - "주문은 아직 실행되지 않았습니다" 안내
 *
 * 본 패널은 *주문이 아니라 승인 후보*임을 강조 — 운영자가 "AI/전략의
 * 추천이지 자동 발주가 아님"을 인지하도록 시각적 distinction.
 */
export function ApprovalProposalSummary({ approval }) {
  if (!approval) return null;
  const source = approval.request_source || "UNKNOWN";
  const label = approval.request_source_label || "알 수 없음";
  const isAI = source === "AI";
  const meta = approval.ai_decision_meta || {};
  const supporting = Array.isArray(meta.supporting_reasons) ? meta.supporting_reasons : [];
  // 83: strategy displayName.
  const { lookup: strategyLookup } = useStrategyDisplayNames();
  const strategyDisplay = approval.strategy
    ? strategyDisplayShort(approval.strategy, strategyLookup)
    : "";
  const showInternal = strategyDisplay && strategyDisplay !== approval.strategy;
  const opposing   = Array.isArray(meta.opposing_reasons)   ? meta.opposing_reasons   : [];
  const expectedReward = meta.expected_reward;
  const expectedRisk   = meta.expected_risk;
  const rr = meta.risk_reward_ratio;

  const accentColor = isAI ? "#a78bfa"
                    : source === "STRATEGY" ? "#67e8f9"
                    : "#94a3b8";

  return (
    <div data-testid={`approval-proposal-summary-${approval.id ?? "x"}`}
         data-source={source}
         style={{
           marginTop: 6, padding: "6px 8px",
           background: "#0c2035", border: `1px solid ${accentColor}33`,
           borderRadius: 3, fontSize: 10,
         }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        flexWrap: "wrap", marginBottom: 4,
      }}>
        <span
          data-testid="proposal-source-badge"
          style={{
            fontSize: 9, fontWeight: 700, padding: "1px 5px",
            borderRadius: 3, color: accentColor,
            background: `${accentColor}15`, border: `1px solid ${accentColor}55`,
          }}>
          {isAI ? "🤖 " : source === "STRATEGY" ? "📊 " : "✋ "}
          {label}
        </span>
        {approval.strategy && (
          <span data-testid="proposal-strategy"
                data-internal-id={approval.strategy}
                style={{ fontSize: 9, color: "#7dd3fc" }}>
            {/* 83: displayName + (internal id). lookup 부재 시 internal id 그대로. */}
            {strategyDisplay}
            {showInternal ? (
              <span style={{
                marginLeft: 4, fontSize: 8, fontFamily: "monospace",
                color: "#7dd3fc99", fontWeight: 500,
              }}>
                ({approval.strategy})
              </span>
            ) : null}
          </span>
        )}
        {approval.signal_confidence != null && (
          <span data-testid="proposal-confidence"
                style={{ fontSize: 9, color: "#94a3b8" }}>
            conf {approval.signal_confidence}
          </span>
        )}
        {approval.signal_strength != null && (
          <span style={{ fontSize: 9, color: "#94a3b8" }}>
            strength {approval.signal_strength}
          </span>
        )}
      </div>

      {/* Reasons — AI 양면 carry */}
      {supporting.length > 0 && (
        <div data-testid="proposal-supporting-reasons"
             style={{ fontSize: 9, color: "#86efac", marginBottom: 2 }}>
          <span style={{ fontWeight: 700, marginRight: 4 }}>+</span>
          {supporting.join(" · ")}
        </div>
      )}
      {opposing.length > 0 && (
        <div data-testid="proposal-opposing-reasons"
             style={{ fontSize: 9, color: "#fca5a5", marginBottom: 2 }}>
          <span style={{ fontWeight: 700, marginRight: 4 }}>−</span>
          {opposing.join(" · ")}
        </div>
      )}
      {meta.risk_note && (
        <div data-testid="proposal-risk-note"
             style={{ fontSize: 9, color: "#fbbf24", marginBottom: 2 }}>
          ⚠ {meta.risk_note}
        </div>
      )}

      {/* Expected reward / risk */}
      {(expectedReward != null || expectedRisk != null || rr != null) && (
        <div data-testid="proposal-rr"
             style={{ fontSize: 9, color: "#94a3b8", marginTop: 2 }}>
          {expectedReward != null && (
            <span style={{ marginRight: 8 }}>
              예상 수익 <span style={{ color: "#22c55e" }}>
                +{fmtKRW(expectedReward)}원
              </span>
            </span>
          )}
          {expectedRisk != null && (
            <span style={{ marginRight: 8 }}>
              예상 손실 <span style={{ color: "#ef4444" }}>
                -{fmtKRW(expectedRisk)}원
              </span>
            </span>
          )}
          {rr != null && (
            <span>R:R <span style={{ color: "#a78bfa" }}>
              {Number(rr).toFixed(2)}
            </span></span>
          )}
        </div>
      )}

      {/* 주문 아님 invariant 안내 */}
      <div data-testid="proposal-not-order-note"
           style={{
             fontSize: 9, color: "#64748b", marginTop: 4,
             paddingTop: 4, borderTop: "1px dashed #1e3a5c",
           }}>
        ※ 주문은 아직 실행되지 않았습니다 — 승인 시점에 백엔드가 다시 검증합니다.
      </div>
    </div>
  );
}


// ---------- 3. Risk Summary panel ----------

/**
 * RiskManager가 NEEDS_APPROVAL로 분류한 사유를 카테고리별로 묶어 표시.
 *
 * 카테고리:
 *   - freshness: stale price / market closed
 *   - position:  max_positions / max_symbol_exposure / max_total_exposure
 *   - loss:      max_daily_loss / weekly_loss_limit / consecutive_loss_limit
 *   - ai:        AI permission / AI rate limit / AI reasoning
 *   - guard:     duplicate / cooldown / pending same side
 *   - other:     위 카테고리 미매칭
 *
 * 데이터 부족 시 친절한 안내 — "위험 없음"이라고 단언하지 *않는다*.
 */
const _RISK_CATEGORY_RULES = [
  {
    key: "freshness", label: "데이터 신선도", color: "#fbbf24",
    keywords: ["stale", "market", "closed", "outside", "freshness", "frozen"],
  },
  {
    key: "position", label: "포지션 한도", color: "#67e8f9",
    keywords: ["max_positions", "max_symbol_exposure", "max_total_exposure",
               "position_limit", "exposure", "notional"],
  },
  {
    key: "loss", label: "손실 한도", color: "#fb7185",
    keywords: ["daily_loss", "weekly_loss", "consecutive_loss",
               "loss_limit", "pnl"],
  },
  {
    key: "ai", label: "AI 권한", color: "#a78bfa",
    keywords: ["ai_", "ai-", " ai ", "rate_limit", "reasoning",
               "confidence", "permission"],
  },
  {
    key: "guard", label: "주문 가드", color: "#facc15",
    keywords: ["duplicate", "cooldown", "fingerprint", "pending",
               "guard", "client_order_id"],
  },
];


export function categorizeRiskReasons(reasons) {
  const buckets = { freshness: [], position: [], loss: [],
                     ai: [], guard: [], other: [] };
  for (const reason of reasons || []) {
    const text = String(reason || "").toLowerCase();
    let matched = false;
    for (const rule of _RISK_CATEGORY_RULES) {
      if (rule.keywords.some((kw) => text.includes(kw))) {
        buckets[rule.key].push(reason);
        matched = true;
        break;
      }
    }
    if (!matched) buckets.other.push(reason);
  }
  return buckets;
}


export function ApprovalRiskSummary({ approval }) {
  const reasons = Array.isArray(approval?.reasons) ? approval.reasons : [];
  const hasReasons = reasons.length > 0;

  if (!hasReasons) {
    return (
      <div data-testid={`approval-risk-summary-${approval?.id ?? "x"}`}
           data-empty="true"
           style={{
             marginTop: 6, padding: "6px 8px",
             background: "#0c2035", border: "1px solid #1e3a5c",
             borderRadius: 3, fontSize: 10, color: "#94a3b8",
             lineHeight: 1.5,
           }}>
        <div style={{ fontWeight: 700, color: "#7dd3fc", marginBottom: 2 }}>
          ⚖ 리스크 요약
        </div>
        <div>표시 가능한 리스크 사유 없음.</div>
        <div style={{ fontSize: 9, color: "#64748b", marginTop: 2 }}>
          ※ 승인 시점에 백엔드가 다시 가격/잔고/리스크를 검증합니다.
        </div>
      </div>
    );
  }

  const buckets = categorizeRiskReasons(reasons);
  return (
    <div data-testid={`approval-risk-summary-${approval?.id ?? "x"}`}
         data-empty="false"
         style={{
           marginTop: 6, padding: "6px 8px",
           background: "#0c2035", border: "1px solid #1e3a5c",
           borderRadius: 3, fontSize: 10,
         }}>
      <div style={{ fontWeight: 700, color: "#7dd3fc", marginBottom: 4 }}>
        ⚖ 리스크 요약 ({reasons.length}건)
      </div>
      {_RISK_CATEGORY_RULES.map((rule) => {
        const items = buckets[rule.key];
        if (items.length === 0) return null;
        return (
          <div key={rule.key}
               data-testid={`risk-category-${rule.key}`}
               style={{ marginBottom: 2 }}>
            <span style={{
              fontSize: 9, fontWeight: 700,
              padding: "0 4px", borderRadius: 2,
              color: rule.color,
              background: `${rule.color}15`,
              marginRight: 4,
            }}>
              {rule.label}
            </span>
            <span style={{ fontSize: 9, color: "#cbd5e1" }}>
              {items.join(" / ")}
            </span>
          </div>
        );
      })}
      {buckets.other.length > 0 && (
        <div data-testid="risk-category-other"
             style={{ marginBottom: 2, fontSize: 9, color: "#cbd5e1" }}>
          <span style={{
            fontSize: 9, fontWeight: 700,
            padding: "0 4px", borderRadius: 2,
            color: "#94a3b8", background: "#94a3b815",
            marginRight: 4,
          }}>
            기타
          </span>
          {buckets.other.join(" / ")}
        </div>
      )}
    </div>
  );
}


// ---------- 4. ApproveConfirmSummary — 2차 확인 모달용 ----------

/**
 * 승인 모달 내부에 표시할 핵심 요약. _OrderSummary보다 더 강하게:
 *   - 종목 / side / quantity / strategy / mode
 *   - stale 여부 + 경고 문구
 *   - risk reasons 요약 (분류된 형태)
 *   - "정말 승인하시겠습니까?" 제목 + "백엔드 재검증 안내"
 *
 * `action === "approve"`일 때만 stale 경고 노출 — reject/cancel은 폐기
 * 동작이라 경고 의미가 약함.
 */
export function ApproveConfirmSummary({ approval, action, now }) {
  if (!approval) return null;
  const stale = isPendingStale(approval.created_at, now);
  const showStaleWarning = action === "approve" && stale;
  const reasons = Array.isArray(approval.reasons) ? approval.reasons : [];
  const top3Reasons = reasons.slice(0, 3);
  // 83: strategy displayName.
  const { lookup: strategyLookup } = useStrategyDisplayNames();
  const strategyDisplay = approval.strategy
    ? strategyDisplayShort(approval.strategy, strategyLookup)
    : "";
  const showInternalStrategy = strategyDisplay && strategyDisplay !== approval.strategy;
  return (
    <div
      data-testid="approve-confirm-summary"
      style={{
        fontSize: 11, color: "#94a3b8",
        padding: "8px 10px", marginBottom: 10,
        background: "#010a14", border: "1px solid #0c2035", borderRadius: 4,
      }}
    >
      <div style={{ marginBottom: 4 }}>
        <span style={{ color: "#7dd3fc", fontWeight: 700 }}>
          {approval.symbol}
        </span>
        <span style={{
          color: approval.side === "BUY" ? "#22c55e" : "#ef4444",
          fontSize: 10, marginLeft: 8, fontWeight: 700,
        }}>
          {approval.side}
        </span>
        <span style={{ marginLeft: 8 }}>
          {approval.quantity}주 · {approval.order_type}
          {approval.limit_price ? ` · ${fmtKRW(approval.limit_price)}원` : ""}
        </span>
      </div>
      <div style={{ fontSize: 9, color: "#475569" }}>
        #{approval.id} · {approval.mode}
        {approval.strategy && (
          <span data-internal-id={approval.strategy}
                style={{ marginLeft: 4, color: "#7dd3fc" }}>
            {/* 83: displayName + (internal id). */}
            · {strategyDisplay}
            {showInternalStrategy ? (
              <span style={{
                marginLeft: 4, fontSize: 8, fontFamily: "monospace",
                color: "#7dd3fc99",
              }}>
                ({approval.strategy})
              </span>
            ) : null}
          </span>
        )}
        {approval.signal_confidence != null && (
          <span style={{ marginLeft: 4 }}>
            · conf {approval.signal_confidence}
          </span>
        )}
      </div>

      {showStaleWarning && (
        <div
          data-testid="approve-confirm-stale-warning"
          style={{
            marginTop: 6, padding: "6px 8px",
            background: "#7c2d1222",
            border: "1px solid #f5970366",
            borderRadius: 3, fontSize: 10, color: "#fbbf24",
            lineHeight: 1.4,
          }}
        >
          <div style={{ fontWeight: 700 }}>
            ⏰ 이 신호는 오래되었습니다.
          </div>
          <div style={{ fontSize: 9, color: "#fde68a", marginTop: 2 }}>
            승인 전 현재 시장 상황을 다시 확인하세요. 백엔드가 가격/잔고/리스크를
            재검증하므로 거부될 수 있습니다.
          </div>
        </div>
      )}

      {top3Reasons.length > 0 && (
        <div
          data-testid="approve-confirm-reasons"
          style={{
            marginTop: 6, padding: 4,
            background: "#0c2035",
            border: "1px solid #1e3a5c",
            borderRadius: 3, fontSize: 9, color: "#cbd5e1",
            lineHeight: 1.5,
          }}
        >
          <span style={{ color: "#64748b", marginRight: 4 }}>
            리스크 사유:
          </span>
          {top3Reasons.join(" / ")}
          {reasons.length > 3 && (
            <span style={{ color: "#475569" }}>
              {" "}외 {reasons.length - 3}건
            </span>
          )}
        </div>
      )}
    </div>
  );
}


// ---------- 5. Mobile-friendly action button bar ----------

/**
 * 모바일에서 가로로 좁아 보이지 않도록 동등 너비 + 큰 터치영역.
 * action 핸들러는 부모가 주입 — 본 모듈은 직접 API 호출하지 않는다.
 */
export function ApprovalActionBar({ onApprove, onReject, onCancel, busy }) {
  return (
    <div
      data-testid="approval-action-bar"
      style={{
        display: "flex", gap: 6, marginTop: 8,
      }}
    >
      <div style={{ flex: 1 }}>
        <Btn color="#22c55e" onClick={onApprove} disabled={busy} small full>
          ✓ 승인
        </Btn>
      </div>
      <div style={{ flex: 1 }}>
        <Btn color="#ef4444" onClick={onReject} disabled={busy} small full>
          ✗ 거부
        </Btn>
      </div>
      <div style={{ flex: 1 }}>
        <Btn color="#94a3b8" onClick={onCancel} disabled={busy} small full>
          ⊘ 취소
        </Btn>
      </div>
    </div>
  );
}


// ---------- 6. Empty / Connection-loss state ----------

export function ApprovalQueueEmptyState({ kind = "empty" }) {
  // kind: "empty" | "demo" | "loading"
  if (kind === "loading") {
    return (
      <div data-testid="approval-queue-empty-loading"
           style={{ color: "#475569", fontSize: 12,
                    textAlign: "center", padding: 16 }}>
        승인 큐를 불러오는 중…
      </div>
    );
  }
  if (kind === "demo") {
    return (
      <div data-testid="approval-queue-empty-demo"
           style={{ color: "#94a3b8", fontSize: 11,
                    textAlign: "center", padding: 16,
                    lineHeight: 1.5 }}>
        GitHub Pages 데모에서는 예시 승인 큐가 표시되지 않을 수 있습니다.
        <br />
        로컬 환경에서는 backend가 LIVE_MANUAL_APPROVAL 흐름에서 NEEDS_APPROVAL
        주문을 큐로 보냅니다.
      </div>
    );
  }
  return (
    <div data-testid="approval-queue-empty"
         style={{ color: "#1e3a5c", fontSize: 12,
                  textAlign: "center", padding: 16,
                  lineHeight: 1.5 }}>
      승인 대기 항목이 없습니다.
      <br />
      <span style={{ fontSize: 10, color: "#475569" }}>
        AI 제안 / 전략 신호가 RiskManager NEEDS_APPROVAL로 분류되면 여기에 나타납니다.
      </span>
    </div>
  );
}


export const _APPROVAL_QUEUE_INVARIANTS = {
  PENDING_STALE_THRESHOLD_MS,
  RISK_CATEGORIES: _RISK_CATEGORY_RULES.map((r) => r.key),
};
