# Kiwoom REST API 도입 조사 (체크리스트 #15)

본 문서는 키움증권 **REST API**를 본 프로젝트의 2차 브로커로 도입하기 위한 사전 조사표다. 이번 작업은 **문서/설계 조사만** 수행하며, 코드 변경 / API Key·계좌·시크릿 입력 / 실 주문 호출은 일절 없다.

> 키움의 옛 **OpenAPI+ (Windows COM/OCX)**와 신규 **REST API**는 별개의 채널이다 — 본 문서가 다루는 것은 후자뿐이다 ([7절](#7-기존-openapi-comocx와-rest-차이)).

## 1. 결론

| 항목 | 결정 |
|---|---|
| 1차 브로커 | **MockBroker** + **KIS SHADOW/PAPER** 유지 ([`broker_selection.md`](broker_selection.md)) |
| 2차 브로커 후보 | **Kiwoom REST API** (조건검색 / 국내주식 확장용) |
| 선물 어댑터 | **`FuturesBrokerAdapter`** 별도 트랙 — 본 조사 범위 밖 |
| 본 PR 범위 | **Phase 1만** — 조사 문서 작성. 코드 변경 0건. |
| 후속 활성화 | Phase 2~6 단계별 옵트인 PR ([8절](#8-도입-단계)) |

도입 가치 — KIS 단독 의존 해소, 조건검색 / 순위 정보 같은 KIS에 부재하거나 제약 있는 기능 보강, 장애 시 fallback 후보 확보. 단, **즉시 도입의 marginal value는 낮다** — KIS PAPER가 이미 read 경로 PASS이고 `LIVE_MANUAL_APPROVAL` 라우팅이 다음 옵트인 단계이므로, Kiwoom REST는 KIS LIVE 안정화 후 진입한다.

## 2. 공식 사이트

| 항목 | 링크 |
|---|---|
| **Kiwoom REST API 포털** (본 조사 대상) | <https://openapi.kiwoom.com/> |
| 기존 OpenAPI+ (COM/OCX) 안내 | <https://www.kiwoom.com/h/customer/download/VOpenApiInfoView> |
| (참고) KIS Developers (1차 브로커) | <https://apiportal.koreainvestment.com/> |

> 본 문서의 표는 운영자가 위 포털의 **실제 명세**를 확인한 뒤 채워야 하는 필드를 "(확인 필요)"로 남겼다. 사실 확인 없이 추정으로 채우지 않는다.

## 3. Kiwoom REST 특징 (포털 안내 기준 요약)

| 항목 | 내용 |
|---|---|
| 프로토콜 | REST API (HTTPS) |
| 환경 의존성 | 다양한 OS / 언어 환경 대응 가능 (Windows COM/OCX 의존 없음) |
| 조건검색 | API 통한 조건검색 활용 가능 — KIS 대비 차별 포인트로 거론됨 |
| 보안 | 허용 IP 기반 보안 정책 존재 — 운영자가 사전 등록한 IP에서만 호출 가능 |
| 모의투자 | "모의투자 이용안내" 항목 존재 — 본 프로젝트 PAPER 대응 가능성 (확인 필요) |
| 사용 신청 | 별도 API 사용신청 필요 |
| 토큰 | OAuth 기반 (확인 필요) |
| 호출 제한 | 별도 정책 존재 (확인 필요 — [`api_limits.md`](api_limits.md) 갱신 대상) |

## 4. 조사해야 할 API 범위

본 프로젝트의 `BrokerAdapter` 6개 메서드 + 운영 도구를 충족하기 위해 운영자가 포털에서 정확한 endpoint / tr_id / 파라미터 / 호출 제한을 확인해야 하는 범위:

| 카테고리 | 필요 API | KIS 대응 (참고) | 확인 항목 |
|---|---|---|---|
| 인증 / 토큰 | OAuth `access_token` 발급 / 갱신 | `POST /oauth2/tokenP` | 발급 RPM, 만료 정책, 재발급 조건 |
| 현재가 / 시세 | 단일 종목 현재가 | `inquire-price` (`FHKST01010100`) | 응답 스키마 (`stck_prpr` 등 가격 필드명) |
| 차트 / 분봉 | 분봉 / 일봉 / 틱 | `inquire-time-itemchartprice` 등 | 백테스트 데이터 소스 가능성 |
| 계좌 잔고 | 현금 + equity + 보유 종목 | `inquire-balance` (`VTTC8434R/TTTC8434R`) | summary vs positions 분리 여부 |
| 주문 가능 금액 | 매수 가능 금액 / buying_power | `inquire-psbl-order` | balance와의 정합성 |
| 주문 | 시장가 / 지정가 BUY / SELL | `order-cash` (`VTTC0802U/0801U`) | 응답에 ODNO 동기 반환 여부 |
| 주문 취소 | 주문 ID로 취소 | `order-rvsecncl` | 부분취소 / 정정 정책 |
| 주문 상태 | 단건 / 일자별 조회 | `inquire-daily-ccld` | 체결 vs 미체결 구분 |
| 체결 내역 | 일자별 체결 리스트 | (위 동일) | 부분체결 / 평균체결가 |
| 조건검색 | 조건검색식 실행 / 결과 | (KIS 미지원) | 조건식 등록 / 호출 / 결과 polling 또는 push |
| 순위 정보 | 거래량 / 등락률 등 순위 | `volume-rank` 등 | 단타 후보 종목 선정 활용 |
| 오류 코드 | 인증 / RATE / 권한 / 시스템 | KIS `EGW00133` (token RPM), `EGW00201` (TPS) | 본 문서 + `api_limits.md`에 매핑표 추가 |

## 5. 우리 프로젝트 BrokerAdapter 매핑

`app/brokers/base.py`의 `BrokerAdapter` ABC 6개 메서드 + 보조 함수에 대한 1:1 매핑.

| `BrokerAdapter` 메서드 | Kiwoom REST API (예상) | 비고 |
|---|---|---|
| `get_price(symbol)` | 단일 종목 현재가 endpoint | 응답 → `Quote(price=int, source="kiwoom", timestamp=ISO)` |
| `get_balance()` | 잔고 / 예수금 endpoint | summary 행에서 `dnca_tot_amt`-류, `tot_evlu_amt`-류 추출 → `Balance(currency="KRW")` |
| `get_positions()` | 잔고 endpoint의 보유종목 출력 | 보유수량 > 0 행만 → `Position[]` |
| `place_order(OrderRequest)` | 주식 주문 (현금) endpoint | **Phase 4 이후** — Phase 2/3에서는 `NotImplementedError` 유지 |
| `cancel_order(order_id)` | 정정/취소 endpoint | **Phase 4 이후** — 영구 stub 시작 |
| `get_order_status(order_id)` | 일자별 체결 조회 → ODNO filter | KIS와 동일하게 단건 lookup 부재 시 client-side filter |
| `has_credentials()` | env 점검 | `KIWOOM_APP_KEY` / `KIWOOM_APP_SECRET` / `KIWOOM_ACCOUNT_NO` 동시 존재 |

추가 클래스 (KIS와 동형):

| 모듈 | 역할 |
|---|---|
| `app/brokers/kiwoom_client.py` | httpx 기반 REST 호출 + token 캐싱 + rate limiter 주입 (KisClient 패턴) |
| `app/brokers/kiwoom.py` | `KiwoomBrokerAdapter(BrokerAdapter)` — 위 매핑 구현 |
| `app/core/config.py` | `kiwoom_app_key` / `kiwoom_app_secret` / `kiwoom_account_no` / `kiwoom_is_paper` / `kiwoom_rate_limit_*` 추가 |
| `app/api/deps.py::get_broker()` | 새 분기 추가 (현재 KIS 단일) |

## 6. KIS vs Kiwoom REST 비교

| 항목 | KIS Open API (현재 1차) | Kiwoom REST API (2차 후보) |
|---|---|---|
| 1차 도입 난이도 | 낮음 — 본 프로젝트가 이미 4 read + paper place_order 구현 | 중간 — 신규 어댑터 + 클라이언트 + 테스트 필요 |
| 공식 샘플 (Python) | 풍부 (`koreainvestment/open-trading-api` GitHub) | 포털 자료 확인 필요 |
| 모의투자 | ✓ (`openapivts.koreainvestment.com:29443`, `KIS_IS_PAPER=true`) | △ "모의투자 이용안내" 항목 존재 — 호스트/스펙 확인 필요 |
| 조건검색 | ✗ (별도 종목 스크리닝 필요) | ✓ (도입의 차별 가치) |
| 주문 API | ✓ 시장가/지정가, 정정/취소 | ✓ (확인 필요) |
| WebSocket / 실시간성 | KIS WebSocket 별도 채널 — 현재 미통합 | 실시간 API 존재 (확인 필요) |
| 자동매매 생태계 | 큼 (오픈소스 / 커뮤니티) | 키움 사용자 기반 큼, 단 OpenAPI+ 자료가 다수라 REST 한정 자료는 분리해 봐야 함 |
| 본 프로젝트 적합성 (현재) | **즉시 적합** — `LIVE_MANUAL_APPROVAL` 다음 옵트인 단계 | **Phase 2 이후** — KIS LIVE 안정화 후 |

## 7. 기존 OpenAPI+ (COM/OCX)와 REST 차이

| 항목 | OpenAPI+ (Windows COM/OCX) | Kiwoom REST API |
|---|---|---|
| 호출 모델 | Windows OCX 컨트롤 + 이벤트 콜백 | HTTPS 요청 / 응답 (+ 별도 실시간 채널) |
| 플랫폼 | Windows 32-bit Python + 별도 런타임 (예: 32-bit pythonw) | 크로스 플랫폼 (Linux 컨테이너 포함) |
| 본 프로젝트 backend 호환성 | **낮음** — FastAPI(uvloop, async, Linux 컨테이너 친화)와 충돌 | **높음** — `httpx.AsyncClient`로 `KisClient` 동형 구현 가능 |
| 자료 다수성 | 구 자료 多 — 단, 본 프로젝트엔 **부적합** | REST 한정 자료를 별도로 확인해야 함 |
| 운영 비용 | Windows 호스트 / GUI 의존 | 표준 컨테이너 / 헤드리스 |
| 본 프로젝트 결정 | **채택하지 않음** | **Phase 2 후보** |

→ 본 프로젝트는 FastAPI backend (Linux/Windows 모두 헤드리스 지원) + httpx async — REST가 구조적으로 정합. OpenAPI+는 본 프로젝트에 도입하지 않는다. 단, 조건검색 같은 특수 기능은 REST 명세에서 별도 확인이 필요하다.

## 8. 도입 단계

본 프로젝트의 [`docs/promotion_policy.md`](promotion_policy.md) 단계별 승격 흐름과 동일한 패턴.

| Phase | 범위 | 산출물 | 안전 플래그 |
|---|---|---|---|
| **Phase 1** *(현재 PR)* | 문서 조사만 | 본 문서 | 무관 |
| **Phase 2** | `KiwoomRestBrokerAdapter` **stub** + `KiwoomClient` skeleton + `get_broker()` 분기 추가 (비활성) | 어댑터 6개 메서드 모두 `NotImplementedError`. 단위 테스트 (init / has_credentials) | `BROKER_PROVIDER=kis` (기본) — Kiwoom 미선택 |
| **Phase 3** | read-only — `get_price` / `get_balance` / `get_positions` 구현 + 모킹된 transport 단위 테스트 + 실 PAPER 1회 검증 | `kiwoom_connection_test_log.md` (KIS 로그 동형) | `BROKER_PROVIDER=kiwoom` 옵트인 시에만 활성화. `KIWOOM_IS_PAPER=true` 강제 |
| **Phase 4** | PAPER / 모의투자 `place_order` 활성. `cancel_order` 정정 정책 결정 | `kiwoom_paper_mode.md` 운영자 가이드 | `KIWOOM_IS_PAPER=true` 필수, `ENABLE_LIVE_TRADING=false` 유지 |
| **Phase 5** | `LIVE_SHADOW` 검증 — 실 시세 read-only, 주문 금지 | RiskManager가 모든 주문 REJECTED. 4주 이상 운영 | `DEFAULT_MODE=LIVE_SHADOW` |
| **Phase 6** | `LIVE_MANUAL_APPROVAL` 전환 검토 — 운영자 명시 옵트인 PR 별도 | `live_activation_blockers.md` Kiwoom 항목 추가 | `ENABLE_LIVE_TRADING=true` (운영자 명시), PermissionGate 큐 통과 후에만 broker 도달 |

각 Phase 진입 조건은 직전 Phase의 검증 로그(`docs/`) 통과 + PR 리뷰. **Phase 건너뛰기 금지** — KIS 도입 패턴과 동일.

## 9. 안전 원칙

본 프로젝트의 [`CLAUDE.md` 절대 원칙](../CLAUDE.md)을 Kiwoom 어댑터에도 그대로 적용:

| 원칙 | Kiwoom 적용 |
|---|---|
| frontend에 App Key / Secret 저장 금지 | `KIWOOM_APP_KEY` / `KIWOOM_APP_SECRET` 모두 backend `.env`에만 |
| 시크릿은 `.env`에만 | `.gitignore`가 이미 `.env` 무시 — 추가 룰 없음 |
| `ENABLE_LIVE_TRADING=false` 기본 유지 | Kiwoom LIVE place_order는 Phase 6까지 차단 |
| 실주문 별도 승인 전까지 금지 | `KiwoomBrokerAdapter.place_order(is_paper=False)` `NotImplementedError` (KIS와 동일 패턴) |
| **broker selection guard** — KIS와 동시에 실거래 주문이 나가지 않도록 | `BROKER_PROVIDER=kis` 또는 `kiwoom` 단일 선택. `get_broker()` 팩토리에서 enforce. 둘 다 LIVE를 동시에 활성화하는 구성은 거부 |
| `route_order` 단일 진입점 통과 | KIS와 동일 — `RiskManager → PermissionGate → OrderExecutor` |
| 다층 가드 (adapter is_paper / factory refusal / RiskManager / PermissionGate) | Kiwoom 어댑터에도 독립 적용 |

추가 invariant — Kiwoom IP 화이트리스트가 운영자 호스트에만 등록되어 있어야 한다. 공유 / CI / GitHub Actions 호스트에는 등록하지 않는다.

## 10. 체크리스트 #15 판정

| 항목 | 상태 |
|---|---|
| 본 문서 (`docs/kiwoom_rest_research.md`) 작성 | **완료** |
| 코드 변경 | **없음** (Phase 2 이후) |
| 실제 API 신청 / 키 발급 / 계좌 등록 | **사용자 작업** (포털 가입 + IP 등록 + 모의투자 신청) |
| 실 호출 / 토큰 발급 테스트 | **본 PR 범위 외** (Phase 3에서 KIS와 동형 로그 작성 예정) |
| `broker_selection.md` 업데이트 | 본 문서로 대체 (broker_selection은 이미 Kiwoom REST를 Phase 2로 명시) |

체크리스트 #15 — **조사 문서 작성 완료**. 다음 진입은 사용자가 Kiwoom 포털에서 (1) API 사용신청, (2) IP 등록, (3) 모의투자 신청을 마친 뒤 Phase 2 stub PR에서 시작.

## 관련 문서

- [`broker_selection.md`](broker_selection.md) — 어댑터 활성화 매트릭스 + 새 어댑터 체크리스트
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + 환경 플래그
- [`api_limits.md`](api_limits.md) — 호출 제한 정책 (Kiwoom 항목 Phase 2에서 추가)
- [`kis_connection_test_log.md`](kis_connection_test_log.md) — KIS 연결 검증 로그 (Phase 3 동형 패턴 참고)
- [`architecture.md`](architecture.md) — 가드 체인 + 라우트 surface
- [`live_activation_blockers.md`](live_activation_blockers.md) — LIVE 활성화 전 운영자 절차 (Kiwoom Phase 6에서 갱신)
