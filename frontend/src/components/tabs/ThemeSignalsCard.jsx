import { useState } from "react";

import { Btn, Card, SectionLabel } from "../common";
import { useThemes } from "../../store/useThemes";


/**
 * 테마/뉴스 후보 필터 카드 (#22)
 *
 * - 상위 테마 / 키워드 / 관련 후보 종목 / score / grade / source / confidence /
 *   summary를 표시한다.
 * - **"주문 신호 아님" 배지를 항상 노출**.
 * - BUY/SELL 버튼 / 주문 관련 UI는 절대 렌더하지 않는다 (CLAUDE.md).
 */
export function ThemeSignalsCard() {
  const { signals, candidates, provider, providerEnabled,
          loading, error, scanMsg, scan } = useThemes();
  const [universe, setUniverse] = useState("");

  const handleScan = async () => {
    const list = universe.split(",").map(s => s.trim().toUpperCase()).filter(Boolean);
    try {
      await scan({ universe: list.length ? list : null, limit: 20 });
    } catch {
      // scanMsg에 이미 메시지가 잡혀 있음
    }
  };

  return (
    <Card>
      <div data-testid="theme-signals-card">
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <SectionLabel>📰 테마 / 뉴스 후보 필터</SectionLabel>
        <span data-testid="theme-card-not-order-badge"
              style={{
                fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
                padding: "2px 6px", borderRadius: 3,
                background: "#7f1d1d33", color: "#fca5a5",
                border: "1px solid #ef444466",
              }}>
          주문 신호 아님 · 후보 필터 전용
        </span>
      </div>

      <div style={{
        fontSize: 11, color: "#94a3b8", lineHeight: 1.6, marginBottom: 10,
        padding: "8px 10px", background: "#0c2035", borderRadius: 4,
        border: "1px solid #1a3a5c",
      }}>
        구글 트렌드 / 뉴스 / 공시 데이터는 <b>universe 후보 필터</b>로만 사용됩니다.
        매수/매도 결정은 Strategy → RiskManager → PermissionGate → OrderExecutor를
        반드시 거칩니다. provider=<code>{provider}</code>{" "}
        {providerEnabled ? "(활성)" : "(비활성 — Mock fallback)"}.
      </div>

      {error && (
        <div style={{
          fontSize: 11, color: "#fca5a5", marginBottom: 8,
          padding: "6px 8px", background: "#7f1d1d22", borderRadius: 3,
        }}>{error}</div>
      )}

      {/* Mock scan 트리거 */}
      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <input
          value={universe}
          onChange={(e) => setUniverse(e.target.value)}
          placeholder="universe (콤마 구분, 비우면 전체. 예: 005930,247540)"
          style={{
            flex: 1, background: "var(--c-surface)",
            border: "1px solid var(--c-border-strong)",
            borderRadius: "var(--r-md)", padding: "8px 10px",
            color: "var(--c-text)", fontSize: 11, fontFamily: "inherit",
          }} />
        <Btn small onClick={handleScan} disabled={loading}>Mock 스캔</Btn>
      </div>
      {scanMsg && (
        <div data-testid="theme-scan-msg"
             style={{ fontSize: 10, color: "#22c55e", marginBottom: 8 }}>
          {scanMsg}
        </div>
      )}

      {/* Candidate symbols (universe 좁힘 결과) */}
      {candidates.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>
            후보 종목 ({candidates.length}종) — 본 목록은 universe 후보일 뿐입니다.
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {candidates.map((c) => (
              <span key={c.symbol}
                    data-testid={`theme-candidate-${c.symbol}`}
                    title={`${c.themes.join(", ")} · best=${c.best_grade}/${c.best_score}`}
                    style={{
                      padding: "3px 6px", borderRadius: 3,
                      background: "#02101e", border: "1px solid #1a3a5c",
                      fontSize: 11, color: "#cbd5e1", fontFamily: "monospace",
                    }}>
                {c.symbol}
                <span style={{ color: "#475569", marginLeft: 4, fontSize: 10 }}>
                  · {c.best_grade}/{c.best_score}
                </span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 신호 리스트 */}
      {signals.length === 0 && !loading && (
        <div data-testid="theme-signals-empty"
             style={{
               textAlign: "center", padding: "24px 12px",
               fontSize: 12, color: "#475569", background: "#0c2035",
               borderRadius: 4, border: "1px dashed #1a3a5c",
             }}>
          아직 테마 신호가 없습니다.<br />
          위 "Mock 스캔"을 실행하면 데모 테마 후보를 확인할 수 있습니다.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {signals.map((s) => (
          <div key={s.id}
               data-testid={`theme-signal-${s.id}`}
               style={{
                 padding: 8, background: "#0c2035",
                 borderRadius: 4, border: "1px solid #1a3a5c",
               }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 6,
                          justifyContent: "space-between" }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: "#cbd5e1" }}>
                {s.theme}
              </span>
              <span style={{
                fontSize: 9, fontWeight: 700, padding: "2px 6px", borderRadius: 3,
                color:      _gradeColor(s.grade),
                background: _gradeBg(s.grade),
                border:     `1px solid ${_gradeColor(s.grade)}66`,
              }}>{s.grade} · {s.score}</span>
            </div>
            <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 4, lineHeight: 1.5 }}>
              {s.summary || "(요약 없음)"}
            </div>
            <div style={{ fontSize: 9, color: "#475569", marginTop: 4,
                          display: "flex", flexWrap: "wrap", gap: 8 }}>
              <span>source={s.source}</span>
              <span>provider={s.provider}</span>
              <span>conf={s.confidence}</span>
              {s.related_symbols?.length > 0 && (
                <span>관련 {s.related_symbols.slice(0, 4).join(", ")}{
                  s.related_symbols.length > 4 ? `, +${s.related_symbols.length - 4}` : ""
                }</span>
              )}
              {s.keywords?.length > 0 && (
                <span>키워드 {s.keywords.slice(0, 3).join(", ")}</span>
              )}
            </div>
          </div>
        ))}
      </div>
      </div>
    </Card>
  );
}


function _gradeColor(grade) {
  return ({
    STRONG: "#22c55e", WATCH: "#fbbf24", WEAK: "#94a3b8", IGNORE: "#64748b",
  })[grade] || "#94a3b8";
}


function _gradeBg(grade) {
  return ({
    STRONG: "#14532d33", WATCH: "#7c2d1233", WEAK: "#33415533", IGNORE: "#33415533",
  })[grade] || "#33415533";
}
