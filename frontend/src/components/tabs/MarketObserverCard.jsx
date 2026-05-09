import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

// 52: Market Observer card — 장중 시장 환경 snapshot.
//
// **주문 신호가 아님** 명시 — BUY/SELL/HOLD를 표시하지 않으며, 본 카드의
// 어떤 데이터도 자동 주문으로 연결되지 않는다. 운영자/Agent들이 참고할
// context-only.
//
// 절대 invariant:
// - "주문 실행" / "활성화" 버튼 0개 — read-only card.
// - API 응답에 BUY/SELL/HOLD가 없음 (backend dataclass 가드).
// - is_order_signal=False 명시 표시.

const _RISK_PALETTE = {
  LOW:     { color: "#22c55e", bg: "#0c2035",  label: "낮음" },
  MEDIUM:  { color: "#fbbf24", bg: "#3b2a05", label: "보통" },
  HIGH:    { color: "#ef4444", bg: "#3b1f25", label: "높음" },
  BLOCKED: { color: "#64748b", bg: "#1e293b", label: "차단" },
};

const _STANCE_LABEL = {
  AGGRESSIVE:    "적극",
  NORMAL:        "보통",
  DEFENSIVE:     "보수적",
  WATCH_ONLY:    "관찰만",
  PAUSE_NEW_BUY: "신규 매수 중단",
};


function _Field({ label, value, mono = false }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between",
                   padding: "5px 0", borderBottom: "1px solid #1e3a5c33",
                   fontSize: 11 }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span style={{ color: "#e2e8f0", fontWeight: 700,
                      fontFamily: mono ? "monospace" : "inherit" }}>
        {value}
      </span>
    </div>
  );
}


function _SectorList({ label, sectors, color }) {
  if (!sectors || sectors.length === 0) {
    return (
      <_Field label={label} value="—" />
    );
  }
  return (
    <div style={{ display: "flex", justifyContent: "space-between",
                   padding: "5px 0", borderBottom: "1px solid #1e3a5c33",
                   fontSize: 11 }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span style={{ display: "flex", flexWrap: "wrap", gap: 4,
                      justifyContent: "flex-end" }}>
        {sectors.slice(0, 5).map((s) => (
          <span key={s} style={{
            fontSize: 9, fontWeight: 700, color,
            padding: "1px 6px", borderRadius: 3,
            border: `1px solid ${color}66`, background: `${color}15`,
          }}>{s}</span>
        ))}
      </span>
    </div>
  );
}


export function MarketObserverCard({ snapshot, loading, error, onRefresh }) {
  if (loading && !snapshot) {
    return (
      <Card>
        <SectionLabel>📡 시장 관찰</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>📡 시장 관찰</SectionLabel>
        <div data-testid="market-observer-error"
             style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.6 }}>
          시장 관찰 데이터를 아직 불러오지 못했습니다. Demo Mode에서는 mock
          snapshot을 표시합니다.
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

  const risk = _RISK_PALETTE[snapshot.risk_level] || _RISK_PALETTE.MEDIUM;
  const stance = _STANCE_LABEL[snapshot.recommended_stance]
    || snapshot.recommended_stance;

  return (
    <Card data-testid="market-observer-card"
          accentColor={`${risk.color}55`}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>📡 시장 관찰</SectionLabel>
        <span data-testid="market-observer-not-order-badge" style={{
          fontSize: 9, fontWeight: 700, color: "#94a3b8",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #94a3b855", background: "#94a3b815",
        }}>
          주문 신호 아님
        </span>
      </div>

      {/* 3-line summary */}
      <div style={{ marginBottom: 8, padding: "8px 10px",
                     background: risk.bg, borderRadius: 4,
                     border: `1px solid ${risk.color}33` }}>
        {(snapshot.summary_lines || []).map((line, i) => (
          <div key={i} data-testid={`market-observer-line-${i}`}
               style={{ fontSize: 11, color: "#e2e8f0",
                         lineHeight: 1.6 }}>
            {line}
          </div>
        ))}
      </div>

      {/* 핵심 상태 */}
      <_Field
        label="시장 위험도"
        value={
          <span style={{ color: risk.color }}>{risk.label}</span>
        }
      />
      <_Field
        label="권장 스탠스"
        value={
          <span data-testid="market-observer-stance">{stance}</span>
        }
      />
      <_Field
        label="거래대금"
        value={snapshot.turnover_state}
        mono
      />
      <_Field
        label="변동성"
        value={snapshot.volatility_state}
        mono
      />
      <_Field
        label="시세 freshness"
        value={snapshot.freshness_status}
        mono
      />
      <_Field
        label="급등 / 급락"
        value={`${snapshot.surge_count} / ${snapshot.plunge_count}`}
        mono
      />
      <_SectorList label="강한 섹터/테마"
                    sectors={snapshot.leading_sectors}
                    color="#22c55e" />
      <_SectorList label="약한 섹터/테마"
                    sectors={snapshot.lagging_sectors}
                    color="#ef4444" />

      {snapshot.market_regime && (
        <div style={{ marginTop: 6, padding: "5px 8px",
                       background: "#0c2035", borderRadius: 3,
                       fontSize: 10, color: "#94a3b8" }}>
          regime: <span style={{ color: "#7dd3fc",
                                 fontFamily: "monospace" }}>
            {snapshot.market_regime.regime}
          </span>
          {" "}(perm: {snapshot.market_regime.trade_permission})
        </div>
      )}

      <div style={{ marginTop: 8, fontSize: 9, color: "#64748b",
                     lineHeight: 1.5 }}>
        ※ 본 snapshot은 *주문 신호가 아닙니다*. BUY/SELL/HOLD는 RiskManager +
        PermissionGate + OrderExecutor 흐름에서만 만들어집니다.
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


// 52: hook for /api/agents/market-observer. mount 시 1회 호출 + 수동 새로고침.
//
// Demo Mode 또는 backend 미기동 상태에서는 빈 입력으로 호출 → friendly
// fallback (UNKNOWN 상태) 표시. 외부 네트워크 호출 0건.
export function useMarketObserver(input = {}) {
  const [snapshot, setSnapshot] = useState(null);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState("");

  const refresh = async () => {
    setLoading(true); setError("");
    try {
      const data = await backendApi.marketObserver(input);
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
        const data = await backendApi.marketObserver(input);
        if (!cancelled) setSnapshot(data);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
    // input은 dict — input 변경 시 refresh. JSON.stringify로 deep compare.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(input)]);

  return { snapshot, loading, error, refresh };
}
