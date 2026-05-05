import { Card, SectionLabel, StatBox } from "../common";
import { fmtKRW, fmtPct, pnlColor } from "../../utils/format";

export function Dashboard({ portfolio, bot, botControls }) {
  const { totalAsset, totalPnL, totalPnLPct, cash, positions } = portfolio;
  const { stats, winRate, trades, running } = bot;
  const { start, stop } = botControls;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>

      {/* KPI */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
        <Card>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>총 자산</div>
          <div style={{ fontSize: 14, fontWeight: 700 }}>{fmtKRW(Math.round(totalAsset))}원</div>
          <div style={{ fontSize: 10, color: "#334155", marginTop: 2 }}>현금 {fmtKRW(cash)}원</div>
        </Card>
        <Card>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>평가손익</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: pnlColor(totalPnL) }}>
            {totalPnL >= 0 ? "+" : ""}{fmtKRW(Math.round(totalPnL))}원
          </div>
          <div style={{ fontSize: 10, color: pnlColor(totalPnLPct), marginTop: 2 }}>
            {fmtPct(totalPnLPct)}
          </div>
        </Card>
        <Card>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>봇 누적</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: pnlColor(stats.pnl) }}>
            {stats.pnl >= 0 ? "+" : ""}{fmtKRW(stats.pnl)}원
          </div>
          <div style={{ fontSize: 10, color: "#334155", marginTop: 2 }}>승률 {winRate}%</div>
        </Card>
      </div>

      {/* 봇 컨트롤 */}
      <Card accentColor={running ? "#22c55e33" : undefined}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
              width: 8, height: 8, borderRadius: "50%",
              background: running ? "#22c55e" : "#334155",
              boxShadow: running ? "0 0 8px #22c55e" : "none",
            }} />
            <span style={{ fontSize: 12, fontWeight: 700, color: running ? "#22c55e" : "#475569" }}>
              {running ? "BOT RUNNING" : "BOT STOPPED"}
            </span>
          </div>
          <button
            onClick={running ? stop : start}
            style={{
              padding: "7px 18px", borderRadius: 4, border: "none",
              cursor: "pointer", fontFamily: "inherit", fontWeight: 700, fontSize: 12,
              background: running ? "#ef4444" : "#22c55e",
              color: "#010a14",
            }}
          >
            {running ? "⏹ 정지" : "▶ 시작"}
          </button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", textAlign: "center" }}>
          <StatBox label="매매" value={stats.total} color="#7dd3fc" />
          <StatBox label="승"   value={stats.wins}  color="#22c55e" />
          <StatBox label="패"   value={stats.losses} color="#ef4444" />
          <StatBox label="승률" value={`${winRate}%`} color={+winRate >= 55 ? "#22c55e" : "#f59e0b"} />
        </div>
      </Card>

      {/* 포지션 */}
      <Card>
        <SectionLabel>LIVE POSITIONS</SectionLabel>
        {positions.length === 0 ? (
          <div style={{ color: "#1e3a5c", textAlign: "center", padding: 16, fontSize: 12 }}>
            보유 포지션 없음
          </div>
        ) : positions.map((p) => {
          const pnl = (p.cur - p.avg) * p.qty;
          const pp  = ((p.cur - p.avg) / p.avg) * 100;
          return (
            <div key={p.code} style={{
              display: "flex", justifyContent: "space-between",
              padding: "7px 0", borderBottom: "1px solid #05121f", fontSize: 12,
            }}>
              <div>
                <span style={{ color: "#7dd3fc", fontSize: 11 }}>{p.code}</span>
                <br /><span>{p.name}</span>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ color: pnlColor(pnl), fontWeight: 700 }}>
                  {pnl >= 0 ? "+" : ""}{fmtKRW(Math.round(pnl))}원
                </div>
                <div style={{ fontSize: 11, color: pnlColor(pp) }}>{fmtPct(pp)}</div>
              </div>
            </div>
          );
        })}
      </Card>

      {/* 최근 체결 */}
      <Card>
        <SectionLabel>RECENT TRADES {running && <span style={{ color: "#22c55e" }}>● LIVE</span>}</SectionLabel>
        {trades.length === 0 ? (
          <div style={{ color: "#1e3a5c", textAlign: "center", padding: 16, fontSize: 12 }}>
            봇 시작 후 체결 내역 표시
          </div>
        ) : trades.slice(0, 8).map((t) => (
          <div key={t.id} style={{
            display: "flex", justifyContent: "space-between",
            padding: "5px 0", borderBottom: "1px solid #05121f", fontSize: 11,
          }}>
            <span style={{ color: "#334155" }}>{t.ts}</span>
            <span style={{ color: "#94a3b8" }}>{t.name}</span>
            <span style={{ color: pnlColor(t.pnl), fontWeight: 700 }}>
              {t.pnl >= 0 ? "+" : ""}{fmtKRW(t.pnl)}원
            </span>
          </div>
        ))}
      </Card>
    </div>
  );
}
