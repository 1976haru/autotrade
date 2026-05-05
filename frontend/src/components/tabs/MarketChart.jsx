import { useState } from "react";
import { Btn, Card, Inp, SectionLabel } from "../common";
import { fmtKRW } from "../../utils/format";
import { useMarketBars } from "../../store/useMarketBars";

const DEFAULT_FORM = {
  symbol: "005930",
  start:  "2026-01-01",
  end:    "2026-03-31",
};


function LineChart({ bars, height = 220 }) {
  if (!bars || bars.length === 0) {
    return (
      <div style={{ color: "#1e3a5c", fontSize: 12, textAlign: "center", padding: 24 }}>
        조회 결과 없음
      </div>
    );
  }

  const width = 480;
  const padding = { top: 10, right: 60, bottom: 24, left: 8 };
  const w = width  - padding.left - padding.right;
  const h = height - padding.top  - padding.bottom;

  const closes = bars.map((b) => b.close);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const range = max - min || 1;
  const denom = bars.length - 1 || 1;

  const points = bars.map((b, i) => {
    const x = padding.left + (i / denom) * w;
    const y = padding.top  + (1 - (b.close - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  const last = bars[bars.length - 1];
  const trendColor = last.close >= bars[0].close ? "#22c55e" : "#ef4444";

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", display: "block" }}>
      {/* grid baselines */}
      <line x1={padding.left} y1={padding.top} x2={padding.left + w} y2={padding.top}
            stroke="#0c2035" strokeWidth="0.5" />
      <line x1={padding.left} y1={padding.top + h} x2={padding.left + w} y2={padding.top + h}
            stroke="#0c2035" strokeWidth="0.5" />

      <polyline points={points} fill="none" stroke={trendColor} strokeWidth="1.5" />

      {/* y-axis labels */}
      <text x={width - 4} y={padding.top + 8} textAnchor="end" fontSize="9" fill="#94a3b8">
        {fmtKRW(max)}
      </text>
      <text x={width - 4} y={padding.top + h + 4} textAnchor="end" fontSize="9" fill="#94a3b8">
        {fmtKRW(min)}
      </text>

      {/* x-axis labels */}
      <text x={padding.left} y={height - 6} fontSize="9" fill="#475569">
        {bars[0].timestamp.slice(5, 10)}
      </text>
      <text x={padding.left + w} y={height - 6} textAnchor="end" fontSize="9" fill="#475569">
        {last.timestamp.slice(5, 10)}
      </text>
    </svg>
  );
}


export function MarketChart() {
  const [form, setForm] = useState(DEFAULT_FORM);
  const { bars, source, count, loading, error, fetch } = useMarketBars();

  const update = (key) => (v) => setForm((prev) => ({ ...prev, [key]: v }));

  const onFetch = () => {
    fetch({
      symbol:   form.symbol,
      start:    `${form.start}T00:00:00+00:00`,
      end:      `${form.end}T00:00:00+00:00`,
      interval: "1d",
    });
  };

  const last  = bars && bars.length ? bars[bars.length - 1] : null;
  const first = bars && bars.length ? bars[0] : null;
  const change    = last && first ? last.close - first.close : 0;
  const changePct = last && first ? (change / first.close) * 100 : 0;
  const changeColor = change >= 0 ? "#22c55e" : "#ef4444";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Card>
        <SectionLabel>시장 데이터 조회</SectionLabel>
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>종목 코드</div>
          <Inp value={form.symbol} onChange={update("symbol")} placeholder="005930" />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
          <div>
            <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>시작일</div>
            <Inp value={form.start} onChange={update("start")} type="date" />
          </div>
          <div>
            <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>종료일</div>
            <Inp value={form.end} onChange={update("end")} type="date" />
          </div>
        </div>
        <Btn onClick={onFetch} disabled={loading} color="#7dd3fc" full>
          {loading ? "⟳ 조회 중..." : "🔍 조회"}
        </Btn>
      </Card>

      {error && (
        <Card accentColor="#ef444433">
          <div style={{ color: "#f87171", fontSize: 12 }}>조회 오류: {error}</div>
        </Card>
      )}

      {bars && (
        <Card accentColor={changeColor + "22"}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
            <SectionLabel>{form.symbol} · {count}개 봉</SectionLabel>
            <span style={{ fontSize: 9, color: "#475569" }}>
              source: {source}
            </span>
          </div>
          {last && (
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
              <span style={{ fontSize: 18, fontWeight: 700, color: "#e2e8f0" }}>
                {fmtKRW(last.close)}원
              </span>
              <span style={{ fontSize: 12, fontWeight: 700, color: changeColor }}>
                {change >= 0 ? "+" : ""}{fmtKRW(change)} ({changePct.toFixed(2)}%)
              </span>
            </div>
          )}
          <LineChart bars={bars} />
        </Card>
      )}

      <div style={{ fontSize: 10, color: "#1e3a5c", lineHeight: 1.6, padding: "0 4px" }}>
        ⚠ MarketDataAdapter가 mock인 환경에서는 결정론적 합성 데이터입니다.
        실제 OHLCV는 backend MARKET_DATA_PROVIDER=yfinance로 전환 후 사용하세요.
      </div>
    </div>
  );
}
