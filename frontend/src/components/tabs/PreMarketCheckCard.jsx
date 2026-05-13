/**
 * 체크리스트 #80: Pre-market Checklist card (read-only).
 *
 * CLAUDE.md 절대 원칙:
 *   1. 본 카드는 *점검 결과 표시*만 — broker / 주문 / route_order 호출 0건.
 *   2. 본 카드는 *자동매매 시작 / mode 변경 / flag 토글 버튼 0개*.
 *      "다시 점검" / "확인했습니다" 버튼만 — 확인은 UI 상태만 갱신.
 *   3. required FAIL 이 있으면 start_allowed=False — UI 의 *어떤 버튼도*
 *      이 결과를 우회하지 않는다.
 *
 * 표시:
 *   - 모바일 헤드라인: "오늘 자동운용 가능" / "주의 필요" / "시작 금지"
 *   - PASS / WARN / FAIL 항목 목록
 *   - 실패 / 경고 / 필요 조치
 *   - 수동 확인 버튼 ("확인했습니다") — 기록 / 표시만, 실제 start_allowed 불변
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const VERDICT_COLOR = {
  READY_TO_START:         "#22c55e",
  WARN_BUT_START_ALLOWED: "#f59e0b",
  DO_NOT_START:           "#ef4444",
};

const VERDICT_HEADLINE = {
  READY_TO_START:         "오늘 자동운용 가능",
  WARN_BUT_START_ALLOWED: "주의 필요 — 운영자 검토 후 시작",
  DO_NOT_START:           "시작 금지 — required FAIL 해결 필요",
};

const STATUS_COLOR = {
  PASS:    "#22c55e",
  WARN:    "#f59e0b",
  FAIL:    "#ef4444",
  SKIP:    "#94a3b8",
  UNKNOWN: "#94a3b8",
};


function HeadlineBanner({ verdict, startAllowed }) {
  const color = VERDICT_COLOR[verdict] || STATUS_COLOR.UNKNOWN;
  const head  = VERDICT_HEADLINE[verdict] || verdict || "—";
  return (
    <div
      data-testid="pre-market-headline"
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
      <div style={{ fontSize: 11, marginTop: 4, fontWeight: 600 }}>
        start_allowed = {String(!!startAllowed)}
      </div>
    </div>
  );
}


function StatusPill({ status }) {
  const color = STATUS_COLOR[status] || STATUS_COLOR.UNKNOWN;
  return (
    <span style={{
      display: "inline-block", minWidth: 48, textAlign: "center",
      padding: "2px 6px", borderRadius: 3,
      fontSize: 10, fontWeight: 700, fontFamily: "monospace",
      color, background: `${color}15`, border: `1px solid ${color}55`,
    }}>
      {status}
    </span>
  );
}


export function PreMarketCheckCard({
  mode = "SIMULATION",
  inputOverride = null,
  resultOverride = null,
}) {
  const [result, setResult]   = useState(resultOverride);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState("");
  const [expanded, setExpanded] = useState(false);
  const [ackUi, setAckUi]       = useState(false);

  useEffect(() => {
    if (resultOverride) setResult(resultOverride);
  }, [resultOverride]);

  const onCheck = async () => {
    if (resultOverride) return;
    setLoading(true);
    setError("");
    try {
      // POST 로 운영자 명시 입력이 있으면 그걸로, 아니면 GET dry-run.
      let r;
      if (inputOverride) {
        r = await backendApi.preMarketCheckPost({ ...inputOverride, mode });
      } else {
        r = await backendApi.preMarketCheckGet({ mode });
      }
      setResult(r);
    } catch (e) {
      setError(e?.message || "Pre-market 점검 실패");
    } finally {
      setLoading(false);
    }
  };

  const onAck = () => {
    // UI 상태만 — 서버 start_allowed 는 변경되지 않는다.
    setAckUi(true);
  };

  const verdict       = result?.verdict || "DO_NOT_START";
  const startAllowed  = !!result?.start_allowed;
  const items         = result?.items || [];
  const failed        = result?.failed_required || [];
  const warnings      = result?.warnings || [];
  const actions       = result?.required_actions || [];

  return (
    <Card style={{ marginBottom: 12 }} accentColor={VERDICT_COLOR[verdict]}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>Pre-market Checklist (#80)</SectionLabel>
        <span data-testid={`pre-market-verdict-${verdict}`} style={{
          padding: "2px 8px", borderRadius: 3,
          fontSize: 10, fontWeight: 700,
          color: VERDICT_COLOR[verdict],
          background: `${VERDICT_COLOR[verdict]}15`,
          border: `1px solid ${VERDICT_COLOR[verdict]}55`,
        }}>{verdict}</span>
      </div>

      <HeadlineBanner verdict={verdict} startAllowed={startAllowed} />

      <div
        data-testid="pre-market-disclaimer"
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
        ⚠️ 본 카드는 *자동매매 시작 전 안전 점검*입니다.
        주문 / 모드 / 안전 플래그를 변경하지 않습니다. "확인했습니다"
        버튼은 UI 상태 기록일 뿐이며, **required FAIL 을 우회하지 않습니다.**
        실제 자동매매 시작은 BotControl 탭에서 별도로 진행하며, 본 점검
        결과를 반드시 참고하세요.
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <button
          data-testid="pre-market-recheck-btn"
          onClick={onCheck}
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
          {loading ? "점검 중…" : "다시 점검"}
        </button>
        <button
          data-testid="pre-market-ack-btn"
          onClick={onAck}
          disabled={ackUi}
          style={{
            padding: "8px 14px",
            borderRadius: 6,
            border: "1px solid var(--c-border-strong)",
            background: ackUi ? "var(--c-surface-3)" : "var(--c-surface-2)",
            color: ackUi ? "var(--c-text-3)" : "var(--c-text)",
            cursor: ackUi ? "default" : "pointer",
            fontSize: 12,
          }}
        >
          {ackUi ? "확인됨 (UI 상태만)" : "확인했습니다"}
        </button>
      </div>

      {error ? (
        <div data-testid="pre-market-error" style={{
          padding: 8, color: "#b91c1c", fontSize: 12,
          background: "#fee2e2", borderRadius: 4, marginBottom: 10,
        }}>
          {error}
        </div>
      ) : null}

      {failed.length > 0 ? (
        <div data-testid="pre-market-failed" style={{
          border: "1px solid #ef444455", borderRadius: 6,
          marginBottom: 10, padding: 10, background: "#fee2e2",
        }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#b91c1c" }}>
            실패 항목 (required FAIL) — 시작 금지
          </div>
          {failed.map((f) => (
            <div key={f} style={{ fontSize: 12, marginTop: 4 }}>🛑 {f}</div>
          ))}
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <div data-testid="pre-market-warnings" style={{
          border: "1px solid #f59e0b55", borderRadius: 6,
          marginBottom: 10, padding: 10, background: "#fef3c7",
        }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#92400e" }}>
            경고 (WARN) — 운영자 검토 권장
          </div>
          {warnings.map((w, i) => (
            <div key={i} style={{ fontSize: 12, marginTop: 4 }}>⚠️ {w}</div>
          ))}
        </div>
      ) : null}

      {actions.length > 0 ? (
        <div data-testid="pre-market-actions" style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
          marginBottom: 10, padding: 10,
        }}>
          <div style={{ fontSize: 11, fontWeight: 700 }}>필요 조치</div>
          {actions.map((a, i) => (
            <div key={i} style={{ fontSize: 12, marginTop: 4 }}>📝 {a}</div>
          ))}
        </div>
      ) : null}

      <button
        data-testid="pre-market-toggle-detail-btn"
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
        {expanded ? "세부 항목 접기" : "세부 항목 펼치기"}
      </button>

      {expanded && items.length > 0 ? (
        <div data-testid="pre-market-items" style={{
          border: "1px solid var(--c-border)", borderRadius: 6,
        }}>
          <div style={{
            padding: "6px 10px",
            background: "var(--c-surface-2)",
            fontSize: 11, fontWeight: 700,
          }}>
            점검 항목 ({items.length}개)
          </div>
          {items.map((it) => (
            <div key={it.name} style={{
              display: "flex", justifyContent: "space-between",
              alignItems: "center",
              padding: "6px 10px", fontSize: 12,
              borderTop: "1px solid var(--c-border)",
            }}>
              <span style={{ fontFamily: "monospace" }}>
                {it.name}
                {it.required ? null : (
                  <span style={{
                    marginLeft: 6, fontSize: 9, color: "var(--c-text-3)",
                    fontWeight: 700,
                  }}>(optional)</span>
                )}
                {" · "}
                <span style={{ color: "var(--c-text-3)" }}>{it.category}</span>
              </span>
              <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <span style={{ color: "var(--c-text-3)", fontSize: 10 }}>
                  {it.message}
                </span>
                <StatusPill status={it.status} />
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {ackUi ? (
        <div
          data-testid="pre-market-ack-note"
          style={{
            marginTop: 10, padding: "8px 10px",
            fontSize: 11, color: "#92400e",
            background: "#fef3c7", borderRadius: 4,
          }}
        >
          ⚠️ "확인했습니다" 가 기록되었습니다 (UI 상태만). required FAIL 이
          있으면 본 ack 와 무관하게 자동매매 시작이 차단됩니다.
        </div>
      ) : null}

      <div style={{
        marginTop: 10, padding: "8px 10px",
        fontSize: 10, color: "var(--c-text-3)",
        background: "var(--c-surface-2)", borderRadius: 4,
      }}>
        * 본 카드는 *안전 점검*만 합니다. 자동매매 시작 / mode 변경 / flag
        토글은 본 카드에서 *불가능*합니다.
      </div>
    </Card>
  );
}

export default PreMarketCheckCard;
