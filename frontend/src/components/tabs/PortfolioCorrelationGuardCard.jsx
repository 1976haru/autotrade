/**
 * 체크리스트 #95: Portfolio Correlation Guard Card (read-only).
 *
 * 본 카드는 *포트폴리오 내 종목 간 수익률 상관관계* 매트릭스를 시각화한다.
 * #78 `CorrelationGuardCard.jsx` (*sector/theme 노출 cap*) 와는 별개 개념:
 *
 *   #78 CorrelationGuardCard            : sector/theme 메타 기반 노출 cap
 *   #95 PortfolioCorrelationGuardCard   : 종목 간 historical return 상관관계
 *
 * CLAUDE.md 절대 원칙:
 *   1. 본 카드는 *advisory 분석 결과 표시*만 — broker / 주문 / route_order
 *      호출 0건.
 *   2. 본 카드는 *어떤 .env / settings 도 자동 수정하지 않는다*.
 *   3. *매수 / 매도 / Place Order / 실거래 라벨 button 0개*.
 *      "다시 평가" 버튼만 — UI 동작만.
 *   4. secret 입력 form (input / textarea) 0개. KIS / Anthropic / 계좌번호
 *      원문 표시 0건.
 *   5. BLOCK verdict 시 *신규 진입 차단 권장* 배지 — 실제 차단은 별도
 *      RiskRule 에서 처리.
 *
 * Props:
 *   - inputOverride: 평가 입력 (resultOverride 없을 때 fetch 시 사용)
 *   - resultOverride: 테스트 / preview 용 mock 결과
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const VERDICT_COLOR = {
  HEALTHY:           "#22c55e",
  WATCH:             "#fbbf24",
  WARN:              "#f59e0b",
  BLOCK:             "#ef4444",
  INSUFFICIENT_DATA: "#94a3b8",
};

const VERDICT_HEADLINE = {
  HEALTHY:           "포트폴리오 상관관계 정상",
  WATCH:             "일부 종목 쌍 상관관계 감지 — 모니터링",
  WARN:              "상관관계 주의 — 신규 진입 시 보수적 사이즈 권장",
  BLOCK:             "상관관계 과다로 신규 진입 주의",
  INSUFFICIENT_DATA: "표본 부족 — 평가 불가",
};

const SEVERITY_COLOR = {
  LOW:     "#22c55e",
  MEDIUM:  "#fbbf24",
  HIGH:    "#f59e0b",
  EXTREME: "#ef4444",
};


function _VerdictHeadline({ verdict, score, maxCorr, newEntryAllowed }) {
  const color = VERDICT_COLOR[verdict] || VERDICT_COLOR.INSUFFICIENT_DATA;
  const head  = VERDICT_HEADLINE[verdict] || verdict || "—";
  return (
    <div
      data-testid="portfolio-corr-headline"
      style={{
        padding: "12px 16px",
        borderRadius: 8,
        border: `2px solid ${color}55`,
        background: `${color}10`,
        color,
        fontSize: 16,
        fontWeight: 800,
        textAlign: "center",
        marginBottom: 12,
      }}
    >
      {head}
      <div style={{ fontSize: 11, marginTop: 4, fontWeight: 600,
                     fontFamily: "monospace" }}>
        verdict={verdict} · score={score.toFixed(1)} ·
        max_|corr|={maxCorr.toFixed(3)} · new_entry={String(newEntryAllowed)}
      </div>
    </div>
  );
}


function _SeverityPill({ severity }) {
  const color = SEVERITY_COLOR[severity] || SEVERITY_COLOR.LOW;
  return (
    <span style={{
      display: "inline-block", minWidth: 56, textAlign: "center",
      padding: "2px 6px", borderRadius: 3,
      fontSize: 10, fontWeight: 700, fontFamily: "monospace",
      color, background: `${color}15`, border: `1px solid ${color}55`,
    }}>
      {severity}
    </span>
  );
}


function _InvariantBadges() {
  const badges = [
    { key: "no-order", text: "주문 신호 아님" },
    { key: "no-auto",  text: "자동 적용 안 함" },
    { key: "no-live",  text: "실거래 허가 아님" },
    { key: "advisory", text: "advisory 분석" },
  ];
  return (
    <div data-testid="portfolio-corr-invariants"
         style={{ display: "flex", flexWrap: "wrap", gap: 4,
                   marginBottom: 10 }}>
      {badges.map(({ key, text }) => (
        <span
          key={key}
          data-testid={`portfolio-corr-invariant-${key}`}
          style={{
            fontSize: 10,
            fontWeight: 700,
            color: "#475569",
            background: "#e2e8f0",
            border: "1px solid #cbd5e1",
            borderRadius: 3,
            padding: "2px 6px",
          }}
        >
          {text}
        </span>
      ))}
    </div>
  );
}


function _BlockBanner({ verdict }) {
  if (verdict !== "BLOCK") return null;
  return (
    <div
      data-testid="portfolio-corr-block-banner"
      style={{
        marginBottom: 10,
        padding: "10px 12px",
        background: "#fee2e2",
        border: "2px solid #ef4444",
        borderRadius: 6,
        color: "#991b1b",
        fontWeight: 800,
        fontSize: 13,
        lineHeight: 1.5,
      }}
    >
      🛑 상관관계 과다로 신규 진입 주의.
      <div style={{ fontSize: 11, fontWeight: 600, marginTop: 4,
                     color: "#7f1d1d" }}>
        포트폴리오 내 종목 쌍이 매우 강한 상관관계를 보입니다. 동일 시장 충격에
        다수 포지션이 *동시에* 손실을 입을 위험이 있습니다. 무상관 / 저상관 자산
        검토를 권장합니다. 본 카드의 BLOCK 은 *권고* 수준 — 실제 차단은 별도
        RiskRule 에서 처리.
      </div>
    </div>
  );
}


export function PortfolioCorrelationGuardCard({
  inputOverride = null,
  resultOverride = null,
}) {
  const [result, setResult]     = useState(resultOverride);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState("");
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (resultOverride) setResult(resultOverride);
  }, [resultOverride]);

  const onEvaluate = async () => {
    if (resultOverride) return;
    setLoading(true);
    setError("");
    try {
      const r = await backendApi.portfolioCorrelationEvaluate(inputOverride || {});
      setResult(r);
    } catch (e) {
      setError(e?.message || "Portfolio Correlation 평가 실패");
    } finally {
      setLoading(false);
    }
  };

  const verdict      = result?.verdict || "INSUFFICIENT_DATA";
  const score        = result?.portfolio_correlation_score ?? 0;
  const maxCorr      = result?.max_pairwise_correlation ?? 0;
  const meanCorr     = result?.mean_pairwise_correlation ?? 0;
  const highCount    = result?.high_correlation_pair_count ?? 0;
  const candidateMax = result?.candidate_max_correlation;
  const newEntry     = !!result?.new_entry_allowed;
  const pairs        = result?.pairs || [];
  const warnings     = result?.warnings || [];
  const advice       = result?.advice || [];
  const insufficient = !!result?.insufficient_data;

  // pair 정렬 — 절대값 큰 순.
  const sortedPairs = [...pairs].sort(
    (a, b) => Math.abs(b.correlation) - Math.abs(a.correlation),
  );

  return (
    <Card style={{ marginBottom: 12 }} accentColor={VERDICT_COLOR[verdict]}>
      <div data-testid="portfolio-corr-card" style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>Portfolio Correlation Guard (#95)</SectionLabel>
        <span data-testid={`portfolio-corr-verdict-${verdict}`} style={{
          padding: "2px 8px", borderRadius: 3,
          fontSize: 10, fontWeight: 700,
          color: VERDICT_COLOR[verdict],
          background: `${VERDICT_COLOR[verdict]}15`,
          border: `1px solid ${VERDICT_COLOR[verdict]}55`,
        }}>{verdict}</span>
      </div>

      <_BlockBanner verdict={verdict} />

      <_VerdictHeadline
        verdict={verdict} score={score} maxCorr={maxCorr}
        newEntryAllowed={newEntry}
      />

      <_InvariantBadges />

      <div
        data-testid="portfolio-corr-disclaimer"
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
        ⚠️ 본 카드는 *포트폴리오 상관관계 advisory* 입니다. 본 모듈은 어떤 주문도
        발행하지 않으며, BLOCK verdict 도 권고 수준입니다. 본 카드와 #78
        *sector/theme 노출* correlation guard 는 별개 개념입니다 — 두 카드 모두 확인 권장.
      </div>

      {candidateMax != null ? (
        <div data-testid="portfolio-corr-candidate"
             style={{ fontSize: 12, marginBottom: 8, fontFamily: "monospace" }}>
          후보 max |corr| = <strong>{Math.abs(candidateMax).toFixed(3)}</strong>
        </div>
      ) : null}

      <div data-testid="portfolio-corr-stats"
           style={{ fontSize: 11, marginBottom: 8, color: "var(--c-text-3)" }}>
        총 {pairs.length} 쌍 · mean |corr| = {meanCorr.toFixed(3)} ·
        block 임계 초과 {highCount} 쌍
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <button
          data-testid="portfolio-corr-evaluate-btn"
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
          }}
        >
          {loading ? "평가 중…" : "다시 평가"}
        </button>
      </div>

      {error ? (
        <div data-testid="portfolio-corr-error" style={{
          padding: 8, color: "#b91c1c", fontSize: 12,
          background: "#fee2e2", borderRadius: 4, marginBottom: 10,
        }}>
          {error}
        </div>
      ) : null}

      {insufficient ? (
        <div data-testid="portfolio-corr-insufficient" style={{
          padding: 8, color: "#92400e", fontSize: 12,
          background: "#fef3c7", borderRadius: 4, marginBottom: 10,
        }}>
          ⚠️ 표본 부족 / 입력 누락 — 평가 신뢰성 낮음 (본 가드 적용 안 됨)
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <div data-testid="portfolio-corr-warnings" style={{
          border: "1px solid #f59e0b55", borderRadius: 6,
          marginBottom: 10, padding: 10, background: "#fef3c7",
        }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#92400e" }}>
            경고 (WARN)
          </div>
          {warnings.map((w, i) => (
            <div key={i} style={{ fontSize: 12, marginTop: 4 }}>⚠️ {w}</div>
          ))}
        </div>
      ) : null}

      {advice.length > 0 ? (
        <div data-testid="portfolio-corr-advice" style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
          marginBottom: 10, padding: 10,
        }}>
          <div style={{ fontSize: 11, fontWeight: 700 }}>권고</div>
          {advice.map((a, i) => (
            <div key={i} style={{ fontSize: 12, marginTop: 4 }}>📝 {a}</div>
          ))}
        </div>
      ) : null}

      <button
        data-testid="portfolio-corr-toggle-detail-btn"
        onClick={() => setExpanded((v) => !v)}
        style={{
          padding: "6px 10px",
          borderRadius: 6,
          border: "1px solid var(--c-border)",
          background: "transparent",
          color: "var(--c-text-2)",
          cursor: "pointer", fontSize: 11, marginBottom: 10,
        }}
      >
        {expanded ? "쌍 상세 접기" : `쌍 상세 펼치기 (${pairs.length})`}
      </button>

      {expanded && sortedPairs.length > 0 ? (
        <div data-testid="portfolio-corr-pairs" style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
        }}>
          <div style={{
            padding: "6px 10px",
            background: "var(--c-surface-2)",
            fontSize: 11, fontWeight: 700,
          }}>
            상관계수 쌍 (severity 순, {sortedPairs.length}개)
          </div>
          {sortedPairs.map((p, i) => (
            <div key={`${p.symbol_a}-${p.symbol_b}-${i}`} style={{
              display: "flex", justifyContent: "space-between",
              alignItems: "center",
              padding: "6px 10px", fontSize: 12,
              borderTop: "1px solid var(--c-border)",
            }}>
              <span style={{ fontFamily: "monospace" }}>
                <strong>{p.symbol_a}</strong> ↔ <strong>{p.symbol_b}</strong>
                {" · "}
                <span style={{ color: "var(--c-text-3)" }}>
                  corr={p.correlation >= 0 ? "+" : ""}{p.correlation.toFixed(3)}{" "}
                  · n={p.sample_size}
                </span>
              </span>
              <_SeverityPill severity={p.severity} />
            </div>
          ))}
        </div>
      ) : null}

      <div style={{
        marginTop: 10, padding: "8px 10px",
        fontSize: 10, color: "var(--c-text-3)",
        background: "var(--c-surface-2)", borderRadius: 4,
      }}>
        * 본 카드는 *advisory 분석* 입니다. AI Agent / Strategy 가 본 verdict 를
        무시하고 BLOCK 상태에서 진입하는 것은 RiskManager / OrderGuard 가
        막을 책임 — 본 카드는 *판단 보조*만 제공.
      </div>
    </Card>
  );
}

export default PortfolioCorrelationGuardCard;
