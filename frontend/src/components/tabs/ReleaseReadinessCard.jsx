/**
 * 체크리스트 #92: Release Readiness Report card (read-only).
 *
 * CLAUDE.md 절대 원칙:
 *   1. 본 카드는 *advisory 리포트 표시*만 — broker / 주문 / route_order 호출 0건.
 *   2. 본 카드는 *어떤 .env / settings 도 자동 수정하지 않는다* — 안내만 제공.
 *   3. *릴리스 자동 태깅 / git tag 자동 생성 / GitHub Release 자동 publish 버튼 0개*.
 *      "다시 평가" / "markdown 복사" 버튼만 — 둘 다 UI / 클립보드 동작만 수행.
 *   4. secret 입력 form (input / textarea) 0개. KIS / Anthropic / 계좌번호
 *      원문 표시 0건.
 *
 * 표시:
 *   - verdict 헤드라인 (READY_TO_TAG / READY_WITH_CAVEATS / DO_NOT_TAG /
 *     INSUFFICIENT_DATA)
 *   - 4 invariant 배지 ("실거래 허가 아님" / "자동 .env 수정 안 함" / "release
 *     자동 태깅 안 함" / "주문 신호 아님")
 *   - 카테고리별 항목 펼치기 토글
 *   - 실패 / 경고 / 필요 조치 리스트
 *   - markdown 미리보기 (옵션)
 *
 * Props:
 *   - inputOverride: 평가 입력 dict (resultOverride 가 없을 때만 fetch)
 *   - resultOverride: 테스트용 mock 결과 — 제공 시 fetch 생략
 *   - markdownOverride: 테스트용 mock markdown
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const VERDICT_COLOR = {
  READY_TO_TAG:        "#22c55e",
  READY_WITH_CAVEATS:  "#f59e0b",
  DO_NOT_TAG:          "#ef4444",
  INSUFFICIENT_DATA:   "#94a3b8",
};

const VERDICT_HEADLINE = {
  READY_TO_TAG:        "릴리스 태그 검토 가능",
  READY_WITH_CAVEATS:  "주의 — 경고 항목 검토 후 진행",
  DO_NOT_TAG:          "릴리스 금지 — required FAIL 해결 필요",
  INSUFFICIENT_DATA:   "데이터 부족 — 입력 보강 후 재평가",
};

const SEVERITY_COLOR = {
  PASS:    "#22c55e",
  WARN:    "#f59e0b",
  FAIL:    "#ef4444",
  SKIP:    "#94a3b8",
  UNKNOWN: "#94a3b8",
};

const CATEGORY_LABEL = {
  safety_flags:      "안전 flag",
  governance_gates:  "Governance gates",
  pre_market:        "Pre-market",
  strategy_health:   "전략 건강",
  desktop_build:     "Desktop 빌드",
  system_hygiene:    "시스템 hygiene",
  documentation:    "문서",
  data_freshness:    "Data freshness",
  recent_activity:   "최근 활동",
  operator:          "운영자",
};


function _VerdictHeadline({ verdict }) {
  const color = VERDICT_COLOR[verdict] || SEVERITY_COLOR.UNKNOWN;
  const head  = VERDICT_HEADLINE[verdict] || verdict || "—";
  return (
    <div
      data-testid="release-readiness-headline"
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
        verdict = {verdict}
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
  // 4개 invariant 영구 배지 — 본 카드의 절대 원칙 표시.
  const badges = [
    { key: "live", text: "실거래 허가 아님" },
    { key: "auto", text: "자동 .env 수정 안 함" },
    { key: "tag",  text: "release 자동 태깅 안 함" },
    { key: "order", text: "주문 신호 아님" },
  ];
  return (
    <div data-testid="release-readiness-invariants"
         style={{ display: "flex", flexWrap: "wrap", gap: 4,
                   marginBottom: 10 }}>
      {badges.map(({ key, text }) => (
        <span
          key={key}
          data-testid={`release-readiness-invariant-${key}`}
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


export function ReleaseReadinessCard({
  inputOverride = null,
  resultOverride = null,
  markdownOverride = null,
}) {
  const [result, setResult]           = useState(resultOverride);
  const [markdown, setMarkdown]       = useState(markdownOverride);
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState("");
  const [expanded, setExpanded]       = useState(false);
  const [showMarkdown, setShowMarkdown] = useState(false);

  useEffect(() => {
    if (resultOverride) setResult(resultOverride);
  }, [resultOverride]);

  useEffect(() => {
    if (markdownOverride) setMarkdown(markdownOverride);
  }, [markdownOverride]);

  const onEvaluate = async () => {
    if (resultOverride) return;
    setLoading(true);
    setError("");
    try {
      const r = await backendApi.releaseReadinessEvaluate(inputOverride || {});
      setResult(r);
    } catch (e) {
      setError(e?.message || "Release readiness 평가 실패");
    } finally {
      setLoading(false);
    }
  };

  const onLoadMarkdown = async () => {
    if (markdownOverride) {
      setShowMarkdown((v) => !v);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const r = await backendApi.releaseReadinessMarkdown(inputOverride || {});
      setMarkdown(r?.markdown || "");
      setShowMarkdown(true);
    } catch (e) {
      setError(e?.message || "markdown 조회 실패");
    } finally {
      setLoading(false);
    }
  };

  const verdict   = result?.verdict || "INSUFFICIENT_DATA";
  const items     = result?.items || [];
  const failed    = result?.failed_required || [];
  const warnings  = result?.warnings || [];
  const actions   = result?.required_actions || [];
  const releaseTag = result?.target_release_tag || "";
  const releaseKind = result?.release_kind || "";

  return (
    <Card style={{ marginBottom: 12 }} accentColor={VERDICT_COLOR[verdict]}>
      <div data-testid="release-readiness-card" style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>Release Readiness Report (#92)</SectionLabel>
        <span data-testid={`release-readiness-verdict-${verdict}`} style={{
          padding: "2px 8px", borderRadius: 3,
          fontSize: 10, fontWeight: 700,
          color: VERDICT_COLOR[verdict],
          background: `${VERDICT_COLOR[verdict]}15`,
          border: `1px solid ${VERDICT_COLOR[verdict]}55`,
        }}>{verdict}</span>
      </div>

      <_VerdictHeadline verdict={verdict} />

      <_InvariantBadges />

      <div
        data-testid="release-readiness-disclaimer"
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
        ⚠️ 본 리포트는 *릴리스 가능 여부 advisory* 입니다. READY_TO_TAG 라벨은
        *실거래 활성화 / 자동 promotion 을 의미하지 않습니다*. 운영자가
        본 리포트를 직접 확인 후 별도 PR / git tag / GitHub Release 생성으로 진행.
      </div>

      {releaseTag ? (
        <div data-testid="release-readiness-target"
             style={{ fontSize: 12, marginBottom: 8 }}>
          대상 릴리스: <code>{releaseTag}</code> · 단계:{" "}
          <code>{releaseKind}</code>
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <button
          data-testid="release-readiness-evaluate-btn"
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
        <button
          data-testid="release-readiness-markdown-btn"
          onClick={onLoadMarkdown}
          disabled={loading}
          style={{
            padding: "8px 14px",
            borderRadius: 6,
            border: "1px solid var(--c-border)",
            background: "transparent",
            color: "var(--c-text-2)",
            cursor: loading ? "wait" : "pointer",
            fontSize: 12,
          }}
        >
          {showMarkdown ? "markdown 접기" : "markdown 미리보기"}
        </button>
      </div>

      {error ? (
        <div data-testid="release-readiness-error" style={{
          padding: 8, color: "#b91c1c", fontSize: 12,
          background: "#fee2e2", borderRadius: 4, marginBottom: 10,
        }}>
          {error}
        </div>
      ) : null}

      {failed.length > 0 ? (
        <div data-testid="release-readiness-failed" style={{
          border: "1px solid #ef444455", borderRadius: 6,
          marginBottom: 10, padding: 10, background: "#fee2e2",
        }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#b91c1c" }}>
            실패 항목 (required FAIL) — 릴리스 금지
          </div>
          {failed.map((f) => (
            <div key={f} style={{ fontSize: 12, marginTop: 4 }}>🛑 {f}</div>
          ))}
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <div data-testid="release-readiness-warnings" style={{
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
        <div data-testid="release-readiness-actions" style={{
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
        data-testid="release-readiness-toggle-detail-btn"
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
        <div data-testid="release-readiness-items" style={{
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
                <span style={{ color: "var(--c-text-3)" }}>
                  {CATEGORY_LABEL[it.category] || it.category}
                </span>
              </span>
              <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <span style={{ color: "var(--c-text-3)", fontSize: 10 }}>
                  {it.message}
                </span>
                <_SeverityPill severity={it.severity} />
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {showMarkdown && markdown ? (
        <pre
          data-testid="release-readiness-markdown"
          style={{
            marginTop: 10, padding: "10px 12px",
            fontSize: 11, color: "var(--c-text-2)",
            background: "var(--c-surface-2)",
            border: "1px solid var(--c-border)",
            borderRadius: 6, whiteSpace: "pre-wrap",
            overflowX: "auto",
          }}
        >
          {markdown}
        </pre>
      ) : null}

      <div style={{
        marginTop: 10, padding: "8px 10px",
        fontSize: 10, color: "var(--c-text-3)",
        background: "var(--c-surface-2)", borderRadius: 4,
      }}>
        * 본 카드는 *advisory 리포트* 입니다. 릴리스 태그 생성 / git push /
        GitHub Release publish 는 본 카드에서 *불가능*합니다 — 운영자가 별도
        흐름으로 진행하세요.
      </div>
    </Card>
  );
}

export default ReleaseReadinessCard;
