# EXE용 .env 설정 가이드 (KIS 모의 자동매매)

> 이 문서는 EXE 사용자가 **기본값만 설정해도 PAPER / KIS 모의 자동매매가
> 안전하게 시작**되도록 하는 `.env` 설정 가이드입니다. 본 설정은 *모의투자
> 전용* 이며 **실거래(실제 돈)는 발생하지 않습니다.**

## 0. 절대 안전 원칙

- `ENABLE_LIVE_TRADING=false` — 실거래 차단 (절대 true 금지)
- `ENABLE_AI_EXECUTION=false` — AI 자동 실행 차단 (절대 true 금지)
- `ENABLE_FUTURES_LIVE_TRADING=false` — 선물 실거래 차단 (절대 true 금지)
- `KIS_IS_PAPER=true` — 한투 *모의투자* 엔드포인트만 사용
- KIS 자격정보(App Key / Secret / 계좌번호)는 **`backend/.env` 에만** 저장하며,
  화면 / 로그 / API 응답에 **값이 노출되지 않습니다** (boolean `*_present` 만 표시).

## 1. .env 위치 + 적용 절차

1. EXE 설치 폴더의 `backend/.env.example` 을 `backend/.env` 로 복사합니다.
2. 아래 §3 의 KIS 모의투자 자격값만 채웁니다 (그 외 기본값은 그대로 둡니다).
3. backend / EXE 를 **재시작** 합니다 (`.env` 는 시작 시 1회 로드).

## 2. 필수 확인값 (이대로 두면 안전)

| 변수 | EXE 기본값 | 의미 |
|---|---|---|
| `DEFAULT_MODE` | `PAPER` | 실시세 + KIS 모의 운용 |
| `ENABLE_LIVE_TRADING` | `false` | 실거래 OFF |
| `ENABLE_AI_EXECUTION` | `false` | AI 자동실행 OFF |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | 선물 실거래 OFF |
| `KIS_IS_PAPER` | `true` | KIS 모의투자 엔드포인트 |
| `PAPER_BROKER_KIND` | `KIS_PAPER` | Paper broker = 한투 모의 |
| `ENABLE_AI_PAPER_BACKGROUND_TICK` | `true` | 장중 자동 판단 반복 |
| `AI_PAPER_TICK_DRY_RUN` | `false` | 가상 주문 생성까지 진행 |
| `AI_PAPER_ALLOW_SIMULATED_FILLS` | `true` | 가상 체결 + 포트폴리오 반영 |
| `ENABLE_KIS_PAPER_AUTO_TRADING` | `true` | KIS 모의 API 자동주문 ON |
| `KIS_PAPER_AUTO_ORDER_DRY_RUN` | `false` | KIS 모의 API 실제 제출(KIS_PAPER_SUBMITTED) |
| `KIS_PAPER_FILL_POLLING` | `true` | 모의주문 체결상태 갱신 |
| `MARKET_DATA_PROVIDER` | `mock` | 시장 데이터 소스 (yfinance 로 변경 가능) |

> **자격 미설정 시 자동 차단**: KIS App Key / Secret / 계좌번호가 비어 있으면
> readiness 가 `BLOCKED` 처리되어 **자동으로 주문이 나가지 않습니다.** 안전하게
> 채운 뒤에만 KIS 모의 자동매매가 시작됩니다.

## 3. 사용자가 직접 넣는 값 (KIS 모의투자 자격)

```
KIS_APP_KEY=<한투 모의투자 App Key>
KIS_APP_SECRET=<한투 모의투자 App Secret>
KIS_ACCOUNT_NO=<한투 모의투자 계좌번호>
```

- 이 값들은 **`backend/.env` 에만** 저장합니다.
- **프론트엔드 / git / 문서에 절대 넣지 않습니다.**
- 화면에는 값이 표시되지 않고 "구성됨 / 미구성" 여부만 보입니다.

## 4. 위험 한도 기본값 (risk limit)

| 변수 | 기본값 | 의미 |
|---|---|---|
| `RISK_MAX_ORDER_NOTIONAL` | `1,000,000` | 1회 주문 최대 금액(원) |
| `RISK_MAX_DAILY_LOSS` | `200,000` | 일일 최대 손실(원) |
| `RISK_MAX_POSITIONS` | `5` | 최대 보유 종목 수 |
| `RISK_MAX_SYMBOL_EXPOSURE` | `1,500,000` | 종목별 최대 노출(원) |
| `KIS_PAPER_AUTO_MAX_ORDERS_PER_DAY` | `10` | KIS 모의 일일 최대 주문 수 |
| `KIS_PAPER_AUTO_MAX_ORDER_NOTIONAL` | `1,000,000` | KIS 모의 1회 최대 주문 금액(원) |
| `KIS_PAPER_AUTO_ORDER_WINDOW_START` ~ `_END` | `09:05` ~ `14:50` | KIS 모의 주문 허용 시간(KST) |
| `KIS_PAPER_AUTO_MIN_CONFIDENCE` | `0.6` | 최소 신뢰도 |
| `KIS_PAPER_AUTO_MIN_QUALITY_SCORE` | `60` | 최소 신호 품질 |

> 위 한도는 **Paper / KIS 모의 운용 기준** 이며, 실전 주문 한도가 아닙니다
> (P-20 정책 — Paper capital ≠ Live capital).

## 5. 장중 화면에서 확인할 값

설정 탭(또는 KIS Paper 상태 카드)에서 다음이 보여야 합니다:

| 표시 | 안전한 값 |
|---|---|
| 운용모드 | `PAPER` |
| 실거래 | `OFF` |
| AI 자동실행 | `OFF` |
| KIS 모의투자 | `ON` |
| Paper broker | `KIS_PAPER` |
| KIS Paper Auto | `ON` |
| dry-run | `OFF` (실제 모의주문 제출) |
| Fill Polling | `ON` |
| 시장 데이터 | `mock` 또는 `yfinance` |
| KIS 자격 | `구성됨` |
| broker_order_type | `KIS_PAPER` |
| is_live_authorization | `false` |

장중 자동매매가 정상 동작하면:

- AI 판단이 카운터로 누적되고,
- KIS 모의 API 주문 성공 시 **`broker_order_no`** 가 표시되며,
- 결과 reason_code 는 **`KIS_PAPER_SUBMITTED`** 가 됩니다.
- 모든 결과에 `is_live_authorization=false` 가 유지됩니다.

## 6. 실거래 OFF 유지 원칙

- 본 가이드의 어떤 단계도 `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
  `ENABLE_FUTURES_LIVE_TRADING` 을 true 로 바꾸지 않습니다.
- 실거래 전환은 **별도 옵트인 PR + 운영자 명시 승인 + live capital review**
  (P-20) 가 필요하며, 본 EXE 기본 흐름에서는 제공되지 않습니다.
- KIS 자격이 *실전* 키여도 `KIS_IS_PAPER=true` 면 모의 엔드포인트만 호출하며,
  실거래 `place_order(is_paper=False)` 는 코드에서 `NotImplementedError` 로
  차단됩니다.

## 7. 관련 문서

- [`docs/kis_paper_oneclick.md`](kis_paper_oneclick.md) — KIS 모의 원클릭 테스트
- [`docs/capital_allocation_policy.md`](capital_allocation_policy.md) — Paper ↔
  Live capital 분리 (P-20)
- [`docs/promotion_policy.md`](promotion_policy.md) — 안전 플래그 매트릭스
