/**
 * 체크리스트 #94: Signal Alpha Decay Card (read-only).
 *
 * 본 카드는 *개별 신호* 의 시간 경과 후 기대수익 감쇠를 분석하는 advisory
 * 리포트. #77 `AlphaDecayCard.jsx` (*전략 단위* 알파 감쇠) 와는 별개 개념:
 *
 *   #77 AlphaDecayCard       : 전략 단위, 일/주 단위 baseline vs recent
 *   #94 SignalAlphaDecayCard : 신호 단위, 1m/3m/5m/10m/30m/60m bucket
 *
 * CLAUDE.md 절대 원칙:
 *   1. 본 카드는 *advisory 분석 결과 표시*만 — broker / 주문 / route_order
 *      호출 0건.
 *   2. 본 카드는 *어떤 .env / settings 도 자동 수정하지 않는다*.
 *   3. *매수 / 매도 / Place Order / 실거래 라벨 button 0개*.
 *      "다시 평가" 버튼만 — UI 동작만.
 *   4. secret 입력 form (input / textarea) 0개. KIS / Anthropic / 계좌번호
 *      원문 표시 0건.
 *   5. EXPIRED verdict 인 신호는 *신규 진입 근거로 사용 금지* — 카드 상단에
 *      "이 신호는 오래되어 진입 근거로 사용 금지" 배지 표시.
 *
 * Props:
 *   - inputOverride: 평가 입력 (resultOverride 가 없을 때 fetch 시 사용)
 *   - resultOverride: 테스트 / preview 용 mock 결과
 *   - currentAgeMinutes: 실시간 신호 age (분) — freshness gate 표시용
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const VERDICT_COLOR = {
  FRESH:    "#22c55e",
  DECAYING: "#fbbf24",
  STALE:    "#f59e0b",
  EXPIRED:  "#ef4444",
  UNKNOWN:  "#94a3b8",
};

const VERDICT_HEADLINE = {
  FRESH:    "신호 신선 — 진입 근거로 유효",
  DECAYING: "신호 감쇠 진행 중 — 주의",
  STALE:    "신호 오래됨 — 진입 권장하지 않음",
  EXPIRED:  "신호 만료 — 신규 진입 근거로 사용 금지",
  UNKNOWN:  "표본 부족 — 평가 불가",
};

const SEVERITY_COLOR = {
  PASS:    "#22c55e",
  WARN:    "#f59e0b",
  FAIL:    "#ef4444",
  UNKNOWN: "#94a3b8",
};


function _VerdictHeadline({ verdict, decayScore, maxAge }) {
  const color = VERDICT_COLOR[verdict] || VERDICT_COLOR.UNKNOWN;
  const head  = VERDICT_HEADLINE[verdict] || verdict || "—";
  return (
    <div
      data-testid="signal-alpha-decay-headline"
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
        verdict={verdict} · decay_score={decayScore.toFixed(1)} ·
        max_actionable_age={maxAge}m
      </div>
    </div>
  );
}


function _SeverityPill({ severity }) {
  const color = SEVERITY_COLOR[severity] || SEVERITY_COLOR.UNKNOWN;
  return (
    <span style={{
      display: "inline-block", minWidth: 48, textAlign: "center",
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
    <div data-testid="signal-alpha-decay-invariants"
         style={{ display: "flex", flexWrap: "wrap", gap: 4,
                   marginBottom: 10 }}>
      {badges.map(({ key, text }) => (
        <span
          key={key}
          data-testid={`signal-alpha-decay-invariant-${key}`}
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


function _ExpiredBanner({ verdict }) {
  if (verdict !== "EXPIRED") return null;
  return (
    <div
      data-testid="signal-alpha-decay-expired-banner"
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
      🛑 이 신호는 오래되어 진입 근거로 사용 금지.
      <div style={{ fontSize: 11, fontWeight: 600, marginTop: 4,
                     color: "#7f1d1d" }}>
        AI Agent / Strategy 는 EXPIRED 신호를 *신규 매수 / 매도 신호 생성에
        사용하지 않아야 합니다*. 새 신호 생성 후 진입 검토.
      </div>
    </div>
  );
}


export function SignalAlphaDecayCard({
  inputOverride = null,
  resultOverride = null,
  currentAgeMinutes = null,
}) {
  const [result, setResult]       = useState(resultOverride);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState("");
  const [expanded, setExpanded]   = useState(false);
  const [freshness, setFreshness] = useState(null);

  useEffect(() => {
    if (resultOverride) setResult(resultOverride);
  }, [resultOverride]);

  useEffect(() => {
    if (currentAgeMinutes == null) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await backendApi.alphaDecayFreshness({
          ageMinutes: currentAgeMinutes,
        });
        if (!cancelled) setFreshness(r);
      } catch (e) {
        if (!cancelled) setError(e?.message || "freshness 조회 실패");
      }
    })();
    return () => { cancelled = true; };
  }, [currentAgeMinutes]);

  const onEvaluate = async () => {
    if (resultOverride) return;
    setLoading(true);
    setError("");
    try {
      const r = await backendApi.alphaDecayEvaluate(inputOverride || {});
      setResult(r);
    } catch (e) {
      setError(e?.message || "Signal Alpha Decay 평가 실패");
    } finally {
      setLoading(false);
    }
  };

  const verdict       = result?.verdict_overall || "UNKNOWN";
  const decayScore    = result?.decay_score ?? 0;
  const maxAge        = result?.max_actionable_age_minutes ?? 30;
  const buckets       = result?.buckets || [];
  const warnings      = result?.warnings || [];
  const advice        = result?.advice || [];
  const strategyName  = result?.strategy_name || "—";
  const insufficient  = !!result?.insufficient_data;

  return (
    <Card style={{ marginBottom: 12 }} accentColor={VERDICT_COLOR[verdict]}>
      <div data-testid="signal-alpha-decay-card" style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>Signal Alpha Decay (#94)</SectionLabel>
        <span data-testid={`signal-alpha-decay-verdict-${verdict}`} style={{
          padding: "2px 8px", borderRadius: 3,
          fontSize: 10, fontWeight: 700,
          color: VERDICT_COLOR[verdict],
          background: `${VERDICT_COLOR[verdict]}15`,
          border: `1px solid ${VERDICT_COLOR[verdict]}55`,
        }}>{verdict}</span>
      </div>

      <_ExpiredBanner verdict={verdict} />

      <_VerdictHeadline
        verdict={verdict} decayScore={decayScore} maxAge={maxAge}
      />

      <_InvariantBadges />

      <div
        data-testid="signal-alpha-decay-disclaimer"
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
        ⚠️ 본 카드는 *신호 신선도 advisory 분석* 입니다. 본 모듈은 어떤 주문도
        발행하지 않으며, AI Agent / Strategy 가 본 verdict 가 EXPIRED 인
        신호를 신규 진입 근거로 사용하지 않아야 합니다. 본 카드와 *전략 단위*
        알파 감쇠 (#77) 는 별개 개념입니다.
      </div>

      <div data-testid="signal-alpha-decay-target"
           style={{ fontSize: 12, marginBottom: 8 }}>
        대상 전략: <code>{strategyName}</code>
      </div>

      {freshness ? (
        <div
          data-testid="signal-alpha-decay-realtime-freshness"
          style={{
            padding: "8px 10px",
            background: "#f1f5f9",
            border: "1px solid var(--c-border)",
            borderRadius: 6,
            fontSize: 11,
            marginBottom: 10,
            fontFamily: "monospace",
          }}
        >
          실시간 freshness (age={freshness.age_minutes}m):
          {" "}<strong>{freshness.verdict}</strong>{" "}
          · actionable={String(freshness.actionable)}
          · strict={String(freshness.actionable_strict)}
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <button
          data-testid="signal-alpha-decay-evaluate-btn"
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
        <div data-testid="signal-alpha-decay-error" style={{
          padding: 8, color: "#b91c1c", fontSize: 12,
          background: "#fee2e2", borderRadius: 4, marginBottom: 10,
        }}>
          {error}
        </div>
      ) : null}

      {insufficient ? (
        <div data-testid="signal-alpha-decay-insufficient" style={{
          padding: 8, color: "#92400e", fontSize: 12,
          background: "#fef3c7", borderRadius: 4, marginBottom: 10,
        }}>
          ⚠️ 표본 부족 / 입력 누락 — 평가 신뢰성 낮음
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <div data-testid="signal-alpha-decay-warnings" style={{
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
        <div data-testid="signal-alpha-decay-advice" style={{
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
        data-testid="signal-alpha-decay-toggle-detail-btn"
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
        {expanded ? "bucket 상세 접기" : "bucket 상세 펼치기"}
      </button>

      {expanded && buckets.length > 0 ? (
        <div data-testid="signal-alpha-decay-buckets" style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
        }}>
          <div style={{
            padding: "6px 10px",
            background: "var(--c-surface-2)",
            fontSize: 11, fontWeight: 700,
          }}>
            bucket ({buckets.length}개)
          </div>
          {buckets.map((b) => (
            <div key={b.label} style={{
              display: "flex", justifyContent: "space-between",
              alignItems: "center",
              padding: "6px 10px", fontSize: 12,
              borderTop: "1px solid var(--c-border)",
            }}>
              <span style={{ fontFamily: "monospace" }}>
                {b.label}
                {" · "}
                <span style={{ color: "var(--c-text-3)" }}>
                  return={b.mean_return_bps?.toFixed(2)}bps,
                  samples={b.sample_count},
                  relative={b.relative_to_t0_pct?.toFixed(1)}%
                </span>
              </span>
              <_SeverityPill severity={b.severity} />
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
        무시하고 EXPIRED 신호로 진입하는 것은 RiskManager / OrderGuard 가
        막을 책임 — 본 카드는 *판단 보조*만 제공.
      </div>
    </Card>
  );
}

export default SignalAlphaDecayCard;
