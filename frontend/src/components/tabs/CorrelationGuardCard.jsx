/**
 * 체크리스트 #78: Correlation Guard preview card.
 *
 * CLAUDE.md 절대 원칙:
 *   1. 본 카드는 *preview*만 — 실제 주문 제출 / 정책 변경 버튼 0개.
 *   2. broker / 주문 / route_order 호출 0건.
 *   3. SELL은 가드 우회 (SKIP_NON_BUY) — 운영자에게 명확히 표시.
 *   4. 위험 문구: "본 카드는 사전 검사이며 실제 주문은 RiskManager + PermissionGate 경유."
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const VERDICT_COLOR = {
  PASS:         "#22c55e",
  WARN:         "#f59e0b",
  REJECT:       "#ef4444",
  SKIP_NON_BUY: "#94a3b8",
};

const VERDICT_LABEL = {
  PASS:         "통과",
  WARN:         "주의",
  REJECT:       "차단",
  SKIP_NON_BUY: "SELL — 가드 우회",
};


function VerdictBadge({ verdict }) {
  const color = VERDICT_COLOR[verdict] || VERDICT_COLOR.SKIP_NON_BUY;
  const label = VERDICT_LABEL[verdict] || verdict || "—";
  return (
    <span
      data-testid={`correlation-guard-verdict-${verdict}`}
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


function ExposureTable({ title, exposure, testId }) {
  const entries = Object.entries(exposure || {}).sort((a, b) => b[1] - a[1]);
  return (
    <div
      data-testid={testId}
      style={{
        border: "1px solid var(--c-border)", borderRadius: 6,
      }}
    >
      <div style={{
        padding: "6px 10px",
        background: "var(--c-surface-2)",
        fontSize: 11, fontWeight: 700,
      }}>
        {title}
      </div>
      {entries.length === 0 ? (
        <div style={{ padding: "6px 10px", fontSize: 11, color: "var(--c-text-3)" }}>
          (보유 노출 없음)
        </div>
      ) : entries.map(([k, v]) => (
        <div key={k} style={{
          display: "flex", justifyContent: "space-between",
          padding: "4px 10px", fontSize: 12,
          borderTop: "1px solid var(--c-border)",
        }}>
          <span style={{ fontFamily: "monospace" }}>{k}</span>
          <span style={{ fontWeight: 600 }}>{Number(v).toLocaleString()} 원</span>
        </div>
      ))}
    </div>
  );
}


export function CorrelationGuardCard({ inputOverride = null, resultOverride = null }) {
  const [result, setResult] = useState(resultOverride);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (resultOverride) setResult(resultOverride);
  }, [resultOverride]);

  const onPreview = async () => {
    if (resultOverride) return;
    setLoading(true);
    setError("");
    try {
      const r = await backendApi.correlationGuardPreview(
        inputOverride || {
          candidate: { symbol: "x", side: "BUY", notional: 0,
                       meta: { symbol: "x" } },
        },
      );
      setResult(r);
    } catch (e) {
      setError(e?.message || "Correlation Guard preview 실패");
    } finally {
      setLoading(false);
    }
  };

  const verdict   = result?.verdict || "PASS";
  const blocked   = result?.blocked_reasons || [];
  const warnings  = result?.warnings || [];
  const sectorExp = result?.sector_exposure || {};
  const themeExp  = result?.theme_exposure || {};
  const projSym   = result?.projected_sector_symbol_count;
  const projSecExp = result?.projected_sector_exposure;

  return (
    <Card style={{ marginBottom: 12 }} accentColor={VERDICT_COLOR[verdict]}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>Correlation Guard — sector / theme 사전 검사</SectionLabel>
        <VerdictBadge verdict={verdict} />
      </div>

      <div
        data-testid="correlation-guard-disclaimer"
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
        ⚠️ 본 카드는 *사전 검사 preview*이며, 실제 주문은 여전히 RiskManager +
        PermissionGate + OrderExecutor 를 통과해야 합니다. SELL/EXIT 은 *리스크
        축소* 목적이므로 본 가드가 차단하지 않습니다 (SKIP_NON_BUY).
      </div>

      <button
        data-testid="correlation-guard-preview-btn"
        onClick={onPreview}
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
        {loading ? "평가 중…" : "Correlation 사전 검사"}
      </button>

      {error ? (
        <div
          data-testid="correlation-guard-error"
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
          <div data-testid="correlation-guard-projection" style={{
            border: "1px solid var(--c-border)", borderRadius: 6,
            marginBottom: 10, padding: 10, fontSize: 12,
            display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6,
          }}>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>대상 sector</div>
              <div style={{ fontWeight: 700, fontFamily: "monospace" }}>
                {result.projected_sector || "—"}
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>예상 sector 종목 수</div>
              <div style={{ fontWeight: 700 }}>{projSym ?? 0}</div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>예상 sector 노출</div>
              <div style={{ fontWeight: 700 }}>
                {Number(projSecExp || 0).toLocaleString()} 원
              </div>
            </div>
          </div>

          <div style={{
            display: "grid", gridTemplateColumns: "1fr 1fr",
            gap: 8, marginBottom: 10,
          }}>
            <ExposureTable
              title="현재 sector 노출"
              exposure={sectorExp}
              testId="correlation-guard-sector"
            />
            <ExposureTable
              title="현재 theme 노출"
              exposure={themeExp}
              testId="correlation-guard-theme"
            />
          </div>

          {blocked.length > 0 ? (
            <div data-testid="correlation-guard-blocked-list" style={{
              border: "1px solid #ef444455", borderRadius: 6,
              marginBottom: 10, padding: 10, background: "#fee2e2",
            }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#b91c1c" }}>
                차단 사유 (REJECT)
              </div>
              {blocked.map((b, i) => (
                <div key={i} style={{ fontSize: 12, marginTop: 4 }}>🛑 {b}</div>
              ))}
            </div>
          ) : null}

          {warnings.length > 0 ? (
            <div data-testid="correlation-guard-warning-list" style={{
              border: "1px solid #f59e0b55", borderRadius: 6,
              padding: 10, background: "#fef3c7",
            }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#92400e" }}>
                주의 (WARN)
              </div>
              {warnings.map((w, i) => (
                <div key={i} style={{ fontSize: 12, marginTop: 4 }}>⚠️ {w}</div>
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
        * 본 카드는 *사전 검사*만 합니다. 실제 주문 / 정책 변경은 RiskManager
        + PermissionGate + OrderExecutor 흐름을 따릅니다.
      </div>
    </Card>
  );
}

export default CorrelationGuardCard;
