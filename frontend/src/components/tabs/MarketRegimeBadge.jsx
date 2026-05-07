import { useEffect, useState } from "react";
import { backendApi } from "../../services/backend/client";

// 225: 현재 장세 배지 — Dashboard 상단에 한 줄로 노출. 사용자가 3초 안에
// 인지할 수 있도록 색·라벨·permission만. 상세는 AI 탭의 OperatingLoopCard에서.
//
// 입력은 0으로 호출 — backend가 deterministic 분기로 CHOPPY 폴백 (정량 지표
// 가 외부에서 들어오기 전 기본). 운영자가 향후 시장 데이터 패치를 fetch
// 한 결과를 인자로 넘기면 정확한 regime이 출력된다.

const REGIME_PALETTE = {
  TREND_UP:        { color: "#22c55e", label: "추세 상승" },
  TREND_DOWN:      { color: "#fbbf24", label: "추세 하락" },
  CHOPPY:          { color: "#94a3b8", label: "횡보" },
  HIGH_VOLATILITY: { color: "#f59e0b", label: "고변동성" },
  LOW_LIQUIDITY:   { color: "#a78bfa", label: "유동성 부족" },
  GAP_DAY:         { color: "#fbbf24", label: "갭 데이" },
  NEWS_DRIVEN:     { color: "#7dd3fc", label: "뉴스 주도" },
  RISK_OFF:        { color: "#ef4444", label: "리스크 오프" },
  OPENING_CHAOS:   { color: "#ef4444", label: "장초반 혼란" },
  LATE_DAY_FADE:   { color: "#a78bfa", label: "마감 약화" },
};

export function MarketRegimeBadge() {
  const [regime, setRegime] = useState(null);
  const [error,  setError]  = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await backendApi.marketRegime({});
        if (!cancelled) setRegime(r);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (error) return null;
  if (!regime || typeof regime.regime !== "string") return null;

  const palette = REGIME_PALETTE[regime.regime] ?? { color: "#475569", label: regime.regime };
  // 213/225: 백엔드가 비정상 응답이어도 .toFixed에서 폭발하지 않도록 정규화.
  const riskMult = typeof regime.risk_multiplier === "number" ? regime.risk_multiplier : 1.0;
  const perm     = regime.trade_permission ?? "—";

  return (
    <div data-testid="market-regime-badge" style={{
      display: "flex", alignItems: "center", gap: 8, padding: "6px 10px",
      background: "#020e1c", border: `1px solid ${palette.color}55`,
      borderRadius: 6, fontSize: 11,
    }}>
      <span data-testid="market-regime-label"
            style={{ color: palette.color, fontWeight: 700 }}>
        장세: {palette.label}
      </span>
      <span style={{ color: "#475569", fontSize: 9 }}>
        ({perm} · 리스크 ×{riskMult.toFixed(1)})
      </span>
    </div>
  );
}
