/**
 * RiskPolicy field metadata mirrored from backend dataclass.
 *
 * Source of truth: backend/app/risk/risk_manager.py::RiskPolicy
 * The defaultValue and envVar columns must stay in lockstep with that file
 * — the StrategyRisk tab uses them to render DEFAULT vs OVERRIDDEN badges
 * so operators can verify their env overrides took effect at runtime.
 */
export const RISK_POLICY_FIELDS = [
  { key: "max_order_notional",  label: "주문당 최대 명목", envVar: "RISK_MAX_ORDER_NOTIONAL",  defaultValue: 1_000_000, kind: "krw" },
  { key: "max_daily_loss",      label: "일일 최대 손실",   envVar: "RISK_MAX_DAILY_LOSS",      defaultValue:   200_000, kind: "krw" },
  { key: "max_positions",       label: "최대 보유 종목",   envVar: "RISK_MAX_POSITIONS",       defaultValue:         5, kind: "count" },
  { key: "max_symbol_exposure", label: "종목 노출 한도",   envVar: "RISK_MAX_SYMBOL_EXPOSURE", defaultValue: 1_500_000, kind: "krw" },
  { key: "enable_live_trading", label: "실거래 활성화",    envVar: "ENABLE_LIVE_TRADING",      defaultValue: false,     kind: "bool" },
  { key: "enable_ai_execution", label: "AI 실행 활성화",   envVar: "ENABLE_AI_EXECUTION",      defaultValue: false,     kind: "bool" },
];
