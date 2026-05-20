import { Card, SectionLabel } from "../common";
import { MarketClosedNotice } from "../common/MarketClosedNotice";
import { strategyDisplayShort, useStrategyDisplayNames } from "../../utils/strategyNames";
import { MarketPhase, currentMarketPhase } from "../../utils/marketHours";

// 85: Strategy Selection Card — 4개 단타 전략 vote → 최적 조합 advisory.
//
// **주문 신호가 아닙니다 / 승인 후보 전 단계.**
// 표시: 선택 전략 + 후보 점수 + 제외 전략 + 충돌 + MarketRegime + 최종 판단.
// 주문 / "승인 큐로 보내기" / Place Order / "전략 적용" 같은 enabling 버튼 0개
// — 본 카드는 *advisory 표시* 전용.

const _ACTION_PALETTE = {
  BUY:       { color: "#22c55e", label: "BUY 후보" },
  SELL:      { color: "#ef4444", label: "SELL (손실 방어)" },
  EXIT:      { color: "#fb923c", label: "EXIT (손실 방어)" },
  WATCH:     { color: "#fbbf24", label: "WATCH" },
  REJECT:    { color: "#94a3b8", label: "REJECT" },
  NO_SIGNAL: { color: "#475569", label: "신호 없음" },
};

const _CONFLICT_PALETTE = {
  NONE:   { color: "#22c55e", label: "충돌 없음" },
  LOW:    { color: "#84cc16", label: "충돌 낮음" },
  MEDIUM: { color: "#fbbf24", label: "충돌 보통" },
  HIGH:   { color: "#ef4444", label: "충돌 높음" },
};

const _BLOCKED_REASON_LABEL = {
  RISK_OFF_REGIME:            "RISK_OFF — BUY 차단",
  LOW_LIQUIDITY_REGIME:       "거래대금 부족 — WATCH 강등",
  ORB_COOLDOWN_ACTIVE:        "ORB cooldown 미통과",
  QUALITY_BELOW_THRESHOLD:    "신호 품질 임계 미달",
  CONFIDENCE_BELOW_THRESHOLD: "Confidence 임계 미달",
  CONFLICT_TOO_HIGH:          "충돌 너무 큼 — queue 차단",
  OPPOSING_VWAP_PRIORITY:     "VWAP 손실 방어 우선",
  NO_SIGNAL:                  "신호 없음",
  WATCH_ONLY:                 "WATCH only",
};


function _Field({ label, value, testid }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between",
                   padding: "5px 0", borderBottom: "1px solid #1e3a5c33",
                   fontSize: 11 }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span data-testid={testid} style={{ color: "#e2e8f0", fontWeight: 700 }}>
        {value}
      </span>
    </div>
  );
}


function _StrategyInline({ strategy, lookup, fallback = "—" }) {
  if (!strategy) {
    return <span style={{ color: "#475569" }}>{fallback}</span>;
  }
  const display = strategyDisplayShort(strategy, lookup);
  if (display === strategy) {
    return <span data-internal-id={strategy}>{strategy}</span>;
  }
  return (
    <span data-internal-id={strategy}>
      {display}
      <span style={{
        marginLeft: 4, fontSize: 9, fontFamily: "monospace", color: "#475569",
      }}>
        ({strategy})
      </span>
    </span>
  );
}


function _CandidateRow({ cand, lookup }) {
  const palette = _ACTION_PALETTE[cand.action] || _ACTION_PALETTE.NO_SIGNAL;
  return (
    <div data-testid={`strategy-selection-candidate-${cand.strategy_id}`}
         data-internal-id={cand.strategy_id}
         style={{ display: "flex", justifyContent: "space-between",
                   alignItems: "center", padding: "5px 8px",
                   marginBottom: 3, background: "#0c2035", borderRadius: 3,
                   borderLeft: cand.is_supporting
                     ? `3px solid ${palette.color}`
                     : "3px solid #1e3a5c",
                   fontSize: 11 }}>
      <div>
        <div style={{ color: cand.is_supporting ? "#e2e8f0" : "#94a3b8",
                       fontWeight: cand.is_supporting ? 700 : 400 }}>
          <_StrategyInline strategy={cand.strategy_id} lookup={lookup} />
          {cand.is_supporting && (
            <span style={{ marginLeft: 6, fontSize: 9, color: palette.color,
                            fontWeight: 700 }}>
              ✓ 채택
            </span>
          )}
        </div>
        <div style={{ fontSize: 9, color: "#475569", marginTop: 2 }}>
          {cand.action} · conf {cand.confidence} · quality {cand.quality_score}
        </div>
      </div>
      <div data-testid={`strategy-selection-score-${cand.strategy_id}`}
           style={{ fontSize: 10, fontWeight: 700, color: "#7dd3fc",
                     fontFamily: "monospace" }}>
        score {Number(cand.score || 0).toFixed(1)}
      </div>
    </div>
  );
}


function _BlockedRow({ entry, lookup }) {
  const reasonLabel = _BLOCKED_REASON_LABEL[entry.reason] || entry.reason;
  return (
    <div data-testid={`strategy-selection-blocked-${entry.strategy_id}`}
         data-internal-id={entry.strategy_id}
         style={{ padding: "5px 8px", marginBottom: 3,
                   background: "#0c2035", borderRadius: 3,
                   borderLeft: "3px solid #475569",
                   fontSize: 11 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span style={{ color: "#94a3b8" }}>
          <_StrategyInline strategy={entry.strategy_id} lookup={lookup} />
        </span>
        <span data-testid={`strategy-selection-blocked-reason-${entry.strategy_id}`}
              style={{ fontSize: 9, fontWeight: 700, color: "#fbbf24" }}>
          {reasonLabel}
        </span>
      </div>
      {entry.detail && (
        <div style={{ fontSize: 9, color: "#475569", marginTop: 2,
                       lineHeight: 1.5 }}>
          {entry.detail}
        </div>
      )}
    </div>
  );
}


export function StrategySelectionCard({
  report,
  loading,
  error,
  onRefresh,
  marketPhase: marketPhaseProp = null,
}) {
  const { lookup: strategyLookup } = useStrategyDisplayNames();
  // 테스트에서 marketPhaseProp 으로 주입 가능 — 미주입 시 client-side 계산.
  const marketPhase = marketPhaseProp || currentMarketPhase();
  const marketClosed = marketPhase !== MarketPhase.OPEN;

  if (loading && !report) {
    return (
      <Card>
        <SectionLabel>🎯 전략 조합 판단</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    // fix/market-closed-state-distinction: 장 종료 / 휴장 시 fetch 가 비어
    // 있더라도 *오류*로 표시하지 않고 friendly market-closed banner 만
    // 노출. 정규장이 열려 있을 때만 기존 fetch-fail 안내를 노출.
    if (marketClosed) {
      return (
        <Card>
          <div data-testid="strategy-selection-card">
            <SectionLabel>🎯 전략 조합 판단</SectionLabel>
            <MarketClosedNotice
              phase={marketPhase}
              testId="strategy-selection-market-closed"
              detail="장 종료 / 휴장 시간에는 신규 전략 vote 가 생성되지 않습니다."
              onRefresh={onRefresh || null}
            />
          </div>
        </Card>
      );
    }
    return (
      <Card>
        <SectionLabel>🎯 전략 조합 판단</SectionLabel>
        <div data-testid="strategy-selection-error"
             style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.6 }}>
          전략 조합 데이터를 아직 불러오지 못했습니다.
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
  if (!report) {
    // 데이터가 비어 있고 정규장이 닫혀 있으면 *오류로 보이지 않게* market-closed
    // banner 를 노출. 장중 비어 있는 상태는 기존처럼 null (caller가 처리).
    if (marketClosed) {
      return (
        <Card>
          <div data-testid="strategy-selection-card">
            <SectionLabel>🎯 전략 조합 판단</SectionLabel>
            <MarketClosedNotice
              phase={marketPhase}
              testId="strategy-selection-market-closed"
              detail="장 종료 / 휴장 시간에는 신규 전략 vote 가 생성되지 않습니다."
              onRefresh={onRefresh || null}
            />
          </div>
        </Card>
      );
    }
    return null;
  }

  const actionPalette = _ACTION_PALETTE[report.final_action] || _ACTION_PALETTE.NO_SIGNAL;
  const conflictPalette = _CONFLICT_PALETTE[report.conflict_level] || _CONFLICT_PALETTE.NONE;
  const candidates = report.candidates || [];
  const blocked = report.blocked || [];

  return (
    <Card>
     <div data-testid="strategy-selection-card">
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>🎯 전략 조합 판단</SectionLabel>
        <span data-testid="strategy-selection-not-order-badge" style={{
          fontSize: 9, fontWeight: 700, color: "#fbbf24",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #fbbf2455", background: "#fbbf2415",
        }}>
          주문 아님 · 승인 후보 전 단계
        </span>
      </div>

      {/* 안내 — 본 카드는 advisory only */}
      <div data-testid="strategy-selection-notice"
           style={{ marginBottom: 8, padding: "6px 8px",
                     background: "#0c2035", borderRadius: 4,
                     fontSize: 10, color: "#94a3b8", lineHeight: 1.5 }}>
        본 카드는 시장 상태와 4개 단타 전략의 신호를 종합한 *advisory* 입니다.
        실제 주문은 사람의 명시적 승인을 거쳐 RiskManager → PermissionGate →
        OrderExecutor 흐름에서만 발생합니다 — 본 카드에는 주문 / 적용 / 활성화
        같은 enabling 버튼이 없습니다.
      </div>

      {/* 핵심 상태 */}
      <_Field
        label="종목"
        value={
          <span data-testid="strategy-selection-symbol"
                style={{ fontFamily: "monospace" }}>
            {report.symbol || "—"}
          </span>
        }
      />
      <_Field
        label="MarketRegime"
        value={
          <span data-testid="strategy-selection-regime">
            {report.market_regime || "—"}
          </span>
        }
      />
      <_Field
        label="최종 판단"
        value={
          <span data-testid="strategy-selection-final-action"
                style={{ color: actionPalette.color }}>
            {actionPalette.label} ({report.final_action})
          </span>
        }
      />
      <_Field
        label="선택 전략"
        value={
          <span data-testid="strategy-selection-selected">
            <_StrategyInline strategy={report.selected_strategy}
                              lookup={strategyLookup}
                              fallback="(없음)" />
          </span>
        }
      />
      <_Field
        label="Confidence"
        value={
          <span data-testid="strategy-selection-confidence">
            {report.confidence} / 100
          </span>
        }
      />
      <_Field
        label="Quality"
        value={
          <span data-testid="strategy-selection-quality">
            {report.quality_score} / 100
          </span>
        }
      />
      <_Field
        label="충돌"
        value={
          <span data-testid="strategy-selection-conflict"
                style={{ color: conflictPalette.color }}>
            {conflictPalette.label}
          </span>
        }
      />
      <_Field
        label="후보 자격"
        value={
          <span data-testid="strategy-selection-qualified"
                style={{ color: report.candidate_qualified ? "#22c55e" : "#94a3b8" }}>
            {report.candidate_qualified ? "있음" : "없음"}
          </span>
        }
      />

      {/* 후보 점수 목록 */}
      {candidates.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4,
                         fontWeight: 700 }}>
            후보 전략 ({candidates.length}건)
          </div>
          <div data-testid="strategy-selection-candidates">
            {candidates.map((c) => (
              <_CandidateRow key={c.strategy_id} cand={c}
                              lookup={strategyLookup} />
            ))}
          </div>
        </div>
      )}

      {/* 제외 전략 + 사유 */}
      {blocked.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4,
                         fontWeight: 700 }}>
            제외 전략 ({blocked.length}건)
          </div>
          <div data-testid="strategy-selection-blocked">
            {blocked.map((b, i) => (
              <_BlockedRow key={`${b.strategy_id}-${i}`} entry={b}
                            lookup={strategyLookup} />
            ))}
          </div>
        </div>
      )}

      {/* 사유 / risk note */}
      {(report.reasons || []).length > 0 && (
        <div style={{ marginTop: 10, padding: "6px 8px",
                       background: "#0c2035", borderRadius: 4,
                       fontSize: 10, color: "#94a3b8", lineHeight: 1.6 }}>
          <div style={{ fontWeight: 700, color: "#7dd3fc", marginBottom: 3 }}>
            사유
          </div>
          {report.reasons.slice(0, 5).map((r, i) => (
            <div key={i} data-testid={`strategy-selection-reason-${i}`}>· {r}</div>
          ))}
        </div>
      )}
      {(report.risk_notes || []).length > 0 && (
        <div style={{ marginTop: 6, padding: "6px 8px",
                       background: "#3b1f0533",
                       border: "1px solid #fbbf2444",
                       borderRadius: 4,
                       fontSize: 10, color: "#fbbf24", lineHeight: 1.6 }}>
          <div style={{ fontWeight: 700, marginBottom: 3 }}>
            ⚠ Risk note
          </div>
          {report.risk_notes.slice(0, 5).map((r, i) => (
            <div key={i} data-testid={`strategy-selection-risk-${i}`}>· {r}</div>
          ))}
        </div>
      )}

      {onRefresh && (
        <div style={{ marginTop: 10, textAlign: "right" }}>
          <button data-testid="strategy-selection-refresh"
                  onClick={onRefresh}
                  style={{
            fontSize: 10, padding: "3px 8px", background: "#0c2035",
            border: "1px solid #1e3a5c", borderRadius: 3,
            cursor: "pointer", color: "#7dd3fc",
          }}>↻ 새로고침</button>
        </div>
      )}
     </div>
    </Card>
  );
}
