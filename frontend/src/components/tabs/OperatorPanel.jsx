import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";
import { usePersistedState } from "../../store/usePersistedState";

// 227: Smartphone Operator Panel — 사용자가 3초 안에 현재 상태를 인지하고
// 시작 / 일시정지 / 긴급중단만 누를 수 있도록 단순화. PC에서도 동일 패널이
// 노출되며 (반응형 222 grid 안에서 한 행 차지), 모바일에서는 단일 컬럼 형태.
//
// CLAUDE.md 준수:
// - 시작/일시정지는 운영자 intent를 localStorage에만 저장 (운영자 의도 표시).
//   실제 자동 운용 ON/OFF 스위치는 별도 PR.
// - 긴급중단은 기존 backendApi.setEmergencyStop을 호출 — RiskManager가
//   처리하는 진짜 토글.
// - 화면은 모든 데이터를 advisory로 표시 — 어떤 broker 주문도 만들지 않는다.

const INTENT_STORAGE_KEY = "autotrade.operatorIntent";
const _VALID_INTENTS = new Set(["running", "paused"]);

export const isValidOperatorIntent = (v) => _VALID_INTENTS.has(v);


export function OperatorPanel({ pendingCount = 0, emergencyStop = false, onEmergencyStop }) {
  const [intent, setIntent] = usePersistedState(
    INTENT_STORAGE_KEY, "paused", isValidOperatorIntent,
  );
  const [readiness, setReadiness] = useState(null);
  const [regime,    setRegime]    = useState(null);
  const [openPos,   setOpenPos]   = useState(null);
  const [error,     setError]     = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [r, rg, vp] = await Promise.all([
          backendApi.preMarketBrief({}),
          backendApi.marketRegime({}),
          backendApi.virtualPositions({}),
        ]);
        if (cancelled) return;
        setReadiness(r);
        setRegime(rg);
        setOpenPos(Array.isArray(vp) ? vp.length : 0);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const start = () => setIntent("running");
  const pause = () => setIntent("paused");

  const intentColor = intent === "running" ? "#22c55e" : "#94a3b8";
  const intentLabel = intent === "running" ? "RUNNING" : "PAUSED";

  // 색·라벨은 readiness가 있을 때만. 없으면 회색 + dash.
  const readinessLabel = readiness?.readiness_label || "—";
  const readinessScore = typeof readiness?.readiness_score === "number" ? readiness.readiness_score : null;
  const readinessColor = readinessLabel === "READY" ? "#22c55e"
                       : readinessLabel === "CAUTION" ? "#fbbf24"
                       : readinessLabel === "BLOCKED" ? "#ef4444" : "#94a3b8";

  const regimeLabel = regime?.regime || "—";
  const tradePerm   = regime?.trade_permission || "—";

  return (
    <Card data-testid="operator-panel" accentColor={emergencyStop ? "#ef444466" : `${readinessColor}33`}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>📱 Operator</SectionLabel>
        <span data-testid="virtual-mode-badge" style={{
          fontSize: 9, padding: "2px 6px", borderRadius: 3,
          color: "#7dd3fc", border: "1px solid #7dd3fc55", background: "#0c2035",
        }}>🧪 VIRTUAL MODE</span>
      </div>

      {error && (
        <div style={{ fontSize: 11, color: "#f87171", marginBottom: 6 }}>
          데이터 일부 조회 실패: {error}
        </div>
      )}

      {/* 3 big buttons */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginBottom: 10 }}>
        <button data-testid="operator-start"
                onClick={start}
                disabled={intent === "running"}
                style={_buttonStyle(intent === "running" ? "#22c55e" : "#0c2035",
                                    intent === "running" ? "#010a14" : "#22c55e")}>
          ▶ 시작
        </button>
        <button data-testid="operator-pause"
                onClick={pause}
                disabled={intent === "paused"}
                style={_buttonStyle(intent === "paused" ? "#94a3b8" : "#0c2035",
                                    intent === "paused" ? "#010a14" : "#94a3b8")}>
          ⏸ 일시정지
        </button>
        <button data-testid="operator-emergency-stop"
                onClick={onEmergencyStop}
                style={_buttonStyle("#ef4444", "#fff", true)}>
          🛑 긴급정지
        </button>
      </div>

      {/* compact status grid */}
      <div data-testid="operator-status" style={{
        display: "grid", gridTemplateColumns: "1fr 1fr",
        gap: 6, fontSize: 11,
      }}>
        <_StatusRow label="운영자 의도" value={intentLabel} color={intentColor} />
        <_StatusRow label="긴급 정지"
                    value={emergencyStop ? "ON" : "OFF"}
                    color={emergencyStop ? "#ef4444" : "#22c55e"} />
        <_StatusRow label="준비도"
                    value={readinessScore !== null ? `${readinessLabel} (${readinessScore})` : readinessLabel}
                    color={readinessColor} />
        <_StatusRow label="장세"
                    value={`${regimeLabel} · ${tradePerm}`}
                    color="#7dd3fc" />
        <_StatusRow label="가상 포지션"
                    value={openPos === null ? "—" : `${openPos}건`}
                    color="#a78bfa" />
        <_StatusRow label="결재 대기"
                    value={`${pendingCount}건`}
                    color={pendingCount > 0 ? "#f59e0b" : "#94a3b8"} />
      </div>

      <div style={{ fontSize: 9, color: "#334155", marginTop: 8, lineHeight: 1.5 }}>
        시작/일시정지는 운영자 의도 기록(가상 모드 advisory). 긴급정지는 RiskManager
        의 실제 토글입니다 — ON 상태에서는 모든 모드의 신규 주문이 차단됩니다.
      </div>
    </Card>
  );
}


function _StatusRow({ label, value, color }) {
  return (
    <div style={{
      background: "#0c2035", padding: "4px 8px", borderRadius: 3,
      display: "flex", justifyContent: "space-between", alignItems: "center",
    }}>
      <span style={{ color: "#475569", fontSize: 9 }}>{label}</span>
      <span style={{ color, fontWeight: 700 }}>{value}</span>
    </div>
  );
}


function _buttonStyle(bg, color, big = false) {
  return {
    padding: big ? "10px 0" : "8px 0",
    border: "none",
    borderRadius: 4,
    cursor: "pointer",
    background: bg,
    color: color,
    fontFamily: "inherit",
    fontSize: big ? 13 : 11,
    fontWeight: 700,
  };
}
