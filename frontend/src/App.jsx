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
import { usePortfolio }  from "./store/usePortfolio";
import { useBot }        from "./store/useBot";
import { useStrategy }   from "./store/useStrategy";
import { useRisk }       from "./store/useRisk";
import { useRiskPolicy } from "./store/useRiskPolicy";
import { useSettings }   from "./store/useSettings";

export default function App() {
  const [tab, setTab] = useState("dash");
  const portfolio  = usePortfolio();
  const strategy   = useStrategy();
  const { risk, update: updateRisk } = useRisk();
  const riskPolicy = useRiskPolicy();
  const bot        = useBot();
  const settings   = useSettings();

  const renderTab = () => {
    switch (tab) {
      case "dash":   return <Dashboard portfolio={portfolio} bot={bot} botControls={{ start: bot.start, stop: bot.stop }} />;
      case "strat":  return <StrategyRisk strategyOn={strategy.strategyOn} toggle={strategy.toggle} strategyParams={strategy.strategyParams} updateParam={strategy.updateParam} risk={risk} updateRisk={updateRisk} riskPolicy={riskPolicy} />;
      case "bot":      return <BotControl bot={bot} />;
      case "approve":  return <Approvals />;
      case "chart":    return <MarketChart />;
      case "backtest": return <Backtest />;
      case "audit":    return <AuditLog />;
      case "signal":   return <AISignal activeStratIds={strategy.activeIds} />;
      case "engine":   return <LiveEngine />;
      case "futures":  return <Futures />;
      case "config":   return <Settings settings={settings} />;
      default:       return null;
    }
  };

  return (
    <div style={{ minHeight:"100vh", background:"#010a14", color:"#c9d6e3", fontFamily:"'JetBrains Mono','Courier New',monospace", maxWidth:520, margin:"0 auto", display:"flex", flexDirection:"column" }}>
      <TopBar brokerId={settings.brokerId} tradeMode={settings.tradeMode} connected={settings.connected} />
      <div style={{ flex:1, overflowY:"auto", padding:"14px 14px 90px", scrollbarWidth:"thin" }}>
        {renderTab()}
      </div>
      <BottomNav active={tab} onChange={setTab} />
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
