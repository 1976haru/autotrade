# 포트폴리오 데이터 소스 통일 (#55 / 7-03)

Dashboard / Settings / Agent 화면에서 표시되는 **현금 · 총자산 · 포지션** 값의
데이터 소스를 명확히 통일하고, **API 실패 시 임의로 0원 fallback 하지 않도록**
강제하는 정책 문서.

> 본 화면은 Paper / 모의 포트폴리오이며 **실제 (실전) 계좌 잔고가 아니다.**
> 본 작업은 *표시 정합성* 작업이다 — 매매 로직 / KIS 주문 경로 / 실전 기능
> 활성화와 무관하다.

관련 코드:
- `backend/app/portfolio/portfolio_snapshot.py` — 표준 snapshot + builder
- `backend/app/api/routes_auto_paper.py` `GET /api/auto-paper/portfolio-source`
- `frontend/src/components/common/PortfolioSourceCard.jsx` — 표시 카드
- `frontend/src/components/common/PortfolioCard.jsx` — 0원 fallback 제거
- 테스트: `backend/tests/test_portfolio_source_consistency.py`,
  `frontend/src/components/common/PortfolioSourceCard.test.jsx`

---

## 1. Paper simulated portfolio 정의 (`PAPER_SIMULATED`)

내부 Paper 모의 포트폴리오. `CapitalState`(현금/투자원금) + `PaperCapitalConfig`
(한도) + `position_engine`(FIFO 보유) + `loss_limits`(오늘 매수 사용금액) 를
read-only 로 조합한 `build_portfolio_state()` 결과를 source 태깅한 것이다.

- **실제 KIS 계좌 잔고가 아니다.** Paper capital/ledger/episode 기반.
- broker 호출 0건, DB SELECT only.

## 2. KIS Paper account portfolio 정의 (`KIS_PAPER_ACCOUNT`)

KIS *모의투자* 계좌 조회 기반. KIS 모의 API 조회가 **성공한 경우에만** 값을
표시한다.

- **실전 계좌가 아니다.** KIS 모의투자(paper) 계좌 기준.
- 본 PR 시점에는 KIS 모의 잔고 조회 콜백(`balance_fetcher`)을 *주입하지 않는다*
  (broker 호출 금지). 따라서 KIS snapshot 은 `NOT_CONFIGURED` 로 표시되며,
  **이는 잔고 0원이 아니라 "아직 연결되지 않음"** 을 의미한다.
- 향후 read-only KIS 모의 잔고 조회를 별도 PR 에서 `balance_fetcher` 로 주입하면
  동일 카드가 `KIS_PAPER_ACCOUNT` source 로 값을 표시한다 (모듈은 broker 를
  import 하지 않으므로 주입 지점은 endpoint 호출자다).

## 3. 둘을 섞지 않는 이유

Paper 모의 자금과 KIS 모의 계좌는 **자금 기준 · 체결 품질 · 갱신 시점이 서로
다르다.** 한 숫자(예: "총자산")에 두 source 값을 섞으면 사용자가 어느 쪽 기준인지
알 수 없고, Paper capital 을 KIS 잔고처럼 오해할 수 있다.

- **한 카드(섹션)의 cash / total_asset / positions 는 같은 source 여야 한다.**
- source 가 다르면 별도 섹션으로 분리한다 (`PortfolioSourceCard` 는 ①Paper
  ②KIS 두 섹션으로 분리 표시).
- 합산을 시도하면 `assert_single_source()` 가 `MIXED_BLOCKED` 를 반환해 차단한다.

## 4. API 실패 시 0원 fallback 금지

조회가 실패하면 cash / total_asset / position_count 를 **0 으로 채우지 않고
`None`** 으로 둔다. UI 는 이를 **"확인 불가"** 로 표시한다.

- `PortfolioSourceSnapshot.__post_init__` 가 *status 가 실패/미설정 계열이거나
  source 가 UNAVAILABLE/MIXED_BLOCKED 인데 값이 None 이 아니면* `ValueError` 로
  raise → 코드 단에서 0원 fallback 을 원천 차단.
- `zero_fallback_used` 필드는 **항상 False** 불변 (True 면 ValueError).
- frontend `PortfolioCard` 의 위험 패턴 `cash?.available_cash_krw ?? 0` 을
  `?? null` 로 바꾸고, 표시 시 `_krwOrUnknown()` 가 null → "확인 불가" 로 렌더.

표준 source / status / reason_code:

| source | 의미 |
|---|---|
| `PAPER_SIMULATED` | 내부 Paper 모의 포트폴리오 |
| `KIS_PAPER_ACCOUNT` | KIS 모의 계좌 조회 성공 |
| `UNAVAILABLE` | 조회 실패 / 자격 미설정 / API 오류 — **0원 아님** |
| `MIXED_BLOCKED` | 서로 다른 source 혼합 차단 |

| status | 의미 |
|---|---|
| `OK` / `STALE` | 값 표시 가능 (STALE 은 오래된 값) |
| `ERROR` / `API_UNAVAILABLE` / `CREDENTIALS_MISSING` / `NOT_CONFIGURED` / `UNKNOWN` | 값 None (확인 불가) |

| reason_code | 의미 |
|---|---|
| `PORTFOLIO_SOURCE_PAPER_SIMULATED` | Paper 모의 포트폴리오 기준 |
| `PORTFOLIO_SOURCE_KIS_PAPER_ACCOUNT` | KIS 모의 계좌 기준 |
| `PORTFOLIO_SOURCE_UNAVAILABLE` | 조회 불가 |
| `PORTFOLIO_API_FAILED_NO_ZERO_FALLBACK` | 조회 실패 — 0원 fallback 금지 |
| `PORTFOLIO_CREDENTIALS_MISSING` | KIS 자격 미설정 |
| `PORTFOLIO_KIS_PAPER_NOT_CONFIGURED` | KIS 잔고 조회 미연결 |
| `PORTFOLIO_STALE_DATA` | 값이 오래됨 |
| `PORTFOLIO_MIXED_SOURCE_BLOCKED` | source 혼합 차단 |
| `PORTFOLIO_ACCOUNT_VALUE_NOT_DISPLAYED` | 계좌 값 미표시 |
| `PORTFOLIO_SECRET_REDACTED` | secret 제거됨 |

## 5. 진짜 0원과 조회 실패 구분

- **실제 0원**: `status=OK`, `cash=0` → 카드에 "0원" 표시 (정상).
- **조회 실패**: `status≠OK`, `cash=None` → 카드에 "확인 불가" + "조회 실패:
  실제 잔고 0원이 아닙니다." 안내.

테스트로 고정:
- `test_real_zero_balance_is_displayable` — OK + cash=0 은 표시 가능.
- `test_paper_api_failure_is_unavailable_no_zero` — 실패 → cash=None.
- `test_constructing_failure_with_zero_value_raises` — 실패 상태 + cash=0 구성 차단.

## 6. Dashboard 에서 source / status 확인 방법

Dashboard 상단 `PortfolioSourceCard`(💰 포트폴리오 데이터 소스):
- 각 섹션 상단에 **데이터 소스**(라벨 + enum)와 **상태**(라벨 + enum + reason_code).
- 현금 / 총자산 / 포지션 타일 (실패 시 "확인 불가").
- **마지막 갱신** 시각.
- 상단에 "조회 실패는 실제 잔고 0원이 아닙니다" 경고 상시 노출.

## 7. KIS Paper 조회 실패 시 조치

1. `Settings` 탭 KIS 모의 자격(App Key / Secret / 계좌번호 / 상품코드)이 모두
   설정되었는지 확인 (`CREDENTIALS_MISSING` 이면 자격 누락).
2. `KIS_IS_PAPER=true` 인지 확인 (모의투자 전용).
3. `NOT_CONFIGURED` 는 자격이 있어도 **잔고 조회 콜백이 아직 연결되지 않은**
   상태 — 본 PR 시점의 정상 상태이며 잔고 0원이 아니다.
4. 어떤 경우에도 화면의 "확인 불가" 를 "잔고 0원" 으로 오해하지 않는다.

## 8. Portfolio drift 와의 관계

본 source 통일은 *표시* 정합성을 담당한다. 실제 broker view 와 audit view 간
수량/금액 불일치(drift) 감지는 `app/reconciliation/` 가 담당하며, 본 카드와는
별개 레이어다. 단, drift 진단 시에도 "조회 실패 ≠ 0원" 원칙은 동일하게 적용된다.

## 9. secret / account 미노출 원칙

- snapshot dict 에 API key / app secret / access token / 계좌번호 필드 0건.
- position dict 은 화이트리스트 필드만 carry(`_POSITION_KEYS`), secret 의심 키는
  `_sanitize_positions()` 가 제거.
- `contains_secret=False` 불변. KIS 자격은 *존재 여부(boolean)* 만 readiness 로
  판정하며 원문은 carry 하지 않는다.

## 10. 실전 계좌 잔고가 아님

본 카드의 모든 source(`PAPER_SIMULATED` / `KIS_PAPER_ACCOUNT`)는 **모의(paper)**
기준이다. `is_paper_only=True` / `is_live_authorization=False` 불변. 실전 계좌
잔고는 표시하지 않으며, 본 PR 은 실전 기능을 켜지 않는다 (`ENABLE_LIVE_TRADING` /
`ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER` 변경 0건).

---

## 안전 가드 (정적 grep + dataclass 불변)

- `portfolio_snapshot.py`: broker / OrderExecutor / route_order / paper_trader /
  `app.ai.assist` / `app.ai.client` / `anthropic` / `openai` / `httpx` /
  `requests` / `get_settings` import 0건, `.place_order(` / `.cancel_order(` /
  `route_order(` 호출 0건.
- `zero_fallback_used` / `is_order_signal` / `auto_apply_allowed` /
  `is_live_authorization` / `contains_secret` 항상 False, `is_paper_only` 항상
  True (불변, ValueError 가드).
- endpoint: broker 주문 0건, DB SELECT only, secret 원문 0건.
- UI: 매수/매도/실전/Place Order 버튼 0개, 입력 form 0개.
