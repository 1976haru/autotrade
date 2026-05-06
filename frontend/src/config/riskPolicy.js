/**
 * RiskPolicy field metadata mirrored from backend dataclass.
 *
 * Source of truth: backend/app/risk/risk_manager.py::RiskPolicy
 * The defaultValue and envVar columns must stay in lockstep with that file
 * — the StrategyRisk tab uses them to render DEFAULT vs OVERRIDDEN badges
 * so operators can verify their env overrides took effect at runtime.
 *
 * 199: Surface every guard (22 fields) — earlier the FE only showed 6,
 * leaving 16 behind-the-scenes guards invisible to the operator.
 */
export const RISK_POLICY_FIELDS = [
  // ---------- 명목/한도 (1) ----------
  { key: "max_order_notional",  label: "주문당 최대 명목", envVar: "RISK_MAX_ORDER_NOTIONAL",  defaultValue: 1_000_000, kind: "krw" },
  { key: "max_daily_loss",      label: "일일 최대 손실",   envVar: "RISK_MAX_DAILY_LOSS",      defaultValue:   200_000, kind: "krw" },
  { key: "max_positions",       label: "최대 보유 종목",   envVar: "RISK_MAX_POSITIONS",       defaultValue:         5, kind: "count" },
  { key: "max_symbol_exposure", label: "종목 노출 한도",   envVar: "RISK_MAX_SYMBOL_EXPOSURE", defaultValue: 1_500_000, kind: "krw" },

  // ---------- 활성화 플래그 (2) ----------
  { key: "enable_live_trading", label: "실거래 활성화",    envVar: "ENABLE_LIVE_TRADING",      defaultValue: false,     kind: "bool" },
  { key: "enable_ai_execution", label: "AI 실행 활성화",   envVar: "ENABLE_AI_EXECUTION",      defaultValue: false,     kind: "bool" },
  { key: "disable_ai_orders",   label: "AI 주문 차단",     envVar: "DISABLE_AI_ORDERS",        defaultValue: false,     kind: "bool" },

  // ---------- 시세 신선도 (143) ----------
  { key: "stale_price_max_age_seconds", label: "시세 stale 한도(초)",
    envVar: "STALE_PRICE_MAX_AGE_SECONDS", defaultValue: 60, kind: "seconds" },

  // ---------- AI 가드 (158, 159, 161) ----------
  { key: "min_ai_confidence",            label: "AI 최소 confidence",
    envVar: "RISK_MIN_AI_CONFIDENCE",      defaultValue: 0,   kind: "count" },
  { key: "enforce_ai_reasoning",         label: "AI reasoning 필수",
    envVar: "RISK_ENFORCE_AI_REASONING",   defaultValue: true, kind: "bool" },
  { key: "ai_rate_limit_window_seconds", label: "AI rate-limit window(초)",
    envVar: "RISK_AI_RATE_LIMIT_WINDOW_SECONDS", defaultValue: 60, kind: "seconds" },
  { key: "ai_rate_limit_max_count",      label: "AI rate-limit 최대",
    envVar: "RISK_AI_RATE_LIMIT_MAX_COUNT",      defaultValue: 0,  kind: "count" },

  // ---------- 종목/시간/포지션 비율 (174-176) ----------
  { key: "max_position_size_pct", label: "종목 최대 비중(%)",
    envVar: "RISK_MAX_POSITION_SIZE_PCT",  defaultValue: 0.0, kind: "pct" },
  { key: "symbol_whitelist",      label: "심볼 화이트리스트",
    envVar: "RISK_SYMBOL_WHITELIST",       defaultValue: [],  kind: "list" },
  { key: "enforce_market_hours",  label: "장 시간 가드",
    envVar: "RISK_ENFORCE_MARKET_HOURS",   defaultValue: false, kind: "bool" },

  // ---------- 글로벌 rate-limit (177) ----------
  { key: "global_rate_limit_window_seconds", label: "전역 rate-limit window(초)",
    envVar: "RISK_GLOBAL_RATE_LIMIT_WINDOW_SECONDS", defaultValue: 60, kind: "seconds" },
  { key: "global_rate_limit_max_count",      label: "전역 rate-limit 최대",
    envVar: "RISK_GLOBAL_RATE_LIMIT_MAX_COUNT",      defaultValue: 0,  kind: "count" },

  // ---------- 노출 비율 (179-181) ----------
  { key: "max_total_exposure",      label: "총 노출 한도",
    envVar: "RISK_MAX_TOTAL_EXPOSURE",       defaultValue: 0,   kind: "krw" },
  { key: "max_total_exposure_pct",  label: "총 노출 비중(%)",
    envVar: "RISK_MAX_TOTAL_EXPOSURE_PCT",   defaultValue: 0.0, kind: "pct" },
  { key: "max_symbol_exposure_pct", label: "종목 노출 비중(%)",
    envVar: "RISK_MAX_SYMBOL_EXPOSURE_PCT",  defaultValue: 0.0, kind: "pct" },

  // ---------- 자동 정지 (182) + 일일 한도 (183) ----------
  { key: "auto_stop_consecutive_rejections", label: "연속거부 자동정지(회)",
    envVar: "RISK_AUTO_STOP_CONSECUTIVE_REJECTIONS", defaultValue: 0, kind: "count" },
  { key: "max_orders_per_day", label: "일일 최대 주문 수",
    envVar: "RISK_MAX_ORDERS_PER_DAY",       defaultValue: 0,   kind: "count" },
];
