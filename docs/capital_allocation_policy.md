# Paper 자본 배분 정책 — Seed Money + 종목당 투자금 (P-시리즈)

> 본 문서는 AI Paper 모의매매에서 *가상 자본* 을 어떻게 배분하는지의 단일
> 진실. 실전 계좌와 *완전히 분리* 된 가상 자본 — 어떤 broker / OrderExecutor /
> route_order / KIS 실거래 한도와도 결합되지 않는다.

## 1. P-01 — 초기 시드머니 (본 PR)

### 1-1. 허용 옵션

| 옵션 | KRW 정수 | 한국어 라벨 |
|---|---|---|
| 1 | `10_000_000` | 1,000만원 (기본값) |
| 2 | `30_000_000` | 3,000만원 |
| 3 | `50_000_000` | 5,000만원 |

위 3종 외 값은 *허용되지 않는다* — caller 는 `InvalidPaperCapitalError` 로
거부되거나, `fallback_to_default=True` 시 default (1,000만원) 로 대체된다.

### 1-2. 통화

KRW 한정 (P-01 시점). USD / JPY 등 타 통화는 별도 PR.

### 1-3. 영구 저장

P-01 시점 *in-memory* 만 — 프로세스 재시작 시 default 로 복귀. DB / settings
영구화는 **P-16** 에서 별도 PR.

### 1-4. 절대 invariant

| 항목 | 값 |
|---|---|
| `PaperCapitalConfig.is_paper_only` | `True` (영구 — 변경 시 ValueError) |
| `PaperCapitalConfig.is_live_authorization` | `False` (영구) |
| `PaperCapitalConfig.currency` | `"KRW"` (영구) |
| capital_config 모듈 import 대상 | broker / OrderExecutor / KIS / 외부 HTTP / AI SDK / `app.core.config.get_settings` 어느 것도 0건 |
| `.enable_live_trading = ...` mutation | 0건 |
| `broker.place_order(` / `route_order(` 호출 | 0건 |

### 1-5. 코드 위치

| 항목 | 경로 |
|---|---|
| 모듈 | `backend/app/auto_paper/capital_config.py` |
| Test | `backend/tests/test_paper_capital_config.py` (32 PASS) |
| API GET | `GET /api/auto-paper/capital-config` |
| API POST | `POST /api/auto-paper/capital-config` |
| Frontend | `frontend/src/components/common/PaperCapitalCard.jsx` |
| Frontend test | `frontend/src/components/common/PaperCapitalCard.test.jsx` (11 PASS) |

### 1-6. API 응답 contract

`GET /api/auto-paper/capital-config`:

```json
{
  "initial_cash":                  10000000,
  "allowed_initial_cash_options":  [10000000, 30000000, 50000000],
  "currency":                      "KRW",
  "is_paper_only":                 true,
  "is_live_authorization":         false,
  "updated_at":                    "2026-05-21T01:23:45.678+00:00",
  "notice":                        "Paper 시드머니는 *모의매매 전용* ..."
}
```

`POST /api/auto-paper/capital-config` 입력:

```json
{
  "initial_cash":         30000000,
  "fallback_to_default":  false
}
```

응답: 위 GET 응답 + `"fallback_used": false`. 허용되지 않은 값 + `fallback_to_default=false` → **400**.

### 1-7. UI 동작

`PaperCapitalCard` 카드에 다음을 항상 노출:

- 3개 옵션 button (선택된 옵션은 `data-selected="true"`)
- "이 값은 모의매매 전용이며 실전 계좌와 무관합니다" disclaimer
- 현재 설정 + KRW 정수 + 통화
- 영구 invariant 배지 3종 (`Paper 전용` / `실거래 OFF 유지` / `주문 권한 없음`)
- *임의 KRW 입력 form 0개* — 허용 옵션만 chip 선택. 잘못된 값 입력 차단.

## 2. P-02 (예정) — 종목당 투자금

본 PR 시점 미구현. 다음 PR 에서:
- 시드머니 대비 % (예: 5% / 10% / 20%)
- 절대 KRW (예: 500,000 / 1,000,000 / 2,000,000)
- `PositionSizingPolicy.max_position_krw` 와의 결합 방식

설계 원칙은 P-01 과 동일 — 실전 계좌 / 실거래 한도와 *완전 분리*.

## 3. 사용자 흐름 예시 (P-01 동작 확인)

```
1. EXE 실행 → Dashboard → "Paper 시드머니" 카드 노출
2. 기본 1,000만원 선택됨
3. 사용자가 "5,000만원" 클릭
4. backend POST /api/auto-paper/capital-config { initial_cash: 50_000_000 }
5. 응답: initial_cash=50000000, fallback_used=false
6. 카드 요약에 "현재 Paper 시드머니: 5,000만원 (KRW)" 표시
7. AI Paper Auto Loop 가 매수 판단 시 본 값을 `account_equity` 로 사용
   (P-02 종목당 투자금이 이 값을 기준으로 sizing 결정)
8. broker / KIS / 실거래 호출 0건
9. 앱 재시작 시 default 1,000만원 으로 복귀 (P-16 에서 영구화)
```

## 4. P-시리즈 전체 매핑

| 번호 | 항목 | 본 PR | 상태 |
|---|---|---|---|
| P-01 | 초기 Paper 시드머니 설정 | ✅ 본 PR | done |
| P-02 | 종목당 투자금 설정 | (다음) | pending |
| P-... | (이후 항목) | (별도 PR) | pending |
| P-16 | Paper 자본 설정 영구 저장 | (예정) | pending |
