import { STRATEGIES } from "../../config/strategies";
import { Card, SectionLabel, Toggle, Slider } from "../common";

export function StrategyRisk({ strategyOn, toggle, strategyParams, updateParam, risk, updateRisk }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ fontSize: 11, color: "#475569", marginBottom: 2 }}>
        복수 전략 동시 활성화 → 신호 합류(Confluence) 시 진입
      </div>

      {/* 전략 카드 */}
      {Object.values(STRATEGIES).map((s) => {
        const isOn = strategyOn[s.id];
        return (
          <Card key={s.id} accentColor={isOn ? s.color + "55" : undefined}>
            {/* 헤더 */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <span style={{ fontSize: 22 }}>{s.icon}</span>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 13, color: isOn ? s.color : "#64748b" }}>
                    {s.name}
                  </div>
                  <div style={{ fontSize: 10, color: "#475569" }}>
                    {s.desc} · 승률 ~{s.winRate}%
                  </div>
                </div>
              </div>
              <Toggle value={isOn} onChange={() => toggle(s.id)} color={s.color} />
            </div>

            {/* 파라미터 (ON일 때만 표시) */}
            {isOn && (
              <div style={{ marginTop: 12, borderTop: "1px solid #0c2035", paddingTop: 12 }}>
                <div style={{ fontSize: 10, color: "#475569", marginBottom: 8, fontStyle: "italic" }}>
                  → {s.detail}
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4 }}>
                  {Object.entries(s.params).map(([pk, pv]) => (
                    <Slider
                      key={pk}
                      label={pv.label}
                      value={strategyParams[s.id]?.[pk] ?? pv.default}
                      min={pv.min} max={pv.max} step={pv.step}
                      onChange={(v) => updateParam(s.id, pk, v)}
                    />
                  ))}
                </div>
                <div style={{ display: "flex", gap: 12, fontSize: 10, color: "#334155", marginTop: 4 }}>
                  <span>⏰ {s.bestTime}</span>
                  <span>🎯 {s.bestTarget}</span>
                </div>
              </div>
            )}
          </Card>
        );
      })}

      {/* 리스크 설정 */}
      <div style={{ marginTop: 10 }}>
        <SectionLabel>━━ 리스크 관리</SectionLabel>
      </div>

      <Card accentColor="#ef444422">
        {[
          { label: "일일 최대 손실 (원)", key: "maxDailyLoss", min: 50_000,    max: 2_000_000, step: 50_000 },
          { label: "종목당 투자 한도 (원)", key: "maxPerTrade",  min: 200_000,  max: 5_000_000, step: 100_000 },
          { label: "최대 보유 종목 수",    key: "maxPositions", min: 1,        max: 20,        step: 1 },
          { label: "연속 손실 정지 (회)",  key: "pauseOnStreak",min: 2,        max: 10,        step: 1 },
          { label: "최대 낙폭 서킷브레이커 (%)", key: "maxDrawdown", min: 2, max: 20, step: 0.5 },
        ].map(({ label, key, ...rest }) => (
          <Slider key={key} label={label} value={risk[key]} onChange={(v) => updateRisk(key, v)} {...rest} />
        ))}
      </Card>

      <Card accentColor="#f59e0b22">
        <SectionLabel>강제 청산 시간</SectionLabel>
        <div style={{ display: "flex", gap: 8 }}>
          {["15:00", "15:10", "15:20", "15:25"].map((t) => (
            <button
              key={t}
              onClick={() => updateRisk("forceCloseAt", t)}
              style={{
                flex: 1, padding: "7px 0", borderRadius: 4,
                border: `1px solid ${risk.forceCloseAt === t ? "#f59e0b" : "#1a3a5c"}`,
                background: risk.forceCloseAt === t ? "#f59e0b" : "transparent",
                color:      risk.forceCloseAt === t ? "#010a14" : "#64748b",
                cursor: "pointer", fontFamily: "inherit", fontSize: 12, fontWeight: 700,
              }}
            >{t}</button>
          ))}
        </div>

        <div style={{ marginTop: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 11, color: "#64748b" }}>트레일링 스탑</span>
          <Toggle
            value={risk.trailingStop}
            onChange={(v) => updateRisk("trailingStop", v)}
            color="#f59e0b"
          />
        </div>
        <div style={{ marginTop: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 11, color: "#64748b" }}>서킷브레이커</span>
          <Toggle
            value={risk.circuitBreaker}
            onChange={(v) => updateRisk("circuitBreaker", v)}
            color="#f59e0b"
          />
        </div>
      </Card>
    </div>
  );
}
