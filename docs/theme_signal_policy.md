# Theme Signal Policy (체크리스트 #22)

## 1. 목적

구글 트렌드 / 뉴스 / 공시 / 수동 입력 데이터는 **주문 신호가 아니라 후보 필터**다. 테마/뉴스/트렌드 점수가 높아도 `BUY/SELL/HOLD` 결정으로 직접 이어지면 안 된다 — 모든 주문은 `RiskManager → PermissionGate → OrderExecutor` 단일 경로를 거쳐야 한다 (CLAUDE.md 절대 원칙).

본 PR은 해당 데이터를 다루는 기반 (DB 테이블 / Provider abstraction / `ThemeFilter` / read-only API / UI 카드 / 문서 / 테스트)을 만든다. **외부 유료 API 호출 0건, 실 Google Trends 호출 0건**.

## 2. 데이터 소스

| Provider | 식별자 | 본 PR 상태 | 비고 |
|---|---|---|---|
| Mock | `mock` | **활성** | deterministic stub. CI/데모 기본 |
| Google Trends API alpha | `google_trends_alpha` | **disabled** | alpha tester 권한 필요. 본 PR에서는 영구 disabled — 별도 옵트인 PR로 활성화 |
| News provider stub | `news_stub` | **disabled** | 외부 News API rate limit / 약관 검토 필요 |
| 공시 (DART) stub | `disclosure_dart_stub` | **disabled** | DART OpenAPI 별도 가입 |
| Manual | `manual` | **활성** (호출자 주입) | 운영자가 직접 ThemeRecord 주입 — UI/CSV는 향후 작업 |

## 3. Google Trends API 상태

- 일반 공개 API가 아니라 **alpha tester 신청 기반**.
- 본 프로젝트는 alpha 권한을 보유하지 않으므로 `GoogleTrendsAlphaProvider.is_enabled() = False` 영구.
- 호출자(`ThemeFilter` / 라우트)는 disabled provider를 만나면 **빈 결과**를 반환하고 fallback으로 Mock provider 사용.
- alpha 권한을 획득하면 별도 옵트인 PR에서 `is_enabled()` / `scan()` 본체 활성화. 본 PR에서는 `scan()` 호출 시 `NotImplementedError`로 차단해 실수로 LIVE 호출이 일어나지 않게 한다.

## 4. 안전 원칙

| 원칙 | 코드 단 강제 |
|---|---|
| Theme score만으로 매수/매도 금지 | `ThemeFilter`는 `candidate_symbols()`만 반환 — `BUY/SELL/HOLD` 메서드 없음 |
| `ThemeFilter`는 주문 결정 미반환 | 클래스 표면에 `to_order` / `decide` / `place_order` 류 메서드 없음 (테스트로 검증) |
| `ThemeSignal.used_for_order` 기본 `False` | DB column default `False`. 본 PR에서 True로 바꾸는 경로 없음 |
| Agent는 theme_context로만 활용 | NewsTrendAgent (council)는 별도 stub, 본 데이터 직접 import 안 함 |
| 최종 주문은 Strategy/Risk/Permission/Virtual 흐름 통과 | 본 모듈은 `app.brokers` / `app.risk` / `app.permission` / `app.execution` import 0건 (테스트로 검증) |

## 5. 점수 / 등급 해석

| 등급 | 점수 | 권장 동작 |
|---|---|---|
| `STRONG` | ≥ 80 | universe 후보로 강력 추천. *직접 BUY 의미 아님* |
| `WATCH` | 60–79 | 후보군에 포함, 운영자 검토 |
| `WEAK` | 30–59 | 후보군 기본 제외. 운영자 명시 옵트인 시에만 |
| `IGNORE` | < 30 | 후보군에서 완전히 제외 |

`compute_theme_score(raw_score, confidence, related_symbol_count, keyword_count)` 산식:
- 베이스 = `raw_score` (0~100). `confidence < 50`이면 × 0.6 (low-confidence dampening).
- `related_symbol_count ≥ 3`이면 +5, `keyword_count ≥ 3`이면 +3 (다양성 보너스).
- `[0, 100]`로 clamp.

## 6. Watchlist 연계

- `ThemeFilter.candidate_symbols(universe=...)` — universe로 좁힘.
- 운영자가 active watchlist (#18)를 universe로 주입하면 watchlist 안에서만 후보가 만들어진다.
- watchlist 밖 종목은 기본적으로 제외. 운영자가 universe=None으로 부르면 전체 catalog 노출.
- 향후 `LiveStrategyEngine`에 `theme_context`를 read-only로 주입할 가능성 — 별도 옵트인 PR.

## 7. 실거래 전 검증

LIVE 활성화 단계에 진입하기 전 운영자가 확인해야 하는 것:

1. **테마 점수와 실제 성과의 상관 검증** — 점수 높았던 테마의 후속 단타 결과를 백테스트로 평가.
2. **테마 과열 시 리스크 확대** — STRONG이 너무 많이 동시 발생하면 시장 체제 변동 신호일 수 있음.
3. **뉴스/트렌드는 지연 가능성** — 뉴스 발생 → 시장 반영 사이에 지연/노이즈가 있다.
4. **유료 API rate limit + 약관** — 실 알파/News API 활성화 PR에서 호출 한도, 약관(상업 사용 가능 여부) 확인.
5. **AI Agent와의 결합 정책** — `NewsTrendAgent` (10-Agent Council)는 INFO/WARN만 반환. theme_signals와 합산되더라도 RiskManager 우회 금지.

## 8. API (read-only)

| Method | Path | 의미 |
|---|---|---|
| `GET`  | `/api/themes/signals` | 최근 ThemeSignal 행 (limit/grade/provider 필터) |
| `GET`  | `/api/themes/summary` | Dashboard 요약 (grade 분포 + top STRONG) |
| `POST` | `/api/themes/scan`    | Mock provider로 신호 생성 + DB 영구화 |

모든 응답에 `used_for_order=false` invariant. `/scan` 응답의 `candidate_symbols`는 BUY/SELL 정보를 포함하지 않으며 `(symbol, themes, best_score, best_grade)` 4개 필드만.

## 9. UI

### AI Signal 탭 — `ThemeSignalsCard`
- 상단 "주문 신호 아님 · 후보 필터 전용" 배지 (항상 노출).
- 구글 트렌드/뉴스/공시/Mock 데이터 안내 + provider 식별자.
- universe 입력 + Mock 스캔 버튼.
- 후보 종목 chip + 신호 카드 (theme/score/grade/source/provider/confidence/summary).
- BUY/SELL 버튼 / 주문 관련 UI 없음 (테스트로 검증).

### Dashboard — `ThemeSummaryTile`
- "주문 신호 아님" 배지.
- 총 신호 수 + STRONG/WATCH 카운트 + top STRONG chip.
- "테마 후보 자세히 보기 →" 링크 (AI Signal 탭으로 점프).
- 빈 상태: "아직 테마 신호가 없습니다. AI 탭에서 Mock 스캔을 실행해 보세요."

## 10. 안전 invariant (본 PR이 지키는 것)

- `app/themes/` 모듈은 `app.brokers` / `app.risk` / `app.permission` / `app.execution`을 import하지 않는다 (코드 검사 + 테스트).
- `ThemeFilter`는 `BUY/SELL/HOLD` 결정을 반환하는 메서드를 가지지 않는다 (테스트).
- `ThemeSignal.used_for_order`는 기본 `False`이며 본 PR에서 `True`로 만드는 경로가 없다 (DB row 검증).
- `/api/themes/scan` 응답에 `side / order_type / decision / quantity / BUY / SELL / HOLD` 같은 주문 필드가 없다 (테스트).
- 외부 네트워크 호출 0건 — Mock / disabled stub만 동작.
- `provider="mock"` 모듈은 `requests / httpx / urllib`를 import하지 않는다.
- `ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING` 변경 0건.
- API Key / Secret / 계좌번호 변경 0건. frontend 시크릿 노출 0건.
- `RiskManager` / `PermissionGate` / `OrderExecutor` / `route_order` / 기존 broker adapter 변경 0건.

## 11. 후속 작업 (Backlog)

| 항목 | 트리거 |
|---|---|
| 실 Google Trends alpha 통합 | alpha 권한 획득 + 운영자 옵트인 PR |
| News API 통합 | rate limit / 약관 검토 후 별도 PR |
| DART 공시 OpenAPI 통합 | DART 가입 + 별도 PR |
| Manual provider UI / CSV import | 운영자가 수동 입력 흐름 사용 시점 |
| `ThemeSignal` 중복 dedup 정책 | 시계열 누적이 부담 임계 도달 시 |
| Strategy/Agent에 `theme_context` 주입 (read-only) | LIVE strategy 활성화 PR |
| Frontend 데이터 품질 / 테마 분포 카드 | UI 요청 시 |
| 테마와 실 단타 성과의 상관 분석 리포트 | 운영 데이터 누적 후 |

## 관련 문서

- [`watchlist_policy.md`](watchlist_policy.md) — universe 후보군과의 연계
- [`news_trend_agent.md`](news_trend_agent.md) — `NewsTrendAgent` 정책 + theme_signals 요약 (#53)
- [`agent_decision_schema.md`](agent_decision_schema.md) — 10-Agent Council의 NewsTrendAgent
- [`risk_policy.md`](risk_policy.md), [`risk_guards_matrix.md`](risk_guards_matrix.md) — 우회 금지 가드 체인
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + 환경 플래그
- [`api_limits.md`](api_limits.md) — 외부 API rate limit 정책
- [`database_schema.md`](database_schema.md) — `theme_signals` 컬럼/인덱스
