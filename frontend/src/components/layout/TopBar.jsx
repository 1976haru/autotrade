import { useState, useEffect } from "react";
import { BROKERS } from "../../config/brokers";
import { APP_NAME, APP_VERSION } from "../../config/constants";

export function TopBar({ brokerId, tradeMode, connected }) {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const broker = BROKERS[brokerId];
  const timeStr = [time.getHours(), time.getMinutes(), time.getSeconds()]
    .map((v) => String(v).padStart(2, "0")).join(":");

  return (
    <div style={{
      background: "#020e1c",
      borderBottom: "1px solid #0c2035",
      padding: "10px 16px",
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      flexShrink: 0,
    }}>
      <div>
        <div style={{ fontSize: 9, color: "#00d4aa", letterSpacing: "0.2em" }}>
          ◆ AI QUANT v{APP_VERSION}
        </div>
        <div style={{ fontSize: 15, fontWeight: 700, letterSpacing: "-0.02em" }}>
          {APP_NAME}
        </div>
      </div>

      <div style={{ display: "flex", gap: 14, alignItems: "center", fontSize: 10 }}>
        <div style={{ textAlign: "right" }}>
          <div style={{ color: tradeMode === "live" ? "#ef4444" : "#7dd3fc", fontWeight: 700 }}>
            {tradeMode === "live" ? "● LIVE" : "● SIM"}
          </div>
          <div style={{ color: broker?.color ?? "#94a3b8" }}>
            {broker?.short ?? broker?.name}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ color: "#475569" }}>
            {time.toLocaleDateString("ko-KR")}
          </div>
          <div style={{ color: "#00d4aa", fontWeight: 700, fontSize: 13 }}>{timeStr}</div>
        </div>
        {/* 연결 상태 LED */}
        <div style={{
          width: 9, height: 9, borderRadius: "50%",
          background: connected ? "#22c55e" : "#334155",
          boxShadow: connected ? "0 0 8px #22c55e" : "none",
          transition: "all .3s",
        }} />
      </div>
    </div>
  );
}
