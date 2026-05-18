import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

// #4-05: Paper 실행 전 최종 설명 카드.
//
// **주문이 아닙니다. 실거래는 절대 켜지지 않습니다.** 본 카드는 운영자가
// [시작] 버튼 누르기 전 AI Agent 가 *왜* 이 전략을 추천했는지 / *왜* 어떤
// 전략은 제외/보류했는지 설명하는 advisory UI 입니다.
//
// 버튼 정책 (테스트 lock):
// - "매수" / "매도" / "Place Order" / "실거래 시작" / "ENABLE_LIVE_TRADING"
//   라벨 button 0개.
// - 추천/제외/보류는 *Paper 판단 결과 라벨* 로만 표시 (`<span>` 배지).
// - 본 카드 에서는 실제 시작 버튼을 *제공하지 않음* — 운영자가 별도
//   BotControl / Paper Auto Loop 에서 명시 시작.

const _VERDICT_COLOR = {
  READY_TO_REVIEW:     "#22c55e",
  REVIEW_WITH_WARNING: "#fbbf24",
  HOLD:                "#94a3b8",
  DO_NOT_START:        "#ef4444",
  INSUFFICIENT_DATA:   "#6b7280",
};

const _BUCKET_BADGE = {
  recommended: { label: "추천", color: "#22c55e" },
  watchlist:   { label: "보류", color: "#fbbf24" },
  excluded:    { label: "제외", color: "#ef4444" },
};

export function PaperStartExplanationCard({
  apiClient = backendApi,
  marketState = null,
  preMarket   = null,
  pollIntervalMs = 0,
} = {}) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const body = {
        ...(marketState ? { market_state: marketState } : {}),
        ...(preMarket   ? { pre_market:   preMarket }   : {}),
      };
      const r = await apiClient.paperStartExplanation(body);
      setData(r);
      setError(null);
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setLoading(false);
    }
  }, [apiClient, marketState, preMarket]);

  useEffect(() => {
    refresh();
    if (!pollIntervalMs || pollIntervalMs <= 0) return undefined;
    const t = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(t);
  }, [refresh, pollIntervalMs]);

  if (loading && !data) {
    return (
      <Card data-testid="paper-start-explanation-card">
        <SectionLabel>Paper 실행 전 설명</SectionLabel>
        <div style={{ fontSize: "var(--fs-sm)", color: "var(--c-text-3)" }}>
          분석 중...
        </div>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card data-testid="paper-start-explanation-card">
        <SectionLabel>Paper 실행 전 설명</SectionLabel>
        <div data-testid="paper-start-explanation-error"
              style={{ fontSize: "var(--fs-sm)", color: "#7f1d1d" }}>
          분석 실패: {error || "응답 없음"}
        </div>
      </Card>
    );
  }

  const verdictColor = _VERDICT_COLOR[data.verdict] || "#6b7280";

  return (
    <Card data-testid="paper-start-explanation-card">
      <SectionLabel>Paper 실행 전 설명 — AI Agent 추천 이유</SectionLabel>

      {/* 안전 배지 — 항상 표시 */}
      <div data-testid="paper-explanation-badges"
            style={{ marginBottom: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
        <span
          data-testid="badge-paper-only"
          style={{
            display: "inline-block",
            padding: "3px 10px",
            borderRadius: 6,
            fontSize: "var(--fs-xs)",
            fontWeight: "var(--fw-bold)",
            background: "#1e3a8a",
            color: "#fff",
          }}
        >
          Paper 전용 · 모의매매 advisory
        </span>
        <span
          data-testid="badge-not-real-order"
          style={{
            display: "inline-block",
            padding: "3px 10px",
            borderRadius: 6,
            fontSize: "var(--fs-xs)",
            background: "#6b7280",
            color: "#fff",
          }}
        >
          실거래 주문 아님
        </span>
        <span
          data-testid="badge-not-auto-start"
          style={{
            display: "inline-block",
            padding: "3px 10px",
            borderRadius: 6,
            fontSize: "var(--fs-xs)",
            background: "#0ea5e9",
            color: "#fff",
          }}
        >
          자동 시작 아님
        </span>
      </div>

      {/* Verdict 헤드라인 */}
      <div data-testid="paper-explanation-verdict"
            data-verdict={data.verdict}
            style={{ marginBottom: 8 }}>
        <span style={{
          display: "inline-block",
          padding: "4px 12px",
          borderRadius: 999,
          fontSize: "var(--fs-sm)",
          fontWeight: "var(--fw-bold)",
          background: verdictColor,
          color: "#fff",
        }}>
          {data.verdict}
        </span>
        <div style={{ marginTop: 6, fontSize: "var(--fs-sm)" }}>
          {data.verdict_label_ko || data.headline || ""}
        </div>
      </div>

      <div data-testid="paper-explanation-headline"
            style={{ marginBottom: 10, fontSize: "var(--fs-sm)", color: "var(--c-text)" }}>
        {data.headline}
      </div>

      {/* 장세 정보 */}
      <div data-testid="paper-explanation-regime"
            style={{ marginBottom: 10, padding: "6px 10px",
                     background: "#f8fafc",
                     border: "1px solid #e2e8f0",
                     borderRadius: "var(--r-md)" }}>
        <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)" }}>
          현재 장세: <code>{data.market_regime}</code>{" "}
          <span style={{ color: "var(--c-text-3)" }}>
            (신뢰도 {Math.round((data.regime_confidence || 0) * 100)}%)
          </span>
        </div>
        {data.regime_reasons && data.regime_reasons.length > 0 && (
          <div data-testid="paper-explanation-regime-reasons"
                style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 4 }}>
            {data.regime_reasons.slice(0, 3).join(" · ")}
          </div>
        )}
      </div>

      {/* DO_NOT_START / blocking_reasons */}
      {data.can_start_paper === false && data.blocking_reasons && data.blocking_reasons.length > 0 && (
        <div data-testid="paper-explanation-blocking"
              style={{ marginBottom: 10, padding: 10,
                       background: "#fef2f2",
                       border: "1px solid #fecaca",
                       borderRadius: "var(--r-md)",
                       color: "#7f1d1d", fontSize: "var(--fs-xs)" }}>
          <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
            ⚠ 시작 차단 사유
          </div>
          {data.blocking_reasons.map((r, i) => (
            <div key={i} data-testid={`paper-explanation-blocking-${i}`}>· {r}</div>
          ))}
        </div>
      )}

      {/* 추천 전략 목록 */}
      <_StrategyList
        title="추천 전략 (Paper 검토 가능)"
        items={data.recommended_explanations}
        bucket="recommended"
        emptyText="오늘 추천 전략이 없습니다."
      />
      {/* 보류 전략 */}
      <_StrategyList
        title="보류 전략"
        items={data.watchlist_explanations}
        bucket="watchlist"
        emptyText="보류 전략 없음"
      />
      {/* 제외 전략 (과최적화 / 위험 한도 등) */}
      <_StrategyList
        title="제외 전략 (과최적화 / 위험 한도 / 검증 미통과)"
        items={data.excluded_explanations}
        bucket="excluded"
        emptyText="제외 전략 없음"
      />

      {/* 위험 요약 */}
      {data.risk_summary && data.risk_summary.length > 0 && (
        <div data-testid="paper-explanation-risk-summary"
              style={{ marginTop: 10, padding: 8,
                       background: "#fffbeb",
                       border: "1px solid #fde68a",
                       borderRadius: "var(--r-md)",
                       fontSize: "var(--fs-xs)", color: "#92400e" }}>
          <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
            ⚠ 위험 신호 요약 ({data.risk_summary.length}건)
          </div>
          {data.risk_summary.slice(0, 8).map((r, i) => (
            <div key={i}>· {r}</div>
          ))}
        </div>
      )}

      {/* 과최적화 별도 표시 */}
      {data.overfit_count > 0 && (
        <div data-testid="paper-explanation-overfit"
              style={{ marginTop: 8, padding: 8,
                       background: "#fef2f2",
                       border: "1px solid #fecaca",
                       borderRadius: "var(--r-md)",
                       fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
          <div style={{ fontWeight: "var(--fw-bold)" }}>
            과최적화 의심 전략 {data.overfit_count}건 — Paper 운용 전 재검증 필요
          </div>
          {data.overfit_strategies && data.overfit_strategies.length > 0 && (
            <div style={{ marginTop: 4 }}>
              {data.overfit_strategies.slice(0, 5).join(", ")}
            </div>
          )}
        </div>
      )}

      {/* 다음 행동 */}
      {data.next_actions && data.next_actions.length > 0 && (
        <div data-testid="paper-explanation-next-actions"
              style={{ marginTop: 10, fontSize: "var(--fs-xs)" }}>
          <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
            다음 행동
          </div>
          {data.next_actions.map((a, i) => (
            <div key={i} style={{ marginLeft: 8 }}>· {a}</div>
          ))}
        </div>
      )}

      {/* Advisory disclaimer */}
      <div data-testid="paper-explanation-disclaimer"
            style={{ marginTop: 10, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
        {data.advisory_disclaimer}
      </div>
    </Card>
  );
}

function _StrategyList({ title, items, bucket, emptyText }) {
  const badge = _BUCKET_BADGE[bucket] || { label: bucket, color: "#94a3b8" };
  return (
    <div data-testid={`paper-explanation-bucket-${bucket}`}
          style={{ marginTop: 10 }}>
      <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
                     marginBottom: 4 }}>
        {title}
      </div>
      {!items || items.length === 0 ? (
        <div data-testid={`paper-explanation-bucket-${bucket}-empty`}
              style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          {emptyText}
        </div>
      ) : (
        <div>
          {items.map((e, i) => (
            <div key={`${e.strategy}-${e.symbol}-${i}`}
                  data-testid={`paper-explanation-${bucket}-${e.strategy}-${e.symbol}`}
                  style={{ padding: "4px 0", borderBottom: "1px dashed #e2e8f0",
                           fontSize: "var(--fs-xs)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{
                  display: "inline-block",
                  padding: "1px 6px",
                  borderRadius: 4,
                  fontWeight: "var(--fw-bold)",
                  background: badge.color,
                  color: "#fff",
                }}>
                  {badge.label}
                </span>
                <b>{e.strategy}</b>
                <span style={{ color: "var(--c-text-2)" }}>{e.symbol}</span>
                {e.overfit_verdict && e.overfit_verdict !== "HEALTHY" && (
                  <span style={{ color: "#7f1d1d", fontWeight: "var(--fw-bold)" }}>
                    ⚠ {e.overfit_verdict}
                  </span>
                )}
              </div>
              {e.rationale_lines && e.rationale_lines.length > 0 && (
                <div style={{ marginLeft: 8, color: "var(--c-text)" }}>
                  {e.rationale_lines.map((line, j) => (
                    <div key={j}>· {line}</div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default PaperStartExplanationCard;
