/**
 * #4-10: Paper AI 판단 로그 카드 — 최근 결정 시계열 표시.
 *
 * 본 카드는 *read-only* — Paper AI 판단의 영구 기록만 표시.
 *
 * CLAUDE.md 절대 원칙 (테스트로 lock):
 *   1. 본 카드는 *read-only* — 실거래 / 주문 / 정책 변경 버튼 0개.
 *   2. broker / route_order / OrderExecutor 호출 0건 — backend read-only API 만.
 *   3. "지금 매수" / "지금 매도" / "Place Order" / "실거래 시작" / "ENABLE_*"
 *      라벨 button 0개.
 *   4. BUY/SELL/EXIT 은 로그 *라벨* 로만 표시 — 버튼 0개.
 *   5. 영구 배지: "Paper 전용" / "실거래 아님" / "투자 조언 아님" /
 *      "주문 신호 아님".
 *
 * props.entries:
 *   GET /api/auto-paper/decision-log 의 응답 .entries 배열. 또는 직접 주입.
 * props.summary:
 *   GET 응답의 .summary 객체 (by_action / veto_count / sizing_reduced).
 * props.onRefresh: 새로고침 콜백.
 */

import { useEffect, useState } from "react";

import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const ACTION_COLOR = {
  BUY:   "#22c55e",
  SELL:  "#fbbf24",
  HOLD:  "#94a3b8",
  EXIT:  "#6b7280",
  NO_OP: "#cbd5e1",
};

const ACTION_LABEL = {
  BUY:   "매수 (로그)",
  SELL:  "매도 (로그)",
  HOLD:  "보류",
  EXIT:  "청산 (로그)",
  NO_OP: "변경 없음",
};


function ActionLabel({ action }) {
  const color = ACTION_COLOR[action] || "#cbd5e1";
  const label = ACTION_LABEL[action] || action;
  return (
    <span
      data-testid={`decision-log-action-${action}`}
      style={{
        display:       "inline-block",
        padding:       "2px 8px",
        borderRadius:  4,
        fontSize:      11,
        fontWeight:    700,
        color:         "#fff",
        background:    color,
      }}
    >
      {label}
    </span>
  );
}


function PaperOnlyBadge() {
  return (
    <span
      data-testid="decision-log-paper-only-badge"
      style={{
        display:       "inline-block",
        padding:       "3px 8px",
        borderRadius:  4,
        fontSize:      11,
        fontWeight:    600,
        color:         "#0f172a",
        background:    "#bef264",
        border:        "1px solid #65a30d",
        marginRight:   6,
      }}
    >
      Paper 전용 · 실거래 아님
    </span>
  );
}


function DisclaimerBadge({ children, testid }) {
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


function VetoChip({ entry }) {
  if (!entry.risk_veto) return null;
  return (
    <span
      data-testid={`decision-log-veto-${entry.decision_id}`}
      style={{
        display:       "inline-block",
        marginLeft:    6,
        padding:       "1px 6px",
        borderRadius:  3,
        fontSize:      10,
        fontWeight:    600,
        color:         "#991b1b",
        background:    "#fee2e2",
        border:        "1px solid #fca5a5",
      }}
    >
      Risk veto: {(entry.risk_veto_reasons || []).join(", ") || "—"}
    </span>
  );
}


function RiskFlagsChips({ flags }) {
  if (!flags || flags.length === 0) return null;
  return (
    <span data-testid="decision-log-risk-flags" style={{ marginLeft: 6 }}>
      {flags.map((f) => (
        <span
          key={f}
          style={{
            display:       "inline-block",
            marginRight:   3,
            padding:       "1px 5px",
            borderRadius:  3,
            fontSize:      10,
            color:         "#92400e",
            background:    "#fef3c7",
            border:        "1px solid #fcd34d",
          }}
        >
          {f}
        </span>
      ))}
    </span>
  );
}


function EntryRow({ entry }) {
  return (
    <div
      data-testid={`decision-log-entry-${entry.decision_id}`}
      style={{
        padding:      "6px 0",
        borderBottom: "1px solid var(--c-border-faint)",
        fontSize:     12,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <code style={{ color: "var(--c-text-3)", fontSize: 11 }}>
          {(entry.timestamp || "").slice(11, 19)}
        </code>
        <ActionLabel action={entry.decision_action} />
        <strong>{entry.strategy || "(unknown)"}</strong>
        <span style={{ color: "#64748b" }}>· {entry.symbol}</span>
        {entry.confidence != null ? (
          <span
            data-testid={`decision-log-confidence-${entry.decision_id}`}
            style={{ color: "#64748b", fontSize: 11 }}
          >
            conf={entry.confidence}
          </span>
        ) : null}
        {entry.position_size > 0 ? (
          <span
            data-testid={`decision-log-position-${entry.decision_id}`}
            style={{ color: "#1e3a8a", fontSize: 11, fontWeight: 600 }}
          >
            size={entry.position_size}
          </span>
        ) : null}
        <VetoChip entry={entry} />
        <RiskFlagsChips flags={entry.risk_flags} />
      </div>
      {entry.reason ? (
        <div style={{ marginLeft: 4, marginTop: 2, color: "#475569" }}>
          {entry.reason}
        </div>
      ) : null}
      <div
        style={{ marginLeft: 4, marginTop: 2,
                 fontSize: 11, color: "#94a3b8" }}
      >
        regime={entry.market_regime || "—"}
        {entry.sizing_verdict ? ` · sizing=${entry.sizing_verdict}` : ""}
        {entry.paper_fill_status ? ` · fill=${entry.paper_fill_status}` : ""}
      </div>
    </div>
  );
}


function SummaryStrip({ summary }) {
  if (!summary) return null;
  const byAction = summary.by_action || {};
  const totalActions = Object.values(byAction).reduce((a, b) => a + b, 0);
  return (
    <div
      data-testid="decision-log-summary"
      style={{
        display:    "flex",
        gap:        12,
        fontSize:   12,
        color:      "#64748b",
        marginBottom: 8,
      }}
    >
      <span data-testid="decision-log-total">총 {totalActions}건</span>
      {Object.entries(byAction).map(([action, count]) => (
        <span
          key={action}
          data-testid={`decision-log-by-action-${action}`}
        >
          {action}: <strong>{count}</strong>
        </span>
      ))}
      {summary.veto_count > 0 ? (
        <span data-testid="decision-log-veto-count" style={{ color: "#991b1b" }}>
          Risk veto: <strong>{summary.veto_count}</strong>
        </span>
      ) : null}
    </div>
  );
}


export default function PaperDecisionLogCard({
  entries: initialEntries,
  summary: initialSummary,
  autoload = false,
  apiClient = null,
}) {
  const [entries, setEntries] = useState(initialEntries || null);
  const [summary, setSummary] = useState(initialSummary || null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function loadFromApi() {
    setLoading(true);
    setError(null);
    try {
      const client = apiClient || backendApi;
      const r = await client.get("/api/auto-paper/decision-log?limit=20");
      setEntries(r.entries || []);
      setSummary(r.summary || {});
    } catch (e) {
      setError(e?.message || "load_failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (autoload && initialEntries == null) {
      loadFromApi();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div data-testid="paper-decision-log-card">
      <Card>
        <SectionLabel>Paper AI 판단 로그</SectionLabel>

        <div style={{ marginBottom: 8 }}>
          <PaperOnlyBadge />
          <DisclaimerBadge testid="decision-log-disclaimer-not-advice">
            투자 조언 아님
          </DisclaimerBadge>
          <DisclaimerBadge testid="decision-log-disclaimer-not-signal">
            주문 신호 아님
          </DisclaimerBadge>
          <DisclaimerBadge testid="decision-log-disclaimer-not-live">
            실거래 활성화 아님
          </DisclaimerBadge>
        </div>

        <SummaryStrip summary={summary} />

        {loading ? (
          <div data-testid="decision-log-loading" style={{ fontSize: 12 }}>
            불러오는 중...
          </div>
        ) : null}

        {error ? (
          <div
            data-testid="decision-log-error"
            style={{ color: "#991b1b", fontSize: 12, marginBottom: 8 }}
          >
            로그 로드 실패: {error}
          </div>
        ) : null}

        {entries == null ? (
          <div
            data-testid="decision-log-empty-uninit"
            style={{ color: "#64748b", fontSize: 12 }}
          >
            로그 미로드. 새로고침을 눌러 최근 판단을 표시합니다.
          </div>
        ) : entries.length === 0 ? (
          <div
            data-testid="decision-log-empty"
            style={{ color: "#64748b", fontSize: 12 }}
          >
            기록된 Paper AI 판단이 없습니다.
          </div>
        ) : (
          <div data-testid="decision-log-list">
            {entries.map((e) => (
              <EntryRow key={e.decision_id || `${e.timestamp}-${e.symbol}`}
                        entry={e} />
            ))}
          </div>
        )}

        <div
          data-testid="decision-log-footer-note"
          style={{
            marginTop: 12,
            fontSize:  11,
            color:     "#64748b",
            borderTop: "1px solid var(--c-border-faint)",
            paddingTop: 8,
          }}
        >
          본 로그는 mode=PAPER 의 영구 기록입니다. *실거래 주문이 아니며*
          broker 호출 0건. BUY/SELL/EXIT 은 로그 라벨로만 표시되며 본 카드에서
          어떠한 주문 / 실거래 활성화 버튼도 노출되지 않습니다.
        </div>
      </Card>
    </div>
  );
}
