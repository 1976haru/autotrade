import { Card, SectionLabel } from "../common";
import { useWatchlistSummary } from "../../store/useWatchlists";


/**
 * Dashboard 요약 타일 (#18)
 *
 * - active watchlist 이름 + 종목 수 + top 5 symbol 표시
 * - "관심종목 관리로 이동" 링크 (Settings 탭으로 라우팅)
 *
 * Watchlist는 universe 후보군이라 Dashboard 본화면 1차에선 절제된 표시만 한다.
 */
export function WatchlistSummaryTile({ onNavigate }) {
  const { summary, loading, error } = useWatchlistSummary();

  if (loading && !summary) {
    return (
      <Card>
        <div data-testid="watchlist-summary-loading">
          <SectionLabel>📋 관심종목</SectionLabel>
          <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
        </div>
      </Card>
    );
  }
  if (error && !summary) {
    return (
      <Card>
        <div data-testid="watchlist-summary-error">
          <SectionLabel>📋 관심종목</SectionLabel>
          <div style={{ fontSize: 11, color: "#fca5a5" }}>{error}</div>
        </div>
      </Card>
    );
  }
  if (!summary) return null;

  const active = summary.active;
  const topSymbols = summary.top_symbols || [];

  return (
    <Card>
      <div data-testid="watchlist-summary-tile">
      <SectionLabel>📋 관심종목</SectionLabel>

      {!active ? (
        <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.6 }}>
          활성 관심종목 목록이 없습니다.{" "}
          {summary.watchlist_count > 0
            ? `등록된 목록 ${summary.watchlist_count}개 중 하나를 활성화하세요.`
            : "Settings 탭에서 첫 목록을 만들어 보세요."}
          {onNavigate && (
            <button onClick={onNavigate}
                    data-testid="watchlist-summary-link"
                    style={{
                      marginLeft: 6, background: "transparent",
                      border: "none", color: "#7dd3fc",
                      cursor: "pointer", fontFamily: "inherit",
                      fontSize: 11, padding: 0,
                    }}>
              관심종목 관리로 이동 →
            </button>
          )}
        </div>
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "baseline",
                        justifyContent: "space-between", marginBottom: 6 }}>
            <span style={{ fontSize: 13, fontWeight: 700, color: "#cbd5e1" }}>
              {active.name}
            </span>
            <span style={{ fontSize: 11, color: "#475569" }}>
              {summary.active_item_count} / {summary.max_items}
            </span>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 6 }}>
            {topSymbols.length === 0 && (
              <span style={{ fontSize: 11, color: "#475569" }}>
                활성 목록에 종목이 없습니다.
              </span>
            )}
            {topSymbols.map((s) => (
              <span key={s}
                    data-testid={`watchlist-summary-symbol-${s}`}
                    style={{
                      padding: "2px 6px", borderRadius: 3,
                      background: "#02101e", border: "1px solid #1a3a5c",
                      fontSize: 11, color: "#cbd5e1", fontFamily: "monospace",
                    }}>
                {s}
              </span>
            ))}
            {summary.active_item_count > topSymbols.length && (
              <span style={{ fontSize: 10, color: "#475569" }}>
                외 {summary.active_item_count - topSymbols.length}종
              </span>
            )}
          </div>
          {onNavigate && (
            <button onClick={onNavigate}
                    data-testid="watchlist-summary-link"
                    style={{
                      background: "transparent", border: "none",
                      color: "#7dd3fc", cursor: "pointer",
                      fontFamily: "inherit", fontSize: 11, padding: 0,
                    }}>
              관심종목 관리로 이동 →
            </button>
          )}
        </>
      )}
      </div>
    </Card>
  );
}
