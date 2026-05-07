import { useEffect, useState } from "react";
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
    // 239 (Light-002): light surface + 토큰 기반 색.
    <div data-testid="operator-panel" style={_panelStyle(emergencyStop, readinessColor)}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <span style={{
          fontSize: "var(--fs-md)", fontWeight: "var(--fw-bold)",
          color: "var(--c-text)", letterSpacing: "0.04em",
        }}>📱 Operator</span>
        <span data-testid="virtual-mode-badge" style={{
          fontSize: "var(--fs-xs)", padding: "4px 10px", borderRadius: "var(--r-md)",
          color: "var(--c-accent)",
          border: "1px solid #ddd6fe",
          background: "#f5f3ff",
          fontWeight: "var(--fw-bold)",
        }}>🧪 VIRTUAL MODE</span>
      </div>

      {error && (
        <div style={{
          fontSize: "var(--fs-sm)", color: "var(--c-danger)",
          marginBottom: 8, padding: "6px 10px",
          background: "#fef2f2", border: "1px solid #fecaca",
          borderRadius: "var(--r-md)",
        }}>
          ⚠ 일부 데이터를 불러올 수 없습니다 (데모 모드일 수 있어요).
        </div>
      )}

      {/* 3 big buttons — 모바일에서도 누르기 쉽게 큰 hit area */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginBottom: 14 }}>
        <button data-testid="operator-start"
                onClick={start}
                disabled={intent === "running"}
                style={_buttonStyle({
                  active: intent === "running", color: "#10b981",
                  bgIdle: "#ecfdf5", bgActive: "#10b981",
                })}>
          ▶ 시작
        </button>
        <button data-testid="operator-pause"
                onClick={pause}
                disabled={intent === "paused"}
                style={_buttonStyle({
                  active: intent === "paused", color: "#64748b",
                  bgIdle: "#f1f5f9", bgActive: "#64748b",
                })}>
          ⏸ 일시정지
        </button>
        <button data-testid="operator-emergency-stop"
                onClick={onEmergencyStop}
                style={_buttonStyle({
                  active: false, color: "#fff",
                  bgIdle: "#ef4444", bgActive: "#ef4444",
                  big: true,
                })}>
          🛑 긴급정지
        </button>
      </div>

      {/* compact status grid */}
      <div data-testid="operator-status" style={{
        display: "grid", gridTemplateColumns: "1fr 1fr",
        gap: 6, fontSize: "var(--fs-sm)",
      }}>
        <_StatusRow label="운영자 의도" value={intentLabel} color={intentColor} />
        <_StatusRow label="긴급 정지"
                    value={emergencyStop ? "ON" : "OFF"}
                    color={emergencyStop ? "var(--c-danger)" : "var(--c-success)"} />
        <_StatusRow label="준비도"
                    value={readinessScore !== null ? `${readinessLabel} (${readinessScore})` : readinessLabel}
                    color={readinessColor} />
        <_StatusRow label="장세"
                    value={`${regimeLabel} · ${tradePerm}`}
                    color="var(--c-info)" />
        <_StatusRow label="가상 포지션"
                    value={openPos === null ? "—" : `${openPos}건`}
                    color="var(--c-accent)" />
        <_StatusRow label="결재 대기"
                    value={`${pendingCount}건`}
                    color={pendingCount > 0 ? "var(--c-warning)" : "var(--c-text-3)"} />
      </div>

      <div style={{
        fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
        marginTop: 12, lineHeight: "var(--lh-loose)",
      }}>
        시작/일시정지는 운영자 의도 기록 (가상 모드 advisory). 긴급정지는 RiskManager
        의 실제 토글입니다 — ON 상태에서는 모든 모드의 신규 주문이 차단됩니다.
      </div>
    </div>
  );
}


function _panelStyle(emergencyStop, readinessColor) {
  // accent border — emergency 시 빨강, 평소 readiness 색.
  const border = emergencyStop ? "#fca5a5" : "var(--c-border)";
  return {
    background: "var(--c-surface)",
    border: `1px solid ${border}`,
    borderRadius: "var(--r-xl)",
    padding: "var(--s-5)",
    boxShadow: "var(--sh-1)",
  };
}


function _StatusRow({ label, value, color }) {
  return (
    <div style={{
      background: "var(--c-surface-2)",
      border: "1px solid var(--c-border)",
      padding: "8px 12px", borderRadius: "var(--r-md)",
      display: "flex", justifyContent: "space-between", alignItems: "center",
    }}>
      <span style={{ color: "var(--c-text-3)", fontSize: "var(--fs-xs)",
                      fontWeight: "var(--fw-medium)" }}>{label}</span>
      <span style={{ color, fontWeight: "var(--fw-bold)",
                      fontSize: "var(--fs-sm)" }}>{value}</span>
    </div>
  );
}


function _buttonStyle({ active, color, bgIdle, bgActive, big = false }) {
  // active(현재 의도가 이 버튼인 상태)면 강조 색 그대로, 그 외엔 light idle.
  return {
    padding: big ? "14px 0" : "12px 0",
    border: `1px solid ${active ? bgActive : (big ? "transparent" : "var(--c-border)")}`,
    borderRadius: "var(--r-md)",
    cursor: "pointer",
    background: active ? bgActive : bgIdle,
    color: active ? "#fff" : (big ? color : color),
    fontFamily: "inherit",
    fontSize: big ? "var(--fs-md)" : "var(--fs-sm)",
    fontWeight: "var(--fw-bold)",
  };
}
