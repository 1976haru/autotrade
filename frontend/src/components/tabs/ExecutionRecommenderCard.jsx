import { useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";
// 83: strategy displayName 매핑.
import {
  strategyDisplayShort,
  useStrategyDisplayNames,
} from "../../utils/strategyNames";

// 56: Execution Recommender card — 매수/매도 *제안*만 surface.
//
// **주문 아님 · 승인 필요**. 본 카드의 어떤 버튼도 직접 주문을 발생시키지
// 않는다:
// - "위험 사전검사" → backend /precheck (audit row 0건, broker 호출은 시세
//   조회만 read-only)
// - "승인 대기 후보로 보내기" → backend /submit, 그 endpoint는 기존 sanctioned
//   AI Assist 흐름 (route_order → RiskManager → PermissionGate.submit)에 위임
//
// 본 카드는 어떤 경우에도 BrokerAdapter / OrderExecutor를 직접 호출하지 않는다.

const _OUTCOME_PALETTE = {
  APPROVED:       { color: "#22c55e", label: "통과" },
  NEEDS_APPROVAL: { color: "#7dd3fc", label: "승인 필요" },
  REDUCED:        { color: "#fbbf24", label: "한도 축소 권고" },
  REJECTED:       { color: "#ef4444", label: "거부" },
  BLOCKED:        { color: "#ef4444", label: "차단" },
};

const _DECISION_PALETTE = {
  APPROVED:       { color: "#22c55e", label: "승인" },
  NEEDS_APPROVAL: { color: "#7dd3fc", label: "승인 대기 큐 등록됨" },
  REJECTED:       { color: "#ef4444", label: "거부됨" },
  BLOCKED:        { color: "#ef4444", label: "차단" },
};


function _Field({ label, value }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between",
                   padding: "4px 0", borderBottom: "1px solid #1e3a5c33",
                   fontSize: 11 }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span style={{ color: "#e2e8f0", fontWeight: 700 }}>{value}</span>
    </div>
  );
}


function _ProposalRow({ proposal, onPrecheck, onSubmit, busy }) {
  // 83: strategy displayName.
  const { lookup: strategyLookup } = useStrategyDisplayNames();
  const strategyDisplay = proposal.strategy
    ? strategyDisplayShort(proposal.strategy, strategyLookup)
    : "";
  const showInternalStrategy = strategyDisplay && strategyDisplay !== proposal.strategy;
  const sideColor = proposal.side === "BUY" ? "#22c55e" : "#ef4444";
  const [precheck, setPrecheck] = useState(null);
  const [submitResult, setSubmitResult] = useState(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState("");

  const handlePrecheck = async () => {
    setRunning("precheck"); setError(""); setPrecheck(null);
    try {
      const r = await onPrecheck(proposal);
      setPrecheck(r);
    } catch (e) {
      setError(`사전검사 실패: ${e.message}`);
    }
    setRunning("");
  };

  const handleSubmit = async () => {
    setRunning("submit"); setError(""); setSubmitResult(null);
    try {
      const r = await onSubmit(proposal);
      setSubmitResult(r);
    } catch (e) {
      setError(`승인 큐 등록 실패: ${e.message}`);
    }
    setRunning("");
  };

  const palette = precheck && _OUTCOME_PALETTE[precheck.outcome];
  const decisionPalette = submitResult
    && _DECISION_PALETTE[submitResult.decision];

  return (
    <div data-testid={`exec-rec-proposal-${proposal.proposal_id}`}
         style={{ padding: "8px 10px", marginBottom: 8,
                   background: "#0c2035", borderRadius: 4,
                   border: `1px solid ${sideColor}55` }}>
      {/* 헤더 — 종목 / 방향 / confidence */}
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "baseline", marginBottom: 4 }}>
        <div>
          <span data-testid="exec-rec-symbol"
                style={{ fontFamily: "monospace", fontWeight: 700,
                          color: "#e2e8f0", fontSize: 12 }}>
            {proposal.symbol}
          </span>
          <span data-testid="exec-rec-side" style={{
            marginLeft: 8, fontSize: 10, fontWeight: 700,
            padding: "1px 5px", borderRadius: 3,
            background: `${sideColor}22`, color: sideColor,
          }}>
            {proposal.side}
          </span>
          <span style={{ marginLeft: 6, fontSize: 9, color: "#94a3b8" }}>
            qty {proposal.quantity}
          </span>
        </div>
        <span data-testid="exec-rec-confidence"
              style={{ fontSize: 10, fontWeight: 700, color: "#7dd3fc" }}>
          conf {proposal.confidence}
        </span>
      </div>

      {/* 핵심 metric — 83: displayName + (internal id) 함께 표시. */}
      <_Field label="전략" value={
        <span data-testid="exec-rec-strategy"
              data-internal-id={proposal.strategy || ""}
              style={{ fontSize: 10 }}>
          {proposal.strategy ? (
            <>
              {strategyDisplay}
              {showInternalStrategy ? (
                <span style={{
                  marginLeft: 4, fontSize: 9, fontFamily: "monospace",
                  color: "#475569",
                }}>
                  ({proposal.strategy})
                </span>
              ) : null}
            </>
          ) : "—"}
        </span>
      } />
      {proposal.expected_reward != null && (
        <_Field label="기대 reward"
                value={`${proposal.expected_reward.toLocaleString()}원`} />
      )}
      {proposal.expected_risk != null && (
        <_Field label="기대 risk"
                value={`${proposal.expected_risk.toLocaleString()}원`} />
      )}
      {proposal.risk_reward_ratio != null && (
        <_Field label="R:R ratio"
                value={
                  <span data-testid="exec-rec-rr"
                        style={{ color:
                          proposal.risk_reward_ratio >= 2 ? "#22c55e" : "#fbbf24"
                        }}>
                    {proposal.risk_reward_ratio.toFixed(2)}
                  </span>
                } />
      )}
      <_Field label="만료" value={
        <span data-testid="exec-rec-expiry"
              style={{ fontSize: 10 }}>
          {new Date(proposal.expires_at).toLocaleString()}
        </span>
      } />

      {/* 근거 */}
      {proposal.supporting_reasons && proposal.supporting_reasons.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>주요 근거</div>
          <ul data-testid="exec-rec-supporting"
              style={{ margin: 2, paddingLeft: 18, color: "#22c55e",
                        fontSize: 10 }}>
            {proposal.supporting_reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
      {proposal.opposing_reasons && proposal.opposing_reasons.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>반대 근거</div>
          <ul data-testid="exec-rec-opposing"
              style={{ margin: 2, paddingLeft: 18, color: "#fb923c",
                        fontSize: 10 }}>
            {proposal.opposing_reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
      {proposal.risk_note && (
        <div data-testid="exec-rec-risk-note"
             style={{ marginTop: 4, fontSize: 10, color: "#fbbf24",
                       fontStyle: "italic" }}>
          ⚠ {proposal.risk_note}
        </div>
      )}

      {/* 사전검사 결과 */}
      {precheck && (
        <div data-testid="exec-rec-precheck-result"
             style={{ marginTop: 6, padding: "6px 8px",
                       background: "#000a14", borderRadius: 3,
                       border: `1px solid ${palette.color}55`, fontSize: 11 }}>
          <div style={{ fontWeight: 700, color: palette.color }}>
            사전검사: {palette.label} ({precheck.outcome})
          </div>
          {precheck.reasons && precheck.reasons.length > 0 && (
            <ul style={{ margin: 2, paddingLeft: 18, color: "#94a3b8",
                          fontSize: 10 }}>
              {precheck.reasons.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          )}
          <div style={{ fontSize: 9, color: "#64748b", marginTop: 3 }}>
            ※ 사전검사는 audit row를 작성하지 않습니다 (advisory dry-run).
          </div>
        </div>
      )}

      {/* 큐 등록 결과 */}
      {submitResult && (
        <div data-testid="exec-rec-submit-result"
             style={{ marginTop: 6, padding: "6px 8px",
                       background: "#000a14", borderRadius: 3,
                       border: `1px solid ${decisionPalette.color}55`,
                       fontSize: 11 }}>
          <div style={{ fontWeight: 700, color: decisionPalette.color }}>
            결과: {decisionPalette.label} ({submitResult.decision})
          </div>
          {submitResult.approval_id && (
            <div style={{ fontSize: 10, color: "#7dd3fc" }}>
              Approval ID: #{submitResult.approval_id} — 결재 탭에서 처리하세요.
            </div>
          )}
          {submitResult.audit_id && !submitResult.approval_id && (
            <div style={{ fontSize: 10, color: "#94a3b8" }}>
              Audit ID: #{submitResult.audit_id} (큐 등록 X — audit만 작성됨)
            </div>
          )}
        </div>
      )}

      {error && (
        <div data-testid="exec-rec-error"
             style={{ marginTop: 4, fontSize: 10, color: "#ef4444" }}>
          {error}
        </div>
      )}

      {/* 액션 버튼 — 직접 주문 X. */}
      <div style={{ marginTop: 8, display: "flex", gap: 6,
                     justifyContent: "flex-end" }}>
        <button data-testid="exec-rec-precheck-btn"
                onClick={handlePrecheck}
                disabled={busy || running !== ""}
                style={{
          fontSize: 10, padding: "4px 10px", background: "#0c2035",
          border: "1px solid #1e3a5c", borderRadius: 3,
          cursor: (busy || running) ? "not-allowed" : "pointer",
          color: "#7dd3fc",
        }}>
          {running === "precheck" ? "검사 중…" : "위험 사전검사"}
        </button>
        <button data-testid="exec-rec-submit-btn"
                onClick={handleSubmit}
                disabled={busy || running !== ""}
                style={{
          fontSize: 10, padding: "4px 10px", background: "#0c2035",
          border: "1px solid #1e3a5c", borderRadius: 3,
          cursor: (busy || running) ? "not-allowed" : "pointer",
          color: "#7dd3fc",
        }}>
          {running === "submit" ? "등록 중…" : "승인 대기 후보로 보내기"}
        </button>
      </div>
    </div>
  );
}


export function ExecutionRecommenderCard({
  result, loading, error, onPrecheck, onSubmit, onRefresh,
}) {
  if (loading && !result) {
    return (
      <Card>
        <SectionLabel>🎯 Execution Recommender</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>🎯 Execution Recommender</SectionLabel>
        <div data-testid="exec-rec-load-error"
             style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.6 }}>
          제안 데이터를 아직 불러오지 못했습니다.
          {onRefresh && (
            <div style={{ marginTop: 8 }}>
              <button onClick={onRefresh} style={{
                fontSize: 10, padding: "3px 8px", background: "#0c2035",
                border: "1px solid #1e3a5c", borderRadius: 3,
                cursor: "pointer", color: "#7dd3fc",
              }}>↻ 다시 시도</button>
            </div>
          )}
        </div>
      </Card>
    );
  }
  if (!result) return null;

  const proposals = result.proposals || [];
  const skipped   = result.skipped   || [];

  return (
    <Card data-testid="execution-recommender-card">
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>🎯 Execution Recommender</SectionLabel>
        <span data-testid="exec-rec-not-order-badge" style={{
          fontSize: 9, fontWeight: 700, color: "#fbbf24",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #fbbf2455", background: "#fbbf2415",
        }}>
          주문 아님 · 승인 필요
        </span>
      </div>

      {/* notice */}
      <div data-testid="exec-rec-notice"
           style={{ marginBottom: 8, padding: "6px 8px",
                     background: "#0c2035", borderRadius: 4,
                     fontSize: 10, color: "#94a3b8", lineHeight: 1.5 }}>
        본 카드의 제안은 *주문이 아닙니다*. "위험 사전검사"는 audit row를 만들지
        않으며, "승인 대기 후보로 보내기"는 기존 결재 큐로만 전달됩니다 — 실제
        주문은 운영자 승인 시점에 RiskManager 재검증을 통과해야 발생합니다.
      </div>

      {/* 제안 목록 */}
      {proposals.length === 0 ? (
        <div data-testid="exec-rec-empty"
             style={{ fontSize: 11, color: "#94a3b8" }}>
          현재 추천 후보가 없습니다 (임계 미달 {skipped.length}건).
        </div>
      ) : (
        <div data-testid="exec-rec-proposals">
          {proposals.map((p) => (
            <_ProposalRow key={p.proposal_id} proposal={p}
                          onPrecheck={onPrecheck} onSubmit={onSubmit} />
          ))}
        </div>
      )}

      {/* skipped */}
      {skipped.length > 0 && (
        <details style={{ marginTop: 6, fontSize: 10 }}>
          <summary style={{ color: "#475569", cursor: "pointer" }}>
            임계 미달 ({skipped.length}건)
          </summary>
          <ul data-testid="exec-rec-skipped"
              style={{ margin: 4, paddingLeft: 16, color: "#94a3b8" }}>
            {skipped.map((s, i) => (
              <li key={i}>
                <span style={{ fontFamily: "monospace" }}>{s.symbol}</span>: {s.reason}
              </li>
            ))}
          </ul>
        </details>
      )}

      {onRefresh && (
        <div style={{ marginTop: 6, textAlign: "right" }}>
          <button onClick={onRefresh} style={{
            fontSize: 10, padding: "3px 8px", background: "#0c2035",
            border: "1px solid #1e3a5c", borderRadius: 3,
            cursor: "pointer", color: "#7dd3fc",
          }}>↻ 새로고침</button>
        </div>
      )}
    </Card>
  );
}


// 56: hook helpers — UI에서 사용할 default callbacks를 묶어서 노출.
export function defaultPrecheckHandler(proposal) {
  return backendApi.executionRecommenderPrecheck(proposal);
}


export function defaultSubmitHandler(proposal) {
  return backendApi.executionRecommenderSubmit(proposal);
}
