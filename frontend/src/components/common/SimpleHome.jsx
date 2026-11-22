// 새 홈 "SimpleHome" — 토스/업비트급 간편 대시보드. (STEP 0 시안 승인본 구현)
//
// 대상: 50대·주식/코딩 비전문가·스마트폰. 한 화면 1열 7섹션. 전문용어 0,
// 모든 문구 일상 한국어(번역은 utils/simpleHomeText.js 순수 함수). ★동작 로직은
// 신설하지 않는다 — 시작/정지/긴급정지는 App.jsx가 주입한 *기존 핸들러* 그대로,
// 잔고/포지션/판단은 기존 backendApi 재사용. 기존 홈(Dashboard)은 "전문가 보기"로
// 그대로 보존(회귀 0).
import { useEffect, useState } from "react";
import { backendApi } from "../../services/backend/client";
import { MOCK_STOCKS, PRICE_TICK_MS } from "../../config/constants";
import {
  computeTradingStatus,
  botFilledSymbolSet,
  classifyPositionSource,
  POSITION_SOURCE_BADGE,
  summarizeTodayOrders,
} from "../../utils/tradingStatus";
import { isMarketOpen, currentMarketPhase } from "../../utils/marketHours";
import { fmtKRW, nowKstHm, formatKst } from "../../utils/format";
import { pickLatestChiefDecision } from "../tabs/AgentLatestTile";
import {
  heroFromStatus,
  moneyFailureLine,
  aiOneLiner,
  timelineSentence,
  todaySummaryLine,
} from "../../utils/simpleHomeText";

const UP = "#e5443b";    // 상승 = 빨강 (한국식)
const DOWN = "#2b6ef2";  // 하락 = 파랑
const T1 = "#1b1d22", T2 = "#5b6470", T3 = "#9aa3af", LINE = "#eceef1";

const lookupName = (symbol) =>
  MOCK_STOCKS.find((s) => s.code === symbol)?.name ?? symbol;

const card = {
  background: "#fff", borderRadius: 20, padding: 22, marginBottom: 14,
  boxShadow: "0 1px 3px rgba(0,0,0,.04)",
};
const secTitle = { fontSize: 15, fontWeight: 800, margin: "2px 0 12px", color: T1 };
const pnlColor = (n) => (n > 0 ? UP : n < 0 ? DOWN : T2);
const pnlSign = (n) => (n > 0 ? "+" : "");

export function SimpleHome({
  portfolio,
  bot,
  botControls,
  emergencyStop,
  onEmergencyStop,
  onJumpTab,
  onExpert,
}) {
  const running = !!bot?.running;
  const [orders, setOrders] = useState([]);
  const [latestAi, setLatestAi] = useState(null);
  // modification #1: 잔고를 마지막으로 정상 불러온 시각(KST HH:MM). 실패 시 이걸 표시.
  const [lastOkHm, setLastOkHm] = useState(null);

  // 잔고가 성공적으로 들어오면(에러 없고 로딩 끝) 마지막 확인 시각 기록.
  useEffect(() => {
    if (!portfolio?.loading && !portfolio?.error) setLastOkHm(nowKstHm());
  }, [portfolio?.loading, portfolio?.error, portfolio?.totalAsset]);

  // 오늘 한 일(주문) + AI 한마디 — 기존 endpoint 재사용. 가벼운 폴링.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [aud, dec] = await Promise.all([
          backendApi.listOrderAudits({ limit: 50 }),
          backendApi.aiAgentDecisions(20),
        ]);
        if (cancelled) return;
        setOrders(Array.isArray(aud) ? aud : (aud?.items ?? []));
        setLatestAi(pickLatestChiefDecision(dec));
      } catch {
        // 실패는 다음 폴링에 자동 재시도 (홈을 깨뜨리지 않는다)
      }
    };
    load();
    const t = setInterval(load, Math.max(PRICE_TICK_MS, 5000));
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  // 1. 상태 히어로
  const status = computeTradingStatus({
    running, emergencyStop,
    marketOpen: isMarketOpen(),
    marketPhase: currentMarketPhase(),
  });
  const hero = heroFromStatus(status);

  // 2. 내 돈
  const balanceFailed = !!portfolio?.error;
  const totalAsset = portfolio?.totalAsset ?? 0;
  const totalPnL = portfolio?.totalPnL ?? 0;
  const totalPnLPct = portfolio?.totalPnLPct ?? 0;
  const cash = portfolio?.cash ?? 0;

  // 4. 보유 종목 + 출처 배지
  const positions = portfolio?.positions ?? [];
  const botSet = botFilledSymbolSet(orders);

  // 5. 오늘 한 일
  const today = summarizeTodayOrders(orders);
  const recent = orders.slice(0, 4);

  return (
    <div data-testid="simple-home" style={{ maxWidth: 460, margin: "0 auto" }}>

      {/* 1. 상태 히어로 */}
      <div style={{ ...card, background: hero.bg, display: "flex", gap: 14, alignItems: "center", padding: "24px 22px" }}>
        <div style={{ fontSize: 40 }}>{hero.emoji}</div>
        <div>
          <div data-testid="simple-home-hero" style={{ fontSize: 24, fontWeight: 800, letterSpacing: "-.02em", color: T1 }}>
            {hero.big}
          </div>
          <div style={{ fontSize: 14, color: T2, marginTop: 4, lineHeight: 1.5 }}>{hero.sub}</div>
        </div>
      </div>

      {/* 2. 내 돈 */}
      <div style={card}>
        <div style={{ fontSize: 14, color: T2, marginBottom: 6 }}>총 자산</div>
        {balanceFailed ? (
          <div data-testid="simple-home-balance-failed"
               style={{ fontSize: 16, fontWeight: 700, color: UP, lineHeight: 1.5 }}>
            {moneyFailureLine(lastOkHm)}
          </div>
        ) : (
          <>
            <div style={{ fontSize: 40, fontWeight: 800, letterSpacing: "-.03em", color: T1 }}>
              {fmtKRW(totalAsset)}<span style={{ fontSize: 22, fontWeight: 700 }}>원</span>
            </div>
            <div style={{ display: "flex", gap: 24, marginTop: 14 }}>
              <div>
                <div style={{ color: T3, fontSize: 13 }}>평가 손익</div>
                <div style={{ fontWeight: 700, fontSize: 17, color: pnlColor(totalPnL) }}>
                  {pnlSign(totalPnL)}{fmtKRW(Math.round(totalPnL))}원
                  <span style={{ fontSize: 14 }}> ({pnlSign(totalPnLPct)}{totalPnLPct.toFixed(1)}%)</span>
                </div>
              </div>
              <div>
                <div style={{ color: T3, fontSize: 13 }}>현금</div>
                <div style={{ fontWeight: 700, fontSize: 17, color: T1 }}>{fmtKRW(cash)}원</div>
              </div>
            </div>
          </>
        )}
      </div>

      {/* 3. 시작/정지 + 긴급정지 (기존 핸들러 그대로) */}
      <div style={{ ...card, padding: 18 }}>
        <button
          type="button"
          data-testid="simple-home-startstop"
          onClick={running ? botControls?.stop : botControls?.start}
          style={{
            width: "100%", padding: 18, border: "none", borderRadius: 16,
            fontSize: 19, fontWeight: 800, color: "#fff", cursor: "pointer",
            fontFamily: "inherit", background: running ? "#e5443b" : "#15c47e",
          }}
        >
          {running ? "⏹ 자동매매 정지" : "▶ 자동매매 시작"}
        </button>
        <button
          type="button"
          data-testid="simple-home-emergency"
          onClick={onEmergencyStop}
          style={{
            display: "block", width: "100%", textAlign: "center", marginTop: 12,
            color: "#e5443b", fontSize: 13, textDecoration: "underline",
            background: "none", border: "none", cursor: "pointer", fontFamily: "inherit",
          }}
        >
          🛑 {emergencyStop ? "긴급 정지 해제" : "긴급 정지"}
        </button>
      </div>

      {/* 4. 보유 종목 */}
      <div style={card}>
        <div style={secTitle}>보유 종목</div>
        {positions.length === 0 ? (
          <div style={{ color: T2, fontSize: 14 }}>아직 보유한 종목이 없어요</div>
        ) : positions.map((p) => {
          const pct = p.avg > 0 ? ((p.cur - p.avg) / p.avg) * 100 : 0;
          const src = classifyPositionSource(p.code, botSet);
          const badge = POSITION_SOURCE_BADGE[src];
          return (
            <div key={p.code} style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "12px 0", borderBottom: `1px solid ${LINE}`,
            }}>
              <div style={{ fontSize: 16, fontWeight: 600, color: T1 }}>
                {p.name || lookupName(p.code)}
                <span style={{
                  fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 999,
                  marginLeft: 7, color: src === "BOT" ? "#0a8f5b" : "#6b7280",
                  background: src === "BOT" ? "#e6f7ef" : "#f0f1f3",
                }}>{badge.label}</span>
              </div>
              <div style={{ fontSize: 17, fontWeight: 800, color: pnlColor(pct) }}>
                {pnlSign(pct)}{pct.toFixed(1)}%
              </div>
            </div>
          );
        })}
      </div>

      {/* 5. 오늘 한 일 */}
      <div style={card}>
        <div style={secTitle}>오늘 한 일</div>
        {recent.length === 0 ? (
          <div style={{ color: T2, fontSize: 14 }}>오늘은 아직 주문을 낸 적이 없어요</div>
        ) : recent.map((r, i) => (
          <div key={r.id ?? i} style={{ display: "flex", gap: 10, padding: "8px 0", fontSize: 14 }}>
            <span style={{ color: T3, width: 92, flex: "none" }}>
              {formatKst(r.created_at)?.slice(-5) || "—"}
            </span>
            <span style={{ color: T1 }}>{timelineSentence(r, lookupName)}</span>
          </div>
        ))}
        <div style={{ fontSize: 13, color: T2, marginTop: 8, paddingTop: 10, borderTop: `1px solid ${LINE}` }}>
          {todaySummaryLine(today)}
        </div>
      </div>

      {/* 6. AI 한마디 */}
      <div style={card}>
        <div style={secTitle}>AI 한마디</div>
        <div data-testid="simple-home-ai" style={{ fontSize: 17, fontWeight: 700, lineHeight: 1.5, color: T1 }}>
          {aiOneLiner(latestAi)}
        </div>
        <button
          type="button"
          onClick={() => onJumpTab?.("signal")}
          style={{ fontSize: 13, color: "#3b82f6", marginTop: 10, background: "none", border: "none", cursor: "pointer", fontFamily: "inherit", padding: 0 }}
        >
          전략 자세히 보기 ›
        </button>
      </div>

      {/* 7. 안전 고지 + 전문가 보기 */}
      <div style={{ textAlign: "center", fontSize: 12, color: T3, marginTop: 18, lineHeight: 1.7 }}>
        모의투자 — 실제 돈이 나가지 않아요 · 실거래 OFF<br />
        <button
          type="button"
          data-testid="simple-home-expert"
          onClick={onExpert}
          style={{ marginTop: 8, color: "#3b82f6", textDecoration: "underline", background: "none", border: "none", cursor: "pointer", fontSize: 13, fontFamily: "inherit" }}
        >
          전문가 보기 (기존 화면 전체) ›
        </button>
      </div>
    </div>
  );
}
