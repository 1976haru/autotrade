# News / Trend Agent (#53)

본 문서는 [`NewsTrendAgent`](../backend/app/agents/news_trend_agent.py)의 정책 contract를 정의한다. `theme_signals` 테이블(#22)을 read-only로 요약해 *후보 필터*와 Agent context로만 사용하는 advisory Agent.

**본 Agent는 주문 신호를 만들지 *않는다*.** BUY/SELL/HOLD 반환 금지, approval queue 등록 금지, broker 호출 금지, 외부 API 호출 금지. 뉴스 해석 오류(루머 / 과열 / 악재·호재 오판 / AI 요약 오류)가 직접 주문으로 이어지지 않도록 본 Agent는 read-only 요약 전용으로 둔다.

## 1. 목적

뉴스 / 트렌드 / 공시 데이터를 후보 필터와 Agent context로 요약. 단일 Agent가 "관찰 + 판단 + 주문"을 모두 하지 못하도록 본 Agent는 *순수 요약 계층*에 머문다 — 운영자 / 다른 Agent / RiskManager가 본 요약을 보고 *수동* 결정.

## 2. 입력 데이터

### `ThemeSignal` 테이블 (#22)
주된 입력. 본 Agent는 다음 컬럼만 읽는다 (모두 read-only SELECT):
- `id`, `created_at`, `theme`, `keywords`, `related_symbols`
- `score`, `grade` (STRONG/WATCH/WEAK/IGNORE), `confidence`
- `source`, `provider`, `summary`, `used_for_order`

### Provider stubs (모두 disabled — 외부 호출 0건)
| Provider | 상태 | 의미 |
|---|---|---|
| `MockTrendProvider` | enabled | 테스트 / Demo Mode 전용 fixtures만 반환 |
| `GoogleTrendsAlphaProvider` | **disabled** | alpha 접근 권한 + 약관 + rate limit 확인 후 별도 PR |
| `NewsProvider` | **disabled** | 유료 News API 별도 옵트인 PR |
| `DisclosureProvider` | **disabled** | DART 공시 API 별도 옵트인 PR |

본 PR 시점 모든 provider stub은 빈 list 반환 — 어떤 외부 호출도 발생하지 않는다.

### DB read-only helpers
```python
load_recent_theme_signals(db, limit=100, since=None, min_score=None)
load_theme_signals_by_theme(db, theme, limit=50)
```
- 둘 다 read-only SELECT
- INSERT / UPDATE / DELETE 0건 (정적 grep 가드)
- caller가 미리 조회한 row를 `summarize_themes()`에 전달

## 3. 출력 데이터 (`NewsTrendOutput`)

```python
@dataclass(frozen=True)
class NewsTrendOutput:
    recommended_action:       NewsTrendAction
    summary_lines:            list[str]                 # 운영자용 자연어 요약
    top_themes:               list[ThemeSummary]
    rising_keywords:          list[tuple[str, int]]
    related_candidates:       list[CandidateSymbol]
    caution_themes:           list[ThemeSummary]
    overheating_warnings:     list[str]
    used_for_order_warnings:  list[str]                 # invariant 위반 경고
    total_signal_count:       int
    window_seconds:           int | None
    is_order_signal:          bool                      # *항상 False* (가드)
    created_at:               datetime
```

### `NewsTrendAction` enum (advisory only — BUY/SELL/HOLD 0개)
| 값 | 의미 |
|---|---|
| `MONITOR` | 평소 모니터링 — 상위 테마 STRONG 한 개라도 |
| `RESEARCH` | 새 테마 후보 — 운영자 검토 권장 |
| `CAUTION` | confidence 낮음 또는 used_for_order 위반 의심 |
| `OVERHEAT_WARN` | score ≥ 90 + signal_count ≥ 5 — 과열 가능성 |
| `NO_DATA` | 입력 0건 |

### Top themes / Rising keywords / Candidates
- `top_themes`: score desc, signal_count desc — 상위 5개. 같은 테마의 여러 row는 score=max 통합.
- `rising_keywords`: keyword 빈도 Counter 상위 10개.
- `related_candidates`: related_symbols 빈도 상위 10개 + 관련 테마 carry.
- `caution_themes`: confidence < 30 인 테마 상위 5개.

### Overheating warnings
score ≥ 90 (또는 grade=STRONG) + signal_count ≥ 5인 테마는 과열 경고로 분류. 운영자에게 "추격 매수 자제 권장" 안내 — 자동 차단은 RiskManager가 별도 결정.

### `used_for_order_warnings` (invariant 위반 의심 경고)
ThemeSignal의 `used_for_order=True`인 row가 있으면 본 필드에 명시. 본 Agent는 그 row를 *주문에 사용하지 않으며*, 단지 운영자에게 "다른 caller가 invariant를 위반했을 수도 있다"고 알린다.

## 4. 안전 원칙 (절대 invariant)

| 원칙 | 가드 |
|---|---|
| 주문 신호 아님 | `is_order_signal=False` 불변 (`__post_init__` ValueError) |
| BUY/SELL/HOLD 반환 금지 | `NewsTrendAction` enum에 해당 값 0개 |
| approval queue 직접 등록 금지 | `submit_candidate` / `route_order` import 0건 |
| theme score만으로 매수 금지 | 본 Agent는 매수 결정 X — 운영자/RiskManager가 별도 결정 |
| RiskManager / PermissionGate / OrderExecutor 우회 금지 | 정적 grep 가드 |
| 외부 Google Trends / News / 공시 API 호출 금지 | HTTP client(httpx, requests, urllib3, pytrends) import 0건 |
| DB INSERT/UPDATE/DELETE 금지 | 정적 grep 가드 (read-only SELECT only) |
| AI provider 호출 금지 | anthropic / openai SDK import 0건 (본 PR 시점) |

## 5. Google Trends API alpha 대응

Google Trends API는 현재 **alpha tester 신청 기반**으로만 접근 가능하며, 실제 호출은 별도 권한 / 약관 / rate limit 확인 후 진행해야 한다. 본 PR은:

- `GoogleTrendsAlphaProvider`를 stub class로 export — 모든 메서드가 빈 list 반환
- `enabled=False` 명시
- 운영자가 alpha 접근 권한을 받은 뒤 별도 옵트인 PR로 활성화

본 PR에서 `pytrends` 등 비공식 라이브러리 import도 0건 — 정적 grep 가드.

## 6. 뉴스 해석 위험

본 Agent의 출력을 단독 매매 근거로 쓰면 안 되는 이유:

| 위험 | 영향 |
|---|---|
| 뉴스 지연 | 발표 시점과 시세 반영 시점 간 격차 |
| 루머 | 사실 확인 없이 score만 올라가면 단기 거래 함정 |
| 과열 테마 | "모두가 알면 늦은 신호" — overheating warnings로 carry |
| 악재/호재 오판 | sentiment 분류 오류 (provider별 정확도 상이) |
| AI 요약 오류 | LLM 환각 / 키워드 오추출 |

본 Agent는 위 위험을 *완화하지 않는다* — 단지 운영자에게 *원본 데이터의 요약*을 보여줄 뿐이다. 매매 결정은 RiskManager + PermissionGate + OrderExecutor 흐름에서 이루어져야 한다.

## 7. Agent 관계

| Agent | 본 NewsTrend 사용 패턴 |
|---|---|
| **MarketObserverAgent** (#52) | `top_themes`를 `leading_themes` 입력으로 활용 |
| **StrategySelectionAgent** | `caution_themes` / `overheating_warnings`에 속한 종목은 신규 진입 회피 |
| **ChiefTradingAgent** | 운영자 요약 + 후보 종목 카운트 참고 |
| **ExecutionRecommender** (#51) | `related_candidates`를 후보로 *참고만* — 직접 진입 X |

본 Agent의 출력은 *어떤 caller에게도 매수/매도를 강제하지 않는다*. caller가 자유롭게 활용 / 무시할 수 있는 advisory.

## 8. API surface

| Endpoint | 메서드 | 의미 |
|---|---|---|
| `/api/agents/news-trend` | GET | 최근 ThemeSignal을 read-only 요약. broker 호출 0건, audit row 0건, DB write 0건 |

쿼리 파라미터:
- `limit` (1-500, default 100)
- `min_score` (0-100, optional)

## 9. UI

[`frontend/src/components/tabs/NewsTrendCard.jsx`](../frontend/src/components/tabs/NewsTrendCard.jsx) — Agent / Dashboard 탭에 마운트.

**모바일 우선**:
- 운영자 요약 (3~5줄)
- 권장 액션 + 신호 카운트
- 상위 테마 chip 3개
- 과열 경고 1줄

**PC** (자세히 보기):
- top themes (score / confidence / signal_count)
- rising keywords chip
- related candidates chip
- caution themes
- overheating warnings 박스
- used_for_order 위반 경고 박스

**필수 표시**:
- "주문 신호 아님 · 후보 필터 전용" 배지 (회색)
- "본 요약은 *주문 신호가 아닙니다*. ... 뉴스 해석 오류·루머·과열 위험으로 인해 본 요약을 단독 매매 근거로 쓰지 마세요." disclaimer

**금지된 UI 요소** (테스트로 lock):
- BUY / SELL / HOLD 버튼
- "매수 실행" / "매도 실행" CTA

## 10. 향후 과제 (54번 이후, 본 PR 외)

1. Google Trends alpha 활성화 (운영자 권한 확보 후 별도 옵트인 PR)
2. 유료 News API 통합 — sentiment 분류
3. 공시 (DART) 연동 — 공시 type별 요약
4. AI 요약 모듈 통합 — Anthropic API로 자연어 요약 강화
5. theme_signals 자동 생성 파이프라인 (현재는 외부 caller가 row 작성)
6. 시간 시리즈 시각화 — score / signal_count 추이

## 11. 변경 시 동기화

- 새 `NewsTrendAction` 값 추가 → 본 문서 §3 + 테스트
- 새 입력 필드 → `NewsTrendOutput` + Pydantic schema + 본 문서 §3
- 임계값 (overheating / caution) 조정 → 본 문서 §3.4 + 테스트 boundary 갱신
- 본 Agent가 다른 Agent의 결정을 강제하는 경로 *추가 금지* — advisory invariant 유지
- 새 provider stub → 본 문서 §2 + 정적 grep 가드 갱신

## 관련 문서

- [`agent_architecture.md`](agent_architecture.md) — 6개 표준 Agent 역할 contract (#51)
- [`agent_design.md`](agent_design.md) — Agent 분리 정책
- [`market_observer_agent.md`](market_observer_agent.md) — 시장 환경 snapshot Observer (#52)
- [`theme_signal_policy.md`](theme_signal_policy.md) — `theme_signals` 테이블 + ThemeFilter 정책 (#22)
- `app/agents/news_trend_agent.py` — 본 Agent 구현
- `CLAUDE.md` — 절대 원칙 1번 (AI 직접 호출 금지)
