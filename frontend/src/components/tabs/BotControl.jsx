import { useRef, useEffect } from "react";
import { Card, SectionLabel, StatBox, Btn } from "../common";
import { fmtKRW, pnlColor } from "../../utils/format";

export function BotControl({ bot }) {
  const { running, trades, stats, winRate, start, stop, reset } = bot;
  const logRef = useRef(null);

  // 새 체결 시 맨 위로 스크롤
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = 0;
  }, [trades.length]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* 컨트롤 패널 */}
      <Card accentColor={running ? "#22c55e33" : undefined}>
        <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
          <Btn
            color={running ? "#ef4444" : "#22c55e"}
            onClick={running ? stop : start}
            full
          >
            {running ? "⏹  봇 정지" : "▶  봇 시작 (시뮬레이션)"}
          </Btn>
          <Btn color="#334155" onClick={reset} disabled={running} small>
            초기화
          </Btn>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", textAlign: "center" }}>
          <StatBox label="총 매매" value={stats.total} color="#7dd3fc" />
          <StatBox label="승"      value={stats.wins}  color="#22c55e" />
          <StatBox label="패"      value={stats.losses} color="#ef4444" />
          <StatBox
            label="승률"
            value={`${winRate}%`}
            color={+winRate >= 55 ? "#22c55e" : "#f59e0b"}
          />
        </div>
      </Card>

      {/* 누적 손익 */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <SectionLabel>누적 손익</SectionLabel>
          <span style={{ fontSize: 18, fontWeight: 700, color: pnlColor(stats.pnl) }}>
            {stats.pnl >= 0 ? "+" : ""}{fmtKRW(stats.pnl)}원
          </span>
        </div>
        {/* 간단한 손익 바 */}
        <div style={{ height: 4, background: "#0c2035", borderRadius: 2, overflow: "hidden", marginTop: 4 }}>
          <div style={{
            width: `${Math.min(Math.abs(stats.pnl) / 1000000 * 100, 100)}%`,
            height: "100%",
            background: pnlColor(stats.pnl),
            borderRadius: 2,
          }} />
        </div>
      </Card>

      {/* 체결 로그 */}
      <Card>
        <SectionLabel>
          체결 로그{" "}
          {running && <span style={{ color: "#22c55e" }}>● LIVE</span>}
        </SectionLabel>

        {/* 컬럼 헤더 */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "48px 1fr 72px 64px 42px",
          fontSize: 10, color: "#334155",
          padding: "3px 0", borderBottom: "1px solid #0c2035",
        }}>
          {["시간", "종목", "전략", "손익(원)", "결과"].map((h) => (
            <div key={h}>{h}</div>
          ))}
        </div>

        <div ref={logRef} style={{ maxHeight: 360, overflowY: "auto" }}>
          {trades.length === 0 ? (
            <div style={{ color: "#1e3a5c", textAlign: "center", padding: 24, fontSize: 12 }}>
              봇을 시작하면 체결 내역이 실시간으로 표시됩니다
            </div>
          ) : trades.map((t) => (
            <div
              key={t.id}
              style={{
                display: "grid",
                gridTemplateColumns: "48px 1fr 72px 64px 42px",
                padding: "5px 0",
                borderBottom: "1px solid #05121f",
                fontSize: 11,
                background: t.win ? "#22c55e06" : "#ef444406",
              }}
            >
              <span style={{ color: "#334155" }}>{t.ts.slice(0, 5)}</span>
              <span style={{ color: "#94a3b8" }}>{t.name}</span>
              <span style={{ color: "#64748b", fontSize: 10 }}>{t.strat.slice(0, 6)}</span>
              <span style={{ color: pnlColor(t.pnl), fontWeight: 700 }}>
                {t.pnl >= 0 ? "+" : ""}{fmtKRW(t.pnl)}
              </span>
              <span style={{ color: t.win ? "#22c55e" : "#ef4444", fontWeight: 700 }}>
                {t.win ? "WIN" : "LOSS"}
              </span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
