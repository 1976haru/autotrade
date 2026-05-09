# Market Observer Agent (#52)

본 문서는 [`MarketObserverAgent`](../backend/app/agents/market_observer.py)의 정책 contract를 정의한다. 장중 시장 환경을 *관찰*만 하고 다른 Agent / 운영자가 참고할 snapshot JSON을 생성하는 read-only Agent.

**본 Agent는 주문 신호를 만들지 *않는다*.** BUY/SELL/HOLD 반환 금지, approval queue 등록 금지, broker 호출 금지.

## 1. 목적

장중 시장 환경(시장지수 / 거래대금 / 변동성 / 섹터 흐름 / 데이터 freshness)을 한 객체로 요약해 다른 Agent들이 판단의 *컨텍스트*로 활용할 수 있게 한다. 단일 Agent가 "관찰 + 판단 + 주문"을 모두 하면 책임 추적이 불가능해지므로, Observer는 *순수 관찰 계층*에 머문다.

## 2. 감시 항목

| 항목 | 입력 필드 | 분류 |
|---|---|---|
| 시장 지수 | `indices: list[IndexQuote]` | 그대로 carry |
| 거래대금 (vs 평균) | `turnover_vs_avg: float` | `BELOW_AVG` / `NORMAL` / `ABOVE_AVG` / `SURGE` / `UNKNOWN` |
| 변동성 (%) | `volatility_pct: float` | `LOW` / `NORMAL` / `ELEVATED` / `EXTREME` / `UNKNOWN` |
| 강세 섹터 | `leading_sectors: list[str]` | 그대로 carry |
| 약세 섹터 | `lagging_sectors: list[str]` | 그대로 carry |
| 강세 테마 | `leading_themes: list[str]` | 그대로 carry |
| 급등 종목 수 (+5%↑) | `surge_count: int` | 그대로 carry + summary 반영 |
| 급락 종목 수 (-5%↓) | `plunge_count: int` | 위험 신호로 활용 (>20 → HIGH) |
| 데이터 freshness (초) | `data_freshness_seconds: int` | `FRESH` / `STALE` / `EXPIRED` / `UNKNOWN` |
| Market Regime | `market_regime: RegimeOutput` | classify_market_regime 결과 그대로 carry |

모든 입력은 *optional* — 데이터 부족 시 UNKNOWN / WATCH_ONLY로 friendly fallback (예외 X).

### 2.1 분류 임계값

| State | Turnover | Volatility | Freshness |
|---|---|---|---|
| LOW / FRESH | — | < 1% | < 60s |
| NORMAL | 0.7 ~ 1.3x | 1 ~ 2% | — |
| ABOVE_AVG / ELEVATED / STALE | 1.3 ~ 2.0x | 2 ~ 3.5% | 60s ~ 5min |
| SURGE / EXTREME / EXPIRED | ≥ 2.0x | ≥ 3.5% | > 5min |
| BELOW_AVG | < 0.7x | — | — |

## 3. 출력 구조 (`MarketObserverOutput`)

```python
@dataclass(frozen=True)
class MarketObserverOutput:
    risk_level:            MarketRiskLevel       # LOW / MEDIUM / HIGH / BLOCKED
    recommended_stance:    RecommendedStance     # AGGRESSIVE / NORMAL / DEFENSIVE / WATCH_ONLY / PAUSE_NEW_BUY
    summary_lines:         list[str]             # 정확히 3줄 (모바일 우선)
    turnover_state:        TurnoverState
    volatility_state:      VolatilityState
    freshness_status:      DataFreshnessStatus
    leading_sectors:       list[str]
    lagging_sectors:       list[str]
    leading_themes:        list[str]
    surge_count:           int
    plunge_count:          int
    indices:               list[dict]
    market_regime:         dict | None           # classify_market_regime 결과
    reasons:               list[str]
    is_order_signal:       bool                  # *항상 False* (가드)
    created_at:            datetime
```

**`is_order_signal=False` 불변** — `__post_init__`에서 ValueError 발생 (테스트로 lock).

### 3.1 risk_level 결정 매트릭스

| 조건 | risk_level |
|---|---|
| `freshness=EXPIRED` | BLOCKED |
| `regime.trade_permission=BLOCK` (RISK_OFF 등) | BLOCKED |
| `volatility=EXTREME` 또는 `plunge_count > 20` | HIGH |
| `regime.trade_permission=PAUSE` | HIGH |
| `volatility=ELEVATED` 또는 `turnover=BELOW_AVG` 또는 `freshness=STALE` | MEDIUM |
| `regime.trade_permission=WATCH` | MEDIUM |
| 위 모두 미해당 | LOW |

### 3.2 recommended_stance 결정 매트릭스

| 조건 | stance |
|---|---|
| `risk_level=BLOCKED` | PAUSE_NEW_BUY |
| `risk_level=HIGH` | WATCH_ONLY |
| `risk_level=MEDIUM` | DEFENSIVE |
| `risk_level=LOW` + `turnover ∈ {ABOVE_AVG, SURGE}` + `volatility ≠ EXTREME` | AGGRESSIVE |
| 그 외 | NORMAL |

### 3.3 3줄 요약 정책

운영자가 모바일에서 한눈에 볼 수 있도록 *항상 3줄* 생성:

1. **시장 위험도**: `시장 위험도: 낮음/보통/높음/차단`
2. **거래대금 + 변동성**: 자연어 한 줄
3. **권장 스탠스**: 신규 매수 가능 여부 + 부가 정보 (stale, 급등 종목 수, 강세 섹터)

예시:
```
시장 위험도: 보통
거래대금은 평소 수준입니다. 변동성이 평소보다 높습니다.
신규 매수는 가능하지만 sizing 축소를 권장합니다. (강세 섹터: 반도체, 2차전지)
```

## 4. 다른 Agent와의 관계

| Agent | 본 Observer를 어떻게 사용 |
|---|---|
| **StrategySelectionAgent** | `recommended_stance ∈ {WATCH_ONLY, PAUSE_NEW_BUY}` → 신규 진입 회피 |
| **RiskOfficerAgent** (= RiskAuditor #51) | `risk_level ∈ {HIGH, BLOCKED}` → 더 보수적 가드 적용 |
| **ChiefTradingAgent** | snapshot 전체를 컨텍스트로 종합 판단 |
| **ExecutionRecommender** (#51) | snapshot 참고만 — 본 Observer 출력은 직접 주문으로 연결 X |

본 Agent의 출력은 *어떤 caller에게도 권장 스탠스를 강제하지 않는다*. caller가 자유롭게 활용 / 무시할 수 있는 advisory.

## 5. 주문 신호 아님 원칙 (절대 invariant)

| 원칙 | 가드 |
|---|---|
| BUY / SELL / HOLD 반환 금지 | `recommended_stance` enum에 BUY/SELL/HOLD 값 없음 (AGGRESSIVE / NORMAL / DEFENSIVE / WATCH_ONLY / PAUSE_NEW_BUY 5개만) |
| approval queue 등록 금지 | 본 모듈에 `submit_candidate` / `route_order` import 0건 |
| broker 호출 금지 | `app.brokers.*` import 0건 (정적 grep 가드) |
| OrderExecutor 호출 금지 | `app.execution.executor` import 0건 |
| 외부 네트워크 호출 금지 | 모든 입력은 caller가 dataclass로 주입 (HTTP client import 0건) |
| `is_order_signal=False` 강제 | `__post_init__` ValueError (테스트로 lock) |

`/api/agents/market-observer` endpoint도 read-only — broker 호출 0건, audit row 0건, DB 변경 0건 (`test_api_market_observer_does_not_create_audit_or_orders`로 invariant lock).

## 6. API surface

| Endpoint | 메서드 | 의미 |
|---|---|---|
| `/api/agents/market-observer` | POST | snapshot 생성. 모든 필드 optional — 빈 payload여도 friendly fallback |

응답 형식:
```json
{
  "risk_level": "MEDIUM",
  "recommended_stance": "DEFENSIVE",
  "summary_lines": ["...", "...", "..."],
  "turnover_state": "NORMAL",
  "volatility_state": "ELEVATED",
  "freshness_status": "FRESH",
  "leading_sectors": ["반도체", "2차전지"],
  "lagging_sectors": ["화학"],
  "leading_themes": [],
  "surge_count": 5,
  "plunge_count": 3,
  "indices": [{"name": "KOSPI", "last_price": 2700.5, "change_pct": 0.8, ...}],
  "market_regime": {"regime": "TREND_UP", "confidence": 80, "trade_permission": "ALLOW", ...},
  "reasons": ["turnover=NORMAL", "volatility=ELEVATED", ...],
  "is_order_signal": false,
  "created_at": "2026-05-09T12:00:00+00:00"
}
```

## 7. UI

[`frontend/src/components/tabs/MarketObserverCard.jsx`](../frontend/src/components/tabs/MarketObserverCard.jsx) — Dashboard / Agent 탭에 마운트.

**모바일 우선** (3줄 요약):
- 시장 위험도 / 거래대금 + 변동성 / 권장 스탠스

**PC** (자세히 보기):
- 위 3줄 + 거래대금/변동성/freshness state + 급등/급락 카운트 + 강세/약세 섹터 chip + market_regime + reasons

**필수 표시**:
- "주문 신호 아님" 배지 (회색)
- "본 snapshot은 *주문 신호가 아닙니다*. BUY/SELL/HOLD는 RiskManager + PermissionGate + OrderExecutor 흐름에서만 만들어집니다." disclaimer

**Empty state**:
- "시장 관찰 데이터를 아직 불러오지 못했습니다."
- "Demo Mode에서는 mock snapshot을 표시합니다."

**금지된 UI 요소**:
- BUY / SELL / HOLD 버튼 (테스트로 lock — `does NOT render any BUY/SELL/HOLD buttons or text in primary CTA`)
- "주문 실행" / "활성화" CTA

## 8. 향후 과제 (53번 이후, 본 PR 외)

1. **KOSPI/KOSDAQ 실제 지수 연동** — 현재는 caller가 IndexQuote 주입; KIS / yfinance 어댑터 통합은 별도 PR
2. **Sector breadth** — 상승/하락 섹터 비율 자동 산출
3. **Theme signals 연계** — `theme_signals.py`(#22)와 통합해 leading_themes 자동 채움
4. **Watchlist universe 연계** — 운영자 watchlist의 sector 분포 자동 분석
5. **Real-time dashboard** — 시간 시리즈 시각화 (현재는 단일 snapshot)
6. **Operating loop integration** — `operating_loop.py`의 장중 단계에 본 Observer 자동 호출

## 9. 변경 시 동기화

- 새 `MarketRiskLevel` / `RecommendedStance` 값 추가 → 본 문서 §3.1/§3.2 + 테스트
- 새 입력 필드 추가 → `MarketObserverInput` + `MarketObserverIn` (Pydantic) + 본 문서 §2
- 분류 임계값 조정 → 본 문서 §2.1 + 테스트 boundary 갱신
- 본 Observer가 다른 Agent의 결정을 강제하는 경로 *추가 금지* — advisory invariant 유지

## 관련 문서

- [`agent_architecture.md`](agent_architecture.md) — 6개 표준 Agent 역할 contract (#51)
- [`agent_design.md`](agent_design.md) — Agent 분리 정책
- [`risk_policy.md`](risk_policy.md) — RiskManager 평가 매트릭스 (실제 거부 결정은 여기서)
- `app/agents/market_regime.py` — Regime classifier (#225)
- `CLAUDE.md` — 절대 원칙 1번 (AI 직접 호출 금지)
