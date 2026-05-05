import { useState } from "react";
import { Btn, Card, Inp, SectionLabel, StatBox } from "../common";
import { fmtKRW, pnlColor } from "../../utils/format";
import { useBacktest } from "../../store/useBacktest";

const DEFAULT_FORM = {
  symbol:       "005930",
  start:        "2026-01-01",
  end:          "2026-03-31",
  short:        "5",
  long:         "20",
  initial_cash: "10000000",
  quantity:     "1",
};


function Field({ label, children }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>{label}</div>
      {children}
    </div>
  );
}


/** Cumulative PnL across trades. trades[0] before any trade = 0; subsequent
 *  points add each trade's pnl. Renders a dashed 0-baseline so green/red
 *  zones read instantly even when the curve never crosses zero.
 */
export function EquityCurve({ trades, height = 160 }) {
  if (!trades || trades.length === 0) return null;

  const series = [0];
  let running = 0;
  for (const t of trades) {
    running += t.pnl;
    series.push(running);
  }

  const width = 480;
  const padding = { top: 12, right: 60, bottom: 8, left: 8 };
  const w = width  - padding.left - padding.right;
  const h = height - padding.top  - padding.bottom;

  // Always include 0 in the y-range so the baseline is visible.
  const minPnl = Math.min(0, ...series);
  const maxPnl = Math.max(0, ...series);
  const range  = maxPnl - minPnl || 1;
  const denom  = series.length - 1 || 1;

  const finalPnl  = series[series.length - 1];
  const lineColor = finalPnl >= 0 ? "#22c55e" : "#ef4444";

  const pointsStr = series.map((pnl, i) => {
    const x = padding.left + (i / denom) * w;
    const y = padding.top  + (1 - (pnl - minPnl) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  const zeroY = padding.top + (1 - (0 - minPnl) / range) * h;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      style={{ width: "100%", display: "block" }}
      data-testid="equity-curve"
      data-final-pnl={finalPnl}
    >
      <line
        x1={padding.left} y1={zeroY} x2={padding.left + w} y2={zeroY}
        stroke="#1e3a5c" strokeWidth="0.5" strokeDasharray="2,3"
      />
      <polyline points={pointsStr} fill="none" stroke={lineColor} strokeWidth="1.5" />

      <text x={width - 4} y={padding.top + 8} textAnchor="end" fontSize="9" fill="#94a3b8">
        {fmtKRW(maxPnl)}
      </text>
      <text x={width - 4} y={padding.top + h + 2} textAnchor="end" fontSize="9" fill="#94a3b8">
        {fmtKRW(minPnl)}
      </text>
      <text x={width - 4} y={zeroY + 3} textAnchor="end" fontSize="9" fill="#475569">0</text>
    </svg>
  );
}


export function Backtest() {
  const [form, setForm] = useState(DEFAULT_FORM);
  const { run, loading, error, submit } = useBacktest();

  const update = (key) => (v) => setForm((prev) => ({ ...prev, [key]: v }));

  const onRun = () => {
    submit({
      strategy: "sma_crossover",
      params:   {
        short: parseInt(form.short, 10),
        long:  parseInt(form.long, 10),
      },
      symbol:       form.symbol,
      start:        `${form.start}T00:00:00+00:00`,
      end:          `${form.end}T00:00:00+00:00`,
      interval:     "1d",
      initial_cash: parseInt(form.initial_cash, 10),
      quantity:     parseInt(form.quantity, 10),
    });
  };

  const winRatePct = run ? Math.round(run.win_rate * 1000) / 10 : 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Card>
        <SectionLabel>SMA 교차 백테스트</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569", marginBottom: 10 }}>
          시장 데이터는 backend MarketDataAdapter (기본 mock)에서 가져옵니다.
          결과는 결정론적이며 실제 시장 성과와 무관합니다.
        </div>

        <Field label="종목 코드">
          <Inp value={form.symbol} onChange={update("symbol")} placeholder="005930" />
        </Field>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <Field label="시작일">
            <Inp value={form.start} onChange={update("start")} type="date" />
          </Field>
          <Field label="종료일">
            <Inp value={form.end} onChange={update("end")} type="date" />
          </Field>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <Field label="단기 SMA">
            <Inp value={form.short} onChange={update("short")} type="number" />
          </Field>
          <Field label="장기 SMA">
            <Inp value={form.long} onChange={update("long")} type="number" />
          </Field>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <Field label="초기 자금 (원)">
            <Inp value={form.initial_cash} onChange={update("initial_cash")} type="number" />
          </Field>
          <Field label="회당 수량">
            <Inp value={form.quantity} onChange={update("quantity")} type="number" />
          </Field>
        </div>

        <Btn onClick={onRun} disabled={loading} color="#7dd3fc" full>
          {loading ? "⟳ 실행 중..." : "▶ 백테스트 실행"}
        </Btn>
      </Card>

      {error && (
        <Card accentColor="#ef444433">
          <div style={{ color: "#f87171", fontSize: 12 }}>실행 오류: {error}</div>
        </Card>
      )}

      {run && (
        <>
          <Card accentColor={pnlColor(run.total_pnl) + "33"}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
              <SectionLabel>결과 요약 — Run #{run.run_id}</SectionLabel>
              <span style={{ fontSize: 10, color: "#475569" }}>
                {run.bars_processed}개 봉 · {run.data_source}
              </span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", textAlign: "center", marginBottom: 10 }}>
              <StatBox
                label="총 손익"
                value={`${run.total_pnl >= 0 ? "+" : ""}${fmtKRW(run.total_pnl)}`}
                color={pnlColor(run.total_pnl)}
              />
              <StatBox label="승률" value={`${winRatePct}%`} color={winRatePct >= 50 ? "#22c55e" : "#f59e0b"} />
              <StatBox label="승/패" value={`${run.win_count}/${run.loss_count}`} color="#7dd3fc" />
              <StatBox label="MDD" value={fmtKRW(run.max_drawdown)} color="#ef4444" />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr",
                           textAlign: "center", paddingTop: 10, borderTop: "1px solid #0c2035" }}>
              <StatBox label="평균 익절"   value={fmtKRW(Math.round(run.avg_win  ?? 0))} color="#22c55e" />
              <StatBox label="평균 손절"   value={fmtKRW(Math.round(run.avg_loss ?? 0))} color="#ef4444" />
              <StatBox
                label="Profit Factor"
                value={run.profit_factor == null ? "—" : run.profit_factor.toFixed(2)}
                color="#7dd3fc"
              />
              <StatBox
                label="Sharpe (per-trade)"
                value={run.sharpe_ratio == null ? "—" : run.sharpe_ratio.toFixed(2)}
                color="#a78bfa"
              />
            </div>
            {run.trades.length > 0 && (
              <div style={{ paddingTop: 12, marginTop: 12, borderTop: "1px solid #0c2035" }}>
                <div style={{ fontSize: 10, color: "#475569", marginBottom: 6,
                               letterSpacing: "0.12em", textTransform: "uppercase" }}>
                  누적 손익 곡선
                </div>
                <EquityCurve trades={run.trades} />
              </div>
            )}
          </Card>

          <Card>
            <SectionLabel>체결 ({run.trades.length}건)</SectionLabel>
            {run.trades.length === 0 ? (
              <div style={{ color: "#1e3a5c", fontSize: 12, textAlign: "center", padding: 16 }}>
                전략이 신호를 발생시키지 않았습니다
              </div>
            ) : (
              <>
                <div style={{
                  display: "grid",
                  gridTemplateColumns: "60px 70px 70px 60px 1fr",
                  fontSize: 10, color: "#334155",
                  padding: "3px 0", borderBottom: "1px solid #0c2035",
                }}>
                  {["진입일", "진입가", "청산가", "수량", "손익"].map((h) => <div key={h}>{h}</div>)}
                </div>
                <div style={{ maxHeight: 360, overflowY: "auto" }}>
                  {run.trades.slice(0, 50).map((t, i) => (
                    <div key={i} style={{
                      display: "grid",
                      gridTemplateColumns: "60px 70px 70px 60px 1fr",
                      padding: "5px 0", borderBottom: "1px solid #05121f",
                      fontSize: 11,
                      background: t.pnl >= 0 ? "#22c55e06" : "#ef444406",
                    }}>
                      <span style={{ color: "#334155" }}>{t.entry_ts.slice(5, 10)}</span>
                      <span style={{ color: "#94a3b8" }}>{fmtKRW(t.entry_price)}</span>
                      <span style={{ color: "#94a3b8" }}>{fmtKRW(t.exit_price)}</span>
                      <span style={{ color: "#64748b" }}>{t.quantity}</span>
                      <span style={{ color: pnlColor(t.pnl), fontWeight: 700 }}>
                        {t.pnl >= 0 ? "+" : ""}{fmtKRW(t.pnl)}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </Card>
        </>
      )}

      <div style={{ fontSize: 10, color: "#1e3a5c", lineHeight: 1.6, padding: "0 4px" }}>
        ⚠ MarketDataAdapter가 기본 mock인 환경에서는 합성 데이터로 백테스트가 수행됩니다.
        실제 OHLCV는 MARKET_DATA_PROVIDER=yfinance로 전환 후 사용하세요.
      </div>
    </div>
  );
}
