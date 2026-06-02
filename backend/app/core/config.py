from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.modes import OperationMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_name: str = "auto-trader-backend"
    default_mode: OperationMode = OperationMode.SIMULATION
    enable_live_trading: bool = False
    enable_ai_execution: bool = False
    enable_futures_live_trading: bool = False
    # 작업 10 — 위험 기능 다중 잠금용 추가 flag. 본 settings 는 값을 carry만
    # 하며 활성 가능 여부 판단은 app.core.feature_flags 에서 다중 조건으로
    # 평가한다. 기본값은 반드시 False — 단일 flag 만으로 활성화 불가.
    enable_crypto_futures_live: bool = False  # 코인 선물 실거래 (stock futures와 *별개*)
    enable_kimp_strategy: bool = False        # 김프(Korean Premium) 전략 모듈 활성
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    database_url: str = "sqlite:///./data/auto_trader.db"
    # market data provider — "mock" (합성), "yfinance" (지연 시세), "kis" (실시간
    # KIS 시세, read-only quote API). **provider="kis" 일 때만** KIS 실시세 기반
    # Paper Auto 가 KIS 모의주문을 전송할 수 있다. mock / yfinance 에서는 KIS
    # 모의주문 전송이 차단된다 (mock 시세로 KIS 주문 전송 금지 — auto_permission
    # 의 KIS_REALTIME_PRICE_REQUIRED 가드).
    market_data_provider: Literal["mock", "yfinance", "kis"] = "mock"

    enable_fill_polling:           bool = False
    fill_polling_interval_seconds: int  = 5

    # PART1-3: Paper 현금/원금/실현손익을 재시작 간 디스크에 영속할지 여부.
    # 안전 flag 5종과 무관 — 모의 잔고 보존용. lifespan 이 이 값을 os.environ
    # 으로 동기화해 capital_state(_persistence_enabled) 가 읽는다 (capital_state
    # 는 get_settings 를 import 하지 않는다는 원칙 유지를 위해 env 경유).
    paper_capital_persist: bool = False

    # fix/desktop-nonblocking-migration-health: 데스크톱 EXE 운영자가 첫 실행 시
    # alembic migration 으로 1~2분 멈춰 보이는 문제 해결을 위한 *opt-in* flag.
    # True 이면 lifespan 이 migration 을 background thread 로 띄우고 즉시 yield
    # → `/health` + `/api/status` 가 첫 응답부터 200 응답. False (default) 이면
    # 기존 동기 동작 유지 — 모든 기존 test / CI / script 가 무회귀.
    # 본 flag 는 *주문 / 안전 flag 와 무관* — 단지 startup 흐름 분기.
    migration_nonblocking: bool = False

    # RiskPolicy thresholds — operator-tunable without code changes.
    # Defaults match RiskPolicy() defaults, so unset env vars preserve behavior.
    risk_max_order_notional:   int = 1_000_000
    risk_max_daily_loss:       int = 200_000
    risk_max_positions:        int = 5
    risk_max_symbol_exposure:  int = 1_500_000
    # 143: 시세 응답이 N초보다 오래된 경우 RiskManager가 REJECTED. broker가 죽었거나
    # 데이터 피드가 멈춘 상태에서의 주문을 차단한다. 너무 짧으면 정상 운영을 방해하고
    # 너무 길면 stale 의미가 흐려진다 — 60초 기본은 KIS 분봉 운영을 가정.
    stale_price_max_age_seconds: int = 60

    # 158: AI 제안의 최소 confidence 임계 (0-100). requested_by_ai=True 주문이
    # signal_confidence < 임계이면 거부. 0이면 검사 비활성 (기본). 운영자가
    # 의도적으로 켜야만 가드가 작동.
    min_ai_confidence: int = 0

    # 159: AI 제안의 explainability invariant. True (기본)이면 requested_by_ai=
    # True 주문이 ai_decision_meta.reasons를 갖지 않으면 RiskManager가 REJECTED.
    # 운영자가 backwards-compat 위해 끌 수 있지만 LIVE 단계에서는 절대 false 금지.
    enforce_ai_reasoning: bool = True

    # 161: AI 제안 rate limit. (strategy, symbol)별 N초 안의 제안 카운트가
    # max_count 이상이면 추가 제안 차단. max_count=0이면 비활성 (기본).
    ai_rate_limit_window_seconds: int = 60
    ai_rate_limit_max_count:      int = 0

    # 174: equity 대비 단일 주문 명목 비율 한도 (%). 0이면 비활성. max_order_notional
    # 이 절대값 한도라면 본 항목은 자본 대비 자동 스케일.
    max_position_size_pct: float = 0.0

    # 175: symbol whitelist. 콤마 구분 문자열로 env 입력 (예: "005930,000660").
    # 빈 문자열이면 비활성 (기본). 비어있지 않으면 미등록 symbol 주문 거부.
    symbol_whitelist: str = ""

    # 176: 한국 시장 시간(09:00–15:30 KST 평일) 외 주문 거부. False면 비활성 (기본).
    enforce_market_hours: bool = False

    # 177: 시스템 전체 주문 rate limit (strategy / AI / manual 통합). 0이면 비활성.
    global_rate_limit_window_seconds: int = 60
    global_rate_limit_max_count:      int = 0

    # 178: AI 주문 kill-switch. emergency_stop과 별개로 AI만 차단. 기본 False.
    disable_ai_orders: bool = False

    # 179: 총 노출 한도 (모든 보유 포지션 합). max_symbol_exposure가 종목별 한도라면
    # 본 항목은 전체 합. 절대값(원) + 자본 대비 비율(%) 별도 옵션. 0이면 비활성.
    max_total_exposure:     int   = 0
    max_total_exposure_pct: float = 0.0

    # 181: 종목별 노출의 자본 대비 % 한도 (max_symbol_exposure 절대값에 보완).
    max_symbol_exposure_pct: float = 0.0

    # 182: N건 연속 REJECTED 발생 시 자동 emergency_stop. 0이면 비활성. 권장 5~10.
    auto_stop_consecutive_rejections: int = 0

    # 183: 일일(KST date) 최대 주문 횟수 한도. 0이면 비활성.
    max_orders_per_day: int = 0

    # AI Paper background tick driver — *opt-in*, 기본 OFF. RUNNING + 장중 +
    # PAPER/SIM 모드일 때만 N초마다 run-once diagnostic 파이프라인을 자동 반복
    # 실행한다. **실거래 주문 권한이 아니다** — broker / OrderExecutor /
    # route_order 를 호출하지 않으며, Paper 가상 후보 / dry-run 까지만. LIVE 와
    # 무관하고 `enable_live_trading=true` 면 driver 는 *절대 실행되지 않는다*.
    enable_ai_paper_background_tick: bool = False
    ai_paper_tick_interval_seconds:  int  = 30
    # 일일(KST date) 최대 자동 tick 횟수. 0이면 무제한 (장중 내내).
    ai_paper_tick_max_per_day:       int  = 0
    # True (기본) 이면 ledger 에 체결처럼 반영하지 않고 판단/사유만 기록.
    ai_paper_tick_dry_run:           bool = True
    # Paper 모의 *체결 시뮬레이션* 허용 — 기본 OFF. True + dry_run=false 일 때만
    # VirtualOrder 생성 + Paper fill simulator 까지 진행 (실거래 아님, broker
    # 호출 0건). False 면 가상 후보 생성/체결 없이 판단/사유만 기록.
    ai_paper_allow_simulated_fills:  bool  = False
    # Paper 체결 시뮬 슬리피지 (bps). 0 (기본) 이면 현재가 그대로 체결.
    ai_paper_fill_slippage_bps:      float = 0.0

    # KIS Paper Auto Trading — 한투 *모의투자* API 자동주문. **실거래 권한이
    # 아니다.** 기본 OFF. True + dry_run=false 일 때만 KIS 모의투자 API 로 실제
    # 모의 주문을 전송한다 (모의 계좌, 실제 돈 0원). KIS_IS_PAPER=true +
    # ENABLE_LIVE_TRADING=false 가 *반드시* 필요 — 둘 중 하나라도 어긋나면 차단.
    # 주문은 기존 sanctioned 경로(route_order → RiskManager → PermissionGate →
    # OrderExecutor → KisBrokerAdapter.place_order(is_paper=True))를 통과한다.
    enable_kis_paper_auto_trading:   bool  = False
    # True (기본) 이면 주문 전송 직전까지만 검증하고 KIS API 호출 0건
    # (KIS_PAPER_DRY_RUN_OK). False 일 때만 실제 KIS 모의투자 주문 전송.
    kis_paper_auto_order_dry_run:    bool  = True
    kis_paper_auto_max_orders_per_day: int = 10
    kis_paper_auto_max_order_notional: int = 1_000_000
    # 주문 허용 시간창 (KST "HH:MM"). 장 시작 직후/마감 직전 변동성 회피.
    kis_paper_auto_order_window_start: str = "09:05"
    kis_paper_auto_order_window_end:   str = "14:50"
    # 신호 품질 최소 기준 (자동주문 진입 게이트).
    kis_paper_auto_min_confidence:    float = 0.6   # 0~1
    kis_paper_auto_min_quality_score: int   = 60    # 0~100
    # KIS 모의 체결 조회 polling — 기본 OFF (안전). enable_fill_polling 와 별개.
    kis_paper_fill_polling:           bool  = False
    kis_paper_fill_poll_interval_seconds: int = 10

    # ── KIS Realtime Paper Auto V2 ─────────────────────────────────────────
    # 정상 Paper Auto 운용 한도 (smoke mode 아님). 조건 충족 시 *여러 종목* 을
    # 리스크 한도 내에서 KIS 모의주문. 본 값들은 KIS 모의 한도일 뿐 실거래 한도가
    # 아니다 — is_live_authorization 은 항상 False.
    kis_paper_max_concurrent_positions: int = 5
    kis_paper_per_symbol_notional_krw:  int = 1_000_000
    kis_paper_daily_buy_limit_krw:      int = 3_000_000
    # 1 tick 당 신규 진입 허용 종목 수 (버스트 방지). 0/음수는 1 로 보정.
    kis_paper_max_new_positions_per_tick: int = 1
    # 1 tick 당 KIS 실시세 조회 종목 수 상한 (rate limit 보호). 0 이면 universe 전체.
    kis_paper_scan_max_symbols:         int = 10
    # Smoke mode — *정상 Paper Auto 의 하위 제한 모드*. True 면 단일 종목 / 1주 /
    # 1건만 (최초 안전 확인용). 정상 운용에서는 반드시 false.
    kis_paper_smoke_mode:               bool = False
    kis_paper_smoke_symbol:             str  = "005930"
    kis_paper_smoke_qty:                int  = 1

    # KIS-PAPER-REAL-TICK-RUNNER-WIRING-V1 — quick/slow one-click 모드의 tick_runner
    # 를 *V2 다종목 KIS 실시간 스캔* 으로 흘려보낼지 여부. default False (안전,
    # 기존 단일 종목 live_runner 사용). True 로 켜야만 다종목 실시간 시세 스캔으로
    # 진입한다. 본 flag 는 *실거래 활성화* 와 *전혀 무관* — 모든 KIS 모의주문은
    # 여전히 KIS_IS_PAPER=true / ENABLE_LIVE_TRADING=false 가 강제된다.
    use_real_tick_runner:               bool = False

    def symbol_whitelist_set(self) -> set[str]:
        """env 콤마 문자열을 set으로 파싱. 공백 strip."""
        if not self.symbol_whitelist:
            return set()
        return {s.strip() for s in self.symbol_whitelist.split(",") if s.strip()}

    # 167: PendingApproval TTL. 0이면 만료 안 함 (기본). 운영자가 명시적으로
    # 켜야만 자동 EXPIRED 전환. 권장 600~1800 (10~30분) — 시세 stale 임계와 맞춤.
    approval_ttl_seconds: int = 0

    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""
    # KIS 계좌상품코드 (모의/실 공통). 국내주식 현금은 통상 "01".
    # default "01" — 운영자가 backend/.env 에서 override 가능. *Secret 아님*
    # (계좌 *번호* 가 아니라 상품 구분 코드) 이므로 readiness 응답에 노출 가능.
    kis_product_code: str = "01"
    kis_is_paper: bool = True

    # #42: Paper Trading 사용할 broker 종류. "MOCK" 또는 "KIS_PAPER".
    # default 빈 문자열 — `_default_paper_broker_kind`가 default_mode + kis_is_paper
    # 로 자동 추론. 운영자가 명시 override 가능.
    paper_broker_kind: str = ""
    # KIS 모의투자 quote API documented rate limit = 2 req/s. 이전 default(5/1.0)
    # 는 EGW00201("초당 거래건수 초과") cascade 의 직접 원인이었다 — V2 다종목 scan
    # 이 1~2 tick 만에 quote 호출 5+회 발사 후 KIS 가 500 응답 → engine 자동중단.
    # 안전 margin 포함 2 req per 1.1s (≈ 1.8 req/s) 로 default 조정. 운영자가
    # 필요 시 KIS_RATE_LIMIT_CALLS / KIS_RATE_LIMIT_WINDOW_SECONDS 로 override.
    kis_rate_limit_calls:          int   = 2
    kis_rate_limit_window_seconds: float = 1.1

    # EGW00201 후속 (2026-06-02): 다종목 scan 은 동일 tick 안에서 잔고를 종목마다
    # 다시 조회하고(잔고는 tick 내 사실상 불변) 같은 종목 시세를 scan + route_order
    # 가 중복 조회한다 — 06-02 EGW00201 141건 중 138건이 balance/quote 였다. 읽기
    # 전용 quote/balance 응답을 *짧게* 캐싱해 중복 호출을 제거한다.
    #   - TTL 은 STALE_PRICE_MAX_AGE_SECONDS(60) 보다 *훨씬* 짧게 둬 시세 신선도를
    #     해치지 않는다(캐시된 Quote 는 원래 조회 timestamp 를 보존 — stale 판정 정직).
    #   - 0 이면 캐싱 비활성(기존 동작). 주문 체결 후에는 balance 캐시를 즉시 무효화.
    kis_quote_cache_ttl_seconds:   float = 1.5
    kis_balance_cache_ttl_seconds: float = 5.0

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_max_retries:    int   = 2
    anthropic_timeout_seconds: float = 30.0

    # #64: Notifications. *Secret 보관 위치는 본 settings + backend .env 만*.
    # frontend / docs / git에 token 또는 chat_id를 저장하지 않는다.
    notifications_enabled:                  bool  = False
    notifications_min_severity:             str   = "INFO"   # DEBUG/INFO/WARN/CRITICAL
    notifications_dedupe_window_seconds:    int   = 60
    notifications_always_send_critical:     bool  = True
    telegram_bot_token:        str = ""      # backend/.env only — never log
    telegram_chat_id:          str = ""
    telegram_timeout_seconds:  float = 5.0
    telegram_max_retries:      int   = 1

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
