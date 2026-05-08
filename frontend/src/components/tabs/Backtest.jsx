import { useState } from "react";
import { Btn, Card, Inp, SectionLabel, StatBox } from "../common";
import { fmtKRW, pnlColor } from "../../utils/format";
import { useBacktest } from "../../store/useBacktest";
import { backendApi } from "../../services/backend/client";
import { PromotionGateCard } from "./PromotionGateCard";

const DEFAULT_FORM = {
  symbol:       "005930",
  start:        "2026-01-01",
  end:          "2026-03-31",
  short:        "5",
  long:         "20",
  initial_cash: "10000000",
  quantity:     "1",
};

// Whitelist mirrors backend CompareSortBy Literal — keep in sync with
// backend/app/api/routes_backtest.py::CompareSortBy.
const COMPARE_SORT_OPTIONS = [
  { value: "total_pnl",     label: "총 손익" },
  { value: "sharpe_ratio",  label: "Sharpe (per-trade)" },
  { value: "profit_factor", label: "Profit Factor" },
  { value: "win_rate",      label: "승률" },
];

const MAX_COMPARE_ROWS = 50;
const DEFAULT_COMPARE_ROWS = [
  { short: "5",  long: "20" },
  { short: "10", long: "30" },
  { short: "5",  long: "30" },
];


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


/**
 * Monte Carlo risk simulation 카드 (#26).
 * "주문 신호 아님 / 전략 리스크 검증" 배지 항상 노출. BUY/SELL 버튼 0건.
 */
export function MonteCarloCard({ run }) {
  const [result, setResult] = useState(null);
  const [busy,   setBusy]   = useState(false);
  const [err,    setErr]    = useState("");
  const [method, setMethod] = useState("shuffle");
  const [iters,  setIters]  = useState(1000);

  const handleRun = async () => {
    setBusy(true);
    setErr("");
    try {
      const trades = (run.trades || []).map((t) => ({ pnl: t.pnl }));
      const out = await backendApi.monteCarlo({
        trades,
        config: {
          method, iterations: Number(iters), seed: 42,
          initial_cash: run.initial_cash,
          ruin_drawdown_pct: -0.5,
        },
      });
      setResult(out);
    } catch (e) {
      setErr(e.message || "Monte Carlo 실행 실패");
    } finally {
      setBusy(false);
    }
  };

  const flagColor = ({
    PASS: "#22c55e", CAUTION: "#fbbf24", FAIL: "#ef4444",
  })[result?.promotion_risk_flag] || "#94a3b8";

  return (
    <Card>
      <div data-testid="monte-carlo-card">
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
          <SectionLabel>🎲 Monte Carlo 리스크 검증</SectionLabel>
          <span data-testid="mc-not-order-badge"
                style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
                  padding: "2px 6px", borderRadius: 3,
                  background: "#7f1d1d33", color: "#fca5a5",
                  border: "1px solid #ef444466",
                }}>
            주문 신호 아님 · 전략 리스크 검증
          </span>
        </div>

        <div style={{
          fontSize: 11, color: "#94a3b8", lineHeight: 1.5, marginBottom: 8,
          padding: "6px 8px", background: "#0c2035", borderRadius: 3,
        }}>
          거래 순서를 섞거나 복원추출해 N회 시뮬. 운 좋게 나온 백테스트인지,
          최악 5% 시나리오의 MDD / 파산위험을 검증합니다.
          <b> Monte Carlo 결과만으로 전략 승격 금지</b> — Backtest, Walk-forward,
          Data Quality, Paper/Shadow와 함께 평가하세요.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8, alignItems: "center" }}>
          <select value={method} onChange={(e) => setMethod(e.target.value)}
                  style={{
                    background: "var(--c-surface)", color: "var(--c-text)",
                    border: "1px solid var(--c-border-strong)",
                    borderRadius: 4, padding: "6px 8px", fontSize: 11,
                  }}>
            <option value="shuffle">shuffle (순서만)</option>
            <option value="bootstrap">bootstrap (복원추출)</option>
            <option value="block_bootstrap">block bootstrap</option>
          </select>
          <select value={iters} onChange={(e) => setIters(e.target.value)}
                  style={{
                    background: "var(--c-surface)", color: "var(--c-text)",
                    border: "1px solid var(--c-border-strong)",
                    borderRadius: 4, padding: "6px 8px", fontSize: 11,
                  }}>
            <option value="500">500회</option>
            <option value="1000">1000회</option>
            <option value="5000">5000회</option>
          </select>
          <Btn small onClick={handleRun} disabled={busy}>
            {busy ? "실행 중…" : "Monte Carlo 실행"}
          </Btn>
        </div>

        {err && (
          <div style={{ fontSize: 11, color: "#fca5a5", marginBottom: 6 }}>{err}</div>
        )}

        {!result && !busy && (
          <div data-testid="mc-empty"
               style={{
                 textAlign: "center", padding: "16px 8px",
                 fontSize: 11, color: "#475569",
                 background: "#0c2035", borderRadius: 4,
                 border: "1px dashed #1a3a5c",
               }}>
            아직 실행되지 않았습니다. 위에서 method / 횟수를 고른 뒤 실행하세요.
          </div>
        )}

        {result && (
          <div data-testid="mc-result">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr",
                          textAlign: "center", gap: 8, marginBottom: 10 }}>
              <StatBox label="파산위험"
                       value={`${(result.risk_of_ruin * 100).toFixed(1)}%`}
                       color={result.risk_of_ruin >= 0.05 ? "#ef4444" : "#22c55e"} />
              <StatBox label="최악 5% MDD"
                       value={fmtKRW(result.worst_5pct_avg_mdd)}
                       color="#ef4444" />
              <StatBox label="p05 PnL"
                       value={fmtKRW(result.p05_total_pnl)}
                       color={pnlColor(result.p05_total_pnl)} />
              <StatBox label="p95 MDD"
                       value={fmtKRW(result.p95_max_drawdown)}
                       color="#ef4444" />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
                          textAlign: "center", gap: 8, marginBottom: 10 }}>
              <StatBox label="중앙값 자산"
                       value={fmtKRW(result.median_final_equity)}
                       color="#7dd3fc" />
              <StatBox label="최장 연속손실"
                       value={`${result.longest_losing_streak}회`}
                       color="#ef4444" />
              <StatBox label="시뮬"
                       value={`${result.iterations}회`}
                       color="#94a3b8" />
            </div>

            <div style={{ display: "flex", gap: 8, alignItems: "center",
                          paddingTop: 8, borderTop: "1px solid #0c2035",
                          flexWrap: "wrap" }}>
              <span data-testid="mc-promotion-flag"
                    style={{
                      fontSize: 11, fontWeight: 700, padding: "3px 8px",
                      borderRadius: 3, color: flagColor,
                      background: `${flagColor}22`,
                      border: `1px solid ${flagColor}66`,
                    }}>
                {result.promotion_risk_flag}
              </span>
              <span style={{ fontSize: 10, color: "#94a3b8" }}>
                stability: <b style={{ color: "#cbd5e1" }}>{result.stability_grade}</b>
              </span>
              <span style={{ fontSize: 10, color: "#475569" }}>
                ({result.config.method} · seed {result.config.seed ?? "—"})
              </span>
            </div>

            {result.warnings && result.warnings.length > 0 && (
              <div data-testid="mc-warnings"
                   style={{ marginTop: 8, paddingTop: 8,
                            borderTop: "1px solid #0c2035",
                            fontSize: 11, color: "#fbbf24" }}>
                {result.warnings.map((w, i) => (
                  <div key={i} style={{ marginBottom: 2 }}>⚠ {w}</div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}


function ModeToggle({ mode, onChange }) {
  const opt = (value, label) => (
    <button
      key={value}
      onClick={() => onChange(value)}
      style={{
        flex: 1, padding: "7px 0", borderRadius: 4,
        border: `1px solid ${mode === value ? "#7dd3fc" : "#1a3a5c"}`,
        background: mode === value ? "#7dd3fc" : "transparent",
        color:      mode === value ? "#010a14" : "#64748b",
        cursor: "pointer", fontFamily: "inherit", fontSize: 12, fontWeight: 700,
      }}
    >{label}</button>
  );
  return (
    <div style={{ display: "flex", gap: 6 }}>
      {opt("single",  "단일 실행")}
      {opt("compare", "비교 (param sweep)")}
    </div>
  );
}


export function CompareTable({ comparison }) {
  if (!comparison) return null;
  const { sort_by, runs, bars_processed } = comparison;
  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "baseline", marginBottom: 8 }}>
        <SectionLabel>비교 결과 ({runs.length}개)</SectionLabel>
        <span style={{ fontSize: 10, color: "#475569" }}>
          {bars_processed}개 봉 · 정렬: {sort_by}
        </span>
      </div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "30px 1fr 90px 70px 60px 60px 90px",
        fontSize: 10, color: "#334155",
        padding: "3px 0", borderBottom: "1px solid #0c2035",
      }}>
        {["#", "params", "총손익", "Sharpe", "PF", "승률", "MDD"]
          .map((h) => <div key={h}>{h}</div>)}
      </div>
      <div data-testid="compare-rows">
        {runs.map((r, i) => {
          const winner = i === 0;
          return (
            <div
              key={r.run_id}
              data-testid="compare-row"
              data-rank={i}
              style={{
                display: "grid",
                gridTemplateColumns: "30px 1fr 90px 70px 60px 60px 90px",
                padding: "5px 0", borderBottom: "1px solid #05121f",
                fontSize: 11,
                background: winner ? "#7dd3fc14" : (r.total_pnl >= 0 ? "#22c55e06" : "#ef444406"),
              }}
            >
              <span style={{ color: winner ? "#7dd3fc" : "#475569", fontWeight: 700 }}>
                {i + 1}
              </span>
              <span style={{ color: "#94a3b8", fontFamily: "monospace", fontSize: 10 }}>
                {JSON.stringify(r.params)}
              </span>
              <span style={{ color: pnlColor(r.total_pnl), fontWeight: 700 }}>
                {r.total_pnl >= 0 ? "+" : ""}{fmtKRW(r.total_pnl)}
              </span>
              <span style={{ color: "#a78bfa" }}>
                {r.sharpe_ratio == null ? "—" : r.sharpe_ratio.toFixed(2)}
              </span>
              <span style={{ color: "#7dd3fc" }}>
                {r.profit_factor == null ? "—" : r.profit_factor.toFixed(2)}
              </span>
              <span style={{ color: "#94a3b8" }}>
                {(r.win_rate * 100).toFixed(0)}%
              </span>
              <span style={{ color: "#ef4444" }}>{fmtKRW(r.max_drawdown)}</span>
            </div>
          );
        })}
      </div>
    </Card>
  );
}


function CompareSetupCard({ shared, loading, onCompare }) {
  const [rows,    setRows]    = useState(DEFAULT_COMPARE_ROWS);
  const [sortBy,  setSortBy]  = useState("total_pnl");

  const updateRow = (idx, key) => (v) => setRows((prev) => {
    const next = [...prev];
    next[idx] = { ...next[idx], [key]: v };
    return next;
  });
  const addRow    = () => rows.length < MAX_COMPARE_ROWS && setRows((p) => [...p, { short: "5", long: "20" }]);
  const removeRow = (idx) => setRows((p) => p.filter((_, i) => i !== idx));

  const onSubmit = () => {
    const param_sets = rows
      .map((r) => ({ short: parseInt(r.short, 10), long: parseInt(r.long, 10) }))
      .filter((p) => Number.isFinite(p.short) && Number.isFinite(p.long));
    onCompare({
      strategy:     "sma_crossover",
      param_sets,
      sort_by:      sortBy,
      symbol:       shared.symbol,
      start:        `${shared.start}T00:00:00+00:00`,
      end:          `${shared.end}T00:00:00+00:00`,
      interval:     "1d",
      initial_cash: parseInt(shared.initial_cash, 10),
      quantity:     parseInt(shared.quantity, 10),
    });
  };

  return (
    <Card>
      <SectionLabel>Param sets ({rows.length}/{MAX_COMPARE_ROWS})</SectionLabel>
      <div data-testid="compare-rows-input">
        {rows.map((r, i) => (
          <div key={i} style={{
            display: "grid",
            gridTemplateColumns: "30px 1fr 1fr 30px",
            gap: 6, alignItems: "center", marginBottom: 6,
          }}>
            <span style={{ fontSize: 10, color: "#475569", textAlign: "center" }}>{i + 1}</span>
            <Inp value={r.short} onChange={updateRow(i, "short")} type="number" />
            <Inp value={r.long}  onChange={updateRow(i, "long")}  type="number" />
            <button
              onClick={() => removeRow(i)}
              disabled={rows.length === 1}
              style={{
                padding: 0, height: 26, borderRadius: 4,
                border: "1px solid #1a3a5c",
                background: rows.length === 1 ? "transparent" : "#0c2035",
                color: rows.length === 1 ? "#1a3a5c" : "#94a3b8",
                cursor: rows.length === 1 ? "not-allowed" : "pointer",
                fontFamily: "inherit", fontSize: 14,
              }}
            >×</button>
          </div>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "30px 1fr 1fr 30px", gap: 6,
                     fontSize: 9, color: "#334155", marginTop: 2 }}>
        <span></span>
        <span style={{ paddingLeft: 4 }}>short SMA</span>
        <span style={{ paddingLeft: 4 }}>long SMA</span>
        <span></span>
      </div>

      <Btn
        onClick={addRow}
        disabled={rows.length >= MAX_COMPARE_ROWS}
        color="#475569" small
      >
        + 행 추가
      </Btn>

      <Field label="정렬 기준 (내림차순, None은 마지막)">
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
          style={{
            width: "100%", background: "#010a14", border: "1px solid #1a3a5c",
            borderRadius: 4, padding: "8px 10px", color: "#c9d6e3",
            fontSize: 12, fontFamily: "inherit", outline: "none", boxSizing: "border-box",
          }}
        >
          {COMPARE_SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </Field>

      <Btn onClick={onSubmit} disabled={loading || rows.length === 0} color="#a78bfa" full>
        {loading ? "⟳ 비교 중..." : `▶ 비교 실행 (${rows.length}건)`}
      </Btn>
    </Card>
  );
}


export function Backtest() {
  const [form, setForm] = useState(DEFAULT_FORM);
  const [mode, setMode] = useState("single");
  const { run, comparison, loading, error, submit, compare } = useBacktest();

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

        <div style={{ marginBottom: 10 }}>
          <ModeToggle mode={mode} onChange={setMode} />
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
          <Field label="초기 자금 (원)">
            <Inp value={form.initial_cash} onChange={update("initial_cash")} type="number" />
          </Field>
          <Field label="회당 수량">
            <Inp value={form.quantity} onChange={update("quantity")} type="number" />
          </Field>
        </div>

        {mode === "single" ? (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <Field label="단기 SMA">
                <Inp value={form.short} onChange={update("short")} type="number" />
              </Field>
              <Field label="장기 SMA">
                <Inp value={form.long} onChange={update("long")} type="number" />
              </Field>
            </div>
            <Btn onClick={onRun} disabled={loading} color="#7dd3fc" full>
              {loading ? "⟳ 실행 중..." : "▶ 백테스트 실행"}
            </Btn>
          </>
        ) : (
          <CompareSetupCard
            shared={form}
            loading={loading}
            onCompare={compare}
          />
        )}
      </Card>

      {error && (
        <Card accentColor="#ef444433">
          <div style={{ color: "#f87171", fontSize: 12 }}>실행 오류: {error}</div>
        </Card>
      )}

      {mode === "compare" && comparison && <CompareTable comparison={comparison} />}

      {mode === "single" && run && (
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
            {/* 24: 신규 metric 요약 — 거래가 있을 때만 */}
            {run.trades.length > 0 && (
              <div data-testid="backtest-metric-summary"
                   style={{ marginTop: 10, paddingTop: 10,
                            borderTop: "1px solid #0c2035",
                            display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr",
                            textAlign: "center", gap: 8 }}>
                <StatBox
                  label="기대값"
                  value={fmtKRW(Math.round(run.expectancy ?? 0))}
                  color={(run.expectancy ?? 0) >= 0 ? "#22c55e" : "#ef4444"} />
                <StatBox
                  label="최대 연속수익"
                  value={`${run.max_consecutive_wins ?? 0}회`}
                  color="#22c55e" />
                <StatBox
                  label="최대 연속손실"
                  value={`${run.max_consecutive_losses ?? 0}회`}
                  color="#ef4444" />
                <StatBox
                  label="Flat"
                  value={`${run.flat_count ?? 0}회`}
                  color="#94a3b8" />
              </div>
            )}

            {/* 24: 시간대별 손익 — 정규장 시간대(UTC) 기준 mini bar */}
            {run.hourly_pnl && Object.keys(run.hourly_pnl).length > 0 && (
              <div data-testid="backtest-hourly-pnl"
                   style={{ marginTop: 10, paddingTop: 10,
                            borderTop: "1px solid #0c2035" }}>
                <div style={{ fontSize: 10, color: "#475569", marginBottom: 6,
                              textTransform: "uppercase", letterSpacing: "0.06em" }}>
                  시간대별 손익 (UTC)
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {Object.entries(run.hourly_pnl)
                    .sort((a, b) => Number(a[0]) - Number(b[0]))
                    .map(([hour, pnl]) => (
                    <span key={hour}
                          data-testid={`hourly-pnl-${hour}`}
                          title={`${hour}시 — ${fmtKRW(pnl)}`}
                          style={{
                            padding: "3px 6px", borderRadius: 3,
                            background: pnl >= 0 ? "#14532d33" : "#7f1d1d33",
                            color:      pnl >= 0 ? "#22c55e"   : "#ef4444",
                            border: `1px solid ${pnl >= 0 ? "#22c55e66" : "#ef444466"}`,
                            fontSize: 10, fontFamily: "monospace",
                          }}>
                      {hour === "-1" ? "?" : `${hour}h`} · {pnl >= 0 ? "+" : ""}{fmtKRW(pnl)}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* 23: 비용 모델 요약 — config가 적용됐을 때만 노출. 미적용 시 모두 0 */}
            {(run.config || run.total_fees > 0 || run.total_taxes > 0 || run.total_slippage > 0) && (
              <div data-testid="backtest-cost-summary"
                   style={{ marginTop: 10, paddingTop: 10,
                            borderTop: "1px solid #0c2035",
                            fontSize: 10, color: "#94a3b8",
                            display: "flex", flexWrap: "wrap", gap: 12 }}>
                {run.config && (
                  <span>체결: <b style={{ color: "#cbd5e1" }}>{run.config.execution_model}</b>
                    {run.config.execution_delay_bars != null
                      && ` · delay ${run.config.execution_delay_bars}봉`}</span>
                )}
                <span>gross {fmtKRW(run.gross_pnl ?? 0)}</span>
                <span>net <b style={{ color: pnlColor(run.net_pnl ?? 0) }}>{fmtKRW(run.net_pnl ?? 0)}</b></span>
                <span>수수료 {fmtKRW(run.total_fees ?? 0)}</span>
                <span>세금 {fmtKRW(run.total_taxes ?? 0)}</span>
                <span>슬리피지 {fmtKRW(run.total_slippage ?? 0)}</span>
              </div>
            )}
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

          {run.trades.length > 0 && <MonteCarloCard run={run} />}

          {run.trades.length > 0 && <PromotionGateCard run={run} />}

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
