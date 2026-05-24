import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

// P-18: Paper 가상 포트폴리오 카드 — 현재 현금 / 보유 종목 / 평가금액 / 총 자산 /
// 평가손익 / 일일 매수 사용·잔여 / 종목 비중 + 상태. 모두 *모의매매 전용* 이며
// 실거래 계좌와 무관 (broker 호출 0건).
//
// 데이터 source (우선순위):
//   1. autoPaperPortfolio() → GET /api/auto-paper/portfolio (P-18 종합 요약)
//   2. fallback: paperCashState() + virtualPositions() (구버전 backend / 테스트)
//
// 실거래/매수/매도/Place Order 버튼 0개 — read-only 표시 전용.

const POLL_INTERVAL_MS = 5_000;

function _krw(n) {
  return `${Number(n || 0).toLocaleString("ko-KR")}원`;
}

// #55 / 7-03: 조회 실패(null/undefined)를 0원으로 표시하지 않는다 — "확인 불가".
// 실제 0원(숫자 0)은 그대로 0원 표시.
function _krwOrUnknown(n) {
  if (n == null || !Number.isFinite(Number(n))) return "확인 불가";
  return _krw(n);
}

function _pct(ratio) {
  if (ratio == null || !Number.isFinite(Number(ratio))) return "—";
  const v = Number(ratio) * 100;
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function _weightPct(ratio) {
  if (ratio == null || !Number.isFinite(Number(ratio))) return "—";
  return `${(Number(ratio) * 100).toFixed(1)}%`;
}

function _pnlColor(n) {
  return Number(n) > 0 ? "#16a34a" : Number(n) < 0 ? "#dc2626" : "var(--c-text)";
}

const _WEIGHT_STATUS = {
  OK:       { label: "정상",  color: "#16a34a", bg: "#dcfce7" },
  WARN:     { label: "주의",  color: "#a16207", bg: "#fef9c3" },
  EXCEEDED: { label: "초과",  color: "#b91c1c", bg: "#fee2e2" },
};

function _StatusBadge({ status }) {
  const s = _WEIGHT_STATUS[status] || _WEIGHT_STATUS.OK;
  return (
    <span
      data-testid={`portfolio-weight-status-${status || "OK"}`}
      style={{
        padding: "1px 6px", borderRadius: 4, fontSize: "var(--fs-xs)",
        fontWeight: "var(--fw-bold)", color: s.color, background: s.bg,
      }}
    >
      {s.label}
    </span>
  );
}


/** legacy virtualPositions 행 → 통일 position shape. */
function _normLegacyPosition(p) {
  const mark = p.last_price && p.last_price > 0 ? p.last_price : p.avg_price;
  const qty = Number(p.quantity || 0);
  return {
    symbol:               p.symbol,
    quantity:             qty,
    average_price:        Number(p.avg_price || 0),
    current_price:        Number(mark || 0),
    market_value:         Number(mark || 0) * qty,
    unrealized_pnl:       Number(p.unrealized_pnl || 0),
    unrealized_pnl_pct:   Number(p.unrealized_pct || 0),
    portfolio_weight_pct: null,
    symbol_weight_status: null,
  };
}


export function PortfolioCard({
  apiClient = backendApi,
  pollIntervalMs = POLL_INTERVAL_MS,
} = {}) {
  const [summary, setSummary] = useState(null);
  const [cash, setCash] = useState(null);
  const [positions, setPositions] = useState([]);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    // 1) P-18 종합 요약 우선.
    if (typeof apiClient.autoPaperPortfolio === "function") {
      try {
        const s = await apiClient.autoPaperPortfolio();
        if (s && typeof s === "object") {
          setSummary(s);
          setError(null);
          return;   // 요약 성공 — legacy 조회 생략.
        }
      } catch {
        // 요약 미가용 — legacy fallback 으로 진행.
      }
    }
    // 2) legacy fallback: cash-state + virtual-positions.
    setSummary(null);
    try {
      const c = await apiClient.paperCashState();
      setCash(c || null);
      setError(null);
    } catch (err) {
      setError(err?.message || String(err));
    }
    if (typeof apiClient.virtualPositions === "function") {
      try {
        const p = await apiClient.virtualPositions();
        if (Array.isArray(p)) setPositions(p);
        else if (p && Array.isArray(p.positions)) setPositions(p.positions);
      } catch {
        // positions 미가용 — 조용히 무시 (현금 표시는 유지).
      }
    }
  }, [apiClient]);

  useEffect(() => {
    refresh();
    if (!pollIntervalMs || pollIntervalMs <= 0) return undefined;
    const t = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(t);
  }, [refresh, pollIntervalMs]);

  // ── 통일 view ──
  const summaryMode = summary != null;
  const startingCash = summaryMode ? summary.starting_cash : null;
  // #55 / 7-03: legacy cash 조회 실패 시 0원 fallback 금지 — null 로 두고 "확인 불가".
  const currentCash = summaryMode
    ? summary.current_cash
    : (cash?.available_cash_krw ?? null);
  const viewPositions = summaryMode
    ? (Array.isArray(summary.positions) ? summary.positions : [])
    : positions.map(_normLegacyPosition);
  const positionValue = summaryMode
    ? summary.total_position_value
    : viewPositions.reduce((a, p) => a + Number(p.market_value || 0), 0);
  const totalEquity = summaryMode
    ? summary.total_equity
    : (currentCash == null ? null : currentCash + positionValue);
  const unrealized = summaryMode
    ? summary.total_unrealized_pnl
    : viewPositions.reduce((a, p) => a + Number(p.unrealized_pnl || 0), 0);
  const unrealizedPct = summaryMode ? summary.total_unrealized_pnl_pct : null;

  const positionCount = summaryMode ? summary.position_count : viewPositions.length;
  const maxPositions = summaryMode ? summary.max_positions : null;
  const todayBuyUsed = summaryMode ? summary.today_buy_used_amount : null;
  const remainingDailyBuy = summaryMode ? summary.remaining_daily_buy_amount : null;

  return (
    <Card data-testid="portfolio-card">
      <SectionLabel>📈 가상 포트폴리오</SectionLabel>

      <div
        data-testid="portfolio-intro"
        style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8 }}
      >
        AI Paper 운용 기준 현재 현금, 보유 종목, 평가금액을 보여줍니다. 실제 계좌
        잔고가 아닙니다.
      </div>

      <div style={{ marginBottom: 10 }} data-testid="portfolio-badges">
        <span
          data-testid="portfolio-badge-paper"
          style={{
            display: "inline-block", padding: "3px 10px", borderRadius: 6,
            fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
            background: "#1e3a8a", color: "#fff", marginRight: 6,
          }}
        >
          모의 전용 · 실제 주문 아님
        </span>
        <span
          data-testid="portfolio-badge-no-broker"
          style={{
            display: "inline-block", padding: "3px 10px", borderRadius: 6,
            fontSize: "var(--fs-xs)", background: "#6b7280", color: "#fff",
          }}
        >
          broker_order_sent=false
        </span>
      </div>

      {/* 요약 타일 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
          gap: 8, marginBottom: 10,
        }}
      >
        <div data-testid="portfolio-cash"
             style={{ padding: "8px 10px", background: "#f1f5f9", borderRadius: "var(--r-sm)" }}>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>Paper 현금</div>
          <div style={{ fontWeight: "var(--fw-bold)" }}>{_krwOrUnknown(currentCash)}</div>
        </div>
        <div data-testid="portfolio-position-value"
             style={{ padding: "8px 10px", background: "#f1f5f9", borderRadius: "var(--r-sm)" }}>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>보유 평가금액</div>
          <div style={{ fontWeight: "var(--fw-bold)" }}>{_krw(positionValue)}</div>
        </div>
        <div data-testid="portfolio-total-equity"
             style={{ padding: "8px 10px", background: "#ecfeff", borderRadius: "var(--r-sm)" }}>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>총 Paper 자산</div>
          <div style={{ fontWeight: "var(--fw-bold)" }}>{_krwOrUnknown(totalEquity)}</div>
        </div>
        <div data-testid="portfolio-unrealized-pnl"
             style={{ padding: "8px 10px", background: "#f1f5f9", borderRadius: "var(--r-sm)" }}>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>평가손익</div>
          <div style={{ fontWeight: "var(--fw-bold)", color: _pnlColor(unrealized) }}>
            {unrealized >= 0 ? "+" : ""}{_krw(unrealized)}
            {unrealizedPct != null ? ` (${_pct(unrealizedPct)})` : ""}
          </div>
        </div>
        {summaryMode && (
          <>
            <div data-testid="portfolio-starting-cash"
                 style={{ padding: "8px 10px", background: "#f1f5f9", borderRadius: "var(--r-sm)" }}>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>시작 시드머니</div>
              <div style={{ fontWeight: "var(--fw-bold)" }}>{_krw(startingCash)}</div>
            </div>
            <div data-testid="portfolio-today-buy-used"
                 style={{ padding: "8px 10px", background: "#f1f5f9", borderRadius: "var(--r-sm)" }}>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>오늘 매수 사용</div>
              <div style={{ fontWeight: "var(--fw-bold)" }}>{_krw(todayBuyUsed)}</div>
            </div>
            <div data-testid="portfolio-remaining-daily-buy"
                 style={{ padding: "8px 10px", background: "#f1f5f9", borderRadius: "var(--r-sm)" }}>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>남은 일일 매수</div>
              <div style={{ fontWeight: "var(--fw-bold)" }}>{_krw(remainingDailyBuy)}</div>
            </div>
            <div data-testid="portfolio-position-slots"
                 style={{ padding: "8px 10px", background: "#f1f5f9", borderRadius: "var(--r-sm)" }}>
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>보유 / 최대 종목</div>
              <div style={{ fontWeight: "var(--fw-bold)" }}>
                {positionCount} / {maxPositions}종목
              </div>
            </div>
          </>
        )}
      </div>

      {/* 보유 종목 테이블 */}
      <div data-testid="portfolio-holdings">
        <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)", marginBottom: 4, color: "var(--c-text-2)" }}>
          보유 종목 ({positionCount})
        </div>
        {viewPositions.length === 0 ? (
          <div data-testid="portfolio-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            현재 보유 중인 Paper 포지션이 없습니다.
          </div>
        ) : (
          <div data-testid="portfolio-positions-table">
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1.2fr 0.7fr 1fr 1fr 1fr 1fr 0.8fr 0.7fr",
                gap: 4, fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
                fontWeight: "var(--fw-bold)", padding: "2px 0",
                borderBottom: "1px solid var(--c-border)",
              }}
            >
              <span>종목</span><span>수량</span><span>평균단가</span>
              <span>현재가</span><span>평가금액</span><span>평가손익</span>
              <span>비중</span><span>상태</span>
            </div>
            {viewPositions.map((p) => (
              <div
                key={`${p.symbol}-${p.strategy || ""}`}
                data-testid={`portfolio-holding-${p.symbol}`}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1.2fr 0.7fr 1fr 1fr 1fr 1fr 0.8fr 0.7fr",
                  gap: 4, fontSize: "var(--fs-xs)", padding: "4px 0",
                  borderBottom: "1px dashed #e2e8f0", alignItems: "center",
                }}
              >
                <span style={{ fontWeight: "var(--fw-bold)" }}>{p.symbol}</span>
                <span data-testid={`portfolio-qty-${p.symbol}`}>{p.quantity}주</span>
                <span>{_krw(p.average_price)}</span>
                <span>{_krw(p.current_price)}</span>
                <span>{_krw(p.market_value)}</span>
                <span style={{ color: _pnlColor(p.unrealized_pnl) }}>
                  {Number(p.unrealized_pnl) >= 0 ? "+" : ""}{_krw(p.unrealized_pnl)}
                  {" "}({_pct(p.unrealized_pnl_pct)})
                </span>
                <span>{_weightPct(p.portfolio_weight_pct)}</span>
                <span>
                  {p.symbol_weight_status
                    ? <_StatusBadge status={p.symbol_weight_status} />
                    : "—"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div data-testid="portfolio-error"
             style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
          {error}
        </div>
      )}

      <div data-testid="portfolio-disclaimer"
           style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
        본 화면은 Paper / 가상 포트폴리오이며 실제 계좌 잔고가 아닙니다. Paper
        모의매매 전용 — broker 호출 0건.
      </div>
    </Card>
  );
}

export default PortfolioCard;
