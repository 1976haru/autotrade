/**
 * 체크리스트 #77: Alpha Decay Monitor read-only card.
 *
 * CLAUDE.md 절대 원칙:
 *   1. *전략 자동 비활성 / 삭제 / promotion 변경 버튼 0개* — read-only 분석.
 *   2. broker / 주문 / route_order 호출 0건.
 *   3. UI 상단 고지 강제: "DISABLE_CANDIDATE는 *비활성 후보*이며 자동 비활성이 아닙니다."
 *   4. 전략 삭제/중단은 운영자 수동 승인 필요 안내.
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const STATUS_COLOR = {
  HEALTHY:           "#22c55e",
  WATCH:             "#f59e0b",
  DECAY_WARNING:     "#f97316",
  DISABLE_CANDIDATE: "#ef4444",
  INSUFFICIENT_DATA: "#94a3b8",
};

const STATUS_LABEL = {
  HEALTHY:           "정상",
  WATCH:             "주의",
  DECAY_WARNING:     "감쇠 경고",
  DISABLE_CANDIDATE: "비활성 후보",
  INSUFFICIENT_DATA: "표본 부족",
};

const KIND_LABEL = {
  NONE:                "—",
  SHORT_TERM_DRAWDOWN: "단기 부진",
  REGIME_MISMATCH:     "Regime 변화",
  STRUCTURAL_DECAY:    "구조적 감쇠",
  DATA_QUALITY_ISSUE: "데이터 품질 이슈",
  INSUFFICIENT_DATA:   "표본 부족",
};


function StatusBadge({ status }) {
  const color = STATUS_COLOR[status] || STATUS_COLOR.INSUFFICIENT_DATA;
  const label = STATUS_LABEL[status] || status || "—";
  return (
    <span
      data-testid={`alpha-decay-status-${status}`}
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


function DisableCandidateBadge() {
  return (
    <span
      data-testid="alpha-decay-disable-candidate-badge"
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 3,
        fontSize: 10,
        fontWeight: 700,
        color: "#ef4444",
        background: "#fee2e2",
        border: "1px solid #ef444455",
        marginLeft: 6,
      }}
    >
      비활성 후보 (자동 비활성 아님)
    </span>
  );
}


function DeltaCell({ label, baseline, recent, suffix = "" }) {
  const baseNum = Number(baseline);
  const recNum  = Number(recent);
  const delta   = Number.isFinite(baseNum) && Number.isFinite(recNum)
    ? recNum - baseNum : 0;
  const color   = delta > 0 ? "#22c55e" : delta < 0 ? "#ef4444" : "var(--c-text-3)";
  return (
    <div style={{ fontSize: 12 }}>
      <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>{label}</div>
      <div>
        baseline: <span style={{ fontWeight: 600 }}>{baseNum}</span>{suffix}
      </div>
      <div>
        recent:   <span style={{ fontWeight: 600 }}>{recNum}</span>{suffix}
      </div>
      <div style={{ color, fontWeight: 700 }}>
        Δ {delta > 0 ? "+" : ""}{Math.round(delta * 10000) / 10000}{suffix}
      </div>
    </div>
  );
}


export function AlphaDecayCard({ inputOverride = null, resultOverride = null }) {
  const [result, setResult] = useState(resultOverride);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (resultOverride) setResult(resultOverride);
  }, [resultOverride]);

  const onEvaluate = async () => {
    if (resultOverride) return;
    setLoading(true);
    setError("");
    try {
      const r = await backendApi.alphaDecayEvaluate(
        inputOverride || { strategy_name: "sma_cross", baseline: {}, recent: {} },
      );
      setResult(r);
    } catch (e) {
      setError(e?.message || "Alpha Decay 평가 실패");
    } finally {
      setLoading(false);
    }
  };

  const status   = result?.status || "INSUFFICIENT_DATA";
  const kind     = result?.kind || "NONE";
  const score    = result?.score;
  const metrics  = result?.metrics || {};
  const base     = metrics.baseline || {};
  const recent   = metrics.recent || {};
  const action   = result?.recommended_action || "";
  const signals  = result?.degraded_signals || [];
  const cautions = result?.cautions || [];
  const isCandidate = status === "DISABLE_CANDIDATE";

  return (
    <Card style={{ marginBottom: 12 }} accentColor={STATUS_COLOR[status]}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>
          Alpha Decay — {result?.strategy_name || "(strategy)"}
        </SectionLabel>
        <div>
          <StatusBadge status={status} />
          {isCandidate ? <DisableCandidateBadge /> : null}
        </div>
      </div>

      <div
        data-testid="alpha-decay-disclaimer"
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
        ⚠️ DISABLE_CANDIDATE 는 *비활성 후보* 표시이지 자동 비활성이 아닙니다.
        전략 삭제 / 중단은 *운영자 수동 승인*이 필요하며, 본 카드는 read-only
        분석 자료입니다.
      </div>

      <button
        data-testid="alpha-decay-evaluate-btn"
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
        {loading ? "평가 중…" : "알파 감쇠 평가"}
      </button>

      {error ? (
        <div
          data-testid="alpha-decay-error"
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
          <div data-testid="alpha-decay-summary" style={{
            display: "grid", gridTemplateColumns: "repeat(3, 1fr)",
            gap: 8, marginBottom: 10,
          }}>
            <div style={{
              border: "1px solid var(--c-border)", borderRadius: 6, padding: 8,
            }}>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>score (0~100)</div>
              <div style={{
                fontWeight: 700, fontSize: 18,
                color: STATUS_COLOR[status],
              }}>
                {score == null || score < 0 ? "—" : score}
              </div>
            </div>
            <div style={{
              border: "1px solid var(--c-border)", borderRadius: 6, padding: 8,
            }}>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>kind</div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>
                {KIND_LABEL[kind] || kind}
              </div>
            </div>
            <div style={{
              border: "1px solid var(--c-border)", borderRadius: 6, padding: 8,
            }}>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>recent trades</div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>
                {recent.trade_count || 0}
              </div>
            </div>
          </div>

          <div data-testid="alpha-decay-deltas" style={{
            border: "1px solid var(--c-border)", borderRadius: 6,
            marginBottom: 10, padding: 10,
            display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 8,
          }}>
            <DeltaCell label="expectancy"
                       baseline={base.expectancy} recent={recent.expectancy} />
            <DeltaCell label="profit_factor"
                       baseline={base.profit_factor} recent={recent.profit_factor} />
            <DeltaCell label="win_rate"
                       baseline={base.win_rate} recent={recent.win_rate} />
            <DeltaCell label="max_drawdown"
                       baseline={base.max_drawdown} recent={recent.max_drawdown} />
            <DeltaCell label="max_consecutive_losses"
                       baseline={base.max_consecutive_losses}
                       recent={recent.max_consecutive_losses} />
          </div>

          {signals.length > 0 ? (
            <div data-testid="alpha-decay-signals" style={{
              border: "1px solid var(--c-border)", borderRadius: 6,
              marginBottom: 10,
            }}>
              <div style={{
                padding: "6px 10px",
                background: "var(--c-surface-2)",
                fontSize: 11, fontWeight: 700,
              }}>
                악화 신호 (advisory)
              </div>
              <div style={{ padding: "6px 10px", display: "flex", flexWrap: "wrap", gap: 6 }}>
                {signals.map((s) => (
                  <span key={s} style={{
                    fontSize: 11, padding: "2px 8px", borderRadius: 3,
                    background: "#fef3c7", color: "#92400e",
                    border: "1px solid #f59e0b55",
                    fontFamily: "monospace",
                  }}>{s}</span>
                ))}
              </div>
            </div>
          ) : null}

          {action ? (
            <div data-testid="alpha-decay-recommendation" style={{
              border: "1px solid var(--c-border)", borderRadius: 6,
              marginBottom: 10, padding: 10, fontSize: 12,
            }}>
              <div style={{
                fontSize: 11, fontWeight: 700, color: "var(--c-text-2)",
                marginBottom: 4,
              }}>
                권장 조치 (advisory)
              </div>
              <div>📝 {action}</div>
              <div style={{
                marginTop: 6, fontSize: 10, color: "var(--c-text-3)",
                fontWeight: 600,
              }}>
                운영자 검토 필요 · 전략 삭제/중단은 수동 승인.
              </div>
            </div>
          ) : null}

          {cautions.length > 0 ? (
            <div data-testid="alpha-decay-cautions" style={{
              border: "1px solid #f59e0b55", borderRadius: 6,
              padding: 10, background: "#fef3c7",
            }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#92400e" }}>
                CAUTION
              </div>
              {cautions.map((c, i) => (
                <div key={i} style={{ fontSize: 12, marginTop: 4 }}>⚠️ {c}</div>
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
        * 본 카드는 *분석*만 합니다. 전략 비활성화 / 삭제 / 파라미터 변경은
        Strategy Researcher Agent(#55) 분석 + 운영자 수동 승인 + 별도 PR이 필요합니다.
      </div>
    </Card>
  );
}

export default AlphaDecayCard;
