/**
 * 체크리스트 #81: Strategy Registry beginner-friendly card.
 *
 * CLAUDE.md 절대 원칙:
 *   1. 본 카드는 *기존 매매 로직을 변경하지 않는다* — 메타데이터 표시만.
 *   2. broker / 주문 / route_order 호출 0건.
 *   3. 본 카드에 *전략 활성화 / 비활성화 / 파라미터 적용 / 주문 실행* 버튼 0개.
 *      운영은 기존 BotControl / LiveEngine 흐름에서.
 *   4. UI는 displayName / beginnerName 으로 표시하지만 internal_name(strategy_id)
 *      는 *항상 함께 노출* — 운영자가 로그/audit과 매핑 가능하도록.
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const RISK_COLOR = {
  low:    "#22c55e",
  medium: "#f59e0b",
  high:   "#ef4444",
};

const RISK_LABEL = {
  low:    "낮음",
  medium: "보통",
  high:   "높음",
};

const MODE_HINT = {
  paper_recommended:     "모의투자 권장",
  live_after_validation: "검증 후 실전 가능",
  live_caution:          "실전 주의",
};


function RiskBadge({ level }) {
  const color = RISK_COLOR[level] || RISK_COLOR.medium;
  const label = RISK_LABEL[level] || level || "—";
  return (
    <span
      data-testid={`strategy-risk-${level}`}
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 3,
        fontSize: 10,
        fontWeight: 700,
        color, background: `${color}15`, border: `1px solid ${color}55`,
      }}
    >
      위험도 {label}
    </span>
  );
}


function AvailabilityChip({ label, ok, testId }) {
  const color = ok ? "#22c55e" : "#94a3b8";
  return (
    <span
      data-testid={testId}
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 3,
        fontSize: 10, fontWeight: 600,
        color, background: `${color}15`, border: `1px solid ${color}55`,
        marginRight: 4,
      }}
    >
      {ok ? `✓ ${label}` : `— ${label}`}
    </span>
  );
}


function StrategyRow({ entry, expanded, onToggle }) {
  return (
    <div
      data-testid={`strategy-row-${entry.strategy_id}`}
      style={{
        borderTop: "1px solid var(--c-border)", padding: "10px 12px",
      }}
    >
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", gap: 10,
      }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 13 }}>
            {entry.display_name}{" "}
            <span style={{
              fontFamily: "monospace", fontSize: 10, color: "var(--c-text-3)",
              fontWeight: 500,
            }}>
              ({entry.strategy_id})
            </span>
          </div>
          <div style={{ fontSize: 11, color: "var(--c-text-3)", marginTop: 2 }}>
            {entry.beginner_name}
          </div>
        </div>
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <RiskBadge level={entry.risk_level} />
        </div>
      </div>

      <div style={{ marginTop: 6, fontSize: 12 }}>
        {entry.description}
      </div>

      <div style={{ marginTop: 6, fontSize: 10, color: "var(--c-text-3)" }}>
        권장: {MODE_HINT[entry.recommended_mode] || entry.recommended_mode}
        {entry.typical_hold_minutes != null
          ? ` · 일반 보유 ${entry.typical_hold_minutes}분`
          : ""}
      </div>

      <div style={{ marginTop: 6 }}>
        <AvailabilityChip
          label="백테스트"
          ok={entry.backtest_available}
          testId={`strategy-${entry.strategy_id}-backtest`}
        />
        <AvailabilityChip
          label="모의투자"
          ok={entry.paper_trading_available}
          testId={`strategy-${entry.strategy_id}-paper`}
        />
        <AvailabilityChip
          label="실전투자"
          ok={entry.live_trading_available}
          testId={`strategy-${entry.strategy_id}-live`}
        />
        <span style={{
          marginLeft: 4, fontSize: 9, color: "var(--c-text-3)",
        }}>
          {entry.live_trading_available
            ? null
            : "(실전 broker 미구현 — 모든 전략 공통)"}
        </span>
      </div>

      <button
        data-testid={`strategy-${entry.strategy_id}-toggle-detail`}
        onClick={() => onToggle(entry.strategy_id)}
        style={{
          marginTop: 8, padding: "4px 10px",
          fontSize: 10, borderRadius: 4,
          border: "1px solid var(--c-border)",
          background: "transparent", color: "var(--c-text-2)",
          cursor: "pointer",
        }}
      >
        {expanded ? "세부 정보 접기" : "세부 정보 보기"}
      </button>

      {expanded ? (
        <div data-testid={`strategy-${entry.strategy_id}-detail`} style={{
          marginTop: 8, padding: 10,
          background: "var(--c-surface-2)", borderRadius: 6,
          fontSize: 11,
        }}>
          {entry.entry_rule ? (
            <div><strong>매수 규칙:</strong> {entry.entry_rule}</div>
          ) : null}
          {entry.exit_rule ? (
            <div style={{ marginTop: 4 }}>
              <strong>매도 규칙:</strong> {entry.exit_rule}
            </div>
          ) : null}
          {entry.invalidation ? (
            <div style={{ marginTop: 4 }}>
              <strong>무효화:</strong> {entry.invalidation}
            </div>
          ) : null}
          {entry.required_regime ? (
            <div style={{ marginTop: 4 }}>
              <strong>요구 regime:</strong> {entry.required_regime}
            </div>
          ) : null}

          {entry.notes && entry.notes.length > 0 ? (
            <div style={{ marginTop: 6 }}>
              <strong>운영 노트:</strong>
              <ul style={{ marginTop: 2, paddingLeft: 16 }}>
                {entry.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {entry.parameters && entry.parameters.length > 0 ? (
            <div style={{ marginTop: 6 }}>
              <strong>파라미터 ({entry.parameters.length}):</strong>
              <div style={{
                marginTop: 2, display: "flex", flexWrap: "wrap", gap: 4,
              }}>
                {entry.parameters.map((p) => (
                  <span key={p.name} style={{
                    fontFamily: "monospace", fontSize: 10,
                    padding: "1px 6px", borderRadius: 3,
                    background: "var(--c-surface-3)",
                    border: "1px solid var(--c-border)",
                  }}>
                    {p.name}={String(p.default)}
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          <div style={{
            marginTop: 6,
            fontSize: 10, color: "var(--c-text-3)",
          }}>
            모드 매트릭스: {(entry.supported_modes || []).join(" / ") || "—"}
          </div>
        </div>
      ) : null}
    </div>
  );
}


export function StrategyRegistryCard({ registryOverride = null }) {
  const [items, setItems] = useState(registryOverride || []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState({});

  useEffect(() => {
    if (registryOverride) {
      setItems(registryOverride);
      return;
    }
    let cancelled = false;
    setLoading(true);
    backendApi.engineBeginnerRegistry()
      .then((data) => { if (!cancelled) setItems(data || []); })
      .catch((e) => { if (!cancelled) setError(e?.message || "전략 목록 조회 실패"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [registryOverride]);

  const onToggle = (id) =>
    setExpanded((m) => ({ ...m, [id]: !m[id] }));

  return (
    <Card style={{ marginBottom: 12 }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>매매 전략 목록 (Strategy Registry)</SectionLabel>
        <span data-testid="strategy-registry-count" style={{
          fontSize: 10, fontWeight: 700, color: "var(--c-text-3)",
        }}>
          {items.length}개
        </span>
      </div>

      <div
        data-testid="strategy-registry-disclaimer"
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
        ⚠️ 본 카드는 *전략 메타데이터 표시*만 합니다.
        본 화면에서 전략 활성화 / 비활성화 / 주문 실행은 *불가능*하며,
        운영은 기존 BotControl / LiveEngine / 백테스트 탭에서 진행합니다.
        표시명은 *초보자용 한글 이름*이며, internal id(괄호 표시)는
        로그·audit과 매핑하는 식별자입니다.
      </div>

      {loading ? (
        <div data-testid="strategy-registry-loading" style={{
          padding: 16, color: "var(--c-text-3)", fontSize: 12,
          textAlign: "center",
        }}>
          전략 목록 로딩 중…
        </div>
      ) : null}

      {error ? (
        <div data-testid="strategy-registry-error" style={{
          padding: 8, color: "#b91c1c", fontSize: 12,
          background: "#fee2e2", borderRadius: 4, marginBottom: 10,
        }}>
          {error}
        </div>
      ) : null}

      <div
        data-testid="strategy-registry-list"
        style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
          overflow: "hidden",
        }}
      >
        {items.map((entry) => (
          <StrategyRow
            key={entry.strategy_id}
            entry={entry}
            expanded={!!expanded[entry.strategy_id]}
            onToggle={onToggle}
          />
        ))}
      </div>

      <div style={{
        marginTop: 10, padding: "8px 10px",
        fontSize: 10, color: "var(--c-text-3)",
        background: "var(--c-surface-2)", borderRadius: 4,
      }}>
        * 실전투자(live) 미가용은 KIS 실주문 어댑터가 미구현(KIS_IS_PAPER=false
        분기 NotImplementedError) 상태이기 때문이며, *모든 전략에 공통* 입니다.
        실전 활성화는 별도 게이트(#73 / #74 / #75) 통과 + 옵트인 PR 필요합니다.
      </div>
    </Card>
  );
}

export default StrategyRegistryCard;
