import { useMemo, useState } from "react";
import { Btn, Card, Inp, SectionLabel } from "../common";
import { fmtKRW, pnlColor } from "../../utils/format";
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
    </Card>
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
  const { signal, intended_order, routing } = result;
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

      <div style={{ fontSize: 10, color: "#1e3a5c", lineHeight: 1.6, padding: "0 4px" }}>
        ⚠ 엔진은 단일 인스턴스로, 한 번에 한 전략만 실행합니다. replay는 신호 워밍업
        전용이라 주문이 발생하지 않으며, tick의 submit 토글은 현재 운용모드에 따라
        라우팅됩니다. 실시간 자동 폴링은 별도 PR.
      </div>
    </div>
  );
}
