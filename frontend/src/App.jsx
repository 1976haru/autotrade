import { useEffect, useState } from "react";
import {
  applyDisplaySettingsToRoot,
  loadDisplaySettings,
} from "./store/useDisplaySettings";
import { TopBar }       from "./components/layout/TopBar";
import { BottomNav }    from "./components/layout/BottomNav";
import { TopNav }       from "./components/layout/TopNav";
import { Dashboard }    from "./components/tabs/Dashboard";
import { ReferenceHome } from "./components/common/ReferenceHome";
import { usePersistedState } from "./store/usePersistedState";
import { StrategyRisk } from "./components/tabs/StrategyRisk";
import { BotControl }   from "./components/tabs/BotControl";
import { Approvals }    from "./components/tabs/Approvals";
import { MarketChart }  from "./components/tabs/MarketChart";
import { Backtest }     from "./components/tabs/Backtest";
import { AuditLog }     from "./components/tabs/AuditLog";
import { AISignal }     from "./components/tabs/AISignal";
import { LiveEngine }   from "./components/tabs/LiveEngine";
import { Futures, FuturesDisabledNotice } from "./components/tabs/Futures";
import { Settings }     from "./components/tabs/Settings";
import { isPendingStale } from "./utils/format";
import { emergencyStopOnSince } from "./components/tabs/Dashboard";
import { useApprovals }  from "./store/useApprovals";
import { usePortfolio }  from "./store/usePortfolio";
import { useBot }        from "./store/useBot";
import { useStrategy }   from "./store/useStrategy";
import { useRisk }       from "./store/useRisk";
import { useRiskPolicy } from "./store/useRiskPolicy";
import { useSettings }   from "./store/useSettings";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { BackendOfflineBanner } from "./components/BackendOfflineBanner";
import { OfflineBanner }    from "./components/common/OfflineBanner";
import { PwaInstallHint }   from "./components/common/PwaInstallHint";
import { FEATURES } from "./config/features";
import {
  ReleaseNotesModal,
  useReleaseNotesAutoPopup,
} from "./components/common/VersionBadge";

export default function App() {
  return (
    <ErrorBoundary label="앱 전체">
      <AppShell />
    </ErrorBoundary>
  );
}

function AppShell() {
  // feature/display-settings-and-ui-readability: App boot 시점에 사용자가
  // 이전 세션에서 저장한 display preset 을 root html 요소에 즉시 반영.
  // DisplaySettingsCard 가 mount 되기 전에도 EXE 가 사용자의 선호 글자 크기/
  // 화면 폭/테마로 렌더링되도록 한다. broker / 실거래 호출 0건.
  useEffect(() => {
    applyDisplaySettingsToRoot(loadDisplaySettings());
  }, []);

  const [tab, setTab] = useState("dash");
  // 새 홈(ReferenceHome, reference.jpg 스타일)이 기본. "전문가 보기"를 누르면 기존
  // Dashboard(전체)로 전환되며 선택은 localStorage에 기억된다. 기존 홈은 보존(회귀 0).
  const [homeView, setHomeView] = usePersistedState(
    "home_view", "reference", (v) => v === "reference" || v === "expert",
  );
  const portfolio  = usePortfolio();
  const strategy   = useStrategy();
  const { risk, update: updateRisk } = useRisk();
  const riskPolicy = useRiskPolicy();
  const bot        = useBot();
  const settings   = useSettings();
  // 결재 큐는 App에서 한 번만 인스턴스화 — Approvals 탭의 입력 폼과 BottomNav의
  // PENDING 배지가 같은 폴링 결과를 공유한다 (5s 폴러가 두 번 돌지 않도록).
  const approvals  = useApprovals();

  const renderTab = () => {
    switch (tab) {
      case "dash": {
        // Stale count drives the Dashboard pin color escalation (amber → red)
        // so the operator can tell "3 PENDING with one rotting" apart from
        // "3 PENDING all fresh." Computed each render — pending list is small.
        const stalePendingCount = approvals.pending.filter(
          (a) => isPendingStale(a.created_at)
        ).length;
        // 069: when emergency_stop has been on a while, surface a reminder so
        // the operator doesn't leave the system silently rejecting orders.
        const emergencyStopSince = emergencyStopOnSince(
          riskPolicy.emergencyStop, riskPolicy.history,
        );
        const onEmergencyStop = () => riskPolicy.toggleEmergency({ decided_by: settings.operatorName, note: "operator panel" });
        // 기본은 새 홈(ReferenceHome, reference.jpg 스타일). 시작/정지/긴급정지 핸들러는
        // *동일한* 기존 함수를 그대로 넘긴다(로직 0 수정). "전문가 보기"는 기존 Dashboard를
        // 손대지 않고 위에 얇은 "← 간편 홈으로" 줄만 얹어 보존한다.
        if (homeView === "reference") {
          return <ReferenceHome portfolio={portfolio} emergencyStop={riskPolicy.emergencyStop} onEmergencyStop={onEmergencyStop} onJumpTab={setTab} onExpert={() => setHomeView("expert")} operatorName={settings.operatorName} accountNo={settings.apiKeys?.accountNo} />;
        }
        return (
          <>
            <button type="button" data-testid="home-back-to-simple" onClick={() => setHomeView("reference")}
              style={{ margin: "0 0 12px", padding: "8px 14px", borderRadius: 10, border: "1px solid var(--c-border, #2a3550)", background: "transparent", color: "var(--c-text-2, #94a3b8)", cursor: "pointer", fontFamily: "inherit", fontSize: 13 }}>
              ← 홈으로
            </button>
            <Dashboard portfolio={portfolio} bot={bot} botControls={{ start: bot.start, stop: bot.stop }} emergencyStop={riskPolicy.emergencyStop} emergencyStopSince={emergencyStopSince} pendingCount={approvals.pending.length} stalePendingCount={stalePendingCount} approvals={approvals} onJumpTab={setTab} onEmergencyStop={onEmergencyStop} />
          </>
        );
      }
      case "strat":  return <StrategyRisk strategyOn={strategy.strategyOn} toggle={strategy.toggle} strategyParams={strategy.strategyParams} updateParam={strategy.updateParam} risk={risk} updateRisk={updateRisk} riskPolicy={riskPolicy} operatorName={settings.operatorName} />;
      case "bot":      return <BotControl bot={bot} />;
      case "approve":  return <Approvals approvals={approvals} operatorName={settings.operatorName} />;
      case "chart":    return <MarketChart />;
      case "backtest": return <Backtest />;
      case "audit":    return <AuditLog approvals={approvals} />;
      case "signal":   return <AISignal activeStratIds={strategy.activeIds} />;
      case "engine":   return <LiveEngine />;
      // 50: Futures 탭은 `FEATURES.futuresTab=false`(기본)이면 navigation에서
      // 숨겨진다. URL/state 강제 접근 시에는 `<FuturesDisabledNotice />`로
      // 안전 안내 화면을 보여줘 사용자가 비활성 상태를 명확히 인지하도록 한다.
      case "futures":  return FEATURES.futuresTab
        ? <Futures />
        : <FuturesDisabledNotice />;
      case "config":   return <Settings settings={settings} />;
      default:       return null;
    }
  };

  return (
    <div className="app-shell" style={{ minHeight:"100vh", background:"var(--c-bg)", color:"var(--c-text)", fontFamily:"'Inter', system-ui, -apple-system, 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif", display:"flex", flexDirection:"column" }}>
      <TopBar brokerId={settings.brokerId} tradeMode={settings.tradeMode} connected={settings.connected} />
      <TopNav active={tab} onChange={setTab} badges={{ approve: approvals.pending.length }} />
      {/* #63: 네트워크 단절 알림 — BackendOfflineBanner(backend off)와 의미가
          다름. navigator.onLine 감시. */}
      <OfflineBanner />
      <BackendOfflineBanner />
      {/* #63: 홈화면 설치 안내 — standalone/dismiss 세션에선 노출 X. 새 홈(관제판)에서는
          최상단을 차지하지 않도록 숨기고, 설정(config) 탭으로 이동. */}
      {tab === "config" && <PwaInstallHint />}
      <ReleaseNotesAutoPopup />
      <div style={{ flex:1, overflowY:"auto", padding:"14px 14px 90px", scrollbarWidth:"thin" }}>
        <ErrorBoundary label="현재 탭">
          {renderTab()}
        </ErrorBoundary>
      </div>
      <BottomNav active={tab} onChange={setTab} badges={{ approve: approvals.pending.length }} />
      <style>{`
        @keyframes blink{50%{opacity:0}}
        ::-webkit-scrollbar{width:3px;height:3px}
        ::-webkit-scrollbar-track{background:#010a14}
        ::-webkit-scrollbar-thumb{background:#1a3a5c;border-radius:2px}
        input[type=range]{height:3px;cursor:pointer}
        *{-webkit-tap-highlight-color:transparent}
      `}</style>
    </div>
  );
}


// 새 버전 첫 접속 시 release notes modal 자동 팝업. 사용자가 닫으면 (또는
// "이번 버전 공지 확인" 버튼 클릭) localStorage에 lastSeenVersion 저장.
function ReleaseNotesAutoPopup() {
  const { open, closeModal } = useReleaseNotesAutoPopup();
  return <ReleaseNotesModal open={open} onClose={closeModal} />;
}
