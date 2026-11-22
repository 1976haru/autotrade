# 5m+60m/Council 콤보 — shadow 검증 셋업 (read-only, 실거래 0건)

> `results/multi_timeframe/adverse_selection_measurement.md` 후속. 그 문서의 실측
> 슬리피지는 **지금 봇(30분봉 단일신호)의 대리 데이터**였다 — 이 문서는 콤보 자체를
> (5분 신호빈도로) shadow 관측해 진짜 슬리피지를 재측정하는 셋업이다.

## 무엇을 만들었나

**신규 파일만 추가, 봇(라이브 신호/주문 경로) 0줄 수정:**

| 파일 | 역할 |
|---|---|
| `backend/app/shadow/multi_tf_shadow.py` | 콤보 평가(5m entry + 60m confirm Council) + 슬리피지 측정 + 5분 주기 러너 |
| `backend/app/db/models.py` (+2 클래스) | `MultiTfShadowTick`(틱 요약), `MultiTfShadowSignal`(신호+슬리피지 상세) |
| `alembic/versions/20260709_0026_multi_tf_shadow.py` | 위 2개 테이블 생성 마이그레이션(적용 테스트 완료, 프로덕션 DB는 아직 미적용=0025 그대로) |
| `backend/app/core/config.py` (+3 설정) | `enable_multi_tf_shadow`(기본 False), `multi_tf_shadow_symbols`, `multi_tf_shadow_interval_seconds`(기본 300=5분) |
| `backend/app/main.py` (+18줄) | `bg_driver`/`FillPoller`와 동일한 opt-in 패턴으로 시작/종료 등록 |
| `backend/tests/test_multi_tf_shadow.py` | 9개 테스트(순수로직 + 안전불변식), 전부 통과 |

**절대 원칙 준수 확인**:
- `broker.place_order` / `route_order` / `OrderExecutor` / `RiskManager` — 정적 검사(테스트에 포함)로 import·호출 **0건** 확인.
- 사용하는 함수는 전부 read-only: `fetch_realtime_quote`(시세 조회), `_fetch_full_day_minutes`(분봉 조회, 봇이 이미 쓰는 함수 그대로 재사용 — **같은 rate limiter/페이싱 락을 공유**해 별도 우회 없음), `run_agent_council`(순수 함수, 부작용 0).
- 봇의 라이브 신호 코드(`kis_paper/driver_bridge.py`, `agent_council.py`, `market_data/kis_realtime.py`의 `BAR_INTERVAL_MINUTES` 분기) **0줄 변경**. 전역 상수(`BAR_INTERVAL_MINUTES`)를 건드리면 지금 돌고 있는 봇의 30분봉 판단과 경합할 위험이 있어, 대신 5분/60분을 **파라미터로 받는 별도 리샘플 함수**를 새로 작성했다(코드 중복 최소, 로직은 기존과 동일 — bit-identical 검증은 기존 `bar_interval_generalize` 리포트의 방식과 동일).
- 모든 기록 row는 `is_order_signal=False` / `is_live_authorization=False` 고정.
- 안정화(현재 진행 중인 30분봉 라이브 운영)와 **완전히 분리된 별도 프로세스 내 background task** — 봇의 30초 틱 루프, driver_bridge 스캔과 무관하게 독립적으로 5분 주기로 돈다. 기본값 OFF라 **지금 이 커밋만으로는 아무것도 시작되지 않는다** (아래 "켜는 법" 참고).

## 콤보 신호 정의 (백테스트 그대로)

- entry: 5분봉 `AGENT_COUNCIL` BUY (오늘자 1분봉을 5분 슬롯으로 리샘플 + 과거 2거래일 prior 확보).
- confirm: 그 시점 as-of 최신 60분봉 `AGENT_COUNCIL` BUY (과거 6거래일 prior 확보).
- 둘 다 BUY일 때만 "콤보 신호"로 기록. 신규 전략/파라미터 튜닝 0 — `results/multi_timeframe/design.md`의 pf_bef/pf_aft 산출에 쓰인 것과 동일한 `run_agent_council` 호출.

## 매일/매틱 기록하는 것

**틱마다(5분, 장중만 — `is_market_open` 체크로 장외는 스캔 자체 skip)**:
- `multi_tf_shadow_tick`: 스캔 종목 수, 콤보 신호 발견 수, fetch 에러 수, 레이트리밋(EGW00201) 히트 수, 틱 소요시간.

**콤보 신호(BUY+BUY)가 실제로 뜬 순간마다**:
- `multi_tf_shadow_signal`: 심볼, P1(신호 확정 시점 시세) / P1 시각, **P2(60분 확인평가까지 실제로 걸린 시간 뒤 재조회한 시세)** / P2 시각, 경과초, **슬리피지(bps)**, `prev_close`(국면 재구성용, 별도 corpus 확장 불필요 — 매일 자연히 쌓임), council confidence.

★P2는 인위적 `sleep` 없이, 5분봉+60분봉 평가에 실제로 걸리는 순차 조회 시간(분봉 페이싱 포함) 경과 후 자연스럽게 재조회한다 — 진짜 "판단 완료 시점 대비 가격이 얼마나 움직였나"를 그대로 잰다.

## 감시 대상 종목 — 부하 최소화를 위해 8종목으로 축소

`results/timeframe_test`/`multi_timeframe`의 35종목 백테스트와 달리, 실측 shadow는
**8개 대형주**(005930/000660/005380/035420/373220/000270/006400/035720)로 시작한다.
이유: 5분 틱마다 종목당 quote 1회 + (180초 TTL 만료 시) 분봉 1회가 추가되므로,
35종목 그대로 쓰면 하루 수천 콜이 추가돼 지금 막 배포한 레이트리밋 완화
(`results/ratelimit_fix/design.md`)를 다시 악화시킬 위험이 있다. 8종목이면 신호
표본은 줄지만(트레이드오프, 정직히 명시), 부하는 훨씬 안전한 수준으로 유지된다.
`.env`의 `MULTI_TF_SHADOW_SYMBOLS`로 언제든 조정 가능.

## ★켜는 법 (아직 안 켜짐 — 확인 후 진행)

기본값 `ENABLE_MULTI_TF_SHADOW=false` — 이 커밋만으로는 아무 일도 안 일어난다.
켜려면:
1. `%APPDATA%\Autotrade\.env`(백엔드가 실제 읽는 파일)에 `ENABLE_MULTI_TF_SHADOW=true` 추가.
2. 백엔드 재시작.
3. 로그에 `[startup] multi-tf shadow started (opt-in, read-only — no broker orders)` 확인.
4. **DB 마이그레이션도 같이 적용해야 함**(`alembic upgrade head` — 0025→0026, 새 테이블 2개 생성. 프로덕션 DB는 아직 미적용 상태로 남겨뒀다).

**이 셋업 자체를 실제로 켤지는 확인 후 진행하겠습니다** — 라이브 운영 중인 백엔드에 새 백그라운드 태스크를 추가하고 재시작이 필요한 작업이라, 이번 대화에서 임의로 켜지 않았습니다.

## 4주간 뭘 측정하는지 (그 후 재계산 계획)

| 측정 | 방법 | 4주 후 어떻게 쓰나 |
|---|---|---|
| 신호 빈도 | `multi_tf_shadow_signal` row 수 / 일 | 콤보가 실제로 하루 몇 번 기회를 주는지(백테스트 66,020건/35종목/1년과 대조) |
| ★실제 슬리피지 | `slippage_bps` 분포(평균/중앙값/절대평균) | `adverse_selection_measurement.md`의 대리 슬리피지(-2.59bp~+13.26bp)를 **진짜 5분빈도 값**으로 교체 → design.md의 "비용만 +0.007%"에 대입해 순손익 재계산 |
| 레이트리밋 경합 | `rate_limited_count`/`fetch_errors` (틱당·일당) | 5분빈도 vs 지금 봇의 30분빈도(기존 로그의 EGW00201 발생률)와 직접 비교 — "5분이 30분보다 얼마나 심한가"에 숫자로 답 |
| 국면별(UP/SIDE/DOWN) | 누적된 `prev_close`로 4주 후 일별종가 재구성 → 20일 수익률 국면분류(기존 방법론) | design.md에서 UP/SIDE만 비용만 기준 플러스였던 것이 실측에서도 유지되는지 |

**4주 후 판정 기준(사전 고정)**:
- 실측 슬리피지 대입 순손익 **여전히 플러스** → 실전 후보로 승격 검토(단, 8종목 소표본 한계 명시하고 표본 확대 후 재검증 권장).
- **마이너스로 전환** → `results/timeframe_test/design.md`와 동일 결론("비용벽") 확정, 이 콤보도 정리.

## 재현/검증
- 마이그레이션 적용 테스트: `/tmp` 사본 DB에 0025→0026 적용 성공 확인(프로덕션 DB는 미적용 상태로 보존).
- 신규 테스트 9개 전부 통과, 기존 회귀 스위트(P0 4종 + kis_paper 전체 + routes) 487 passed / 2 failed(둘 다 이번 변경과 무관한 기존 실패 — `git stash`로 사전 확인됨) 확인.
