import { useState } from "react";

import { backendApi } from "../../services/backend/client";
import { fmtKRW, nowKstHm } from "../../utils/format";
import { resolveSymbolName } from "../../utils/referenceHome";
import { splitHoldingsBySource, SOURCE_BADGE } from "../../utils/holdingsBySource";

// M3: 라이브 포지션 상황판 + 종목별 수동 전량 매도.
//   데이터는 부모(ReferenceHome)의 기존 폴링에서 주입(자체 폴링 0). KIS 잔고 SSOT.
//   소유권 배지(봇 운용/내 보유) + 손익 분리 요약 표시.
const _pnlColor = (v) => (v == null ? "rgba(40,20,24,.6)" : v > 0 ? "#c0392b" : v < 0 ? "#1f6feb" : "#2a1418");
const _dash = (v, fmt) => (v == null ? "—" : fmt(v));

function _fmtPnl(pnl, pct) {
  if (pnl == null) return "—";
  const s = pnl > 0 ? "+" : "";
  const p = pct != null ? ` (${pct > 0 ? "+" : ""}${pct}%)` : "";
  return `${s}${fmtKRW(Math.round(pnl))}원${p}`;
}

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

  // F1: data===null = 아직 안 불러옴(로딩). 실패(available=false)·빈보유와 다르게 표시.
  if (!data) {
    return (
      <div data-testid="livepos-card" style={cardStyle}>
        <div style={titleStyle}>보유 종목</div>
        <div data-testid="livepos-loading" style={{ fontSize: 13, color: "rgba(40,20,24,.55)", marginTop: 6 }}>
          불러오는 중…
        </div>
      </div>
    );
  }

  if (data.available === false) {
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
  const { bot, manual } = splitHoldingsBySource(positions);
  // 봇 운용: BOT + MIXED(봇이 일부 보유). 내 보유: MANUAL + UNTAGGED.
  const botPositions    = positions.filter((p) => p.source === "BOT" || p.source === "MIXED");
  const manualPositions = positions.filter((p) => p.source === "MANUAL" || p.source === "UNTAGGED" || !p.source);
  const hasSplit = botPositions.length > 0 && manualPositions.length > 0;

  // 개별 포지션 행 렌더 (인라인 — hook 없음)
  const renderRow = (p) => {
    const st = sell[p.symbol] || {};
    const inProgress = p.sell_in_progress || (!!st.submittedAt);
    const name = p.name || resolveSymbolName(p.symbol);
    const badge = SOURCE_BADGE[p.source] || SOURCE_BADGE.UNTAGGED;
    return (
      <div key={p.symbol} data-testid={`livepos-row-${p.symbol}`}
        style={{ borderTop: "1px solid rgba(40,20,24,.12)", padding: "9px 0" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
              <span style={{ fontSize: 14, fontWeight: 800, color: "#2a1418" }}>{name}</span>
              <span data-testid={`livepos-badge-${p.symbol}`} style={{
                fontSize: 10.5, fontWeight: 700, padding: "1px 6px", borderRadius: 999, flexShrink: 0,
                background: `${badge.color}22`, color: badge.color, border: `1px solid ${badge.color}55`,
              }}>
                {badge.icon} {badge.label}
              </span>
            </div>
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
  };

  return (
    <div data-testid="livepos-card" style={cardStyle}>
      <div style={titleStyle}>보유 종목 {positions.length > 0 && `· ${positions.length}개`}</div>
      {positions.length === 0 ? (
        <div data-testid="livepos-empty" style={{ fontSize: 13, color: "rgba(40,20,24,.6)", marginTop: 6 }}>
          보유 중인 종목이 없어요
        </div>
      ) : (
        <>
          {/* 손익 분리 요약 — 봇/수동 혼재 시만 표시 */}
          {hasSplit && (
            <div data-testid="livepos-pnl-split" style={{
              display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6,
              marginTop: 8, background: "rgba(40,20,24,.05)", borderRadius: 8, padding: "8px 10px",
            }}>
              <div>
                <div style={{ fontSize: 11, color: "rgba(40,20,24,.5)", fontWeight: 600, marginBottom: 2 }}>🤖 봇 운용</div>
                <div style={{ fontSize: 12.5, fontWeight: 800, color: _pnlColor(bot.eval_pnl_krw) }}>
                  {bot.count > 0 ? _fmtPnl(bot.eval_pnl_krw, bot.return_pct) : "없음"}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: "rgba(40,20,24,.5)", fontWeight: 600, marginBottom: 2 }}>🛡️ 내 보유 (보호됨)</div>
                <div style={{ fontSize: 12.5, fontWeight: 800, color: _pnlColor(manual.eval_pnl_krw) }}>
                  {manual.count > 0 ? _fmtPnl(manual.eval_pnl_krw, manual.return_pct) : "없음"}
                </div>
              </div>
            </div>
          )}

          {/* 봇 운용 섹션 */}
          {botPositions.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", marginTop: hasSplit ? 8 : 4 }}>
              {hasSplit && (
                <div style={{ fontSize: 11, fontWeight: 700, color: "#22c55e", margin: "6px 0 2px" }}>
                  🤖 봇 운용 ({botPositions.length}종목)
                </div>
              )}
              {botPositions.map(renderRow)}
            </div>
          )}

          {/* 내 보유(보호됨) 섹션 — 봇이 청산하지 않음 */}
          {manualPositions.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", marginTop: botPositions.length > 0 ? 6 : 4 }}>
              {hasSplit && (
                <div style={{ fontSize: 11, fontWeight: 700, color: "#3b82f6", margin: "6px 0 2px" }}>
                  🛡️ 내 보유 ({manualPositions.length}종목) · 봇이 청산 안 해요
                </div>
              )}
              {manualPositions.map(renderRow)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

const cardStyle = { background: "rgba(255,255,255,.5)", borderRadius: 12, padding: "12px 14px", marginTop: 12 };
const titleStyle = { fontSize: 13, fontWeight: 800, color: "#2a1418" };
