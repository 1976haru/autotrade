/**
 * #50 / 6-05 — 복기 피드백 카드 (read-only, advisory).
 *
 * 거래 후 복기(P-27)/outcome(P-25) 집계를 피드백 태그 + threshold 추천으로 표시한다.
 * threshold 추천은 운영자 승인 없이 자동 적용되지 않는다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 주문/실전/승인/Place Order 버튼 0개, 입력 form 0개.
 *  - "자동 적용 안 됨" / "운영자 승인 필요" / "주문 버튼 아님" 문구 노출.
 *  - secret/account 원문 표시 0건.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _SEV_COLOR = { HIGH: "#b91c1c", WARN: "#a16207", INFO: "#6b7280" };

export function PostTradeFeedbackCard({
  apiClient = backendApi,
  testId = "post-trade-feedback-card",
  limit = 200,
} = {}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (typeof apiClient.agentFeedbackLoop !== "function") return;
    try {
      setReport((await apiClient.agentFeedbackLoop({ limit })) || null);
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
    }
  }, [apiClient, limit]);

  useEffect(() => { refresh(); }, [refresh]);

  const tags = Array.isArray(report?.feedback_tags) ? report.feedback_tags : [];
  const recs = Array.isArray(report?.threshold_recommendations)
    ? report.threshold_recommendations : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#f59e0b33">
        <SectionLabel>🔁 복기 피드백 루프</SectionLabel>

        <div data-testid="feedback-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          거래 후 복기 결과를 다음 판단에 참고하는 분석 정보입니다. threshold 추천은
          자동 적용되지 않으며 운영자 승인이 필요합니다. 주문 버튼이 아니며 수익을
          보장하지 않습니다.
        </div>

        <div data-testid="feedback-badges" style={{ marginBottom: 8 }}>
          <span style={{ padding: "3px 8px", borderRadius: 4, fontSize: "var(--fs-xs)",
                         fontWeight: "var(--fw-bold)", background: "#6b7280", color: "#fff",
                         marginRight: 6 }}>자동 적용 안 됨</span>
          <span style={{ padding: "3px 8px", borderRadius: 4, fontSize: "var(--fs-xs)",
                         background: "#1e3a8a", color: "#fff" }}>운영자 승인 필요</span>
        </div>

        {error ? (
          <div data-testid="feedback-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>{error}</div>
        ) : null}

        {report?.status === "INSUFFICIENT_DATA" ? (
          <div data-testid="feedback-insufficient" style={{
            fontSize: "var(--fs-xs)", color: "#b45309", marginBottom: 8,
          }}>복기 표본이 부족합니다 — 사후 성과 라벨링/복기 누적 후 확인하세요.</div>
        ) : null}

        {report ? (
          <div data-testid="feedback-counts" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 8,
          }}>
            표본 {report.sample_count}건 · 승 {report.win_count} / 패 {report.loss_count} /
            중립 {report.neutral_count}
          </div>
        ) : null}

        {/* 피드백 태그 */}
        <div data-testid="feedback-tags" style={{ marginBottom: 10 }}>
          <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
            피드백 태그
          </div>
          {tags.length === 0 ? (
            <div data-testid="feedback-tags-empty" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            }}>피드백 태그 없음</div>
          ) : (
            tags.map((t) => (
              <div key={t.tag} data-testid={`feedback-tag-${t.tag}`}
                   style={{ fontSize: "var(--fs-xs)", color: _SEV_COLOR[t.severity] || "var(--c-text-2)" }}>
                • [{t.severity}] {t.tag} ({t.count}) — {t.message}
              </div>
            ))
          )}
        </div>

        {/* threshold 추천 */}
        <div data-testid="feedback-recommendations">
          <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
            threshold 추천 (자동 적용 안 됨)
          </div>
          {recs.length === 0 ? (
            <div data-testid="feedback-rec-empty" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            }}>추천 없음</div>
          ) : (
            recs.map((r) => (
              <div key={r.reason_code} data-testid={`feedback-rec-${r.reason_code}`}
                   style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                • {r.message} (근거 {r.evidence_count}건) — 운영자 승인 필요
              </div>
            ))
          )}
        </div>

        <div data-testid="feedback-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          복기 결과는 자동으로 실전 전환을 허용하거나 주문을 생성하지 않습니다.
          threshold 추천은 운영자 승인 후에만 검토됩니다.
        </div>
      </Card>
    </div>
  );
}

export default PostTradeFeedbackCard;
