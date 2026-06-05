import { useState } from "react";

import { backendApi } from "../../services/backend/client";
import { fmtKRW, nowKstHm } from "../../utils/format";
import { resolveSymbolName } from "../../utils/referenceHome";

// M3: 라이브 포지션 상황판 + 종목별 수동 전량 매도.
//   데이터는 부모(ReferenceHome)의 기존 폴링에서 주입(자체 폴링 0). KIS 잔고 SSOT.
const _pnlColor = (v) => (v == null ? "rgba(40,20,24,.6)" : v > 0 ? "#c0392b" : v < 0 ? "#1f6feb" : "#2a1418");
const _dash = (v, fmt) => (v == null ? "—" : fmt(v));

export function LivePositionsCard({ data, onChanged, api = backendApi, confirmFn }) {
  // sell 진행 상태(종목별): { [symbol]: { selling, submittedAt, error } }
  const [sell, setSell] = useState({});

  const _confirm = confirmFn
    || ((msg) => (typeof window !== "undefined" && typeof window.confirm === "function" ? window.confirm(msg) : true));

  const onSell = async (p) => {
    const name = p.name || resolveSymbolName(p.symbol);
    const est = p.market_price ? p.market_price * p.quantity : null;
    const msg = `${name} ${p.quantity}주를 시장가로 전량 매도할까요?`
      + (est ? `\n예상 금액 약 ${fmtKRW(est)}원 (현재가 기준)` : "");
    if (!_confirm(msg)) return; // 확정 전 미발사
    setSell((s) => ({ ...s, [p.symbol]: { selling: true, submittedAt: null, error: null } }));
    try {
      await api.positionSellAll(p.symbol);
      setSell((s) => ({ ...s, [p.symbol]: { selling: false, submittedAt: nowKstHm(), error: null } }));
      onChanged?.(); // 폴링 재조회 유도
    } catch (e) {
      const reason = (e && (e.detail || e.message)) || "잠시 후 다시 시도해주세요";
      setSell((s) => ({ ...s, [p.symbol]: { selling: false, submittedAt: null, error: String(reason) } }));
    }
  };

  if (data && data.available === false) {
    return (
      <div data-testid="livepos-card" style={cardStyle}>
        <div style={titleStyle}>보유 종목</div>
        <div data-testid="livepos-fail" style={{ fontSize: 13, color: "rgba(40,20,24,.6)", marginTop: 6 }}>
          불러오기 실패({data.fetched_at_kst || nowKstHm()})
        </div>
      </div>
    );
  }

  const positions = data?.positions || [];

  return (
    <div data-testid="livepos-card" style={cardStyle}>
      <div style={titleStyle}>보유 종목 {positions.length > 0 && `· ${positions.length}개`}</div>
      {positions.length === 0 ? (
        <div data-testid="livepos-empty" style={{ fontSize: 13, color: "rgba(40,20,24,.6)", marginTop: 6 }}>
          보유 중인 종목이 없어요
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", marginTop: 4 }}>
          {positions.map((p) => {
            const st = sell[p.symbol] || {};
            const inProgress = p.sell_in_progress || (!!st.submittedAt);
            const name = p.name || resolveSymbolName(p.symbol);
            return (
              <div key={p.symbol} data-testid={`livepos-row-${p.symbol}`}
                style={{ borderTop: "1px solid rgba(40,20,24,.12)", padding: "9px 0" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 800, color: "#2a1418" }}>{name}</div>
                    <div style={{ fontSize: 12, color: "rgba(40,20,24,.65)", marginTop: 1 }}>
                      {p.quantity}주 · 평단 {_dash(p.avg_price, (v) => `${fmtKRW(v)}원`)} · 현재 {_dash(p.market_price, (v) => `${fmtKRW(v)}원`)}
                    </div>
                  </div>
                  <div style={{ textAlign: "right", flex: "none" }}>
                    <div data-testid={`livepos-pnl-${p.symbol}`} style={{ fontSize: 13, fontWeight: 800, color: _pnlColor(p.eval_pnl_krw) }}>
                      {p.eval_pnl_krw == null ? "—" : `${p.eval_pnl_krw > 0 ? "+" : ""}${fmtKRW(p.eval_pnl_krw)}원`}
                      {p.return_pct != null && <span style={{ fontSize: 11, marginLeft: 3 }}>({p.return_pct > 0 ? "+" : ""}{p.return_pct}%)</span>}
                    </div>
                  </div>
                </div>

                <div style={{ marginTop: 7 }}>
                  {inProgress ? (
                    <div data-testid={`livepos-inprogress-${p.symbol}`} style={{ fontSize: 12.5, fontWeight: 700, color: "#7a4a00" }}>
                      {st.submittedAt ? `주문 보냄 (${st.submittedAt})` : "매도 주문 진행 중"}
                    </div>
                  ) : (
                    <button type="button" data-testid={`livepos-sell-${p.symbol}`}
                      onClick={() => onSell(p)} disabled={st.selling}
                      style={{
                        padding: "8px 16px", borderRadius: 9, border: "none", cursor: st.selling ? "default" : "pointer",
                        background: st.selling ? "rgba(192,57,43,.4)" : "#c0392b", color: "#fff", fontWeight: 800,
                        fontSize: 13, fontFamily: "inherit",
                      }}>
                      {st.selling ? "처리 중…" : "매도"}
                    </button>
                  )}
                  {st.error && (
                    <div data-testid={`livepos-error-${p.symbol}`} style={{ marginTop: 5, fontSize: 12, fontWeight: 700, color: "#7a1d1d" }}>
                      {st.error}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const cardStyle = { background: "rgba(255,255,255,.5)", borderRadius: 12, padding: "12px 14px", marginTop: 12 };
const titleStyle = { fontSize: 13, fontWeight: 800, color: "#2a1418" };
