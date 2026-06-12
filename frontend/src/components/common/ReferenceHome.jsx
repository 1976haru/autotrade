// 새 홈 "ReferenceHome" — reference.jpg 스타일 + 가독성/통일성 보강.
// 색/폰트는 전역 디자인 토큰(var(--c-*)/var(--fs-*)) 사용 → 앱 chrome과 통일되고,
// 설정탭의 글자크기·테마가 자동 반영. 기본=밝은 테마, 우상단 다크 토글(고대비).
// ★동작 로직 신설 0 — 시작/정지/긴급정지/성향변경은 기존 핸들러·훅 그대로 재사용.
// 정직성: 추정치 금지(실체결 없으면 "거래 시작 전"), 종목코드 대신 한글명.
import { useEffect, useState } from "react";
import "./ReferenceHome.css";
import { backendApi } from "../../services/backend/client";
import {
  APP_NAME, EXIT_PLAN_DEFAULTS, PAPER_ALLOC_FALLBACK, PRICE_TICK_MS,
} from "../../config/constants";
import { computeTradingStatus, summarizeTodayOrders } from "../../utils/tradingStatus";
import { isMarketOpen, currentMarketPhase } from "../../utils/marketHours";
import { fmtKRW, nowKstHm } from "../../utils/format";
import { heroFromStatus, aiOneLiner, moneyFailureLine, translateStopReason } from "../../utils/simpleHomeText";
import { pickLatestChiefDecision } from "../tabs/AgentLatestTile";
import { usePaperCapitalSettings } from "../../store/usePaperCapitalSettings";
import { useAutoPaperLoop } from "../../store/useAutoPaperLoop";
import { usePersistedState } from "../../store/usePersistedState";
import { normalizeRiskProfile } from "../AgentRiskProfileSelector";
import {
  maskAccountNo, todayOpenOrderCount, ACCOUNT_BULLET, resolveSymbolName,
  livePanelLine, groupLiveLines, kstDayLabel, marketClosedLine, miniKpis, dailyProgress,
  strategyChips, FEATURE_SHORTCUTS,
} from "../../utils/referenceHome";
import { RuntimeConfigCard } from "./RuntimeConfigCard";
import { PerformanceCard } from "./PerformanceCard";
import { LivePositionsCard } from "./LivePositionsCard";
import { RiskProfileSwitchCard } from "./RiskProfileSwitchCard";
import { TechniqueScorecard } from "./TechniqueScorecard";
import { AgentDashboard } from "./AgentDashboard";
import { BriefingBoard } from "./BriefingBoard";
import { PreflightPanel } from "./PreflightPanel";

// 등락 색 — 한국식(+빨강 −파랑). 라이트/다크 모두 대비 확보.
const UP = "#e5443b", DOWN = "#2563eb";
// 디자인 토큰 단축
const C = {
  bg: "var(--c-bg)", surface: "var(--c-surface)", surface2: "var(--c-surface-2)",
  border: "var(--c-border)", text: "var(--c-text)", text2: "var(--c-text-2)", text3: "var(--c-text-3)",
};
const F = {
  xs: "var(--fs-xs)", sm: "var(--fs-sm)", base: "var(--fs-base)",
  md: "var(--fs-md)", lg: "var(--fs-lg)", xl: "var(--fs-xl)", xxl: "var(--fs-2xl)",
};

const pnlColor = (n) => (n > 0 ? UP : n < 0 ? DOWN : C.text);
const signed = (n) => `${n > 0 ? "+" : ""}${fmtKRW(Math.round(n))}`;
const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 16, padding: 18, boxShadow: "0 1px 3px rgba(0,0,0,.06)" };
const secLabel = { fontSize: F.lg, fontWeight: 800, color: C.text, marginBottom: 12 };

export function ReferenceHome({
  portfolio, emergencyStop, onEmergencyStop,
  onJumpTab, onExpert, operatorName, accountNo,
}) {
  const [orders, setOrders] = useState([]);
  const [startNote, setStartNote] = useState(null); // 시작 결과(게이트) 안내
  const [latestAi, setLatestAi] = useState(null);
  const [cashState, setCashState] = useState(null);
  const [alloc, setAlloc] = useState(null);
  const [perf, setPerf] = useState(null);   // W1: /api/performance(daily) FIFO 승률 — 미니KPI 단일 소스
  const [logEntries, setLogEntries] = useState(null);
  const [stratReport, setStratReport] = useState(null); // Agent Council 전략별 카운트
  const [lastOkHm, setLastOkHm] = useState(null);
  const [rtConfig, setRtConfig] = useState(null);        // R3: 런타임 설정(실효값)
  const [livePos, setLivePos] = useState(null);          // M3: 라이브 포지션
  const [theme, setTheme] = usePersistedState("refhome_theme", "light", (v) => v === "light" || v === "dark");

  const capital = usePaperCapitalSettings({ api: backendApi });
  const riskProfile = normalizeRiskProfile(capital.settings?.riskProfile);

  // ▶시작/⏸중지를 *실제* Auto Paper Loop에 배선 — 기존 client 함수 재사용.
  const loop = useAutoPaperLoop({ riskProfile, capitalSettings: capital.settings });
  const running = loop.running; // ★히어로 상태도 bot 플래그가 아닌 실제 루프 상태 기준

  // ▶ 클릭: (기존 핸들러로) 시작 → pre-market gate 결과 표시. 장외면 자동 시작 안내.
  const handleStart = async () => {
    const closed = !isMarketOpen();
    const res = await loop.start();
    if (closed) {
      setStartNote({ kind: "closed", text: "지금은 장 시간이 아니에요 — 장이 열리면(평일 09:00) 자동으로 거래를 시작해요" });
    } else if (res.ok) {
      setStartNote({ kind: "ok", text: "자동매매를 시작했어요" });
    } else {
      const reasons = (res.reasons || []).map((r) => translateStopReason(r)).filter(Boolean).join(" · ");
      setStartNote({ kind: "blocked", text: reasons ? `시작하지 못했어요 — ${reasons}` : "지금은 시작할 수 없어요 (잠시 후 다시 시도해 주세요)" });
    }
  };
  const handleStop = async () => { await loop.stop(); setStartNote(null); };

  useEffect(() => {
    if (!portfolio?.loading && !portfolio?.error) setLastOkHm(nowKstHm());
  }, [portfolio?.loading, portfolio?.error, portfolio?.totalAsset]);

  useEffect(() => {
    let cancelled = false;
    let timer;
    let attempts = 0;
    // T2: 일회성 fetch도 G3식 경계된 복구 재시도 — "백엔드 늦게 뜸 → 영구 실패 고착" 차단.
    //   첫 성공까지 지수 백오프(4→8→16, 최대 4회), 소진 시 _failed(임의값 금지).
    const loadCfg = () => {
      attempts += 1;
      backendApi.paperCapitalConfig?.().then((c) => { if (!cancelled) setAlloc(c); }).catch(() => {});
      backendApi.runtimeConfigGet?.().then((c) => { if (!cancelled) setRtConfig(c); })
        .catch(() => {
          if (cancelled) return;
          if (attempts < 4) {
            timer = setTimeout(loadCfg, Math.min(16000, 4000 * 2 ** (attempts - 1)));
          } else {
            setRtConfig({ _failed: true });
          }
        });
    };
    loadCfg();
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const [aud, dec, cs, log, strat, pos, perfRes] = await Promise.allSettled([
        backendApi.listOrderAudits({ limit: 50 }),
        backendApi.aiAgentDecisions(20),
        backendApi.paperCashState(),
        backendApi.paperDecisionLog(12),
        backendApi.agentStrategyPerformance({ period: "daily" }),  // V3: 오늘 신호만(누적 수백 개 아님)
        backendApi.positionsLive?.(),   // M3: 기존 폴링에 편승(신규 폴링 0)
        backendApi.performanceGet?.({ period: "daily" }),  // W1: 승률 단일 소스(FIFO 청산) — 성과카드와 동일
      ]);
      if (cancelled) return;
      if (perfRes.status === "fulfilled" && perfRes.value) setPerf(perfRes.value);
      // F1: 조회 실패(rejected)는 *실패 플래그*로 — null 로 두면 카드가 '보유 0'으로
      //   둔갑한다(실패 ≠ 빈 목록). available=false 면 카드가 '불러오기 실패' 분기.
      setLivePos(pos.status === "fulfilled" && pos.value ? pos.value : { available: false, positions: [] });
      if (aud.status === "fulfilled") { const v = aud.value; setOrders(Array.isArray(v) ? v : (v?.items ?? [])); }
      if (dec.status === "fulfilled") setLatestAi(pickLatestChiefDecision(dec.value));
      if (cs.status === "fulfilled") setCashState(cs.value);
      if (strat.status === "fulfilled") setStratReport(strat.value);
      // 방어: 응답이 배열 등 예상 외 형태면 `.entries`가 함수(Array.prototype.entries)일
      // 수 있다 → setState에 함수가 들어가면 React가 updater로 오인해 크래시. 배열일 때만 수용.
      if (log.status === "fulfilled") {
        setLogEntries(Array.isArray(log.value?.entries) ? log.value.entries : []);
      }
    };
    load();
    const t = setInterval(load, Math.max(PRICE_TICK_MS, 7000));
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const status = computeTradingStatus({
    running, emergencyStop, marketOpen: isMarketOpen(), marketPhase: currentMarketPhase(),
  });
  const hero = heroFromStatus(status);
  const marketOpen = isMarketOpen();

  // 파생값
  const positions = portfolio?.positions ?? [];
  const today = summarizeTodayOrders(orders);
  const openCnt = todayOpenOrderCount(today); // D2: 오늘(KST) 기준 미체결
  // ★전략별 신호 수 = Agent Council 전략별 실제 카운트(strategy-performance).
  //   옛 todayStrategyChips 는 decision-log 의 strategy 필드(빈 값)를 보느라 항상 0이었다.
  const stratChips = strategyChips(stratReport);
  // D3: 집계된 episode 가 0 이면 "집계 전(데이터 없음)" — EGW00201 등으로 스캔이
  //   무산돼 신호 0 이 된 경우와 *진짜 0 신호* 를 구분(코스메틱 0 으로 덮지 않음).
  const stratDataAvailable = Number(stratReport?.episodes_analyzed ?? 0) > 0;
  const kpi = miniKpis({ cashState, today, perf });
  // W2: 일일 매수 한도는 runtime-config 실효값(SSOT, config 에서 읽음) 우선 — 옛
  //   capital-config(alloc)·하드코딩 3,000,000 폴백은 effective 미로딩 시에만.
  // T2(2026-06-12): runtime-config 의 daily_buy_limit_krw 가 {value,…} 객체로 바뀜
  //   (스테퍼 meta). 구 평수(plain int)·capital-config 폴백도 호환.
  const _rtDaily = rtConfig?.daily_buy_limit_krw;
  const buyMax = (_rtDaily && typeof _rtDaily === "object" ? _rtDaily.value : _rtDaily)
    ?? alloc?.daily_buy_limit_krw ?? 3_000_000;
  const prog = dailyProgress({ orders, today, buyMaxKrw: buyMax });
  const balanceFailed = !!portfolio?.error;
  const masked = maskAccountNo(accountNo);
  const realized = kpi.realizedRaw;
  const name = operatorName?.trim() ? operatorName.trim() : "운영자";
  // U4 + R3: SSOT — 히어로 표시와 런타임 설정 카드는 *같은 소스*(runtime-config
  //   실효값)를 본다. rtConfig(런타임) > capital-config effective > fallback 순.
  //   같은 화면에 두 숫자가 다르게 뜨는 일을 방지.
  const maxSymbols = rtConfig?.max_concurrent_positions?.value ?? alloc?.effective_max_concurrent_positions ?? alloc?.max_concurrent_positions ?? PAPER_ALLOC_FALLBACK.maxSymbols;
  const perSymbolKrw = rtConfig?.per_stock_budget?.value ?? alloc?.effective_per_symbol_notional_krw ?? alloc?.effective_per_symbol_cap_krw ?? alloc?.per_symbol_max_krw ?? PAPER_ALLOC_FALLBACK.perSymbolKrw;
  // U7: 연속 동일 이벤트(동일 종목+동일 사유)는 묶어서 표시(원본은 백엔드 보존, 화면만 압축).
  const liveLines = groupLiveLines(
    (logEntries || []).map((e) => ({ ...livePanelLine(e), day: kstDayLabel(e.timestamp) })).filter((l) => l && l.text)
  );

  // 긴급정지 — 실수 방지 확인 1회 (해제는 확인 없이).
  const askEmergency = () => {
    if (emergencyStop) { onEmergencyStop?.(); return; }
    const ok = (typeof window !== "undefined" && typeof window.confirm === "function")
      ? window.confirm("정말 자동매매를 멈출까요?\n긴급정지를 누르면 모든 주문이 차단돼요.")
      : true;
    if (ok) onEmergencyStop?.();
  };

  const bigBtn = {
    flex: 1, padding: "16px 10px", border: "none", borderRadius: 14, cursor: "pointer",
    fontFamily: "inherit", fontSize: F.md, fontWeight: 800, color: "#fff", minHeight: 56,
  };

  const StatCell = ({ k, v, testid }) => (
    <div style={{ textAlign: "center" }} data-testid={testid}>
      <div style={{ fontSize: F.sm, color: "rgba(40,20,24,.78)", fontWeight: 600 }}>{k}</div>
      <div style={{ fontSize: F.md, fontWeight: 800, color: "#2a1418", marginTop: 3 }}>{v}</div>
    </div>
  );

  return (
    <div data-testid="reference-home" className={`rh-root${theme === "dark" ? " rh-dark" : ""}`}>

      {/* 우상단 네비: [오늘 아침 브리핑](G1 드롭다운) + 다크/밝기 토글 */}
      <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <BriefingBoard />
        <button type="button" data-testid="refhome-theme-toggle"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          style={{ background: C.surface, border: `1px solid ${C.border}`, color: C.text2, borderRadius: 999, padding: "7px 14px", cursor: "pointer", fontFamily: "inherit", fontSize: F.sm, fontWeight: 700 }}>
          {theme === "dark" ? "☀️ 밝게 보기" : "🌙 어둡게 보기"}
        </button>
      </div>

      {/* ⑥ 긴급정지 풀폭 배너 */}
      {emergencyStop && (
        <div data-testid="refhome-estop-banner" style={{
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10,
          background: "#c0392b", color: "#fff", borderRadius: 14, padding: "14px 18px", marginBottom: 12,
          fontWeight: 800, fontSize: F.md,
        }}>
          <span>🛑 긴급 정지 중 — 모든 주문이 차단돼 있어요</span>
          <button type="button" data-testid="refhome-estop-release" onClick={onEmergencyStop}
            style={{ background: "#fff", color: "#c0392b", border: "none", borderRadius: 10, padding: "8px 16px", fontWeight: 800, cursor: "pointer", fontFamily: "inherit", flex: "none", fontSize: F.base }}>
            해제
          </button>
        </div>
      )}

      <div className="rh-grid">

        {/* ① 히어로 카드 + 진행률 + ③ 시작/긴급정지 동일 크기 버튼 */}
        <div style={{ gridArea: "hero", borderRadius: 18, padding: "18px 18px 16px",
          background: "linear-gradient(135deg, #f7a8b0 0%, #ef8a86 45%, #e87a6d 100%)", boxShadow: "0 4px 16px rgba(232,122,109,.3)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <div>
              <div style={{ fontSize: F.lg, fontWeight: 800, color: "#2a1418" }}>{APP_NAME}</div>
              <div style={{ fontSize: F.sm, color: "#5a2c30", marginTop: 3, fontWeight: 600 }}>
                {hero.emoji} {status.state === "TRADING" ? "거래 중" : status.state === "WAITING" ? "대기 중" : status.state === "BLOCKED" ? "멈춤" : "정지"}
              </div>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8, marginTop: 14,
            background: "rgba(42,20,24,.16)", borderRadius: 12, padding: "12px 6px" }}>
            {/* C1: 손절/익절은 runtime-config 실효값(SSOT). 미로딩 시 config 기본 폴백. */}
            <StatCell k="손절" v={`-${rtConfig?.stop_loss_pct?.value ?? EXIT_PLAN_DEFAULTS.stopLossPct}%`} testid="refhome-stop-loss" />
            <StatCell k="익절" v={`+${rtConfig?.take_profit_pct?.value ?? EXIT_PLAN_DEFAULTS.takeProfitPct}%`} testid="refhome-take-profit" />
            <StatCell k="설정 종목 수" v={`${maxSymbols}개`} testid="refhome-max-symbols" />
            <StatCell k="종목당 투자금" v={`${Math.round(perSymbolKrw / 10000)}만원`} />
          </div>
          {/* 오늘 진행률 */}
          <div style={{ marginTop: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: F.sm, color: "#2a1418", fontWeight: 700 }}>
              <span>오늘 진행률 · 주문 {prog.orderCount}건</span>
              <span>매수 {Math.round(prog.buyUsedKrw / 10000)}만 / {Math.round(prog.buyMaxKrw / 10000)}만</span>
            </div>
            <div style={{ height: 9, background: "rgba(42,20,24,.18)", borderRadius: 999, marginTop: 5, overflow: "hidden" }}>
              <div data-testid="refhome-progress" style={{ width: `${prog.buyPct}%`, height: "100%", background: "#2a1418", borderRadius: 999 }} />
            </div>
          </div>
          {/* R3: 장중 설정 변경 카드 — 시작/긴급정지 버튼 *위*. 오터치 방지를 위해
              아래에 충분한 간격 + 구분선으로 긴급정지 버튼과 분리. */}
          <RuntimeConfigCard config={rtConfig} onSaved={setRtConfig} />
          <div style={{ height: 1, background: "rgba(42,20,24,.2)", margin: "18px 2px 0" }} />
          {/* V8: 출발 전 점검 — 시작 버튼 바로 위 */}
          <div style={{ marginTop: 14 }}><PreflightPanel /></div>
          {/* ③ 시작 / 긴급정지 — 같은 줄, 같은 크기. 실제 Auto Paper Loop에 배선 */}
          <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
            <button type="button" data-testid="refhome-startstop"
              onClick={running ? handleStop : handleStart} disabled={loop.busy}
              style={{ ...bigBtn, opacity: loop.busy ? 0.6 : 1, background: running ? "#475569" : "#15a05f" }}>
              {loop.busy ? "처리 중…" : running ? "⏸ 자동매매 정지" : "▶ 자동매매 시작"}
            </button>
            <button type="button" data-testid="refhome-emergency" onClick={askEmergency}
              style={{ ...bigBtn, background: "#c0392b" }}>
              🛑 {emergencyStop ? "긴급정지 해제" : "긴급정지"}
            </button>
          </div>
          {/* pre-market gate / 시작 결과 안내 */}
          {startNote && (
            <div data-testid="refhome-start-note" style={{
              marginTop: 10, fontSize: F.sm, fontWeight: 700, lineHeight: 1.45, borderRadius: 10, padding: "10px 12px",
              background: startNote.kind === "ok" ? "rgba(21,160,95,.18)" : "rgba(42,20,24,.16)",
              color: "#2a1418",
            }}>
              {startNote.kind === "ok" ? "✅ " : startNote.kind === "closed" ? "🕘 " : "⚠️ "}{startNote.text}
            </div>
          )}
        </div>

        {/* ⑤ AI 운용 성향 — S2 실전환(봇 정지 시) + S5 기법 성적표 */}
        <div style={{ gridArea: "profile", ...card }}>
          <div style={secLabel}>AI 운용 성향</div>
          <RiskProfileSwitchCard rtConfig={rtConfig} botRunning={running} onChanged={setRtConfig} />
          <TechniqueScorecard activeProfile={rtConfig?.active_profile?.value} />
        </div>

        {/* 숫자 칩 바 */}
        <div style={{ gridArea: "chips", display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 8 }}>
          {[
            // G2: 잔고 미준비(ready 전)엔 0개가 아니라 "—"(가짜 0 금지).
            { k: "보유 종목", v: portfolio?.ready ? positions.length : "—", suffix: portfolio?.ready ? "개" : "", tab: "audit" },
            { k: "미체결", v: openCnt, suffix: "건", tab: "approve" },
            { k: "오늘 주문", v: today.orderCount, suffix: "건", tab: "audit" },
            { k: "오늘 체결", v: today.filledCount, suffix: "건", tab: "audit" },
          ].map((c) => (
            <button key={c.k} type="button" data-testid={`refhome-chip-${c.k}`} onClick={() => onJumpTab?.(c.tab)}
              style={{ ...card, padding: "14px 6px", cursor: "pointer", fontFamily: "inherit", textAlign: "center" }}>
              <div style={{ fontSize: F.sm, color: C.text3, fontWeight: 600 }}>{c.k}</div>
              <div style={{ fontSize: F.xxl, fontWeight: 800, color: C.text, marginTop: 4 }}>
                {c.v}<span style={{ fontSize: F.sm, fontWeight: 600, color: C.text3 }}>{c.suffix}</span>
              </div>
            </button>
          ))}
        </div>

        {/* ⑤ 계좌정보 — 중앙 최상단(내 돈 먼저) */}
        <div style={{ gridArea: "account", ...card }}>
          <div style={{ fontSize: F.lg, fontWeight: 800, color: C.text }}>{name}님의 계좌정보</div>
          <div style={{ fontSize: F.sm, color: C.text3, margin: "3px 0 16px", letterSpacing: ".04em" }}>{masked || "모의계좌 (KIS Paper)"}</div>
          {/* G2: 첫 성공(ready) 전 로딩/재시도 중엔 "불러오는 중"(가짜 0 금지);
              에러면 "불러오지 못했어요"(옛 숫자 대신 안내 — U 정직성 유지); 숫자는
              ready+에러없을 때만(0원은 KIS 가 실제 0 을 반환할 때만). */}
          {!portfolio?.ready && portfolio?.loading ? (
            <div data-testid="refhome-balance-loading" style={{ fontSize: F.md, fontWeight: 700, color: C.text2 }}>불러오는 중…</div>
          ) : balanceFailed ? (
            <div data-testid="refhome-balance-failed" style={{ fontSize: F.md, fontWeight: 700, color: UP }}>{moneyFailureLine(lastOkHm)}</div>
          ) : (
            <div>
            {/* T2: stale(레이트리밋 옛값)은 숫자 + 기준시각 명시 — 가짜 0도, 불필요한 실패도 아님. */}
            {portfolio?.stale && (
              <div data-testid="refhome-balance-stale" style={{ fontSize: F.sm, fontWeight: 700, color: "#7a4a00", marginBottom: 8 }}>
                {portfolio?.asOf || ""} 기준 · 증권사 응답 지연으로 옛 값이에요
              </div>
            )}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", rowGap: 18, columnGap: 14 }}>
              <AcctItem bullet={ACCOUNT_BULLET.estimatedAsset} label="추정자산" value={`${fmtKRW(portfolio?.totalAsset ?? 0)}원`} />
              <AcctItem bullet={ACCOUNT_BULLET.deposit} label="예수금" value={`${fmtKRW(portfolio?.cash ?? 0)}원`} />
              <AcctItem bullet={ACCOUNT_BULLET.stockValue} label="주식평가금액" value={`${fmtKRW(portfolio?.invested ?? 0)}원`} />
              <AcctItem bullet={ACCOUNT_BULLET.realized} label="실현손익" sub="청산 완료분"
                value={realized == null ? "거래 시작 전" : `${signed(realized)}원`} color={realized == null ? C.text3 : pnlColor(realized)} />
              {/* W4/V3: 이 비율은 *현재 보유* 마크투마켓(평가손익률, 미실현) — 위 '실현손익'
                  (청산 라운드트립)과 다른 지표라 라벨 + 보조라벨로 명확히. 부호는 값 그대로. */}
              <AcctItem bullet={ACCOUNT_BULLET.returnPct} label="평가손익률" sub="보유 중"
                value={`${(portfolio?.totalPnLPct ?? 0) > 0 ? "+" : ""}${(portfolio?.totalPnLPct ?? 0).toFixed(2)}%`} color={pnlColor(portfolio?.totalPnLPct ?? 0)} />
            </div>
            </div>
          )}
          {/* P3: 성과 대시보드 — 계좌정보 카드 하단(승률·손익비·순손익 + 봇 vs 지수) */}
          <PerformanceCard />
          {/* M3: 라이브 포지션 상황판 + 종목별 수동 전량 매도 */}
          <LivePositionsCard data={livePos} />
        </div>

        {/* ⑥ 미니 KPI 줄 */}
        <div style={{ gridArea: "kpi", ...card, display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
          {[
            { k: "오늘 실현손익", v: kpi.realizedText, c: realized == null ? C.text3 : pnlColor(realized) },
            { k: "승률", v: kpi.winRateText, c: C.text3 },
            { k: "체결률", v: kpi.fillRateText, c: kpi.fillRateText === "거래 시작 전" ? C.text3 : C.text },
          ].map((x) => (
            <div key={x.k} style={{ textAlign: "center" }}>
              <div style={{ fontSize: F.sm, color: C.text2, fontWeight: 600 }}>{x.k}</div>
              <div style={{ fontSize: x.v === "거래 시작 전" ? F.sm : F.xl, fontWeight: 800, color: x.c, marginTop: 5 }}>{x.v}</div>
            </div>
          ))}
        </div>

        {/* ④ 실시간 현황판 */}
        <div style={{ gridArea: "live", ...card }}>
          <div style={{ ...secLabel, display: "flex", alignItems: "center", gap: 7 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: running && marketOpen ? "#22c55e" : C.text3,
              boxShadow: running && marketOpen ? "0 0 0 3px #22c55e33" : "none" }} />
            {marketOpen ? "지금 AI가 하는 일" : "오늘 AI가 한 일"}
          </div>
          {liveLines.length === 0 ? (
            <div data-testid="refhome-live-empty" style={{ fontSize: F.base, color: C.text2 }}>
              {marketOpen ? "아직 판단 기록이 없어요" : marketClosedLine()}
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column" }}>
              {liveLines.map((l, i) => (
                <div key={i} style={{ display: "flex", gap: 10, padding: "9px 0", borderBottom: `1px solid ${C.border}`, fontSize: F.base }}>
                  <span style={{ color: C.text3, width: 70, flex: "none", fontWeight: 600 }}>
                    {l.day ? `${l.day} ` : ""}{l.time}
                  </span>
                  <span style={{ color: C.text }}>
                    {l.text}
                    {l.count > 1 && (
                      <span data-testid="refhome-live-repeat" style={{ color: C.text3, fontWeight: 700 }}> ×{l.count}회</span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ③ 매매기법 줄 — Agent Council 전략별 실제 신호 수(최근 누적) */}
        <div style={{ gridArea: "strat", ...card }}>
          <div style={secLabel}>매매기법 · 최근 신호</div>
          {!stratDataAvailable && (
            <div data-testid="refhome-strat-nodata" style={{ fontSize: F.sm, color: C.text3, margin: "2px 0 8px" }}>
              아직 집계 전이에요 (장 시작 전 · 시세 제한 등)
            </div>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 8 }}>
            {stratChips.map((s) => (
              <button key={s.key} type="button" data-testid={`refhome-strat-${s.key}`} onClick={() => onJumpTab?.("signal")}
                style={{ background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 12, padding: "12px 6px", cursor: "pointer", fontFamily: "inherit", textAlign: "center" }}>
                <div style={{ fontSize: F.md, fontWeight: 800, color: C.text }}>{s.label}</div>
                <div style={{ fontSize: F.sm, color: C.text2, marginTop: 4 }}>
                  {/* V3: 휴장일엔 "휴장"(누적 신호 수백 개 오표기 금지), 평일 데이터 있으면 오늘 신호 수. */}
                  {stratReport?.market_closed_today ? "휴장" : stratDataAvailable ? `신호 ${s.signals}개` : "집계 전"}
                </div>
                <div style={{ fontSize: F.sm, marginTop: 2, color: s.verdict.startsWith("매수") ? UP : s.verdict.startsWith("매도") ? DOWN : C.text3 }}>{s.verdict}</div>
              </button>
            ))}
          </div>
        </div>

        {/* ④ 에이전트 한 줄 */}
        {/* AG6: 에이전트 전용 섹션 (한 줄 → 대시보드로 확장) */}
        <div style={{ gridArea: "agent", ...card }}>
          <button type="button" data-testid="refhome-agent" onClick={() => onJumpTab?.("signal")}
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: "inherit", textAlign: "left", display: "flex", flexDirection: "column", gap: 6, width: "100%" }}>
            <span style={secLabel}>에이전트 ›</span>
            <span style={{ fontSize: F.md, fontWeight: 700, color: C.text }}>{aiOneLiner(latestAi)}</span>
          </button>
          <AgentDashboard />
        </div>

        {/* ②③ 주요 기능 바로가기 */}
        <div style={{ gridArea: "shortcuts", ...card }}>
          <div style={secLabel}>주요 기능</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
            {FEATURE_SHORTCUTS.map((f) => (
              <button key={f.tab} type="button" data-testid={`refhome-feature-${f.tab}`} onClick={() => onJumpTab?.(f.tab)}
                style={{ background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 12, padding: "14px 4px", cursor: "pointer", fontFamily: "inherit", textAlign: "center", color: C.text, minHeight: 64 }}>
                <div style={{ fontSize: 22 }}>{f.icon}</div>
                <div style={{ fontSize: F.sm, fontWeight: 700, marginTop: 5 }}>{f.label}</div>
              </button>
            ))}
          </div>
        </div>

      </div>

      {/* 하단 한 줄 */}
      <div style={{ textAlign: "center", fontSize: F.sm, color: C.text3, marginTop: 16, lineHeight: 1.8 }}>
        모의투자 모드 — 실제 돈이 나가지 않아요 (안전하게 연습 중)<br />
        <button type="button" data-testid="refhome-expert" onClick={onExpert}
          style={{ color: "#2563eb", textDecoration: "underline", background: "none", border: "none", cursor: "pointer", fontSize: F.base, fontFamily: "inherit" }}>
          전문가 보기 (기존 화면 전체) ›
        </button>
      </div>
    </div>
  );
}

function AcctItem({ bullet, label, value, color, sub }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: "var(--fs-base)", color: "var(--c-text-2)", fontWeight: 600 }}>
        <span style={{ width: 9, height: 9, borderRadius: "50%", background: bullet, flex: "none" }} />
        {label}
        {/* V3: 실현(청산 완료분) vs 평가(보유 중) 혼동 방지 보조 라벨. */}
        {sub && <span style={{ fontSize: "var(--fs-sm)", color: "var(--c-text-3)", fontWeight: 500 }}>· {sub}</span>}
      </div>
      <div style={{ fontSize: "var(--fs-xl)", fontWeight: 800, color: color || "var(--c-text)", marginTop: 5 }}>{value}</div>
    </div>
  );
}
