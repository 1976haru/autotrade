/**
 * #4-09: Risk Veto Priority — AI 추천 vs 위험 거절 시각화.
 *
 * 본 카드는 *advisory* — Paper bridge metadata.risk_veto 의 결과를 표시.
 *
 * CLAUDE.md 절대 원칙:
 *   1. 본 카드는 *read-only* — 실거래 / 주문 / 정책 변경 버튼 0개.
 *   2. broker / 주문 / route_order 호출 0건 — backend bridge 의 결과를 표시만.
 *   3. "지금 매수" / "지금 매도" / "Place Order" / "ENABLE_*" 라벨 button 0개
 *      (테스트로 lock).
 *   4. 위험 문구: "Risk veto 우선 — Paper 주문 후보 생성 안 됨" / "투자 조언
 *      아님" / "실거래 활성화 아님" 영구 배지.
 *
 * props.report 는 BridgeReport.metadata.risk_veto 의 JSON 형태:
 * {
 *   has_global_veto: bool,
 *   global_veto_reasons: [string],
 *   global_severity: "BLOCK" | "BLOCK_NEW_ENTRY" | "NONE",
 *   decisions: [{ strategy, symbol, vetoed, reasons, reasons_label_ko,
 *                 severity, allow_exit_if_holding, detail_lines }],
 *   summary: { REASON: count, ... },
 *   vetoed_count: int,
 *   decision_count: int,
 *   headline: string,
 * }
 */

import { Card, SectionLabel } from "../common";


const SEVERITY_COLOR = {
  NONE:            "#22c55e",
  BLOCK_NEW_ENTRY: "#f59e0b",
  BLOCK:           "#ef4444",
};

const SEVERITY_LABEL = {
  NONE:            "위험 거절 없음",
  BLOCK_NEW_ENTRY: "신규 진입 차단 (EXIT 은 보유 시 허용)",
  BLOCK:           "모든 trade 차단 (EXIT 포함)",
};


function SeverityBadge({ severity }) {
  const color = SEVERITY_COLOR[severity] || SEVERITY_COLOR.NONE;
  const label = SEVERITY_LABEL[severity] || severity || "—";
  return (
    <span
      data-testid={`risk-veto-severity-${severity}`}
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


function _PriorityBadge() {
  return (
    <span
      data-testid="risk-veto-priority-badge"
      style={{
        display:       "inline-block",
        padding:       "3px 8px",
        borderRadius:  4,
        fontSize:      11,
        fontWeight:    600,
        color:         "#0f172a",
        background:    "#fde68a",
        border:        "1px solid #f59e0b",
        marginRight:   6,
      }}
    >
      Risk veto 우선 — Paper 주문 후보 생성 안 됨
    </span>
  );
}


function _DisclaimerBadge({ children, testid }) {
  return (
    <span
      data-testid={testid}
      style={{
        display:       "inline-block",
        padding:       "2px 6px",
        borderRadius:  3,
        fontSize:      10,
        fontWeight:    500,
        color:         "#64748b",
        background:    "#f1f5f9",
        border:        "1px solid #cbd5e1",
        marginRight:   4,
      }}
    >
      {children}
    </span>
  );
}


function ReasonsList({ reasons, labelsKo }) {
  if (!reasons || reasons.length === 0) {
    return <span style={{ color: "#94a3b8" }}>—</span>;
  }
  return (
    <ul
      data-testid="risk-veto-reasons-list"
      style={{ margin: 0, paddingLeft: 16, fontSize: 12 }}
    >
      {reasons.map((r, i) => (
        <li key={r}>
          <code style={{ fontSize: 11 }}>{r}</code>
          {labelsKo && labelsKo[i] ? (
            <span style={{ color: "#64748b", marginLeft: 6 }}>
              — {labelsKo[i]}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}


function DecisionTable({ decisions }) {
  const vetoed = decisions.filter((d) => d.vetoed);
  if (vetoed.length === 0) {
    return (
      <div
        data-testid="risk-veto-no-decisions"
        style={{ color: "#64748b", fontSize: 12, padding: "8px 0" }}
      >
        차단된 전략 없음 — AI 추천 흐름 진행.
      </div>
    );
  }
  return (
    <table
      data-testid="risk-veto-decision-table"
      style={{
        width:         "100%",
        borderCollapse: "collapse",
        fontSize:      12,
      }}
    >
      <thead>
        <tr style={{ borderBottom: "1px solid var(--c-border)" }}>
          <th style={{ textAlign: "left", padding: 6 }}>전략 / 심볼</th>
          <th style={{ textAlign: "left", padding: 6 }}>거절 사유</th>
          <th style={{ textAlign: "left", padding: 6 }}>강도</th>
        </tr>
      </thead>
      <tbody>
        {vetoed.map((d) => (
          <tr
            key={`${d.strategy}/${d.symbol}`}
            data-testid={`risk-veto-row-${d.strategy}-${d.symbol}`}
            style={{ borderBottom: "1px solid var(--c-border-faint)" }}
          >
            <td style={{ padding: 6 }}>
              <code>{d.strategy}</code>
              <span style={{ color: "#64748b", marginLeft: 4 }}>
                / {d.symbol}
              </span>
            </td>
            <td style={{ padding: 6 }}>
              <ReasonsList
                reasons={d.reasons}
                labelsKo={d.reasons_label_ko}
              />
            </td>
            <td style={{ padding: 6 }}>
              <SeverityBadge severity={d.severity} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}


export default function RiskVetoCard({ report }) {
  const _FooterNote = () => (
    <div
      data-testid="risk-veto-footer-note"
      style={{
        marginTop: 12,
        fontSize:  11,
        color:     "#64748b",
        borderTop: "1px solid var(--c-border-faint)",
        paddingTop: 8,
      }}
    >
      Risk veto 우선 — RiskManager / Pre-market / RiskOfficer 가 거절하면 AI
      추천이 아무리 좋아도 Paper BUY/SELL/EXIT 가 생성되지 않습니다. EXIT 은
      보유 포지션이 있을 때 위험 축소 목적으로만 허용됩니다 (EMERGENCY_STOP
      / Pre-market BLOCK 제외).
    </div>
  );

  if (!report) {
    return (
      <div data-testid="risk-veto-card">
        <Card>
          <SectionLabel>Risk Veto Priority</SectionLabel>
          <_PriorityBadge />
          <_DisclaimerBadge testid="risk-veto-disclaimer-not-advice">
            투자 조언 아님
          </_DisclaimerBadge>
          <_DisclaimerBadge testid="risk-veto-disclaimer-not-live">
            실거래 활성화 아님
          </_DisclaimerBadge>
          <_DisclaimerBadge testid="risk-veto-disclaimer-not-signal">
            주문 신호 아님
          </_DisclaimerBadge>
          <div
            data-testid="risk-veto-empty"
            style={{ color: "#64748b", fontSize: 12, marginTop: 12 }}
          >
            평가 결과 없음. Paper bridge 가 먼저 실행되어야 합니다.
          </div>
          <_FooterNote />
        </Card>
      </div>
    );
  }

  const {
    has_global_veto: hasGlobal,
    global_veto_reasons: globalReasons = [],
    global_severity: globalSeverity = "NONE",
    decisions = [],
    summary = {},
    vetoed_count: vetoedCount = 0,
    decision_count: decisionCount = 0,
    headline = "",
  } = report;

  return (
    <div data-testid="risk-veto-card">
    <Card>
      <SectionLabel>Risk Veto Priority</SectionLabel>

      <div style={{ marginBottom: 8 }}>
        <_PriorityBadge />
        <_DisclaimerBadge testid="risk-veto-disclaimer-not-advice">
          투자 조언 아님
        </_DisclaimerBadge>
        <_DisclaimerBadge testid="risk-veto-disclaimer-not-live">
          실거래 활성화 아님
        </_DisclaimerBadge>
        <_DisclaimerBadge testid="risk-veto-disclaimer-not-signal">
          주문 신호 아님
        </_DisclaimerBadge>
      </div>

      <div
        data-testid="risk-veto-headline"
        style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}
      >
        {headline || (hasGlobal
          ? "Risk veto 활성 — 모든 trade 차단"
          : "Risk veto 평가 결과")}
      </div>

      <div
        data-testid="risk-veto-stats"
        style={{
          display:    "flex",
          gap:        12,
          fontSize:   12,
          color:      "#64748b",
          marginBottom: 8,
        }}
      >
        <span data-testid="risk-veto-stats-vetoed">
          차단: <strong>{vetoedCount}</strong> / {decisionCount}
        </span>
        <span data-testid="risk-veto-stats-severity">
          강도: <SeverityBadge severity={globalSeverity} />
        </span>
      </div>

      {hasGlobal ? (
        <div
          data-testid="risk-veto-global-banner"
          style={{
            background: "#fef2f2",
            border:     "1px solid #fecaca",
            borderRadius: 4,
            padding:    8,
            marginBottom: 12,
            fontSize:   12,
            color:      "#991b1b",
          }}
        >
          <strong>AI 추천은 있었지만 Risk가 차단했습니다.</strong>
          <div style={{ marginTop: 4 }}>
            <ReasonsList reasons={globalReasons} />
          </div>
        </div>
      ) : null}

      <div data-testid="risk-veto-summary" style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
          사유별 카운트
        </div>
        {Object.keys(summary).length === 0 ? (
          <span style={{ color: "#94a3b8", fontSize: 12 }}>—</span>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12 }}>
            {Object.entries(summary).map(([reason, count]) => (
              <li
                key={reason}
                data-testid={`risk-veto-summary-${reason}`}
              >
                <code>{reason}</code> × {count}
              </li>
            ))}
          </ul>
        )}
      </div>

      <DecisionTable decisions={decisions} />

      <_FooterNote />
    </Card>
    </div>
  );
}
