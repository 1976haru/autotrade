// UI 대정리 STEP 2: 홈 "조작 버튼 한 줄" — 시작 / 정지 / 긴급정지를 한 곳에만.
// ★동작 코드는 신설하지 않는다 — App.jsx가 주입한 기존 핸들러(bot.start/stop,
// riskPolicy.toggleEmergency)를 *그대로* 호출(위치만 이동). 안전 문구 보존.
import { Card } from "./index";

export function HomeControlBar({
  running,
  onStart,
  onStop,
  emergencyStop,
  onEmergencyStop,
}) {
  return (
    <Card>
      <div data-testid="home-control-bar" style={{
        display: "flex", alignItems: "center", flexWrap: "wrap", gap: 10,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 120 }}>
          <span style={{
            width: 10, height: 10, borderRadius: "50%",
            background: running ? "#10b981" : "var(--c-text-4)",
            boxShadow: running ? "0 0 0 4px #10b98133" : "none",
          }} />
          <span style={{ fontWeight: "var(--fw-bold)",
                         color: running ? "#10b981" : "var(--c-text-3)" }}>
            {running ? "자동매매 가동 중" : "자동매매 정지"}
          </span>
        </div>

        {/* 시작/정지 — 기존 핸들러 그대로 (running이면 정지, 아니면 시작) */}
        <button
          type="button"
          data-testid="home-control-startstop"
          onClick={running ? onStop : onStart}
          style={{
            padding: "10px 22px", borderRadius: "var(--r-md)", border: "none",
            cursor: "pointer", fontFamily: "inherit", fontWeight: "var(--fw-bold)",
            fontSize: "var(--fs-base)", color: "#fff",
            background: running ? "#ef4444" : "#10b981", boxShadow: "var(--sh-1)",
          }}
        >
          {running ? "⏹ 정지" : "▶ 시작"}
        </button>

        {/* 긴급정지 — 한 곳에만. 기존 onEmergencyStop 핸들러 그대로 */}
        <button
          type="button"
          data-testid="home-control-emergency"
          onClick={onEmergencyStop}
          style={{
            padding: "10px 18px", borderRadius: "var(--r-md)",
            border: `1px solid ${emergencyStop ? "#ef4444" : "#ef444466"}`,
            cursor: onEmergencyStop ? "pointer" : "default", fontFamily: "inherit",
            fontWeight: "var(--fw-bold)", fontSize: "var(--fs-sm)",
            background: emergencyStop ? "#ef4444" : "transparent",
            color: emergencyStop ? "#fff" : "#ef4444",
          }}
        >
          🛑 {emergencyStop ? "긴급정지 해제" : "긴급정지"}
        </button>
      </div>
      <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 8 }}>
        모의투자(Paper) 전용 · 실거래 OFF · KIS_IS_PAPER=true — 실제 돈이 나가지 않습니다.
      </div>
    </Card>
  );
}
