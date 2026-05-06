import { useEffect, useMemo, useState } from "react";
import { Btn, Card, Inp, SectionLabel } from "../common";
import { fmtKRW, pnlColor } from "../../utils/format";
import { backendApi } from "../../services/backend/client";
import { useLiveEngine } from "../../store/useLiveEngine";


// String form value -> typed value matching the registry schema.
// Returns undefined when the cast would lose meaning so the caller can omit
// the field and let the backend fall back to the default.
function castParamValue(rawValue, type) {
  if (rawValue === "" || rawValue == null) return undefined;
  if (type === "int") {
    const n = parseInt(rawValue, 10);
    return Number.isNaN(n) ? undefined : n;
  }
  if (type === "float") {
    const n = parseFloat(rawValue);
    return Number.isNaN(n) ? undefined : n;
  }
  if (type === "bool") {
    return rawValue === true || rawValue === "true";
  }
  return rawValue;
}


function inputTypeFor(type) {
  return (type === "int" || type === "float") ? "number" : "text";
}


function StatusCard({ status, busy, onReset }) {
  const configured = status?.configured;
  const accent = configured ? (status.holding ? "#22c55e44" : "#7dd3fc44") : "#33415544";

  return (
    <Card accentColor={accent}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <SectionLabel>엔진 상태</SectionLabel>
        <Btn color="#334155" onClick={onReset} disabled={busy} small>리셋</Btn>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <Field label="구성"      value={configured ? "✓ 구성됨" : "✗ 미구성"} />
        <Field label="전략"      value={status?.strategy ?? "—"} />
        <Field label="처리 봉 수" value={String(status?.bars_seen ?? 0)} />
        <Field label="포지션"    value={status?.holding ? "보유 중" : "없음"} />
      </div>
      {configured && <RegimeIndicator status={status} />}
      {status?.holding && status?.entry_price != null && (
        <PositionBlock status={status} />
      )}
    </Card>
  );
}


// 137 + 144: 전략별 누적 성과. backtest(BacktestRun) + LIVE 체결(OrderAuditLog
// FIFO 페어매칭) 두 출처를 한 표에 surface. /api/strategies/scoreboard 응답을
// 그대로 표시. hook 없이 직접 fetch — Strategies 탭의 다른 hook(useLiveEngine)과
// 라이프사이클 분리.
export function ScoreboardCard() {
  const [rows, setRows]       = useState(null);
  const [error, setError]     = useState("");
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    setLoading(true);
    try {
      const data = await backendApi.engineScoreboard();
      setRows(data);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // setState는 await 다음 — 동기 X
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, []);

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <SectionLabel>전략 누적 성과 (backtest + live)</SectionLabel>
        <Btn color="#334155" onClick={refresh} disabled={loading} small>새로고침</Btn>
      </div>
      <div style={{ fontSize: 9, color: "#334155", marginBottom: 8, lineHeight: 1.5 }}>
        backtest는 모든 BacktestRun을 strategy별로 누적, live는 OrderAuditLog
        의 BUY/SELL 체결을 FIFO 페어매칭하여 realized PnL로 산출. 두 합계로 정렬.
      </div>
      {error && (
        <div style={{ fontSize: 11, color: "#f87171", marginBottom: 8 }}>{error}</div>
      )}
      {loading && rows === null ? (
        <div style={{ fontSize: 11, color: "#475569", padding: 12, textAlign: "center" }}>
          로딩 중…
        </div>
      ) : rows && rows.length === 0 ? (
        <div style={{ fontSize: 12, color: "#1e3a5c", padding: 16, textAlign: "center" }}>
          아직 backtest 기록이 없습니다
        </div>
      ) : rows && (
        <table data-testid="strategy-scoreboard"
               style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {["전략", "runs", "BT PnL", "BT 승률", "live trades", "live PnL", "live 승률"].map((h, i) => (
                <th key={h} style={{
                  fontSize: 10, color: "#475569", fontWeight: 700,
                  borderBottom: "1px solid #1a3a5c",
                  textAlign: i === 0 ? "left" : "right", padding: "3px 6px",
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.strategy}
                  data-testid={`scoreboard-row-${r.strategy}`}>
                <td style={{ padding: "3px 6px", fontSize: 11,
                              color: "#7dd3fc", fontWeight: 700 }}>
                  {r.strategy}
                </td>
                <td style={{ padding: "3px 6px", fontSize: 10, textAlign: "right",
                              color: "#94a3b8" }}>{r.runs}</td>
                <td style={{ padding: "3px 6px", fontSize: 10, textAlign: "right",
                              color: pnlColor(r.total_pnl), fontWeight: 700 }}
                    data-testid={`scoreboard-bt-pnl-${r.strategy}`}>
                  {r.total_pnl >= 0 ? "+" : ""}{fmtKRW(r.total_pnl)}
                </td>
                <td style={{ padding: "3px 6px", fontSize: 10, textAlign: "right",
                              color: "#a78bfa" }}>
                  {Math.round(r.win_rate * 1000) / 10}%
                </td>
                <td style={{ padding: "3px 6px", fontSize: 10, textAlign: "right",
                              color: "#94a3b8" }}
                    data-testid={`scoreboard-live-trades-${r.strategy}`}>
                  {r.live_trades ?? 0}
                </td>
                <td style={{ padding: "3px 6px", fontSize: 10, textAlign: "right",
                              color: pnlColor(r.live_pnl ?? 0), fontWeight: 700 }}
                    data-testid={`scoreboard-live-pnl-${r.strategy}`}>
                  {(r.live_pnl ?? 0) >= 0 ? "+" : ""}{fmtKRW(r.live_pnl ?? 0)}
                </td>
                <td style={{ padding: "3px 6px", fontSize: 10, textAlign: "right",
                              color: "#a78bfa" }}>
                  {Math.round((r.live_win_rate ?? 0) * 1000) / 10}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}


// 136: 신호 강도/신뢰도 advisory. HOLD 신호는 의미 없으므로 미렌더. 그 외엔
// strength / confidence 두 0-100 scale을 mini-bar로 표시 — 운영자가 'BUY인데
// 신뢰도 낮음'을 즉시 인지.
const _BAR_COLOR_FOR = (v) => (
  v >= 70 ? "#22c55e" :
  v >= 40 ? "#fbbf24" :
            "#ef4444"
);

export function SignalQualityBadge({ quality, signal }) {
  if (!quality || signal === "HOLD") return null;
  const _MiniBar = ({ label, value, testId }) => (
    <div data-testid={testId}
         style={{ display: "flex", alignItems: "center", gap: 6, flex: 1 }}>
      <span style={{ color: "#475569", fontSize: 9, width: 50 }}>{label}</span>
      <div style={{ flex: 1, height: 6, background: "#020e1c",
                     borderRadius: 3, overflow: "hidden" }}>
        <div style={{
          width: `${value}%`, height: "100%",
          background: _BAR_COLOR_FOR(value), transition: "width 0.3s",
        }} />
      </div>
      <span style={{ color: _BAR_COLOR_FOR(value), fontSize: 10,
                      fontWeight: 700, width: 32, textAlign: "right" }}>
        {value}
      </span>
    </div>
  );
  return (
    <div data-testid="signal-quality-badge"
         style={{ display: "flex", flexDirection: "column", gap: 4,
                  marginBottom: 8, padding: "6px 8px",
                  background: "#010a14", border: "1px solid #0c2035",
                  borderRadius: 4 }}>
      <_MiniBar label="강도"   value={quality.strength}   testId="signal-quality-strength" />
      <_MiniBar label="신뢰도" value={quality.confidence} testId="signal-quality-confidence" />
    </div>
  );
}


// 135: 현재 시장 체제 + 전략과의 호환성 표시. advisory만 — 신호를 차단하지
// 않는다. 호환되면 청록(neutral OK), 불일치이면 amber(주의). regime이 'any'
// 또는 봉 부족이면 회색.
const _REGIME_LABEL = {
  any:            { label: "분류 전",  color: "#475569" },
  trending_up:    { label: "상승 추세", color: "#22c55e" },
  trending_down:  { label: "하락 추세", color: "#ef4444" },
  trending:       { label: "추세장",    color: "#22c55e" },
  ranging:        { label: "횡보장",    color: "#7dd3fc" },
  high_vol:       { label: "고변동",    color: "#fbbf24" },
};

export function RegimeIndicator({ status }) {
  const regime  = status?.current_regime || "any";
  const matches = status?.regime_matches_strategy !== false;
  const display = _REGIME_LABEL[regime] || { label: regime, color: "#94a3b8" };
  return (
    <div data-testid="regime-indicator"
         data-regime={regime}
         data-matches={matches ? "true" : "false"}
         style={{
           marginTop: 8, padding: "6px 8px",
           border: `1px solid ${matches ? "#0c2035" : "#fbbf2466"}`,
           background: matches ? "#010a14" : "#fbbf2415",
           borderRadius: 4, fontSize: 10,
           display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
         }}>
      <span style={{ color: "#475569" }}>현재 시장 체제:</span>
      <span style={{ color: display.color, fontWeight: 700 }}>{display.label}</span>
      {!matches && (
        <span data-testid="regime-mismatch-warning"
              style={{ color: "#fbbf24" }}>
          ⚠ 전략 required_regime과 불일치 — 신호 신뢰도 주의
        </span>
      )}
    </div>
  );
}


export function PositionBlock({ status }) {
  const { entry_price, last_price, unrealized_pnl, unrealized_pnl_pct } = status;
  const pnlColor =
    unrealized_pnl == null ? "#94a3b8" :
    unrealized_pnl > 0 ? "#22c55e" :
    unrealized_pnl < 0 ? "#ef4444" : "#94a3b8";
  const pctSigned =
    unrealized_pnl_pct == null ? null :
    `${unrealized_pnl_pct >= 0 ? "+" : ""}${(unrealized_pnl_pct * 100).toFixed(2)}%`;
  const pnlSigned =
    unrealized_pnl == null ? "—" :
    `${unrealized_pnl >= 0 ? "+" : ""}${fmtKRW(unrealized_pnl)}`;

  return (
    <div
      data-testid="position-block"
      style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid #0c2035",
                display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}
    >
      <Field label="진입가"     value={`${fmtKRW(entry_price)}원`} />
      <Field label="현재가"     value={last_price != null ? `${fmtKRW(last_price)}원` : "—"} />
      <div>
        <div style={{ fontSize: 9, color: "#475569", marginBottom: 2 }}>평가손익</div>
        <div style={{ fontSize: 12, fontWeight: 700, color: pnlColor }}>
          {pnlSigned}
          {pctSigned != null && (
            <span style={{ fontSize: 10, marginLeft: 6, color: pnlColor }}>
              ({pctSigned})
            </span>
          )}
        </div>
      </div>
    </div>
  );
}


function Field({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: 9, color: "#475569", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 12, color: "#94a3b8", fontWeight: 700 }}>{value}</div>
    </div>
  );
}


function _initialParamValues(strategy) {
  if (!strategy) return {};
  const initial = {};
  for (const p of strategy.params) {
    initial[p.name] = p.default == null ? "" : String(p.default);
  }
  return initial;
}


// 131: 전략 contract 정보를 운영자가 ConfigureCard에서 한 번에 검토할 수
// 있도록 surface. 미작성 필드는 "(미작성)"으로 표시 — 운영자가 Strategy
// contract가 미완인 stub을 즉시 인지. 본 panel은 read-only이고 추가 chip이나
// 필터를 만들지 않는다 — 단순 정보 표시 (MUST 항목, NICE 아님).
export function StrategyContractPanel({ strategy }) {
  if (!strategy) return null;
  const entry        = strategy.entry        || "";
  const exit         = strategy.exit         || "";
  const invalidation = strategy.invalidation || "";
  const regime       = strategy.required_regime || "any";
  const risk         = strategy.risk_profile  || {};
  const _row = (label, value, missing) => (
    <div style={{ marginBottom: 4 }}>
      <span style={{ color: "#475569", marginRight: 6 }}>{label}:</span>
      <span style={{ color: missing ? "#f59e0b" : "#94a3b8" }}>
        {missing ? "(미작성)" : value}
      </span>
    </div>
  );
  const _riskFields = Object.keys(risk);
  return (
    <div data-testid="strategy-contract-panel"
         style={{
           fontSize: 10, padding: "8px 10px", marginBottom: 10,
           background: "#010a14", border: "1px solid #0c2035", borderRadius: 4,
           lineHeight: 1.5,
         }}>
      <div style={{ color: "#7dd3fc", fontWeight: 700, marginBottom: 4,
                     letterSpacing: "0.04em", fontSize: 9 }}>
        STRATEGY CONTRACT
      </div>
      {_row("진입",   entry,        !entry)}
      {_row("청산",   exit,         !exit)}
      {_row("무효화", invalidation, !invalidation)}
      {_row("시장 체제", regime,    regime === "any")}
      <div>
        <span style={{ color: "#475569", marginRight: 6 }}>리스크 프로파일:</span>
        {_riskFields.length === 0 ? (
          <span style={{ color: "#f59e0b" }}>(미작성)</span>
        ) : (
          <span style={{ color: "#94a3b8" }}>
            {_riskFields.map((k) => `${k}=${risk[k]}`).join(" · ")}
          </span>
        )}
      </div>
    </div>
  );
}


export function ConfigureCard({ busy, registry, onConfigure }) {
  // userSelectedName falls through to the first registry entry until the user
  // explicitly picks one. This avoids the "setState in useEffect" anti-pattern
  // that React 19 lints against.
  const [userSelectedName, setUserSelectedName] = useState("");
  const strategyName =
    userSelectedName || registry?.[0]?.name || "";

  const selected = useMemo(
    () => registry?.find((s) => s.name === strategyName) ?? null,
    [registry, strategyName],
  );

  // Reset param values during render whenever the schema (selected.name)
  // changes — the React 19 "Adjusting state on prop change" pattern.
  const [paramValues, setParamValues] = useState(() => _initialParamValues(selected));
  const [lastSchemaName, setLastSchemaName] = useState(selected?.name ?? "");
  if ((selected?.name ?? "") !== lastSchemaName) {
    setParamValues(_initialParamValues(selected));
    setLastSchemaName(selected?.name ?? "");
  }

  const [quantity, setQuantity] = useState("1");

  const updateParam = (name) => (raw) =>
    setParamValues((prev) => ({ ...prev, [name]: raw }));

  const onSubmit = () => {
    if (!selected) return;
    const params = {};
    for (const p of selected.params) {
      const cast = castParamValue(paramValues[p.name], p.type);
      if (cast !== undefined) params[p.name] = cast;
    }
    onConfigure({
      strategy: selected.name,
      params,
      quantity: parseInt(quantity, 10),
    });
  };

  if (!registry) {
    return (
      <Card>
        <SectionLabel>구성</SectionLabel>
        <div style={{ fontSize: 11, color: "#64748b" }}>전략 목록 로딩 중…</div>
      </Card>
    );
  }

  if (registry.length === 0) {
    return (
      <Card accentColor="#ef444433">
        <SectionLabel>구성</SectionLabel>
        <div style={{ fontSize: 11, color: "#f87171" }}>
          서버에 등록된 전략이 없습니다.
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <SectionLabel>구성</SectionLabel>
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>전략</div>
        <select
          value={strategyName}
          onChange={(e) => setUserSelectedName(e.target.value)}
          style={{
            width: "100%", background: "#010a14", border: "1px solid #1a3a5c",
            borderRadius: 4, padding: "8px 10px", color: "#c9d6e3",
            fontSize: 12, fontFamily: "inherit", outline: "none", boxSizing: "border-box",
          }}
        >
          {registry.map((s) => (
            <option key={s.name} value={s.name}>{s.name}</option>
          ))}
        </select>
        {selected?.description && (
          <div style={{ fontSize: 10, color: "#64748b", marginTop: 4, lineHeight: 1.5 }}>
            {selected.description}
          </div>
        )}
      </div>

      <StrategyContractPanel strategy={selected} />

      {selected && selected.params.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
          {selected.params.map((p) => (
            <div key={p.name}>
              <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>
                {p.name}
                <span style={{ color: "#334155", marginLeft: 4 }}>
                  ({p.type}{p.required ? " · required" : ""})
                </span>
              </div>
              <Inp
                value={paramValues[p.name] ?? ""}
                onChange={updateParam(p.name)}
                type={inputTypeFor(p.type)}
              />
            </div>
          ))}
        </div>
      )}

      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>회당 수량</div>
        <Inp value={quantity} onChange={setQuantity} type="number" />
      </div>
      <Btn onClick={onSubmit} disabled={busy || !selected} color="#7dd3fc" full>구성/재구성</Btn>
    </Card>
  );
}


function TickCard({ status, busy, onTick }) {
  const [bar, setBar] = useState({ symbol: "005930", close: "75000" });
  const [submit, setSubmit] = useState(false);
  const update = (key) => (v) => setBar((prev) => ({ ...prev, [key]: v }));

  const onSubmitTick = () => {
    const close = parseInt(bar.close, 10);
    onTick({
      symbol:    bar.symbol,
      timestamp: new Date().toISOString(),
      open:      close,
      high:      close,
      low:       close,
      close,
      volume:    1,
    }, submit);
  };

  const disabled = busy || !status?.configured;

  return (
    <Card>
      <SectionLabel>봉 입력</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>종목</div>
          <Inp value={bar.symbol} onChange={update("symbol")} />
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>종가 (원)</div>
          <Inp value={bar.close} onChange={update("close")} type="number" />
        </div>
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11,
                       color: submit ? "#ef4444" : "#475569", marginBottom: 8, cursor: "pointer" }}>
        <input type="checkbox" checked={submit} onChange={(e) => setSubmit(e.target.checked)} />
        주문 라우팅 활성화 (Risk → Permission → Executor)
      </label>
      {submit && (
        <div style={{ fontSize: 10, color: "#f59e0b", marginBottom: 8, lineHeight: 1.6 }}>
          ⚠ 의도된 주문이 발생하면 RiskManager + PermissionGate를 거쳐 broker로 전송됩니다.
          현재 모드(SIMULATION/SHADOW/PAPER 등)에 따라 결과가 달라집니다.
        </div>
      )}
      <Btn onClick={onSubmitTick} disabled={disabled} color="#22c55e" full>
        ▶ tick
      </Btn>
    </Card>
  );
}


function ResultCard({ result }) {
  if (!result) return null;
  const { signal, intended_order, routing, quality } = result;
  const signalColor =
    signal === "BUY"  ? "#22c55e" :
    signal === "SELL" ? "#ef4444" : "#94a3b8";

  return (
    <Card accentColor={routing ? "#7dd3fc55" : undefined}>
      <SectionLabel>최근 tick 결과</SectionLabel>
      <div style={{ display: "flex", gap: 14, alignItems: "baseline", marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 9, color: "#475569" }}>신호</div>
          <div style={{ fontSize: 16, fontWeight: 700, color: signalColor }}>{signal}</div>
        </div>
        {intended_order && (
          <div>
            <div style={{ fontSize: 9, color: "#475569" }}>의도된 주문</div>
            <div style={{ fontSize: 11, color: "#94a3b8" }}>
              {intended_order.side} {intended_order.quantity}주 · {intended_order.order_type}
            </div>
          </div>
        )}
      </div>
      <SignalQualityBadge quality={quality} signal={signal} />

      {routing && (
        <div style={{ borderTop: "1px solid #0c2035", paddingTop: 8, marginTop: 6 }}>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>라우팅 결과</div>
          <div style={{ fontSize: 11, fontWeight: 700,
                         color: routing.decision === "APPROVED" ? "#22c55e" :
                                routing.decision === "NEEDS_APPROVAL" ? "#f59e0b" : "#ef4444" }}>
            {routing.decision}
            {routing.approval_id != null && ` · 승인 #${routing.approval_id}`}
          </div>
          {routing.order_result && (
            <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 4 }}>
              {routing.order_result.status} · {routing.order_result.filled_quantity}주 @ {fmtKRW(routing.order_result.avg_fill_price ?? 0)}원
            </div>
          )}
          {routing.reasons?.length > 0 && (
            <div style={{ fontSize: 9, color: "#64748b", marginTop: 4 }}>
              {routing.reasons.join(" / ")}
            </div>
          )}
        </div>
      )}

      <div style={{ fontSize: 10, color: pnlColor(0), marginTop: 8 }}>
        bars_seen: {result.bars_seen} · holding: {String(result.holding)}
      </div>
    </Card>
  );
}


function ReplayCard({ status, busy, onReplay, summary }) {
  const [form, setForm] = useState({
    symbol: "005930",
    start:  "2026-01-01",
    end:    "2026-01-31",
  });
  const update = (key) => (v) => setForm((prev) => ({ ...prev, [key]: v }));

  const onSubmit = () => {
    onReplay({
      symbol:   form.symbol,
      start:    `${form.start}T00:00:00+00:00`,
      end:      `${form.end}T00:00:00+00:00`,
      interval: "1d",
    });
  };

  const disabled = busy || !status?.configured;

  return (
    <Card>
      <SectionLabel>시장 데이터로 봉 일괄 입력 (replay)</SectionLabel>
      <div style={{ fontSize: 10, color: "#475569", marginBottom: 8, lineHeight: 1.6 }}>
        주어진 기간의 봉을 시장 어댑터에서 가져와 엔진에 차례로 흘려보냅니다 (BarCache 사용).
        주문 라우팅은 비활성 — 신호 수집 + 엔진 상태 워밍업만 수행합니다.
      </div>
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>종목</div>
        <Inp value={form.symbol} onChange={update("symbol")} />
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
      <Btn onClick={onSubmit} disabled={disabled} color="#a78bfa" full>
        ⏩ replay
      </Btn>

      {summary && (
        <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid #0c2035" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#94a3b8", marginBottom: 6 }}>
            결과: {summary.bars_processed}개 봉 처리됨
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, fontSize: 11 }}>
            <Field label="BUY"  value={String(summary.signals_emitted?.BUY  ?? 0)} />
            <Field label="SELL" value={String(summary.signals_emitted?.SELL ?? 0)} />
            <Field label="HOLD" value={String(summary.signals_emitted?.HOLD ?? 0)} />
          </div>
        </div>
      )}
    </Card>
  );
}


export function LiveEngine() {
  const { status, registry, lastResult, replaySummary, busy, error,
          configure, tick, reset, replay } = useLiveEngine();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <StatusCard    status={status} busy={busy} onReset={reset} />

      {error && (
        <Card accentColor="#ef444433">
          <div style={{ color: "#f87171", fontSize: 12 }}>{error}</div>
        </Card>
      )}

      <ConfigureCard busy={busy} registry={registry} onConfigure={configure} />
      <ReplayCard    status={status} busy={busy} onReplay={replay} summary={replaySummary} />
      <TickCard      status={status} busy={busy} onTick={tick} />
      <ResultCard    result={lastResult} />
      <ScoreboardCard />

      <div style={{ fontSize: 10, color: "#1e3a5c", lineHeight: 1.6, padding: "0 4px" }}>
        ⚠ 엔진은 단일 인스턴스로, 한 번에 한 전략만 실행합니다. replay는 신호 워밍업
        전용이라 주문이 발생하지 않으며, tick의 submit 토글은 현재 운용모드에 따라
        라우팅됩니다. 실시간 자동 폴링은 별도 PR.
      </div>
    </div>
  );
}
