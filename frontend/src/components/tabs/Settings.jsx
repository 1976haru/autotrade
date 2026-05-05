import { BROKERS } from "../../config/brokers";
import { Card, SectionLabel, Btn, Inp } from "../common";

export function Settings({ settings }) {
  const {
    brokerId, broker, tradeMode, apiKeys,
    connected, connecting, connMsg,
    switchBroker, switchMode, updateKey, connect,
  } = settings;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>

      {/* 거래 모드 */}
      <Card>
        <SectionLabel>거래 모드</SectionLabel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          {[
            ["sim",  "🔵 시뮬레이션", "#7dd3fc"],
            ["live", "🔴 실전 거래",  "#ef4444"],
          ].map(([mode, label, color]) => (
            <button
              key={mode}
              onClick={() => switchMode(mode)}
              style={{
                padding: 10, borderRadius: 4, cursor: "pointer",
                fontFamily: "inherit", fontWeight: 700, fontSize: 12,
                background: tradeMode === mode ? color + "22" : "transparent",
                border:     `1px solid ${tradeMode === mode ? color : "#1a3a5c"}`,
                color:      tradeMode === mode ? color : "#475569",
              }}
            >
              {label}
            </button>
          ))}
        </div>
        {tradeMode === "live" && (
          <div style={{ marginTop: 8, padding: "8px 10px", background: "#ef444415", borderRadius: 4, fontSize: 11, color: "#fca5a5" }}>
            ⚠ 실제 자금이 사용됩니다 — API 설정을 반드시 확인하세요
          </div>
        )}
      </Card>

      {/* 증권사 선택 */}
      <Card>
        <SectionLabel>증권사 선택</SectionLabel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 12 }}>
          {Object.values(BROKERS).map((b) => (
            <button
              key={b.id}
              onClick={() => { if (!b.disabled) switchBroker(b.id); }}
              disabled={b.disabled}
              style={{
                padding: "10px 8px", borderRadius: 4,
                cursor:     b.disabled ? "not-allowed" : "pointer",
                fontFamily: "inherit", textAlign: "left",
                background: brokerId === b.id ? b.color + "22" : "transparent",
                border:     `1px solid ${brokerId === b.id ? b.color : "#1a3a5c"}`,
                color:      b.disabled ? "#334155" : brokerId === b.id ? b.color : "#64748b",
                opacity:    b.disabled ? 0.5 : 1,
                fontSize:   12, fontWeight: 700,
              }}
            >
              <div>{b.name}</div>
              <div style={{ fontSize: 10, color: "#475569", marginTop: 2, fontWeight: 400 }}>{b.note}</div>
            </button>
          ))}
        </div>

        {/* API Key 입력 필드 */}
        {tradeMode !== "sim" && !broker.disabled && broker.fields?.map((f) => (
          <div key={f.key} style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>{f.label}</div>
            <Inp
              value={apiKeys[f.key] || ""}
              onChange={(v) => updateKey(f.key, v)}
              placeholder={f.placeholder || f.label}
              type={f.type}
            />
          </div>
        ))}

        <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 8 }}>
          <Btn color={broker.color || "#7dd3fc"} onClick={connect} disabled={connecting}>
            {connecting ? "연결 중..." : connected ? "✓ 재연결" : "API 연결"}
          </Btn>
          {connected && <span style={{ fontSize: 12, color: "#22c55e" }}>● 연결됨</span>}
          {connMsg && (
            <span style={{ fontSize: 11, color: connMsg.includes("성공") || connMsg.includes("시뮬") ? "#22c55e" : "#f87171" }}>
              {connMsg}
            </span>
          )}
        </div>
      </Card>

      {/* 백엔드 프록시 가이드 */}
      {tradeMode === "live" && (
        <Card accentColor="#f59e0b33">
          <SectionLabel>⚙ 실전 백엔드 프록시 설정</SectionLabel>
          <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.9 }}>
            국내 증권사 REST API는 브라우저 직접 호출 시 CORS 차단됩니다.<br />
            <span style={{ color: "#f59e0b" }}>① Python FastAPI / Node.js 로컬 프록시 서버 구축</span><br />
            <span style={{ color: "#f59e0b" }}>② API Key는 서버 .env 파일에만 보관 (보안)</span><br />
            <span style={{ color: "#f59e0b" }}>③ 앱 → localhost:8080/proxy/ 경유 호출</span><br />
            <span style={{ color: "#7dd3fc" }}>★ 권장: 한국투자증권(KIS) — REST API 문서 최충실</span>
          </div>
        </Card>
      )}

      {/* PWA 설치 안내 */}
      <Card accentColor="#a855f733">
        <SectionLabel>📱 스마트폰 앱으로 설치 (PWA)</SectionLabel>
        <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.9 }}>
          <span style={{ color: "#a855f7", fontWeight: 700 }}>Android Chrome</span>: 우상단 메뉴 → 홈 화면에 추가<br />
          <span style={{ color: "#a855f7", fontWeight: 700 }}>iOS Safari</span>: 공유 버튼 → 홈 화면에 추가<br />
          → 설치 후 전체화면 앱으로 실행됩니다
        </div>
      </Card>

      {/* 버전 정보 */}
      <div style={{ textAlign: "center", fontSize: 10, color: "#1e3a5c", padding: "8px 0" }}>
        AI Auto Trader v2.1.0 · 모듈식 아키텍처 · Claude Sonnet 4
      </div>
    </div>
  );
}
