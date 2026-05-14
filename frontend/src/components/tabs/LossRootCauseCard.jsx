/**
 * 체크리스트 #96: Loss Root Cause Tagging Card (read-only).
 *
 * 본 카드는 *결정 시점 / 실행 단계* 손실 원인을 16개 태그로 분류한 결과를 표시.
 * #79 `LossReasonCard.jsx` (post-trade 25 tag × 7 cat) 와는 별개:
 *
 *   #79 LossReasonCard       : post-trade 손실 결과 분류
 *   #96 LossRootCauseCard    : decision/execution 단계 근본원인 추정
 *
 * CLAUDE.md 절대 원칙:
 *   1. 본 카드는 *advisory 분석 결과 표시*만 — broker / 주문 / route_order
 *      호출 0건.
 *   2. 본 카드는 *어떤 .env / settings 도 자동 수정하지 않는다*.
 *   3. *매수 / 매도 / Place Order / 실거래 라벨 button 0개*.
 *   4. secret 입력 form (input / textarea) 0개.
 *   5. 본 카드의 태그는 *추정값* — 확정 원인이 아니며 자동 차단 트리거로
 *      사용하지 않는다.
 *
 * Props:
 *   - resultOverride: 단일 거래 평가 결과 mock (선택)
 *   - summaryOverride: 집계 요약 mock (선택)
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const CATEGORY_COLOR = {
  decision:  "#f59e0b",
  risk:      "#ef4444",
  market:    "#fbbf24",
  execution: "#7dd3fc",
  strategy:  "#a78bfa",
  unknown:   "#94a3b8",
};

const CATEGORY_LABEL = {
  decision:  "결정",
  risk:      "리스크",
  market:    "시장",
  execution: "실행",
  strategy:  "전략",
  unknown:   "원인 불명",
};

const SEVERITY_COLOR = {
  LOW:     "#94a3b8",
  MEDIUM:  "#fbbf24",
  HIGH:    "#ef4444",
  UNKNOWN: "#94a3b8",
};

const TAG_LABEL = {
  late_entry:            "LATE_ENTRY (진입 지연)",
  late_exit:             "LATE_EXIT (청산 지연)",
  stale_signal:          "STALE_SIGNAL (신호 만료)",
  agent_overruled:       "AGENT_OVERRULED (AI 추천 번복)",
  high_correlation:      "HIGH_CORRELATION (포트폴리오 쏠림)",
  risk_gate_rejected:    "RISK_GATE_REJECTED (RiskManager 차단)",
  high_volatility:       "HIGH_VOLATILITY (변동성 과다)",
  bad_regime:            "BAD_REGIME (시장 regime 부적합)",
  news_risk:             "NEWS_RISK (부정적 뉴스)",
  low_liquidity:         "LOW_LIQUIDITY (거래량 부족)",
  slippage:              "SLIPPAGE (체결 슬리피지)",
  spread_too_wide:       "SPREAD_TOO_WIDE (스프레드 과다)",
  stop_loss_hit:         "STOP_LOSS_HIT (손절가 도달)",
  time_stop_hit:         "TIME_STOP_HIT (시간 손절)",
  kimp_convergence_fail: "KIMP_CONVERGENCE_FAIL (crypto, 미사용)",
  unknown:               "UNKNOWN (원인 추정 불가)",
};


function _SeverityPill({ severity }) {
  const color = SEVERITY_COLOR[severity] || SEVERITY_COLOR.UNKNOWN;
  return (
    <span style={{
      display: "inline-block", minWidth: 56, textAlign: "center",
      padding: "2px 6px", borderRadius: 3,
      fontSize: 10, fontWeight: 700, fontFamily: "monospace",
      color, background: `${color}15`, border: `1px solid ${color}55`,
    }}>{severity}</span>
  );
}


function _CategoryPill({ category }) {
  const color = CATEGORY_COLOR[category] || CATEGORY_COLOR.unknown;
  const label = CATEGORY_LABEL[category] || category;
  return (
    <span style={{
      display: "inline-block", padding: "2px 6px", borderRadius: 3,
      fontSize: 10, fontWeight: 700,
      color, background: `${color}15`, border: `1px solid ${color}55`,
    }}>{label}</span>
  );
}


function _InvariantBadges() {
  const badges = [
    { key: "estimated", text: "추정 태그 · 확정 아님" },
    { key: "no-order",  text: "주문 신호 아님" },
    { key: "no-auto",   text: "자동 적용 안 함" },
    { key: "no-advice", text: "투자 조언 아님" },
    { key: "analysis", text: "분석 전용 · 주문 기능 아님" },
  ];
  return (
    <div data-testid="loss-root-cause-invariants"
         style={{ display: "flex", flexWrap: "wrap", gap: 4,
                   marginBottom: 10 }}>
      {badges.map(({ key, text }) => (
        <span
          key={key}
          data-testid={`loss-root-cause-invariant-${key}`}
          style={{
            fontSize: 10, fontWeight: 700,
            color: "#475569", background: "#e2e8f0",
            border: "1px solid #cbd5e1", borderRadius: 3, padding: "2px 6px",
          }}
        >{text}</span>
      ))}
    </div>
  );
}


export function LossRootCauseCard({
  resultOverride  = null,
  summaryOverride = null,
}) {
  const [result, setResult]   = useState(resultOverride);
  const [summary, setSummary] = useState(summaryOverride);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState("");
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (resultOverride) setResult(resultOverride);
  }, [resultOverride]);

  useEffect(() => {
    if (summaryOverride) setSummary(summaryOverride);
  }, [summaryOverride]);

  const onEvaluateMock = async () => {
    if (resultOverride) return;
    setLoading(true); setError("");
    try {
      // demo 입력 — 운영자가 실제 거래 데이터를 채우는 collector 는 후속 PR.
      const r = await backendApi.lossRootCauseEvaluate({
        symbol: "005930",
        is_loss: true,
        trade_pnl: -50000,
        signal_age_minutes_at_entry: 45,
        portfolio_max_correlation: 0.92,
        realized_slippage_bps: 75.0,
      });
      setResult(r);
    } catch (e) {
      setError(e?.message || "근본원인 평가 실패");
    } finally {
      setLoading(false);
    }
  };

  const primaryTag      = result?.primary_tag || null;
  const primaryCategory = result?.primary_category || null;
  const tags            = result?.tags || [];
  const rationale       = result?.rationale || [];
  const advice          = result?.improvement_advice || [];

  const total           = summary?.total_loss_count ?? 0;
  const byTag           = summary?.by_tag || [];
  const topTags         = summary?.top_tags || [];
  const highSeverity    = summary?.high_severity_tags || [];
  const byStrategy      = summary?.by_strategy || {};

  return (
    <Card style={{ marginBottom: 12 }}>
      <div data-testid="loss-root-cause-card" style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>Loss Root Cause Tagging (#96)</SectionLabel>
        {primaryTag ? (
          <span data-testid={`loss-root-cause-primary-${primaryTag}`} style={{
            padding: "2px 8px", borderRadius: 3,
            fontSize: 10, fontWeight: 700,
            color: CATEGORY_COLOR[primaryCategory] || "#94a3b8",
            background: `${CATEGORY_COLOR[primaryCategory] || "#94a3b8"}15`,
            border: `1px solid ${CATEGORY_COLOR[primaryCategory] || "#94a3b8"}55`,
            fontFamily: "monospace",
          }}>{primaryTag}</span>
        ) : null}
      </div>

      <_InvariantBadges />

      <div
        data-testid="loss-root-cause-disclaimer"
        style={{
          padding: "10px 12px",
          background: "#fef3c7",
          border: "1px solid #f59e0b55",
          color: "#92400e",
          borderRadius: 6,
          fontSize: 12,
          fontWeight: 600,
          marginBottom: 12,
          lineHeight: 1.6,
        }}
      >
        ⚠️ 본 카드의 태그는 *추정값* 이며 *확정 원인이 아닙니다*. 본 모듈은 어떤
        주문도 발행하지 않으며, 이 기능은 *분석 전용이며 주문 기능이 아닙니다*.
        AI Agent / Strategy 성능 개선 학습 자료로 사용. #79 post-trade
        loss_tagging 과는 별개 분석 레이어입니다.
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <button
          data-testid="loss-root-cause-evaluate-btn"
          onClick={onEvaluateMock}
          disabled={loading || !!resultOverride}
          style={{
            padding: "8px 14px",
            borderRadius: 6,
            border: "1px solid var(--c-border-strong)",
            background: "var(--c-surface-2)",
            color: "var(--c-text)",
            cursor: loading ? "wait" : "pointer",
            fontSize: 12,
          }}
        >
          {loading ? "평가 중…" : "예시 평가"}
        </button>
      </div>

      {error ? (
        <div data-testid="loss-root-cause-error" style={{
          padding: 8, color: "#b91c1c", fontSize: 12,
          background: "#fee2e2", borderRadius: 4, marginBottom: 10,
        }}>
          {error}
        </div>
      ) : null}

      {/* 단일 거래 결과 */}
      {result ? (
        <div data-testid="loss-root-cause-detail" style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
          padding: 10, marginBottom: 10,
        }}>
          <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 6 }}>
            단일 거래 평가 — <code>{result.symbol}</code>
            {" "}({result.trade_pnl?.toLocaleString()} KRW)
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {tags.map((t, i) => (
              <div key={i} style={{
                display: "flex", justifyContent: "space-between",
                alignItems: "center", padding: "4px 0",
                borderTop: i === 0 ? "none" : "1px solid var(--c-border)",
                fontSize: 11,
              }}>
                <span style={{ fontFamily: "monospace" }}>
                  {TAG_LABEL[t.tag] || t.tag}
                </span>
                <span style={{ display: "flex", gap: 4 }}>
                  <_CategoryPill category={t.category} />
                  <_SeverityPill severity={t.severity} />
                </span>
              </div>
            ))}
          </div>
          {rationale.length > 0 ? (
            <div data-testid="loss-root-cause-rationale" style={{
              marginTop: 6, padding: 6,
              background: "var(--c-surface-2)", borderRadius: 4,
              fontSize: 10, color: "var(--c-text-2)", lineHeight: 1.5,
            }}>
              <div style={{ fontWeight: 700, marginBottom: 2 }}>근거</div>
              {rationale.map((r, i) => (
                <div key={i}>• {r}</div>
              ))}
            </div>
          ) : null}
          {advice.length > 0 ? (
            <div data-testid="loss-root-cause-advice" style={{
              marginTop: 6, padding: 6,
              background: "#dbeafe", borderRadius: 4,
              fontSize: 10, color: "#1e3a8a", lineHeight: 1.5,
            }}>
              <div style={{ fontWeight: 700, marginBottom: 2 }}>개선 제안</div>
              {advice.map((a, i) => (
                <div key={i}>📝 {a}</div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {/* 집계 요약 */}
      {summary ? (
        <div data-testid="loss-root-cause-summary" style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
          padding: 10, marginBottom: 10,
        }}>
          <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 6 }}>
            집계 요약 ({total}건 손실 분석)
          </div>
          {topTags.length > 0 ? (
            <div data-testid="loss-root-cause-top-tags"
                 style={{ fontSize: 11, marginBottom: 4 }}>
              가장 자주 발생한 원인:{" "}
              {topTags.map((t, i) => (
                <span key={i} style={{
                  marginRight: 4, fontFamily: "monospace",
                  fontWeight: 700,
                }}>{t}</span>
              ))}
            </div>
          ) : null}
          {highSeverity.length > 0 ? (
            <div data-testid="loss-root-cause-high-severity"
                 style={{ fontSize: 11, marginBottom: 4, color: "#991b1b" }}>
              위험도 HIGH 태그:{" "}
              {highSeverity.map((t, i) => (
                <span key={i} style={{
                  marginRight: 4, fontFamily: "monospace",
                  fontWeight: 700,
                }}>{t}</span>
              ))}
            </div>
          ) : null}
          <button
            data-testid="loss-root-cause-toggle-detail-btn"
            onClick={() => setExpanded((v) => !v)}
            style={{
              marginTop: 6,
              padding: "4px 10px",
              borderRadius: 4,
              border: "1px solid var(--c-border)",
              background: "transparent",
              color: "var(--c-text-2)",
              cursor: "pointer", fontSize: 10,
            }}
          >
            {expanded ? "태그 분포 접기" : `태그 분포 펼치기 (${byTag.length})`}
          </button>
          {expanded && byTag.length > 0 ? (
            <div data-testid="loss-root-cause-by-tag-table" style={{
              marginTop: 6, fontSize: 10,
            }}>
              {byTag.map((row, i) => (
                <div key={i} style={{
                  display: "flex", justifyContent: "space-between",
                  alignItems: "center",
                  padding: "3px 0",
                  borderTop: i === 0 ? "none" : "1px solid var(--c-border)",
                }}>
                  <span style={{ fontFamily: "monospace" }}>
                    {row.tag} {" · "}
                    <span style={{ color: "var(--c-text-3)" }}>
                      n={row.count} ({row.share_pct}%)
                    </span>
                  </span>
                  <_CategoryPill category={row.category} />
                </div>
              ))}
            </div>
          ) : null}
          {expanded && Object.keys(byStrategy).length > 0 ? (
            <div data-testid="loss-root-cause-by-strategy" style={{
              marginTop: 6, fontSize: 10,
              padding: 6, background: "var(--c-surface-2)", borderRadius: 4,
            }}>
              <div style={{ fontWeight: 700, marginBottom: 3 }}>
                전략별 카테고리 분포
              </div>
              {Object.entries(byStrategy).map(([strat, cats]) => (
                <div key={strat} style={{
                  display: "flex", justifyContent: "space-between",
                  alignItems: "center",
                  padding: "2px 0",
                }}>
                  <code>{strat}</code>
                  <span style={{ fontFamily: "monospace" }}>
                    {Object.entries(cats).map(([c, n]) => (
                      <span key={c} style={{ marginLeft: 6 }}>
                        {c}={n}
                      </span>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <div style={{
        marginTop: 10, padding: "8px 10px",
        fontSize: 10, color: "var(--c-text-3)",
        background: "var(--c-surface-2)", borderRadius: 4,
      }}>
        * 본 카드는 *분석 전용* 입니다. 태그는 *추정값* 이며 자동 차단 트리거로
        사용되지 않습니다. AI Agent / Strategy 가 본 결과를 prompt context 로
        carry 받아 학습할 수 있습니다 — 자동 적용은 없습니다.
      </div>
    </Card>
  );
}

export default LossRootCauseCard;
