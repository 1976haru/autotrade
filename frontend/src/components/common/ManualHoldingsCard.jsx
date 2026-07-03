import { useState } from "react";

import { backendApi } from "../../services/backend/client";
import { fmtKRW } from "../../utils/format";
import { splitHoldingsBySource, SOURCE_BADGE } from "../../utils/holdingsBySource";

// 설계 B — 보유 2섹션(🤖 봇 운용 / 👤 직접 보유) + 직접 매매 패널.
//   Task D: 직접 매수(가격 조회 → 확인 다이얼로그 → 주문)
//           + 직접 보유 행별 [매도] 버튼 (확인 다이얼로그 → 주문).
//   모든 주문: /api/manual-order → route_order → RiskManager → OrderExecutor.
//   봇 격리(조각 2) 전까지 isolated=false 경고 표시.

const _pnlColor = (v) => (v >= 0 ? "#0f7b3b" : "#b3261e");

function _BotSection({ stats }) {
  return (
    <div data-testid="mh-section-BOT" style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 800, color: "#2a1418", marginBottom: 4 }}>
        {SOURCE_BADGE.BOT.icon} 봇 운용 (단타)
      </div>
      <div style={{ fontSize: 12, color: "rgba(40,20,24,.7)" }}>
        {stats.count}종목 · 평가손익{" "}
        <b style={{ color: _pnlColor(stats.eval_pnl_krw || 0) }}>
          {(stats.eval_pnl_krw || 0) >= 0 ? "+" : ""}
          {fmtKRW(stats.eval_pnl_krw || 0)}원
        </b>
        {stats.return_pct != null && (
          <span> ({stats.return_pct >= 0 ? "+" : ""}{stats.return_pct}%)</span>
        )}
      </div>
      {stats.rows.length === 0 ? (
        <div style={{ fontSize: 12, color: "rgba(40,20,24,.45)", marginTop: 4 }}>보유 없음</div>
      ) : (
        stats.rows.map((r, i) => (
          <div key={`${r.symbol}-${i}`} style={{ fontSize: 12.5, color: "#2a1418", marginTop: 3 }}>
            {r.name || r.symbol} {r._qty}주
            {r.return_pct != null && <span style={{ color: "rgba(40,20,24,.55)" }}> · {r.return_pct >= 0 ? "+" : ""}{r.return_pct}%</span>}
          </div>
        ))
      )}
    </div>
  );
}

export function ManualHoldingsCard({ live, api = backendApi, onDone, confirmFn }) {
  const positions = live?.positions || [];
  const { bot, manual } = splitHoldingsBySource(positions);
  const isolated = live?.bot_isolation_active === true;

  // ── 매수 폼 상태 ──────────────────────────────────────────
  const [symbol, setSymbol] = useState("");
  const [qty, setQty] = useState(1);
  const [quote, setQuote] = useState(null);   // {price, name, fetched_at_kst}
  const [quoting, setQuoting] = useState(false);
  const [buyBusy, setBuyBusy] = useState(false);
  const [buyNote, setBuyNote] = useState(null); // {kind, text}

  // ── 매도 상태 (종목별) ────────────────────────────────────
  const [sellState, setSellState] = useState({}); // {[symbol]: {busy, note}}

  const _confirm = confirmFn
    || ((msg) => (typeof window !== "undefined" && typeof window.confirm === "function" ? window.confirm(msg) : true));

  // ── 현재가 조회 ────────────────────────────────────────────
  const lookupQuote = async () => {
    const code = String(symbol || "").trim().toUpperCase();
    if (!code) return;
    setQuoting(true);
    setQuote(null);
    setBuyNote(null);
    try {
      const q = await api.manualOrderQuote(code);
      setQuote(q);
    } catch (e) {
      setBuyNote({ kind: "err", text: `시세 조회 실패 — ${e?.message || "다시 시도해주세요"}` });
    } finally {
      setQuoting(false);
    }
  };

  // ── 직접 매수 ─────────────────────────────────────────────
  const buy = async () => {
    const code = String(symbol || "").trim().toUpperCase();
    if (!code) { setBuyNote({ kind: "err", text: "종목 코드를 입력해주세요." }); return; }
    const n = Number(qty);
    if (!n || n < 1) { setBuyNote({ kind: "err", text: "수량을 입력해주세요." }); return; }
    const name = quote?.name || code;
    const est = quote?.price ? `약 ${fmtKRW(quote.price * n)}원` : "";
    const msg = `${name} ${n}주 직접 매수할까요?${est ? `\n예상 ${est}` : ""}`;
    if (!_confirm(msg)) return;
    setBuyBusy(true); setBuyNote(null);
    try {
      const res = await api.manualOrder({ symbol: code, side: "BUY", quantity: n });
      setBuyNote({ kind: "ok", text: res?.message || "매수 주문을 보냈어요." });
      setQuote(null);
      onDone?.(res);
    } catch (e) {
      setBuyNote({ kind: "err", text: `매수 실패 — ${e?.detail || e?.message || "다시 시도해주세요"}` });
    } finally {
      setBuyBusy(false);
    }
  };

  // ── 직접 매도 (보유 행별) ─────────────────────────────────
  const sell = async (r) => {
    const code = String(r.symbol || "").toUpperCase();
    const n = Math.max(1, Math.round(Number(r._qty) || 1));
    const name = r.name || code;
    const est = r.market_price ? `약 ${fmtKRW(r.market_price * n)}원` : "";
    const msg = `${name} ${n}주 직접 매도할까요?${est ? `\n예상 ${est}` : ""}`;
    if (!_confirm(msg)) return;
    setSellState((s) => ({ ...s, [code]: { busy: true, note: null } }));
    try {
      const res = await api.manualOrder({ symbol: code, side: "SELL", quantity: n });
      setSellState((s) => ({ ...s, [code]: { busy: false, note: { kind: "ok", text: res?.message || "매도 주문을 보냈어요." } } }));
      onDone?.(res);
    } catch (e) {
      setSellState((s) => ({ ...s, [code]: { busy: false, note: { kind: "err", text: `매도 실패 — ${e?.detail || e?.message || "다시 시도해주세요"}` } } }));
    }
  };

  const _card = { background: "rgba(255,255,255,.5)", borderRadius: 12, padding: "12px 14px", marginTop: 12 };
  const _inp = { padding: "9px 10px", borderRadius: 8, border: "1px solid rgba(40,20,24,.25)", fontSize: 14, fontFamily: "inherit" };

  return (
    <div data-testid="manual-holdings-card" style={_card}>
      <div style={{ fontSize: 14, fontWeight: 800, color: "#2a1418" }}>보유 — 봇 / 직접 분리</div>

      {/* 봇 격리 미작동 경고 */}
      {!isolated && (
        <div data-testid="mh-isolation-warning" style={{
          marginTop: 8, fontSize: 12.5, fontWeight: 700, lineHeight: 1.5, borderRadius: 8,
          padding: "8px 10px", background: "rgba(245,170,30,.20)", color: "#7a4a00",
        }}>
          ⚠️ 봇이 아직 직접 보유(👤)를 격리하지 못해요.{" "}
          {live?.manual_isolation_notice
            || "봇 격리 검증(조각 2) 후 사용하세요 — 지금은 봇 PAUSED 상태에서 표시·태깅만 동작해요."}
        </div>
      )}

      {/* 2섹션 */}
      <div style={{ display: "flex", gap: 12, marginTop: 10 }}>
        <_BotSection stats={bot} />

        {/* 직접 보유 섹션 (매도 버튼 포함) */}
        <div data-testid="mh-section-MANUAL" style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 800, color: "#2a1418", marginBottom: 4 }}>
            {SOURCE_BADGE.MANUAL.icon} 직접 보유 (장기)
          </div>
          <div style={{ fontSize: 12, color: "rgba(40,20,24,.7)" }}>
            {manual.count}종목 · 평가손익{" "}
            <b style={{ color: _pnlColor(manual.eval_pnl_krw || 0) }}>
              {(manual.eval_pnl_krw || 0) >= 0 ? "+" : ""}
              {fmtKRW(manual.eval_pnl_krw || 0)}원
            </b>
            {manual.return_pct != null && (
              <span> ({manual.return_pct >= 0 ? "+" : ""}{manual.return_pct}%)</span>
            )}
          </div>
          {manual.rows.length === 0 ? (
            <div style={{ fontSize: 12, color: "rgba(40,20,24,.45)", marginTop: 4 }}>보유 없음</div>
          ) : (
            manual.rows.map((r, i) => {
              const code = String(r.symbol || "");
              const st = sellState[code.toUpperCase()] || {};
              return (
                <div key={`${code}-${i}`} data-testid={`mh-manual-row-${code}`}
                  style={{ marginTop: 6, borderTop: "1px solid rgba(40,20,24,.10)", paddingTop: 5 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                    <div style={{ fontSize: 12.5, color: "#2a1418" }}>
                      {r.name || code} <span style={{ color: "rgba(40,20,24,.65)" }}>{r._qty}주</span>
                      {r.return_pct != null && (
                        <span style={{ color: "rgba(40,20,24,.55)", fontSize: 12 }}>
                          {" "}· {r.return_pct >= 0 ? "+" : ""}{r.return_pct}%
                        </span>
                      )}
                    </div>
                    <button type="button" data-testid={`mh-sell-${code}`}
                      onClick={() => sell(r)} disabled={st.busy}
                      style={{
                        padding: "5px 12px", borderRadius: 7, border: "none", cursor: st.busy ? "default" : "pointer",
                        background: st.busy ? "rgba(59,130,246,.35)" : "#3b82f6",
                        color: "#fff", fontWeight: 800, fontSize: 12, fontFamily: "inherit", flexShrink: 0,
                      }}>
                      {st.busy ? "처리 중…" : "매도"}
                    </button>
                  </div>
                  {st.note && (
                    <div data-testid={`mh-sell-note-${code}`} style={{
                      marginTop: 4, fontSize: 12, fontWeight: 700, borderRadius: 6, padding: "5px 8px",
                      background: st.note.kind === "ok" ? "rgba(21,160,95,.18)" : "rgba(192,57,43,.16)",
                      color: st.note.kind === "ok" ? "#0f5132" : "#7a1d1d",
                    }}>{st.note.text}</div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* 직접 매수 폼 */}
      <div style={{ marginTop: 12, borderTop: "1px solid rgba(40,20,24,.12)", paddingTop: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#2a1418", marginBottom: 6 }}>직접 매수 (👤)</div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <input data-testid="mh-symbol" style={{ ..._inp, width: 120 }} placeholder="종목코드"
            value={symbol} onChange={(e) => { setSymbol(e.target.value); setQuote(null); }} />
          <button data-testid="mh-quote" type="button" onClick={lookupQuote} disabled={quoting}
            style={{
              padding: "9px 12px", borderRadius: 8, border: "1px solid rgba(40,20,24,.25)",
              background: "rgba(40,20,24,.07)", fontSize: 13, fontFamily: "inherit",
              cursor: quoting ? "default" : "pointer", fontWeight: 700, color: "#2a1418",
            }}>
            {quoting ? "조회 중…" : "조회"}
          </button>
          <input data-testid="mh-qty" style={{ ..._inp, width: 80 }} type="number" min={1}
            value={qty} onChange={(e) => setQty(e.target.value)} />
          <span style={{ fontSize: 13, color: "rgba(40,20,24,.6)" }}>주</span>
          <button data-testid="mh-buy" type="button" onClick={buy} disabled={buyBusy}
            style={{
              padding: "9px 16px", borderRadius: 8, border: "none", fontFamily: "inherit",
              background: buyBusy ? "rgba(40,20,24,.25)" : "#3b82f6",
              color: "#fff", fontSize: 14, fontWeight: 800, cursor: buyBusy ? "default" : "pointer",
            }}>
            {buyBusy ? "주문 중…" : "직접 매수"}
          </button>
        </div>

        {/* 현재가 + 예상 금액 */}
        {quote && (
          <div data-testid="mh-quote-display" style={{ marginTop: 6, fontSize: 13, color: "#2a1418", fontWeight: 700 }}>
            {quote.name && <span style={{ color: "rgba(40,20,24,.65)", fontWeight: 600 }}>{quote.name} · </span>}
            {fmtKRW(quote.price)}원
            {Number(qty) > 0 && (
              <span style={{ color: "rgba(40,20,24,.55)", fontWeight: 600 }}>
                {" "}· 예상 {fmtKRW(quote.price * Number(qty))}원
              </span>
            )}
          </div>
        )}

        <div style={{ fontSize: 11.5, color: "rgba(40,20,24,.5)", marginTop: 6 }}>
          직접 매수는 봇 일일한도·전략판단을 거치지 않아요(긴급정지·모의투자 안전장치는 통과).
        </div>
        {buyNote && (
          <div data-testid="mh-note" style={{
            marginTop: 8, fontSize: 13, fontWeight: 700, borderRadius: 8, padding: "8px 10px",
            background: buyNote.kind === "ok" ? "rgba(21,160,95,.18)" : "rgba(192,57,43,.16)",
            color: buyNote.kind === "ok" ? "#0f5132" : "#7a1d1d",
          }}>{buyNote.text}</div>
        )}
      </div>
    </div>
  );
}
