import { useState } from "react";

import { backendApi } from "../../services/backend/client";
import { nowKstHm } from "../../utils/format";

// S2: AI 운용 성향 실전환 (B3 2단계). 봇 정지 상태에서만 변경. 서버 재확인값만 신뢰.
//   표시 숫자는 항상 *클램프 적용 후* 실효값(U3) — 프리셋 원값 금지.
const PROFILES = [
  { value: "conservative", label: "보수적" },
  { value: "balanced", label: "안정적" },
  { value: "aggressive", label: "공격적" },
];

const _conf = (e) => (e ? `확신 기준 ${Math.round(e.effective_min_confidence * 100)}% · 위험 신호 ${e.max_risk_flags}개까지 허용` : "");

export function RiskProfileSwitchCard({ rtConfig, botRunning, onChanged, api = backendApi, confirmFn }) {
  const [note, setNote] = useState(null);
  const [busy, setBusy] = useState(false);

  const active = (rtConfig?.active_profile?.value || "balanced").toLowerCase();
  const profilesEff = rtConfig?.profiles_effective || {};
  const activeEff = profilesEff[active] || rtConfig?.active_profile?.effective;

  const _confirm = confirmFn
    || ((m) => (typeof window !== "undefined" && typeof window.confirm === "function" ? window.confirm(m) : true));

  const pick = async (value) => {
    if (botRunning) {
      setNote({ kind: "info", text: "자동매매를 멈추면 바꿀 수 있어요" });
      return;
    }
    if (value === active) return;
    const label = PROFILES.find((p) => p.value === value)?.label || value;
    const e = profilesEff[value];
    const msg = `${label}으로 바꿀까요?` + (e ? `\n${_conf(e)}` : "");
    if (!_confirm(msg)) return; // 확정 전 미발사
    setBusy(true);
    setNote(null);
    try {
      const res = await api.runtimeProfilePut(value);
      onChanged?.(res); // 서버 재확인값만 신뢰(낙관적 갱신 금지)
      setNote({ kind: "ok", text: `적용됐어요 (${nowKstHm()}) — 다음 매매 판단부터 새 성향으로 진행해요` });
    } catch (err) {
      const reason = (err && (err.detail || err.message)) || "변경에 실패했어요";
      setNote({ kind: "err", text: String(reason) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="profile-switch">
      <div style={{ display: "flex", gap: 8 }}>
        {PROFILES.map((p) => {
          const isActive = p.value === active;
          return (
            <button key={p.value} type="button" data-testid={`profile-tab-${p.value}`}
              aria-pressed={isActive} disabled={busy}
              onClick={() => pick(p.value)}
              style={{
                flex: 1, padding: "12px 4px", borderRadius: 12, cursor: busy ? "default" : "pointer",
                fontFamily: "inherit", fontSize: 14, fontWeight: 800, minHeight: 48,
                border: `2px solid ${isActive ? "#5b9dff" : "rgba(40,20,24,.2)"}`,
                background: isActive ? "rgba(91,157,255,.16)" : "transparent",
                color: isActive ? "#2563eb" : (botRunning ? "rgba(40,20,24,.45)" : "rgba(40,20,24,.75)"),
                opacity: botRunning && !isActive ? 0.6 : 1,
              }}>
              {p.label}{isActive ? " ·적용중" : ""}
            </button>
          );
        })}
      </div>

      {botRunning && (
        <div data-testid="profile-locked" style={{ marginTop: 8, fontSize: 12.5, color: "rgba(40,20,24,.6)" }}>
          자동매매를 멈추면 바꿀 수 있어요
        </div>
      )}

      {activeEff && (
        <div data-testid="profile-effective" style={{ marginTop: 8, fontSize: 12.5, color: "rgba(40,20,24,.7)" }}>
          {_conf(activeEff)}
        </div>
      )}

      {note && (
        <div data-testid="profile-note" style={{
          marginTop: 8, fontSize: 12.5, fontWeight: 700, lineHeight: 1.45, borderRadius: 8, padding: "8px 10px",
          background: note.kind === "ok" ? "rgba(21,160,95,.18)" : note.kind === "err" ? "rgba(192,57,43,.16)" : "rgba(245,170,30,.16)",
          color: note.kind === "ok" ? "#0f5132" : note.kind === "err" ? "#7a1d1d" : "#7a4a00",
        }}>
          {note.text}
        </div>
      )}
    </div>
  );
}
