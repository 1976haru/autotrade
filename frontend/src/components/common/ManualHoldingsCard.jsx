import { useState } from "react";

import { backendApi } from "../../services/backend/client";
import { fmtKRW } from "../../utils/format";
import { splitHoldingsBySource, SOURCE_BADGE } from "../../utils/holdingsBySource";

// 설계 B 조각 1 — 보유 2섹션(🤖 봇 운용 / 👤 직접 보유) + 수동 매수.
//   ★조각 1: 봇 격리(MANUAL 차감)는 미작동(bot_isolation_active=false) → 경고 배너 강제.
//   봇 PAUSED 상태에서만 안전 — 그 안내를 항상 표시한다.

function _Section({ title, badge, stats }) {
  const sign = (stats.eval_pnl_krw || 0) >= 0 ? "+" : "";
  return (
    <div data-testid={`mh-section-${badge}`} style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 800, color: "#2a1418", marginBottom: 4 }}>
        {SOURCE_BADGE[badge].icon} {title}
      </div>
      <div style={{ fontSize: 12, color: "rgba(40,20,24,.7)" }}>
        {stats.count}종목 · 평가손익 <b style={{ color: (stats.eval_pnl_krw || 0) >= 0 ? "#0f7b3b" : "#b3261e" }}>
          {sign}{fmtKRW(stats.eval_pnl_krw || 0)}원</b>
        {stats.return_pct != null && <span> ({sign}{stats.return_pct}%)</span>}
      </div>
      {stats.rows.length === 0 && (
        <div style={{ fontSize: 12, color: "rgba(40,20,24,.45)", marginTop: 4 }}>보유 없음</div>
      )}
      {stats.rows.map((r, i) => (
        <div key={`${r.symbol}-${i}`} style={{ fontSize: 12.5, color: "#2a1418", marginTop: 3 }}>
          {r.name || r.symbol} {r._qty}주
          {r.return_pct != null && <span style={{ color: "rgba(40,20,24,.55)" }}> · {r.return_pct}%</span>}
        </div>
      ))}
    </div>
  );
}

export function ManualHoldingsCard({ live, api = backendApi, onDone }) {
  const positions = live?.positions || [];
  const { bot, manual } = splitHoldingsBySource(positions);
  // 백엔드가 명시적으로 true 를 줄 때만 격리 작동(기본 false = 조각 2 전).
  const isolated = live?.bot_isolation_active === true;

  const [symbol, setSymbol] = useState("");
  const [qty, setQty] = useState(1);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null); // {kind, text}

  const buy = async () => {
    const code = String(symbol || "").trim();
    if (!code) { setNote({ kind: "err", text: "종목 코드를 입력해주세요." }); return; }
    setBusy(true); setNote(null);
    try {
      const res = await api.manualBuy(code, Number(qty));
      setNote({ kind: "ok", text: res?.message || "매수 주문을 보냈어요." });
      onDone?.(res);
    } catch (e) {
      setNote({ kind: "err", text: `매수 실패 — ${e?.message || "다시 시도해주세요"}` });
    } finally {
      setBusy(false);
    }
  };

  const _card = { background: "rgba(255,255,255,.5)", borderRadius: 12, padding: "12px 14px", marginTop: 12 };
  const inp = { padding: "9px 10px", borderRadius: 8, border: "1px solid rgba(40,20,24,.25)", fontSize: 14, fontFamily: "inherit" };

  return (
    <div data-testid="manual-holdings-card" style={_card}>
      <div style={{ fontSize: 14, fontWeight: 800, color: "#2a1418" }}>보유 — 봇 / 직접 분리</div>

      {/* ★조각 1 경고: 봇 격리 미작동 (항상, isolated 가 아닐 때) */}
      {!isolated && (
        <div data-testid="mh-isolation-warning" style={{
          marginTop: 8, fontSize: 12.5, fontWeight: 700, lineHeight: 1.5, borderRadius: 8,
          padding: "8px 10px", background: "rgba(245,170,30,.20)", color: "#7a4a00",
        }}>
          ⚠️ 봇이 아직 직접 보유(👤)를 격리하지 못해요. {live?.manual_isolation_notice
            || "수동 보유 기능은 봇 격리 검증(조각 2) 후 사용하세요 — 지금은 봇 PAUSED 상태에서 표시·태깅만 동작해요."}
        </div>
      )}

      {/* 2섹션 */}
      <div style={{ display: "flex", gap: 12, marginTop: 10 }}>
        <_Section title="봇 운용 (단타)" badge="BOT" stats={bot} />
        <_Section title="직접 보유 (장기)" badge="MANUAL" stats={manual} />
      </div>

      {/* 수동 매수 */}
      <div style={{ marginTop: 12, borderTop: "1px solid rgba(40,20,24,.12)", paddingTop: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#2a1418", marginBottom: 6 }}>직접 매수 (👤)</div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <input data-testid="mh-symbol" style={{ ...inp, width: 120 }} placeholder="종목코드 (예 005930)"
            value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          <input data-testid="mh-qty" style={{ ...inp, width: 80 }} type="number" min={1}
            value={qty} onChange={(e) => setQty(e.target.value)} />
          <span style={{ fontSize: 13, color: "rgba(40,20,24,.6)" }}>주</span>
          <button data-testid="mh-buy" type="button" onClick={buy} disabled={busy}
            style={{
              padding: "9px 16px", borderRadius: 8, border: "none", fontFamily: "inherit",
              background: busy ? "rgba(40,20,24,.25)" : "#3b82f6", color: "#fff",
              fontSize: 14, fontWeight: 800, cursor: busy ? "default" : "pointer",
            }}>{busy ? "주문 중…" : "직접 매수"}</button>
        </div>
        <div style={{ fontSize: 11.5, color: "rgba(40,20,24,.5)", marginTop: 6 }}>
          직접 매수는 봇 일일한도·전략판단을 거치지 않아요(긴급정지·모의투자 안전장치는 통과).
        </div>
        {note && (
          <div data-testid="mh-note" style={{
            marginTop: 8, fontSize: 13, fontWeight: 700, borderRadius: 8, padding: "8px 10px",
            background: note.kind === "ok" ? "rgba(21,160,95,.18)" : "rgba(192,57,43,.16)",
            color: note.kind === "ok" ? "#0f5132" : "#7a1d1d",
          }}>{note.text}</div>
        )}
      </div>
    </div>
  );
}
