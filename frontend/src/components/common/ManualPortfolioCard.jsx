/**
 * ManualPortfolioCard — 직접 보유 자산관리 대시보드 (2차).
 *
 * 기능:
 *   - 기간 탭 (오늘 / 1주 / 1개월 / 직접설정)
 *   - 포트폴리오 요약 (총 평가액 / 미실현 손익 / 기간 실현 손익 / 종목 수 / 비중 바)
 *   - 직접 보유 테이블 (종목명 / 수량 / 평단가 / 현재가 / 평가손익 / 보유일수 / [매도])
 *   - 부분 매도 모달 (전량 / 절반 / 직접입력)
 *
 * 주문: api.manualOrder 재활용 (route_order → 보호 4계층 경유).
 * 보호 4계층 0줄 수정.
 */
import { useEffect, useState, useCallback } from "react";

const UP   = "#e5443b";   // 한국식 상승=빨강
const DOWN = "#2563eb";   // 한국식 하락=파랑
const NEUTRAL = "#888";

const pnlColor = (n) => n > 0 ? UP : n < 0 ? DOWN : NEUTRAL;
const signed   = (n) => `${n > 0 ? "+" : ""}${Math.round(n).toLocaleString()}`;
const fmtKRW   = (n) => Math.abs(Math.round(n)).toLocaleString();

const PERIODS = [
  { key: "today", label: "오늘" },
  { key: "1w",    label: "1주" },
  { key: "1m",    label: "1개월" },
  { key: "custom", label: "직접설정" },
];

export function ManualPortfolioCard({ api = {}, confirmFn }) {
  const confirm = confirmFn ?? ((msg) => (typeof window !== "undefined" ? window.confirm(msg) : true));

  const [period, setPeriod]       = useState("today");
  const [from,   setFrom]         = useState("");
  const [to,     setTo]           = useState("");
  const [data,   setData]         = useState(null);   // summary API response
  const [loading, setLoading]     = useState(false);
  const [error,   setError]       = useState(null);

  // 부분 매도 모달
  const [sellTarget, setSellTarget] = useState(null);  // ManualHolding dict
  const [sellMode,   setSellMode]   = useState("full");
  const [sellQty,    setSellQty]    = useState(1);
  const [sellNotes,  setSellNotes]  = useState({});   // symbol → note text

  // ── 데이터 로드 ───────────────────────────────────────────────────────────

  const load = useCallback(async () => {
    if (!api.manualPortfolioSummary) return;
    setLoading(true);
    setError(null);
    try {
      const params = { period };
      if (period === "custom") {
        if (from) params.from = from;
        if (to)   params.to   = to;
      }
      const res = await api.manualPortfolioSummary(params);
      setData(res);
    } catch (e) {
      setError("불러오기 실패 — 잠시 후 다시 시도해주세요.");
    } finally {
      setLoading(false);
    }
  }, [api, period, from, to]);

  useEffect(() => {
    load();
  }, [load]);

  // ── 매도 모달 열기 ────────────────────────────────────────────────────────

  const openSell = (h) => {
    setSellTarget(h);
    setSellMode("full");
    setSellQty(Math.max(1, Math.floor(h.quantity / 2)));
  };

  // ── 매도 확인 ─────────────────────────────────────────────────────────────

  const handleSellConfirm = async () => {
    if (!sellTarget) return;
    const qty = sellMode === "full"
      ? sellTarget.quantity
      : sellMode === "half"
        ? Math.floor(sellTarget.quantity / 2)
        : Number(sellQty);

    if (!qty || qty < 1) return;

    const ok = confirm(`${sellTarget.name} ${qty}주 매도할까요?`);
    if (!ok) { setSellTarget(null); return; }

    try {
      const res = await api.manualOrder({ symbol: sellTarget.symbol, side: "SELL", quantity: qty });
      setSellNotes((prev) => ({ ...prev, [sellTarget.symbol]: res?.message || "매도 주문을 보냈어요." }));
      setSellTarget(null);
      setTimeout(load, 1500);
    } catch (e) {
      setSellNotes((prev) => ({ ...prev, [sellTarget.symbol]: `매도 실패 — ${e?.message || "오류"}` }));
      setSellTarget(null);
    }
  };

  // ── 렌더 ─────────────────────────────────────────────────────────────────

  const s = data?.summary ?? {};
  const holdings = data?.holdings ?? [];
  const periodRealized = s.period_realized_pnl ?? 0;
  const totalUnreal    = s.total_unrealized_pnl ?? 0;
  const totalValue     = s.total_market_value ?? 0;
  const count          = s.position_count ?? 0;

  return (
    <div data-testid="mhp-root" style={{ marginTop: 20 }}>
      <div style={{ fontSize: 15, fontWeight: 800, marginBottom: 10, color: "var(--c-text)" }}>
        👤 직접 보유 자산관리
      </div>

      {/* 기간 탭 */}
      <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
        {PERIODS.map((p) => (
          <button
            key={p.key}
            type="button"
            data-testid={`mhp-period-${p.key}`}
            onClick={() => setPeriod(p.key)}
            style={{
              padding: "5px 12px", borderRadius: 20, border: "none", cursor: "pointer",
              fontFamily: "inherit", fontSize: 13, fontWeight: 700,
              background: period === p.key ? "var(--c-text)" : "var(--c-surface-2)",
              color: period === p.key ? "var(--c-bg)" : "var(--c-text-2)",
            }}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* 직접설정 날짜 입력 */}
      {period === "custom" && (
        <div style={{ display: "flex", gap: 8, marginBottom: 10, alignItems: "center" }}>
          <input data-testid="mhp-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)}
            style={{ border: "1px solid var(--c-border)", borderRadius: 8, padding: "4px 8px", fontFamily: "inherit" }} />
          <span style={{ color: "var(--c-text-3)" }}>~</span>
          <input data-testid="mhp-to"   type="date" value={to}   onChange={(e) => setTo(e.target.value)}
            style={{ border: "1px solid var(--c-border)", borderRadius: 8, padding: "4px 8px", fontFamily: "inherit" }} />
          <button type="button" onClick={load}
            style={{ padding: "4px 12px", borderRadius: 8, border: "none", background: "var(--c-text)", color: "var(--c-bg)", cursor: "pointer", fontFamily: "inherit", fontWeight: 700 }}>
            조회
          </button>
        </div>
      )}

      {/* 에러 */}
      {error && (
        <div data-testid="mhp-error" style={{ color: UP, fontSize: 13, marginBottom: 8 }}>{error}</div>
      )}

      {/* 포트폴리오 요약 카드 */}
      {data && (
        <div data-testid="mhp-summary" style={{
          background: "var(--c-surface-2)", borderRadius: 12, padding: "12px 16px",
          marginBottom: 12, display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 10,
        }}>
          <_SummaryCell k="총 평가액" v={`${fmtKRW(totalValue)}원`} testid="mhp-summary-value" />
          <_SummaryCell k="미실현 손익" v={`${signed(totalUnreal)}원`} c={pnlColor(totalUnreal)} testid="mhp-summary-unrealized" />
          <_SummaryCell k={`기간 실현 손익`} v={`${signed(periodRealized)}원`} c={pnlColor(periodRealized)} testid="mhp-summary-realized" />
          <_SummaryCell k="보유 종목" v={`${count}종목`} testid="mhp-summary-count" />
        </div>
      )}

      {/* 보유 테이블 */}
      {loading && !data && (
        <div style={{ color: "var(--c-text-3)", fontSize: 13 }}>불러오는 중…</div>
      )}

      {data && holdings.length === 0 && (
        <div data-testid="mhp-empty" style={{ color: "var(--c-text-3)", fontSize: 13, padding: "12px 0" }}>
          직접 보유 종목이 없어요. manual_buy 체결 후 여기에 표시돼요.
        </div>
      )}

      {holdings.length > 0 && (
        <div data-testid="mhp-holdings-table" style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "2px solid var(--c-border)", color: "var(--c-text-3)", fontWeight: 700 }}>
                {["종목명", "수량", "평단가", "현재가", "평가손익(₩)", "수익률", "보유일수", "비중", ""].map((h) => (
                  <th key={h} style={{ padding: "6px 8px", textAlign: h === "" ? "center" : "right",
                    ...(h === "종목명" ? { textAlign: "left" } : {}) }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {holdings.map((h) => (
                <tr key={h.symbol} data-testid={`mhp-row-${h.symbol}`}
                  style={{ borderBottom: "1px solid var(--c-border)" }}>
                  <td style={{ padding: "7px 8px" }}>
                    <div style={{ fontWeight: 700, color: "var(--c-text)" }}>{h.name}</div>
                    <div style={{ color: "var(--c-text-3)", fontSize: 11 }}>{h.symbol}</div>
                  </td>
                  <td style={{ padding: "7px 8px", textAlign: "right" }}>{h.quantity.toLocaleString()}주</td>
                  <td style={{ padding: "7px 8px", textAlign: "right" }}>{h.avg_price.toLocaleString()}</td>
                  <td style={{ padding: "7px 8px", textAlign: "right", color: h.current_price === 0 ? NEUTRAL : "inherit" }}>
                    {h.current_price > 0 ? h.current_price.toLocaleString() : "—"}
                  </td>
                  <td style={{ padding: "7px 8px", textAlign: "right", color: pnlColor(h.unrealized_pnl), fontWeight: 700 }}>
                    {signed(h.unrealized_pnl)}
                  </td>
                  <td style={{ padding: "7px 8px", textAlign: "right", color: pnlColor(h.unrealized_pnl_pct) }}>
                    {h.current_price > 0 ? `${h.unrealized_pnl_pct > 0 ? "+" : ""}${h.unrealized_pnl_pct.toFixed(2)}%` : "—"}
                  </td>
                  <td style={{ padding: "7px 8px", textAlign: "right" }}>{h.holding_days}일</td>
                  <td style={{ padding: "7px 8px", textAlign: "right" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 4, justifyContent: "flex-end" }}>
                      <div style={{ width: 40, height: 6, background: "var(--c-border)", borderRadius: 3, overflow: "hidden" }}>
                        <div style={{ width: `${h.weight_pct}%`, height: "100%", background: "#2563eb", borderRadius: 3 }} />
                      </div>
                      <span>{h.weight_pct.toFixed(1)}%</span>
                    </div>
                  </td>
                  <td style={{ padding: "7px 8px", textAlign: "center" }}>
                    <button
                      type="button"
                      data-testid={`mhp-sell-${h.symbol}`}
                      onClick={() => openSell(h)}
                      style={{
                        padding: "4px 10px", borderRadius: 8, border: "1px solid #e5443b",
                        background: "none", color: "#e5443b", cursor: "pointer",
                        fontFamily: "inherit", fontWeight: 700, fontSize: 12,
                      }}
                    >
                      매도
                    </button>
                    {sellNotes[h.symbol] && (
                      <div data-testid={`mhp-sell-note-${h.symbol}`}
                        style={{ fontSize: 11, color: "var(--c-text-3)", marginTop: 3 }}>
                        {sellNotes[h.symbol]}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 부분 매도 모달 */}
      {sellTarget && (
        <div data-testid="mhp-sell-modal" style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", zIndex: 999,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <div style={{
            background: "var(--c-surface)", borderRadius: 16, padding: "24px 28px",
            minWidth: 300, boxShadow: "0 8px 32px rgba(0,0,0,.18)",
          }}>
            <div style={{ fontSize: 16, fontWeight: 800, marginBottom: 16 }}>
              {sellTarget.name} 매도
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {/* 전량 */}
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input type="radio" data-testid="mhp-sell-full"
                  checked={sellMode === "full"} onChange={() => setSellMode("full")} />
                <span>전량 ({sellTarget.quantity.toLocaleString()}주)</span>
              </label>
              {/* 절반 */}
              {sellTarget.quantity >= 2 && (
                <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                  <input type="radio" data-testid="mhp-sell-half"
                    checked={sellMode === "half"} onChange={() => setSellMode("half")} />
                  <span>절반 ({Math.floor(sellTarget.quantity / 2).toLocaleString()}주)</span>
                </label>
              )}
              {/* 직접입력 */}
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input type="radio" data-testid="mhp-sell-custom"
                  checked={sellMode === "custom"} onChange={() => setSellMode("custom")} />
                <span>직접입력</span>
                {sellMode === "custom" && (
                  <input
                    data-testid="mhp-sell-qty-input"
                    type="number"
                    min={1}
                    max={sellTarget.quantity}
                    value={sellQty}
                    onChange={(e) => setSellQty(Math.max(1, Math.min(sellTarget.quantity, Number(e.target.value))))}
                    style={{ width: 70, border: "1px solid var(--c-border)", borderRadius: 8, padding: "3px 8px", fontFamily: "inherit" }}
                  />
                )}
                {sellMode === "custom" && <span>주</span>}
              </label>
            </div>

            <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
              <button type="button" data-testid="mhp-sell-cancel"
                onClick={() => setSellTarget(null)}
                style={{ flex: 1, padding: "10px 0", border: "1px solid var(--c-border)", borderRadius: 10,
                  background: "none", cursor: "pointer", fontFamily: "inherit", fontWeight: 700 }}>
                취소
              </button>
              <button type="button" data-testid="mhp-sell-confirm"
                onClick={handleSellConfirm}
                style={{ flex: 1, padding: "10px 0", border: "none", borderRadius: 10,
                  background: "#e5443b", color: "#fff", cursor: "pointer",
                  fontFamily: "inherit", fontWeight: 800 }}>
                매도 확인
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function _SummaryCell({ k, v, c, testid }) {
  return (
    <div style={{ textAlign: "center" }} data-testid={testid}>
      <div style={{ fontSize: 11, color: "var(--c-text-3)", fontWeight: 600 }}>{k}</div>
      <div style={{ fontSize: 14, fontWeight: 800, color: c || "var(--c-text)", marginTop: 3 }}>{v}</div>
    </div>
  );
}
