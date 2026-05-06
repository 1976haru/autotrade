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
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    database_url: str = "sqlite:///./data/auto_trader.db"
    market_data_provider: Literal["mock", "yfinance"] = "mock"

    enable_fill_polling:           bool = False
    fill_polling_interval_seconds: int  = 5

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
    kis_is_paper: bool = True
    kis_rate_limit_calls:          int   = 5
    kis_rate_limit_window_seconds: float = 1.0

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_max_retries:    int   = 2
    anthropic_timeout_seconds: float = 30.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
