import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

// 53: News / Trend Agent card.
//
// theme_signals 기반 후보 필터 요약. **주문 신호가 아님** — BUY/SELL/HOLD를
// 표시하지 않으며, 매수/매도 버튼 0개. 운영자가 후보를 한눈에 보기 위한
// read-only context.

const _ACTION_PALETTE = {
  MONITOR:       { color: "#22c55e", label: "모니터링" },
  RESEARCH:      { color: "#7dd3fc", label: "검토 권장" },
  CAUTION:       { color: "#fbbf24", label: "주의" },
  OVERHEAT_WARN: { color: "#ef4444", label: "과열 경고" },
  NO_DATA:       { color: "#94a3b8", label: "데이터 없음" },
};


function _Field({ label, value }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between",
                   padding: "5px 0", borderBottom: "1px solid #1e3a5c33",
                   fontSize: 11 }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span style={{ color: "#e2e8f0", fontWeight: 700 }}>{value}</span>
    </div>
  );
}


function _ChipList({ items, color, dataTestId }) {
  if (!items || items.length === 0) return <span style={{ color: "#475569" }}>—</span>;
  return (
    <div data-testid={dataTestId} style={{
      display: "flex", flexWrap: "wrap", gap: 4,
      justifyContent: "flex-end",
    }}>
      {items.slice(0, 6).map((label, i) => (
        <span key={`${label}-${i}`} style={{
          fontSize: 9, fontWeight: 700, color,
          padding: "1px 6px", borderRadius: 3,
          border: `1px solid ${color}66`, background: `${color}15`,
        }}>{label}</span>
      ))}
    </div>
  );
}


export function NewsTrendCard({ snapshot, loading, error, onRefresh }) {
  if (loading && !snapshot) {
    return (
      <Card>
        <SectionLabel>📰 뉴스 / 트렌드</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>📰 뉴스 / 트렌드</SectionLabel>
        <div data-testid="news-trend-error"
             style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.6 }}>
          뉴스/트렌드 데이터를 아직 불러오지 못했습니다. Demo Mode에서는
          mock provider만 사용합니다.
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
  if (!snapshot) return null;

  const palette = _ACTION_PALETTE[snapshot.recommended_action]
                 || _ACTION_PALETTE.NO_DATA;

  const topThemes  = snapshot.top_themes || [];
  const candidates = snapshot.related_candidates || [];
  const overheating = snapshot.overheating_warnings || [];
  const cautionThemes = snapshot.caution_themes || [];
  const usedForOrderWarns = snapshot.used_for_order_warnings || [];

  return (
    <Card data-testid="news-trend-card" accentColor={`${palette.color}55`}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>📰 뉴스 / 트렌드</SectionLabel>
        <span data-testid="news-trend-not-order-badge" style={{
          fontSize: 9, fontWeight: 700, color: "#94a3b8",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #94a3b855", background: "#94a3b815",
        }}>
          주문 신호 아님 · 후보 필터 전용
        </span>
      </div>

      {/* 운영자 요약 */}
      <div style={{ marginBottom: 8, padding: "8px 10px",
                     background: "#0c2035",
                     border: `1px solid ${palette.color}33`,
                     borderRadius: 4 }}>
        {(snapshot.summary_lines || []).map((line, i) => (
          <div key={i} data-testid={`news-trend-line-${i}`}
               style={{ fontSize: 11, color: "#e2e8f0",
                         lineHeight: 1.6 }}>
            {line}
          </div>
        ))}
      </div>

      {/* 핵심 상태 */}
      <_Field
        label="권장 액션"
        value={
          <span data-testid="news-trend-action"
                style={{ color: palette.color }}>{palette.label}</span>
        }
      />
      <_Field
        label="전체 신호"
        value={`${snapshot.total_signal_count}건`}
      />

      {/* Top themes */}
      {topThemes.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 10, color: "#475569",
                         marginBottom: 4 }}>상위 테마</div>
          <div data-testid="news-trend-top-themes">
            {topThemes.map((t, i) => (
              <div key={t.theme} style={{
                display: "flex", justifyContent: "space-between",
                padding: "4px 6px", marginBottom: 4,
                background: "#0c2035", borderRadius: 3,
                fontSize: 11,
              }}>
                <span style={{ color: "#7dd3fc" }}>
                  {i + 1}. {t.theme}
                </span>
                <span style={{ color: "#94a3b8" }}>
                  score {t.score} ·
                  conf {t.confidence} ·
                  {" "}{t.signal_count}건
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Rising keywords */}
      {snapshot.rising_keywords?.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <_Field
            label="키워드 증가"
            value={
              <_ChipList
                dataTestId="news-trend-keywords"
                items={snapshot.rising_keywords.slice(0, 6).map(
                  (k) => `${k.keyword} (${k.occurrence})`,
                )}
                color="#a78bfa" />
            }
          />
        </div>
      )}

      {/* Related candidates */}
      {candidates.length > 0 && (
        <_Field
          label="관련 종목 후보"
          value={
            <_ChipList
              dataTestId="news-trend-candidates"
              items={candidates.slice(0, 6).map(
                (c) => `${c.symbol} (${c.occurrence})`,
              )}
              color="#7dd3fc" />
          }
        />
      )}

      {/* Caution */}
      {cautionThemes.length > 0 && (
        <_Field
          label="신뢰도 낮은 테마"
          value={
            <_ChipList
              dataTestId="news-trend-caution"
              items={cautionThemes.map((t) => t.theme)}
              color="#fbbf24" />
          }
        />
      )}

      {/* Overheating warnings */}
      {overheating.length > 0 && (
        <div data-testid="news-trend-overheating"
             style={{ marginTop: 8, padding: "6px 8px",
                       background: "#3b1f25",
                       border: "1px solid #ef444466",
                       borderRadius: 4, fontSize: 10,
                       color: "#fca5a5", lineHeight: 1.6 }}>
          ⚠ 과열 경고:
          <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>
            {overheating.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* used_for_order=True 위반 경고 */}
      {usedForOrderWarns.length > 0 && (
        <div data-testid="news-trend-invariant-warn"
             style={{ marginTop: 8, padding: "6px 8px",
                       background: "#3b1f25",
                       border: "1px solid #ef444466",
                       borderRadius: 4, fontSize: 10,
                       color: "#fca5a5", lineHeight: 1.6 }}>
          ⚠ <strong>invariant 위반 의심</strong>:
          {" "}{usedForOrderWarns.length}건의 row가 used_for_order=True.
          <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>
            {usedForOrderWarns.slice(0, 3).map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <div style={{ marginTop: 8, fontSize: 9, color: "#64748b",
                     lineHeight: 1.5 }}>
        ※ 본 요약은 *주문 신호가 아닙니다*. BUY/SELL/HOLD는 RiskManager +
        PermissionGate + OrderExecutor 흐름에서만 만들어집니다. 뉴스 해석
        오류·루머·과열 위험으로 인해 본 요약을 단독 매매 근거로 쓰지 마세요.
      </div>

      {onRefresh && (
        <div style={{ marginTop: 6, textAlign: "right" }}>
          <button onClick={onRefresh} style={{
            fontSize: 10, padding: "3px 8px", background: "#0c2035",
            border: "1px solid #1e3a5c", borderRadius: 3,
            cursor: "pointer", color: "#7dd3fc",
          }}>↻ 새로고침</button>
        </div>
      )}
    </Card>
  );
}


// 53: hook for /api/agents/news-trend. mount 시 1회 + 수동 새로고침.
export function useNewsTrend({ limit = 100, minScore = null } = {}) {
  const [snapshot, setSnapshot] = useState(null);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState("");

  const refresh = async () => {
    setLoading(true); setError("");
    try {
      const data = await backendApi.newsTrend({ limit, minScore });
      setSnapshot(data);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setError("");
      try {
        const data = await backendApi.newsTrend({ limit, minScore });
        if (!cancelled) setSnapshot(data);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [limit, minScore]);

  return { snapshot, loading, error, refresh };
}
