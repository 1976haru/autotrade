# 기본 Universe 50개 상태 확인 (#54 / 7-02)

> 사용자가 관심종목을 설정하지 않아도 자동매매 후보군이 비어 있지 않도록 기본
> Universe 50개를 fallback 으로 사용하고, EXE 화면에서 `universe_source` /
> `universe_count` / `symbols_preview` / `fallback_used` / `reason_code` 를
> 명확히 표시합니다.

## 목적

- 관심종목 미설정 시 후보군이 0개로 *조용히 멈추는* 것을 막습니다.
- 어떤 후보군(사용자 관심종목 vs 기본 50)을 쓰는지 사용자가 한눈에 확인합니다.

> **기본 Universe 50개는 자동매매 후보군 확보용이며 투자 추천이 아닙니다.**
> 시장 상황에 따라 상위 종목은 변할 수 있으므로 "운영 기본 후보군"일 뿐입니다.
> 사용자가 관심종목을 설정하면 사용자 관심종목이 우선됩니다.

## fallback 정책

1. 사용자 관심종목이 있으면 **사용자 관심종목 우선** (`USER_WATCHLIST`).
2. 사용자 관심종목이 없으면 **기본 Universe 50개** (`DEFAULT_UNIVERSE_50`,
   `fallback_used=true`).
3. 종목코드는 6자리 숫자만 유효 — **중복 제거 + 유효하지 않은 코드 제거**.
4. 사용자 종목이 모두 유효하지 않으면 기본 50개로 대체
   (`FALLBACK_DEFAULT_UNIVERSE_50`, reason `NO_VALID_SYMBOLS`).
5. 최종 후보군이 0개이면 `EMPTY` + reason `NO_UNIVERSE_SYMBOLS` 로 *명시* 합니다.

## universe_source / reason_code

| universe_source | 의미 |
|---|---|
| `USER_WATCHLIST` | 사용자 관심종목 사용 |
| `DEFAULT_UNIVERSE_50` | 관심종목 없음 → 기본 50개 |
| `FALLBACK_DEFAULT_UNIVERSE_50` | 사용자 종목 전부 invalid → 기본 50개 대체 |
| `EMPTY` | 후보군 0개 |

| reason_code | 의미 |
|---|---|
| `NO_USER_WATCHLIST` | 관심종목 없음 (기본 50 사용) |
| `USER_WATCHLIST_OK` | 사용자 관심종목 사용 |
| `NO_VALID_SYMBOLS` | 유효한 종목코드 없음 (fallback) |
| `NO_UNIVERSE_SYMBOLS` | 최종 후보군 0개 |

## 표시 항목

- `symbols_preview` 는 앞 N개(기본 8개)만 — 전체 50개를 UI 에 길게 펼치지
  않습니다. 나머지는 "외 N개" 로 표시.
- `universe_count` 로 전체 후보군 수를 표시.
- `fallback_used=true` 면 "사용자 관심종목 없음 → 기본 Universe 50개 사용 중"
  안내.

## 확인 방법 (EXE)

1. Settings(설정) 탭 → "📋 자동매매 후보군 (Universe)" 카드.
2. **소스** (USER_WATCHLIST / DEFAULT_UNIVERSE_50 …) 확인.
3. **후보군 N개** + **미리보기**(앞 몇 개) 확인.
4. 관심종목을 등록하면 소스가 `USER_WATCHLIST` 로 바뀝니다.
5. 후보군이 0개로 표시되면 reason(`NO_UNIVERSE_SYMBOLS` 등)을 확인하고
   관심종목을 점검합니다.

## 장 열린 날 KIS 시세 테스트와의 관계

- 후보군은 *종목 선정* 단계입니다. 장이 열린 날 KIS 시세가 들어와야 후보군
  종목에 대한 실제 판단/주문(모의)이 진행됩니다. 장 닫힌 날
  `NO_MARKET_DATA` / `MARKET_CLOSED` 는 정상입니다(후보군과 별개).

## secret/account 와 무관

- universe 상태는 종목코드/카운트만 다루며, API key / Secret / 계좌번호와는
  무관합니다(`contains_secret=false`). 응답·화면에 자격정보 원문이 표시되지
  않습니다.

## 관련 파일

- `backend/app/universe/universe_status.py` (`build_universe_status`)
- `backend/app/universe/default_universe.py` (기본 50개 — 기존)
- `backend/app/api/routes_auto_paper.py` (`GET /api/auto-paper/universe-status`)
- `backend/tests/test_default_universe_50_status.py`
- `frontend/src/components/common/UniverseStatusCard.jsx` (+`.test.jsx`)
- `frontend/src/components/tabs/Settings.jsx` (mount)
