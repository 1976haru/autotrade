import { useState } from "react";
import { TopBar }       from "./components/layout/TopBar";
import { BottomNav }    from "./components/layout/BottomNav";
import { Dashboard }    from "./components/tabs/Dashboard";
import { StrategyRisk } from "./components/tabs/StrategyRisk";
import { BotControl }   from "./components/tabs/BotControl";
import { Approvals }    from "./components/tabs/Approvals";
import { MarketChart }  from "./components/tabs/MarketChart";
import { Backtest }     from "./components/tabs/Backtest";
import { AuditLog }     from "./components/tabs/AuditLog";
import { AISignal }     from "./components/tabs/AISignal";
import { LiveEngine }   from "./components/tabs/LiveEngine";
import { Futures }      from "./components/tabs/Futures";
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

export default function App() {
  return (
    <ErrorBoundary label="앱 전체">
      <AppShell />
    </ErrorBoundary>
  );
}

function AppShell() {
  const [tab, setTab] = useState("dash");
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
        return <Dashboard portfolio={portfolio} bot={bot} botControls={{ start: bot.start, stop: bot.stop }} emergencyStop={riskPolicy.emergencyStop} emergencyStopSince={emergencyStopSince} pendingCount={approvals.pending.length} stalePendingCount={stalePendingCount} approvals={approvals} onJumpTab={setTab} />;
      }
      case "strat":  return <StrategyRisk strategyOn={strategy.strategyOn} toggle={strategy.toggle} strategyParams={strategy.strategyParams} updateParam={strategy.updateParam} risk={risk} updateRisk={updateRisk} riskPolicy={riskPolicy} operatorName={settings.operatorName} />;
      case "bot":      return <BotControl bot={bot} />;
      case "approve":  return <Approvals approvals={approvals} operatorName={settings.operatorName} />;
      case "chart":    return <MarketChart />;
      case "backtest": return <Backtest />;
      case "audit":    return <AuditLog approvals={approvals} />;
      case "signal":   return <AISignal activeStratIds={strategy.activeIds} />;
      case "engine":   return <LiveEngine />;
      case "futures":  return <Futures />;
      case "config":   return <Settings settings={settings} />;
      default:       return null;
    }
  };

  return (
    <div className="app-shell" style={{ minHeight:"100vh", background:"#010a14", color:"#c9d6e3", fontFamily:"'JetBrains Mono','Courier New',monospace", display:"flex", flexDirection:"column" }}>
      <TopBar brokerId={settings.brokerId} tradeMode={settings.tradeMode} connected={settings.connected} />
      <BackendOfflineBanner />
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
