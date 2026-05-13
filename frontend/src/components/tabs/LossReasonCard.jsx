/**
 * 체크리스트 #79: Loss Reason summary card (read-only).
 *
 * CLAUDE.md 절대 원칙:
 *   1. 모든 표시는 *추정 원인* — "원인" 단독 표현 금지, "추정 원인" 사용.
 *   2. broker / 주문 / route_order 호출 0건.
 *   3. 손실 태그를 *주문 차단 / 실행 트리거*로 사용 금지 — 본 카드는 표시만.
 *   4. 삭제 버튼 / "확정 원인" 표현 / "강제 적용" 버튼 0개.
 *
 * 표시:
 *   - 손실 태그 top list (전체)
 *   - 카테고리별 분포
 *   - 전략별 손실 태그
 *   - 최근 손실 거래 + primary_tag + confidence + 추정 원인 배지
 *   - "확정 원인이 아닙니다" 안내
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const CATEGORY_COLOR = {
  strategy:  "#3b82f6",
  market:    "#f59e0b",
  execution: "#a78bfa",
  risk:      "#ef4444",
  data:      "#06b6d4",
  agent:     "#22c55e",
  unknown:   "#94a3b8",
};


function EstimatedBadge() {
  return (
    <span
      data-testid="loss-reason-estimated-badge"
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 3,
        fontSize: 10,
        fontWeight: 700,
        color: "#92400e",
        background: "#fef3c7",
        border: "1px solid #f59e0b55",
        marginLeft: 6,
      }}
    >
      추정 원인 · 확정 원인 아님
    </span>
  );
}


function TagPill({ tag, category, count, pnlSum }) {
  const color = CATEGORY_COLOR[category] || CATEGORY_COLOR.unknown;
  return (
    <div
      data-testid={`loss-reason-tag-${tag}`}
      style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        padding: "6px 10px",
        borderTop: "1px solid var(--c-border)",
        fontSize: 12,
      }}
    >
      <span style={{
        fontFamily: "monospace",
        padding: "2px 6px", borderRadius: 3,
        color, background: `${color}15`, border: `1px solid ${color}55`,
      }}>
        {tag}
      </span>
      <span style={{ display: "flex", gap: 10 }}>
        <span style={{ color: "var(--c-text-3)" }}>category: {category}</span>
        <span style={{ fontWeight: 700 }}>{count}건</span>
        {pnlSum != null ? (
          <span style={{ color: "#ef4444", fontWeight: 700 }}>
            {Number(pnlSum).toLocaleString()} 원
          </span>
        ) : null}
      </span>
    </div>
  );
}


export function LossReasonCard({
  summaryOverride = null,
  recentOverride  = null,
}) {
  const [summary, setSummary] = useState(summaryOverride);
  const [recent,  setRecent]  = useState(recentOverride);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");

  useEffect(() => {
    if (summaryOverride) setSummary(summaryOverride);
    if (recentOverride)  setRecent(recentOverride);
  }, [summaryOverride, recentOverride]);

  const onRefresh = async () => {
    if (summaryOverride && recentOverride) return;
    setLoading(true);
    setError("");
    try {
      const [s, r] = await Promise.all([
        backendApi.lossTagsSummary({ days: 7 }),
        backendApi.lossTagsRecent({ limit: 20 }),
      ]);
      setSummary(s);
      setRecent(r);
    } catch (e) {
      setError(e?.message || "Loss tags 조회 실패");
    } finally {
      setLoading(false);
    }
  };

  const topTags    = summary?.top_tags || [];
  const byCategory = summary?.by_category || {};
  const byStrategy = summary?.by_strategy || [];
  const items      = recent?.items || [];
  const lossCount  = summary?.loss_count || 0;

  return (
    <Card style={{ marginBottom: 12 }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <div>
          <SectionLabel>Loss Reason — 손실 *추정 원인* 분석</SectionLabel>
        </div>
        <EstimatedBadge />
      </div>

      <div
        data-testid="loss-reason-disclaimer"
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
        ⚠️ 본 카드의 태그는 *추정 원인 / 가능성이 높은 요인*입니다.
        **확정 원인이 아닙니다.** 운영자 검토가 필요하며, 손실 태그를 *투자
        조언*이나 *주문 차단 / 실행*에 사용하지 마세요.
      </div>

      <button
        data-testid="loss-reason-refresh-btn"
        onClick={onRefresh}
        disabled={loading || (summaryOverride && recentOverride)}
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
        {loading ? "조회 중…" : "추정 원인 요약 새로 고침"}
      </button>

      {error ? (
        <div data-testid="loss-reason-error" style={{
          padding: 8, color: "#b91c1c", fontSize: 12,
          background: "#fee2e2", borderRadius: 4, marginBottom: 10,
        }}>
          {error}
        </div>
      ) : null}

      <div data-testid="loss-reason-stats" style={{
        display: "grid", gridTemplateColumns: "repeat(3, 1fr)",
        gap: 8, marginBottom: 10,
      }}>
        <div style={{
          border: "1px solid var(--c-border)", borderRadius: 6, padding: 8,
        }}>
          <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>총 손실 거래</div>
          <div style={{ fontWeight: 700, fontSize: 18 }}>{lossCount}</div>
        </div>
        <div style={{
          border: "1px solid var(--c-border)", borderRadius: 6, padding: 8,
        }}>
          <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>총 손실 합계</div>
          <div style={{ fontWeight: 700, fontSize: 14, color: "#ef4444" }}>
            {Number(summary?.pnl_sum || 0).toLocaleString()} 원
          </div>
        </div>
        <div style={{
          border: "1px solid var(--c-border)", borderRadius: 6, padding: 8,
        }}>
          <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>집계 기간</div>
          <div style={{ fontWeight: 700, fontSize: 14 }}>
            최근 {summary?.days || 7}일
          </div>
        </div>
      </div>

      {topTags.length > 0 ? (
        <div data-testid="loss-reason-top-tags" style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
          marginBottom: 10,
        }}>
          <div style={{
            padding: "6px 10px",
            background: "var(--c-surface-2)",
            fontSize: 11, fontWeight: 700,
          }}>
            추정 원인 Top
          </div>
          {topTags.map((t) => (
            <TagPill
              key={t.tag}
              tag={t.tag}
              category={t.category}
              count={t.count}
              pnlSum={t.pnl_sum}
            />
          ))}
        </div>
      ) : null}

      {Object.keys(byCategory).length > 0 ? (
        <div data-testid="loss-reason-categories" style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
          marginBottom: 10, padding: 10,
        }}>
          <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 6 }}>
            카테고리별 (primary 기준)
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {Object.entries(byCategory).sort((a, b) => b[1] - a[1]).map(([k, v]) => {
              const color = CATEGORY_COLOR[k] || CATEGORY_COLOR.unknown;
              return (
                <span key={k} style={{
                  fontSize: 11, padding: "3px 8px", borderRadius: 3,
                  color, background: `${color}15`, border: `1px solid ${color}55`,
                  fontFamily: "monospace",
                }}>{k}: {v}건</span>
              );
            })}
          </div>
        </div>
      ) : null}

      {byStrategy.length > 0 ? (
        <div data-testid="loss-reason-by-strategy" style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
          marginBottom: 10,
        }}>
          <div style={{
            padding: "6px 10px",
            background: "var(--c-surface-2)",
            fontSize: 11, fontWeight: 700,
          }}>
            전략별 손실 *추정* 패턴
          </div>
          {byStrategy.slice(0, 5).map((s) => (
            <div key={s.strategy} style={{
              display: "flex", justifyContent: "space-between",
              padding: "6px 10px", fontSize: 12,
              borderTop: "1px solid var(--c-border)",
            }}>
              <span style={{ fontFamily: "monospace" }}>{s.strategy}</span>
              <span>
                {s.count}건 · {Number(s.pnl_sum).toLocaleString()} 원 ·{" "}
                {(s.top_tags || []).slice(0, 3).map((t) => t.tag).join(", ")}
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {items.length > 0 ? (
        <div data-testid="loss-reason-recent" style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
        }}>
          <div style={{
            padding: "6px 10px",
            background: "var(--c-surface-2)",
            fontSize: 11, fontWeight: 700,
          }}>
            최근 손실 거래 (추정 원인 표시)
          </div>
          {items.slice(0, 10).map((item) => {
            const cat = item.primary_category || "unknown";
            const color = CATEGORY_COLOR[cat] || CATEGORY_COLOR.unknown;
            return (
              <div key={item.id} style={{
                padding: "6px 10px", fontSize: 12,
                borderTop: "1px solid var(--c-border)",
              }}>
                <div style={{
                  display: "flex", justifyContent: "space-between",
                  alignItems: "center",
                }}>
                  <span style={{ fontFamily: "monospace" }}>
                    {item.symbol}{item.strategy ? ` · ${item.strategy}` : ""}
                  </span>
                  <span style={{ display: "flex", gap: 8 }}>
                    <span style={{ color: "#ef4444", fontWeight: 700 }}>
                      {Number(item.trade_pnl).toLocaleString()} 원
                    </span>
                    <span style={{
                      padding: "2px 6px", borderRadius: 3,
                      color, background: `${color}15`,
                      border: `1px solid ${color}55`,
                      fontFamily: "monospace", fontSize: 10,
                    }}>
                      {item.primary_tag || "—"}
                    </span>
                    <span style={{ fontSize: 10, color: "var(--c-text-3)" }}>
                      confidence {item.confidence}
                    </span>
                  </span>
                </div>
                {item.review_status ? (
                  <div style={{
                    marginTop: 2, fontSize: 10, color: "var(--c-text-3)",
                  }}>
                    review: {item.review_status}
                    {item.reviewed_by ? ` · ${item.reviewed_by}` : ""}
                    {item.review_note ? ` · ${item.review_note}` : ""}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}

      <div style={{
        marginTop: 10, padding: "8px 10px",
        fontSize: 10, color: "var(--c-text-3)",
        background: "var(--c-surface-2)", borderRadius: 4,
      }}>
        * 본 카드는 *분석 / 추정*만 합니다. 손실 태그는 확정 원인이 아니며,
        주문 차단 / 실행 / 전략 변경의 *자동 트리거*로 사용되지 않습니다.
      </div>
    </Card>
  );
}

export default LossReasonCard;
