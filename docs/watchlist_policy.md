# Watchlist Policy (체크리스트 #18)

본 프로젝트의 자동매매 / Agent 판단 대상 universe를 **운영자가 수동으로 관리하는 50~200개 종목**으로 제한하기 위한 정책. 본 문서는 데이터 모델/엔드포인트의 *의미*를 정한다 — 코드 위치는 [`database_schema.md`](database_schema.md), [`broker_selection.md`](broker_selection.md) 참조.

## 1. 핵심 원칙

| 원칙 | 의미 |
|---|---|
| **Watchlist는 universe 후보군이다** | Strategy / Agent가 매매 후보로 사용하는 종목 풀. 등록 자체는 **주문 신호가 아니다**. |
| **전체시장 직접 스캔 금지 (초기)** | KRX 전 종목 스캔은 본 단계에서 비활성. 운영자가 등록한 watchlist에서만 후보 추출. |
| **수동 등록 50~200개 권장** | 50개 미만은 분산 부족, 200개 초과는 universe 의미 흐려짐. **200개는 절대 한도** (코드 강제). |
| **CSV/UI 수동 관리** | 자동 갱신 안 함. 운영자가 주기적 정리 필요. |
| **RiskManager 우회 절대 금지** | watchlist에 등록되어 있어도 모든 주문은 `RiskManager → PermissionGate → OrderExecutor` 단일 경로 유지 ([`risk_policy.md`](risk_policy.md)). |
| **테마/뉴스/Agent score와 결합 가능** | 단, RiskManager 결정 자체는 watchlist 조회 결과를 입력으로 받지 않는다 — Strategy/Agent가 후보 추출에 사용할 뿐. |

## 2. 한도 상수

| 상수 | 값 | 위치 |
|---|---|---|
| `WATCHLIST_MAX_ITEMS` | **200** | `app/watchlist/service.py` |
| `WATCHLIST_RECOMMENDED_ITEMS` | 50 | 동일 |
| `SYMBOL_MAX_LENGTH` | 16 | 동일 (KRX 6자리 + 외국 ticker 여유) |
| `NAME_MAX_LENGTH` | 64 | watchlist 이름 |

200개를 절대 한도로 두는 이유:
- universe가 너무 넓으면 Strategy가 "거의 전체 시장"을 보게 되어 후보군 의미가 흐려진다.
- 단순 행 추가는 가능하지만 *분석 비용*과 *AI 호출 비용*이 종목 수에 곱셈으로 작용한다.
- 운영자가 능동적으로 정리하는 흐름을 유지해야 universe 갱신이 일어난다.

## 3. 사용자 친화 에러 메시지

운영자에게 그대로 표시 가능한 한국어 메시지만 사용한다. 영어 stack trace 또는 영문 코드 노출 금지.

| 상황 | 메시지 |
|---|---|
| 200 한도 초과 | "관심종목은 한 목록당 최대 200개까지 등록할 수 있습니다." |
| 중복 등록 | "이미 등록된 종목입니다." |
| 빈 종목코드 | "종목코드를 입력해 주세요." |
| 16자 초과 | "종목코드가 너무 깁니다 (최대 16자)." |
| 빈 이름 | "관심종목 목록 이름을 입력해 주세요." |
| 64자 초과 | "관심종목 목록 이름이 너무 깁니다 (최대 64자)." |
| 미존재 watchlist/item | "해당 관심종목 목록을 찾을 수 없습니다." / "해당 종목을 찾을 수 없습니다." |
| CSV 본문 빈 값 | "CSV 내용이 비어있습니다." |
| CSV symbol 컬럼 누락 | "CSV에 'symbol' 컬럼이 필요합니다." |
| CSV non-UTF8 | "CSV는 UTF-8 인코딩이어야 합니다." |

## 4. 정규화 규칙

| 필드 | 규칙 |
|---|---|
| `symbol` | trim + uppercase + 16자 이하. 빈 문자열은 거부. (예: `"  aapl  "` → `"AAPL"`) |
| `name` | trim + 64자 cap, 빈 문자열은 NULL 저장 |
| `market` / `sector` / `note` | trim + 각 32 / 64 / 255자 cap, 빈 문자열은 NULL 저장 |

CSV에서도 동일 정규화. CSV 헤더는 case-insensitive (`Symbol` / `SYMBOL` 모두 허용).

## 5. CSV 양식

```csv
symbol,name,market,sector,note
005930,삼성전자,KOSPI,반도체,코어
000660,SK하이닉스,KOSPI,반도체,
035720,카카오,KOSPI,IT,
```

- **필수**: `symbol` 컬럼
- **선택**: `name`, `market`, `sector`, `note`
- 헤더 없으면 거부 (빈 줄 / null 헤더 → 400).
- BOM(`﻿`) 자동 제거 (Excel export 호환).
- 반환 응답:
  ```json
  {
    "added": 12,
    "skipped": 3,
    "invalid": 1,
    "total_after_import": 42,
    "errors": ["3행: 종목코드를 입력해 주세요."]
  }
  ```
- 200 한도 초과 시 추가 거부 + `errors`에 명시. 이후 행은 더 시도하지 않는다.
- 중복(이미 등록된 symbol)은 `skipped`.

## 6. API 엔드포인트

| Method | Path | 의미 |
|---|---|---|
| `GET` | `/api/watchlists` | 전체 목록 + 한도 상수 |
| `POST` | `/api/watchlists` | 생성 (이름 필수, `is_active=true` 시 다른 목록 자동 비활성) |
| `GET` | `/api/watchlists/summary` | Dashboard 요약 (active + top 5 symbol) |
| `GET` | `/api/watchlists/{id}` | 단건 + 모든 items |
| `PATCH` | `/api/watchlists/{id}` | 이름/설명/활성 토글 |
| `DELETE` | `/api/watchlists/{id}` | cascading delete (items 함께 제거) |
| `POST` | `/api/watchlists/{id}/items` | 종목 추가 (정규화 + 한도/중복 검증) |
| `DELETE` | `/api/watchlists/{id}/items/{item_id}` | 종목 삭제 |
| `POST` | `/api/watchlists/{id}/import-csv` | CSV 일괄 추가 (`text/csv` 또는 JSON `{"csv": "..."}`) |

오류 응답 — 모두 FastAPI 표준 `{"detail": "..."}` 형태. 한국어 메시지.

## 7. Frontend UI

### Settings 탭 — `WatchlistsCard`
- universe 안내 banner (주문 신호 아님 + 200/50 한도)
- 새 watchlist 생성
- 목록별: 활성 토글 / 삭제 / 종목 추가 / CSV import textarea / 종목 chip 리스트
- 200 한도 도달 시 "종목 추가" 버튼 disabled
- CSV import 결과 인라인 표시 ("완료 — 추가 N / 중복 N / 무효 N / 총 N")

### Dashboard — `WatchlistSummaryTile`
- 활성 watchlist 이름 + `종목 수 / 200`
- 대표 5종 chip (`외 N종`)
- "관심종목 관리로 이동 →" 링크 (Settings 탭으로 점프)
- 활성 목록 없으면 빈 상태 안내

### Demo Mode
- 백엔드 미연결 시 `useWatchlistSummary`가 `summary=null` + `error` 메시지 반환 — Tile은 빈 상태/에러 표시.
- frontend에 시크릿/계좌번호/실거래 토글이 없는 것은 본 기능에도 그대로 — 운영자 단말 보안 그대로 유지 (절대 원칙 4).

## 8. Strategy / Agent 연계 (정책만)

본 단계에서는 **StrategyEngine / Agent 코드는 변경하지 않는다**. 향후 단계에서 도입할 때 따라야 하는 약속만 정한다.

| 책임 | 본 단계 | 향후 (옵트인 PR) |
|---|---|---|
| 운영자 universe 정의 | watchlist 테이블에 수동 등록 | 동일 |
| Strategy가 universe 사용 | (미연결) | active watchlist의 `WatchlistItem.symbol` 목록을 `LiveStrategyEngine`이 read-only 조회 |
| Agent가 universe 사용 | (미연결) | 동일 패턴 — chain_id 기반 Agent가 후보 추출 시 active watchlist 우선 |
| RiskManager 입력 | 영향 없음 | 영향 없음 (절대 원칙) |
| PermissionGate 입력 | 영향 없음 | 영향 없음 |
| Audit 흐름 | 영향 없음 | 영향 없음 |

→ Watchlist는 *왼쪽* (전략 입력) 결정에만 영향. 주문 *오른쪽*(가드 → 실행) 흐름은 그대로다. CLAUDE.md 절대 원칙 5/7과 정합.

## 9. 운영자 절차

1. Settings 탭 → "관심종목 (Universe)" 카드 → 새 목록 생성 (예: `단타-반도체`).
2. 종목 직접 추가 또는 CSV 일괄 import.
3. 활성화하려는 목록 하나만 "활성화" 토글 → 다른 목록은 자동 비활성.
4. 주기적 정리 — 거래량 급감 종목 / 상장폐지 종목 / 운영자가 더 이상 모니터링하지 않는 종목 제거.

권장 갱신 주기 — 분기 1회 또는 운영 데이터 누적 시. 자동 갱신 도입은 **별도 옵트인 PR** (KRX 종목 마스터 통합 시점, [`database_schema.md`](database_schema.md) `symbol_master` 후보 참조).

## 10. 안전 invariant

본 PR이 만족시키는 invariant — 회귀 시 본 문서 갱신 + RiskManager 가드 확인:

1. **Watchlist 변경은 broker / RiskManager / PermissionGate / OrderExecutor 어떤 분기에도 영향 없다.**
2. **frontend는 시크릿을 다루지 않는다.** Watchlist 자체에 secret 필드 없음.
3. **`ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 모두 default false로 유지.**
4. **Watchlist 등록 자체로 주문이 만들어지지 않는다.**
5. **200 한도는 코드 단에서 강제** — service / API / CSV 모두 동일 검증.

## 11. 향후 작업

| 항목 | 트리거 |
|---|---|
| `LiveStrategyEngine`이 active watchlist를 읽도록 확장 | 별도 옵트인 PR (Strategy LIVE 활성화 단계) |
| Agent가 universe 우선순위로 사용 | 동일 |
| KRX 종목 마스터 통합 (`symbol_master` 테이블) | 운영자가 KRX-기반 자동 갱신을 원할 때 |
| Watchlist export (CSV download) | 운영자 요청 시 |
| 다중 운영자 / 다중 watchlist owner 분리 | 인증 도입 후 |
| Watchlist version history | 변경 추적이 필요해지는 시점 |

## 관련 문서

- [`database_schema.md`](database_schema.md) — `watchlist` / `watchlist_item` 테이블 정의
- [`broker_selection.md`](broker_selection.md) — broker별 universe 매핑
- [`risk_policy.md`](risk_policy.md), [`risk_guards_matrix.md`](risk_guards_matrix.md) — 가드 체인 (영향 없음 invariant)
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격
- [`api_limits.md`](api_limits.md) — 호출 정책 (Watchlist 라우트는 일반 인증 후 제한 대상)
