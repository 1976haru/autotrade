import { Card, SectionLabel } from "../common";
import { useThemesSummary } from "../../store/useThemes";


/**
 * Dashboard 요약 타일 (#22)
 *
 * 오늘 강한 테마 / 후보 수 / "주문 신호 아님" 배지를 한 줄로.
 * BUY/SELL 버튼 절대 렌더하지 않는다 (CLAUDE.md 절대 원칙).
 */
export function ThemeSummaryTile({ onNavigate }) {
  const { summary, loading, error } = useThemesSummary();

  if (loading && !summary) {
    return (
      <Card>
        <div data-testid="theme-summary-loading">
          <SectionLabel>📰 테마 신호</SectionLabel>
          <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
        </div>
      </Card>
    );
  }

  if (error && !summary) {
    return (
      <Card>
        <div data-testid="theme-summary-error">
          <SectionLabel>📰 테마 신호</SectionLabel>
          <div style={{ fontSize: 11, color: "#fca5a5" }}>{error}</div>
        </div>
      </Card>
    );
  }

  if (!summary) return null;

  const strongCount = summary.by_grade?.STRONG || 0;
  const watchCount  = summary.by_grade?.WATCH  || 0;

  return (
    <Card>
      <div data-testid="theme-summary-tile">
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
          <SectionLabel>📰 테마 신호</SectionLabel>
          <span data-testid="theme-summary-not-order-badge"
                style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
                  padding: "2px 6px", borderRadius: 3,
                  background: "#7f1d1d33", color: "#fca5a5",
                  border: "1px solid #ef444466",
                }}>
            주문 신호 아님
          </span>
        </div>

        {summary.total === 0 ? (
          <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.5 }}>
            아직 테마 신호가 없습니다. AI 탭에서 Mock 스캔을 실행해 보세요.
          </div>
        ) : (
          <>
            <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 6 }}>
              총 {summary.total}건 — STRONG {strongCount} · WATCH {watchCount}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 6 }}>
              {(summary.top_themes || []).map((t) => (
                <span key={t.theme}
                      data-testid={`theme-summary-top-${t.theme}`}
                      title={`${t.related_symbols?.join(", ") || ""} · ${t.provider}`}
                      style={{
                        padding: "2px 6px", borderRadius: 3,
                        background: "#14532d33", color: "#22c55e",
                        border: "1px solid #22c55e66",
                        fontSize: 10, fontWeight: 700,
                      }}>
                  {t.theme} · {t.score}
                </span>
              ))}
            </div>
          </>
        )}

        {onNavigate && (
          <button onClick={onNavigate}
                  data-testid="theme-summary-link"
                  style={{
                    background: "transparent", border: "none",
                    color: "#7dd3fc", cursor: "pointer",
                    fontFamily: "inherit", fontSize: 11, padding: 0,
                  }}>
            테마 후보 자세히 보기 →
          </button>
        )}
      </div>
    </Card>
  );
}
