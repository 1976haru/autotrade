import { useState } from "react";
import { Card, SectionLabel, Inp } from "../common";
import { backendApi } from "../../services/backend/client";

// 48: Futures margin / leverage / liquidation risk preview card (read-only).
//
// 운영자가 선물 주문을 *체결하기 전*에 증거금 / 레버리지 / 강제청산 위험을
// 사전 시뮬할 수 있다. broker 호출 0건, audit row 0건 — POST는 read-only
// helper.
//
// 본 카드는 *미래 LIVE 어댑터*를 위한 인터페이스 노출 — 본 PR 시점에는
// MockFuturesBroker / 가상 환경에서만 의미 있다.

const _BANNER = {
  PASS:  { color: "#22c55e", bg: "#0c2035",  label: "통과" },
  WARN:  { color: "#fbbf24", bg: "#3b2a05", label: "경고" },
  BLOCK: { color: "#ef4444", bg: "#3b1f25", label: "차단" },
};


function _RuleResultRow({ label, result }) {
  const palette = _BANNER[result?.decision] || _BANNER.PASS;
  return (
    <div data-testid={`futures-margin-rule-${label}`}
         style={{ display: "flex", justifyContent: "space-between",
                   padding: "5px 0", borderBottom: "1px solid #1e3a5c33",
                   fontSize: 11 }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span style={{ color: palette.color, fontWeight: 700 }}>
        {palette.label}
      </span>
    </div>
  );
}


function _ReasonsBlock({ result }) {
  if (!result) return null;
  const reasons  = result.reasons  || [];
  const warnings = result.warnings || [];
  if (reasons.length === 0 && warnings.length === 0) return null;
  return (
    <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 4,
                   paddingLeft: 8, borderLeft: "2px solid #1e3a5c66" }}>
      {reasons.map((r, i) => (
        <div key={`r${i}`} style={{ color: "#fca5a5" }}>− {r}</div>
      ))}
      {warnings.map((w, i) => (
        <div key={`w${i}`} style={{ color: "#fbbf24" }}>⚠ {w}</div>
      ))}
    </div>
  );
}


export function FuturesMarginRiskCard() {
  const [contract,        setContract]        = useState("KOSPI200_2503");
  const [side,            setSide]            = useState("BUY");
  const [quantity,        setQuantity]        = useState("1");
  const [markPrice,       setMarkPrice]       = useState("1000000");
  const [leverage,        setLeverage]        = useState("5");
  const [marginUsed,      setMarginUsed]      = useState("0");
  const [marginAvailable, setMarginAvailable] = useState("10000000");

  const [busy,    setBusy]    = useState(false);
  const [error,   setError]   = useState("");
  const [result,  setResult]  = useState(null);

  const submit = async () => {
    if (!contract.trim()) return;
    setBusy(true); setError(""); setResult(null);
    try {
      const body = {
        contract:         contract.trim(),
        side,
        quantity:         Math.max(1, Number(quantity) || 1),
        order_type:       "MARKET",
        mark_price:       Math.max(1, Number(markPrice) || 1),
        leverage:         Math.max(0.01, Number(leverage) || 0.01),
        margin_used:      Math.max(0, Number(marginUsed) || 0),
        margin_available: Math.max(0, Number(marginAvailable) || 0),
        positions:        [],
      };
      const res = await backendApi.futuresMarginPreview(body);
      setResult(res);
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  };

  const overall = result?.overall;
  const overallPalette = _BANNER[overall];

  return (
    <Card data-testid="futures-margin-risk-card">
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>마진/레버리지/강제청산 사전 평가</SectionLabel>
        <span data-testid="futures-margin-readonly-badge" style={{
          fontSize: 9, fontWeight: 700, color: "#94a3b8",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #94a3b855", background: "#94a3b815",
        }}>
          read-only / 실제 주문 아님
        </span>
      </div>

      <div style={{ fontSize: 9, color: "#334155", marginBottom: 8, lineHeight: 1.5 }}>
        주문이 들어가면 증거금 / 레버리지 / 강제청산 거리(%)가 어떻게 변하는지
        체결 *전*에 시뮬합니다. broker 호출 0건, audit 기록 0건.
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6,
                    marginBottom: 6 }}>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>contract</div>
          <Inp value={contract} onChange={setContract} placeholder="KOSPI200_2503" />
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>방향</div>
          <select
            data-testid="futures-margin-side-select"
            value={side}
            onChange={(e) => setSide(e.target.value)}
            style={{ width: "100%", padding: "6px 8px",
                      background: "#0c2035", color: "#7dd3fc",
                      border: "1px solid #1e3a5c", borderRadius: 4,
                      fontFamily: "inherit", fontSize: 12 }}
          >
            <option value="BUY">매수 (LONG)</option>
            <option value="SELL">매도 (SHORT)</option>
          </select>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6,
                    marginBottom: 6 }}>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>계약 수</div>
          <Inp value={quantity} onChange={setQuantity} type="number" />
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>mark price</div>
          <Inp value={markPrice} onChange={setMarkPrice} type="number" />
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>leverage</div>
          <Inp value={leverage} onChange={setLeverage} type="number" />
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6,
                    marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>증거금 사용</div>
          <Inp value={marginUsed} onChange={setMarginUsed} type="number" />
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>증거금 가능</div>
          <Inp value={marginAvailable} onChange={setMarginAvailable} type="number" />
        </div>
      </div>

      <button
        type="button"
        data-testid="futures-margin-evaluate-btn"
        onClick={submit}
        disabled={busy || !contract.trim()}
        style={{
          width: "100%", padding: "10px 20px", borderRadius: "var(--r-md)",
          border: "none", cursor: (busy || !contract.trim()) ? "not-allowed" : "pointer",
          background: (busy || !contract.trim()) ? "var(--c-surface-3)" : "#7dd3fc",
          color: (busy || !contract.trim()) ? "var(--c-text-4)" : "#0c2035",
          fontWeight: "var(--fw-bold)", fontSize: "var(--fs-base)",
          fontFamily: "inherit", letterSpacing: "0.02em",
        }}
      >
        {busy ? "⟳ 평가 중..." : "📊 마진/위험 사전 평가"}
      </button>

      {error && (
        <div data-testid="futures-margin-error"
             style={{ marginTop: 8, padding: "6px 8px", background: "#3b1f25",
                       border: "1px solid #ef444466", borderRadius: 4,
                       color: "#fca5a5", fontSize: 11 }}>
          {error}
        </div>
      )}

      {result && (
        <div style={{ marginTop: 10 }}>
          <div data-testid="futures-margin-overall"
               style={{ padding: "8px 10px",
                         background: overallPalette.bg,
                         border: `1px solid ${overallPalette.color}66`,
                         borderRadius: 4, marginBottom: 8 }}>
            <div style={{ color: overallPalette.color, fontWeight: 700,
                           fontSize: 11 }}>
              종합 결정: {overallPalette.label}
            </div>
            <div style={{ fontSize: 9, color: "#94a3b8", marginTop: 2 }}>
              가장 보수적인 Rule 결정이 효력 — BLOCK이 하나라도 있으면 차단,
              WARN이 하나라도 있으면 경고.
            </div>
          </div>

          <_RuleResultRow label="LeverageLimitRule"   result={result.leverage} />
          <_ReasonsBlock result={result.leverage} />
          <_RuleResultRow label="FuturesMarginRule"   result={result.margin} />
          <_ReasonsBlock result={result.margin} />
          <_RuleResultRow label="LiquidationRiskRule" result={result.liquidation} />
          <_ReasonsBlock result={result.liquidation} />

          <div style={{ marginTop: 8, fontSize: 9, color: "#64748b",
                         lineHeight: 1.5 }}>
            {result.notice}
          </div>
        </div>
      )}
    </Card>
  );
}
