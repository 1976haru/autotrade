# 테마 기능 3a 설계 — 종목 테마 태깅 + 수동 ON/OFF

> 범위: **구조 설계만(read-only)**. 코드·DB·주문 경로 변경 없음.
> 3b의 미국 테마 자동 연동은 shadow 검증 전까지 **미구현**이다.
> 보호계층(`RiskManager → PermissionGate → OrderExecutor`) 변경 예상: **0줄**.

## 0. 결론

| 항목 | 결정 |
|---|---|
| 테마 매핑 | **KRX 업종 스냅샷 + 버전 관리 수동 오버레이**. FDR은 TOP402/종목명 검증용으로만 사용 |
| 다중 테마 | 종목당 `primary_theme` 1개 + `themes[]` 여러 개. OFF 테마와 하나라도 겹치면 신규 진입 제외 |
| 필터 지점 | `_scan_universe_symbols()`의 **신규 스캔 풀을 회전시키기 전** 적용 |
| 보유 종목 | 필터 뒤에 기존 `held_symbols` union을 그대로 수행. 따라서 OFF 테마도 손절·익절·청산 평가 유지 |
| 토글 | 테마별 ON/OFF. OFF 시 `오늘만`(기본) 또는 `다시 켤 때까지` 선택 |
| 반영 시점 | API 저장 직후, **재시작 없이 다음 scan tick부터** 반영 |
| 저장 | 기존 런타임 override 저장소에 `theme_exclusions` 키 추가. 전용 API로만 수정 |
| 미분류 처리 | 신규 진입을 임의 차단하지 않는 fail-open. 대신 UI/API에 미분류 수를 경고하고 TOP402 배포 검증은 0개를 강제 |

핵심 실행 순서는 다음과 같다.

```text
auto TOP402 / active watchlist / override
                 |
                 v
       OFF 테마 종목 제거             <- 신규 진입 후보에만 적용
                 |
                 v
          cap 기반 회전 윈도우
                 |
                 v
       + held_symbols 무조건 union     <- OFF 테마 보유 종목도 다시 포함
                 |
                 v
       시세 조회 / Council 판단
          |                    |
     BUY 사전 가드          SELL 보유·원장 가드
          |                    |
          +------ 기존 보호·주문 경로 ------+
```

## 1. 현재 코드 기준점

현재 운영 스캔은 다음 구조다.

- `backend/app/universe/default_universe.py`
  - `FALLBACK_MARKET_CAP_TOP402`가 정확히 402개이며 중복 없음.
  - 앞 100개는 기존 TOP100과 동일하다는 assert가 있다.
- `backend/app/kis_paper/driver_bridge.py:346` `_full_universe_pool()`
  - `auto`: `TOP402[:effective_universe_size()]`
  - `watchlist`: 활성 관심종목, 비면 auto fallback
- `backend/app/kis_paper/driver_bridge.py:365` `_scan_universe_symbols()`
  - 기본 10개 cap의 회전 윈도우를 만든다.
- `backend/app/kis_paper/driver_bridge.py:718-729`
  - KIS 잔고로 `held_symbols`를 구한 뒤 회전 윈도우에 보유 종목을 union한다.
- `backend/app/kis_paper/driver_bridge.py:767-865`
  - 보유 종목에 `PositionContext`를 전달해 손절·익절을 평가한다.
  - BUY 가드는 보유 중복·동시 보유 수·일일 매수 한도·tick당 신규 수만 검사한다.
  - SELL은 보유 수량 및 봇 소유권 원장을 검사한 뒤 기존 주문 경로로 간다.
- `backend/app/core/runtime_config.py`
  - 파일 + 메모리 캐시 방식의 런타임 override가 있고 API 저장 직후 다음 tick에서 getter를 다시 호출한다.
- `frontend/src/components/common/RuntimeConfigCard.jsx`
  - 서버 실효값을 기준으로 런타임 설정을 저장하는 기존 UI 패턴이 있다.

따라서 테마 필터는 주문/리스크 계층이 아니라 **유니버스 선택 계층**에 추가하는 것이 맞다.

## 2. 테마 분류 데이터

### 2.1 소스 비교

| 후보 | 정확성 | 유지보수 | 다중 테마 | 판단 |
|---|---:|---:|---:|---|
| FDR `StockListing("KRX")` | 종목·시장·시총에는 적합 | 추출이 쉬움 | 부족 | TOP402/종목명 검증용 |
| KRX 업종 분류 | 공식 업종의 기준값으로 가장 적합 | 정기 snapshot 필요 | 기본적으로 1개 업종 | **기본 분류 소스** |
| 수동 매핑 파일 | 검토 품질에 좌우 | Git diff/review가 쉬움 | 가능 | **테마 오버레이 및 최종 운영 SSOT** |
| 기존 `ThemeSignal.related_symbols` | 뉴스/트렌드 시점 데이터 | 누락·변동·mock 가능 | 가능 | 종목 마스터로 사용 금지 |
| `WatchlistItem.sector` | 관심종목별 임의 입력 | TOP402 전체 보장 불가 | 단일 문자열 | 보조 정보로만 사용 |

현재 TOP400 추출 스크립트 `scripts/extract_top400_universe.py`도 FDR의 `Code`, `Name`, `Market`, `Marcap`을 사용한다. 반도체·2차전지·인터넷 같은 운영 테마는 시총 listing만으로 안정적으로 만들 수 없다.

또한 KRX의 공식 업종과 운영자가 원하는 “테마”는 동일하지 않다. 예를 들어 2차전지는 여러 공식 업종에 걸치고, 한 회사가 화학과 2차전지에 동시에 속할 수 있다. 따라서 **KRX 단독 자동 분류도 불충분**하다.

권고 방식:

1. 오프라인 생성기가 KRX 업종 snapshot을 가져온다.
2. 업종→표준 테마 변환 규칙으로 1차 분류한다.
3. 사람이 다중 테마와 예외를 review한다.
4. review된 정적 파일만 운영 코드가 읽는다.
5. 운영 중 외부 API 호출이나 자동 재분류는 하지 않는다.

즉, KRX는 근거 데이터이고 버전 관리 파일이 런타임 SSOT다.

### 2.2 파일 구조

신규 파일 권고:

- `data/market/theme_catalog.json`: 테마 정의와 TOP402 매핑
- `scripts/build_theme_catalog.py`: 선택 사항. KRX snapshot + 수동 overlay를 합치고 검증하는 오프라인 도구
- `data/market/theme_overrides.json`: 선택 사항. 사람이 관리할 예외만 분리할 때 사용

운영 단순성을 우선하면 생성 결과인 `theme_catalog.json` 하나만 런타임에서 읽고, 생성 입력은 스크립트/overlay로 분리한다.

예시:

```json
{
  "schema_version": 1,
  "universe": "TOP402",
  "universe_as_of": "2026-07-03",
  "taxonomy_version": "kr-theme-v1",
  "themes": [
    {"id": "semiconductor", "label": "반도체", "order": 10},
    {"id": "bio", "label": "바이오", "order": 20},
    {"id": "automobile", "label": "자동차", "order": 30},
    {"id": "finance", "label": "금융", "order": 40},
    {"id": "secondary_battery", "label": "2차전지", "order": 50},
    {"id": "internet", "label": "인터넷", "order": 60},
    {"id": "other", "label": "기타", "order": 999}
  ],
  "symbols": {
    "005930": {
      "name": "삼성전자",
      "primary_theme": "semiconductor",
      "themes": ["semiconductor"],
      "krx_industry": "전기전자",
      "source": "manual_override",
      "reviewed_at": "2026-07-05"
    },
    "051910": {
      "name": "LG화학",
      "primary_theme": "chemical",
      "themes": ["chemical", "secondary_battery"],
      "krx_industry": "화학",
      "source": "krx+manual",
      "reviewed_at": "2026-07-05"
    }
  }
}
```

규칙:

- 식별자는 표시명과 분리된 영문 `theme_id`를 쓴다. 표시명 변경이 저장 상태를 깨지 않게 한다.
- `primary_theme`은 UI 그룹/집계용이며 반드시 `themes[]`에도 포함한다.
- `themes[]`는 중복 없는 1개 이상 목록이다.
- 한 종목이 여러 테마에 속하면 모두 기록한다.
- OFF 집합과 `themes[]`의 교집합이 하나라도 있으면 신규 진입을 막는다.
- `기타`는 실제 검토를 마친 비핵심 업종에 사용하고, 누락인 “미분류”와 구분한다.
- `ThemeSignal`의 동적 테마명과 이 catalog의 `theme_id`는 3a에서 합치지 않는다.

### 2.3 검증과 갱신

catalog CI/사전검증에서 다음을 강제한다.

1. `set(catalog.symbols) == set(FALLBACK_MARKET_CAP_TOP402)`
2. 402개 코드 모두 6자리 숫자, 중복 0
3. 모든 `primary_theme`과 `themes[]` 값이 등록된 `theme_id`
4. `primary_theme in themes`
5. 빈 `themes[]` 0개
6. catalog 이름과 현재 FDR/TOP402 이름 차이는 경고 또는 review 대상
7. TOP402가 갱신되면 추가·제거·테마 변경 diff를 사람이 승인

TOP402 catalog가 불완전한 빌드는 실패시킨다. 다만 런타임에서 파일 한 행이 깨지거나 watchlist에 TOP402 밖 종목이 들어오는 경우는 거래 전체를 멈추지 않고 그 종목을 **미분류 fail-open** 처리한다. API/UI에는 `unmapped_symbols`와 수를 반드시 표시한다.

갱신 주기는 TOP402 갱신과 함께 월 1회 또는 편입 변경 시가 적절하다. 실시간 자동 갱신은 3a 범위가 아니다.

## 3. 필터 지점과 청산 보장

### 3.1 적용 위치

권고 위치는 `driver_bridge.py::_scan_universe_symbols()` 내부다. 기존 의미를
보존해 명시적 override는 cap/회전 없이 필터만 적용하고, 일반 풀은 필터 후 회전한다.

```python
if override is not None:
    return exclude_disabled_themes(override, now=now)
if smoke_mode:
    return [smoke_symbol]  # 진단은 테마 필터 우회

base_symbols = resolve_auto_or_watchlist()
entry_symbols = exclude_disabled_themes(base_symbols, now=now)
return rotate_with_cap(entry_symbols)
```

그리고 호출부의 기존 순서는 유지한다.

```python
symbols = _scan_universe_symbols(...)       # 신규 진입 가능 풀
held_map = await _kis_held_map(...)
held_symbols = set(held_map)
symbols += [s for s in held_symbols if s not in symbols]  # 기존 코드 유지
```

중요한 점은 테마 제외를 `held_symbols` union **앞**에만 둔다는 것이다. Council 이후에 BUY 결과를 거부하는 방식은 제외 종목의 KIS 시세/봉 호출을 계속 발생시키므로 요구사항인 “스캔 단계에서 후보 제거”를 충족하지 못한다.

회전 전 필터가 회전 후 필터보다 낫다.

- OFF 종목이 cap 슬롯을 소비하지 않는다.
- 허용 종목 10개를 정상적으로 조회한다.
- 400개 중 큰 테마를 꺼도 전체 허용 풀 순회 시간이 불필요하게 늘지 않는다.

테마 상태가 바뀌면 회전 풀 길이가 달라진다. 기존 offset을 새 길이에 modulo 적용하면 안전하며, 최악의 경우 한 종목이 한 번 재조회되거나 다음 회전에 조회된다. 즉시 공정성을 명확히 하려면 토글 저장 성공 시 scan rotation offset을 0으로 초기화할 수 있지만, API 계층이 driver 전역을 직접 건드리는 결합은 권장하지 않는다. 기본 설계는 modulo만 사용한다.

### 3.2 다중 테마 규칙

```python
blocked = bool(set(symbol_themes) & disabled_theme_ids)
```

예를 들어 `["chemical", "secondary_battery"]`인 종목은 둘 중 하나라도 OFF면 제외한다. 사용자가 “2차전지 제외”를 선택했는데 화학 태그도 있다는 이유로 진입되는 것보다 의미가 명확하다.

UI에는 각 테마별 영향 종목 수와 중복 태그 수를 보여 과도한 제외를 알린다.

### 3.3 보유 종목 동작

반도체 OFF 상태의 예:

- 미보유 SK하이닉스: 신규 풀에서 제거 → 시세/Council/BUY 평가 없음
- 보유 삼성전자: 신규 풀에서는 제거되지만 `held_symbols` union으로 다시 추가
- 보유 삼성전자: `PositionContext` 생성 → 기존 손절·익절·청산 평가
- SELL 발생: 기존 보유 수량/봇 소유권/주문가능수량 검사 후 기존 주문 경로 실행
- BUY 발생: 이미 보유이므로 기존 `DUPLICATE_POSITION_BLOCKED`에 의해 차단

따라서 **신규 진입만 차단, 청산은 유지**된다.

청산 보장을 위해 건드리지 않을 영역:

- `_kis_held_map()` 및 snapshot fallback
- `held_symbols` union
- `PositionContext`와 손절/익절 가격 계산
- `_exit_only` 열화 데이터 청산 경로
- SELL 보유·원장·주문가능수량 가드
- `execute_kis_paper_auto_order()`
- `RiskManager`, `PermissionGate`, `OrderExecutor`, `route_order`

### 3.4 smoke/override 정책

- `auto`, `watchlist`, 명시적 `universe_symbols` 모두 같은 테마 필터를 적용한다.
- 테스트용으로 필터를 우회해야 하면 상태 getter를 주입하거나 빈 OFF 집합을 주입한다.
- `kis_paper_smoke_mode`는 주문 파이프라인 진단 목적이므로 **테마 OFF를 우회**하는 편이 맞다. 그렇지 않으면 smoke symbol의 테마가 OFF일 때 진단 자체가 0건이 된다. UI/API에 “smoke 진단에는 적용되지 않음”을 명시한다.

## 4. 수동 토글 UI와 런타임 상태

### 4.1 UI

설정 화면에 별도 `ThemeFilterCard`를 둔다. 기존 `ThemeSignalsCard`는 뉴스/트렌드 후보 신호용이므로 합치지 않는다.

각 행:

- 테마명
- ON/OFF 토글
- 이 테마에 속한 TOP402 종목 수
- OFF 상태일 때 범위: `오늘만` 또는 `해제까지`
- `오늘만`이면 만료 시각: `오늘 24:00 KST`
- 겹치는 다른 OFF 테마가 있으면 중복 제외 수

동작:

1. ON → OFF 시 작은 선택창에서 `오늘만`(기본) / `다시 켤 때까지` 선택
2. OFF → ON은 즉시 해제
3. 요청 중 해당 토글만 비활성
4. 서버 응답 실효값으로 다시 그린다. 낙관적 성공 표시는 하지 않는다.
5. 실패 시 이전 상태를 유지하고 오류 표시
6. 상단에 “신규 진입만 차단 · 보유 종목 청산은 계속” 고정 안내

테마가 여러 개인 종목 때문에 “테마 ON”이 곧 해당 종목 허용을 뜻하지 않을 수 있다. 다른 테마가 OFF면 계속 제외된다. API가 `effective_blocked_symbol_count`를 반환하고 UI가 이를 표시해야 한다.

### 4.2 API

전용 API를 권고한다.

```text
GET   /api/theme-filter
PATCH /api/theme-filter/{theme_id}
```

PATCH 요청:

```json
{"enabled": false, "duration": "today"}
```

또는:

```json
{"enabled": false, "duration": "until_enabled"}
```

ON 요청:

```json
{"enabled": true}
```

GET/PATCH 응답 핵심:

```json
{
  "catalog_version": "kr-theme-v1",
  "timezone": "Asia/Seoul",
  "themes": [
    {
      "id": "semiconductor",
      "label": "반도체",
      "enabled": false,
      "duration": "today",
      "disabled_at_kst": "2026-07-05T18:10:00+09:00",
      "expires_at_kst": "2026-07-06T00:00:00+09:00",
      "mapped_symbol_count": 24
    }
  ],
  "effective_disabled_theme_ids": ["semiconductor"],
  "effective_blocked_symbol_count": 24,
  "unmapped_symbol_count": 0,
  "applies_to": "NEW_ENTRY_ONLY",
  "restart_required": false
}
```

검증:

- catalog에 없는 `theme_id`: 404
- `duration`이 두 허용값 밖: 400
- 서버가 만료시각을 KST 기준으로 계산하고 클라이언트 시각을 신뢰하지 않음
- PATCH는 서버 저장 성공 후 실효 상태를 반환

### 4.3 저장과 만료

기존 `runtime_overrides.json`에 다음 키를 추가하는 안이 가장 작다.

```json
{
  "theme_exclusions": {
    "semiconductor": {
      "duration": "today",
      "disabled_at": "2026-07-05T09:10:00Z",
      "expires_at": "2026-07-05T15:00:00Z"
    },
    "bio": {
      "duration": "until_enabled",
      "disabled_at": "2026-07-05T09:12:00Z",
      "expires_at": null
    }
  }
}
```

- `today`: 저장 시점의 `Asia/Seoul` 날짜 다음 자정에 만료
- `until_enabled`: 운영자가 ON할 때 삭제
- 만료된 항목은 effective getter가 즉시 무시
- GET/PATCH 때 만료 항목을 정리할 수 있으나, 정리 실패와 실효 판단은 분리한다.
- 프로세스 재시작 후에도 파일에서 복원한다.

`effective_disabled_theme_ids(now)`를 scan tick마다 호출한다. API가 같은 프로세스의 캐시를 갱신하므로 저장 직후 다음 tick에 반영되어 재시작이 필요 없다.

현재 런타임 저장은 프로세스 메모리 캐시와 thread lock을 사용하므로 **단일 backend 프로세스**를 전제로 한다. 다중 worker 운영으로 바뀌면 파일 캐시 전파가 되지 않으므로 DB 행 또는 공유 저장소로 옮겨야 한다. 3a에서는 현재 배포 형태에 맞춰 파일 저장을 재사용한다.

일부 키만 갱신하는 PATCH가 기존 런타임 값을 덮어쓰지 않도록 read-modify-write를 기존 lock 안에서 수행한다. 가능하면 기존 `_persist()`도 임시 파일 작성 후 `replace`하는 atomic write로 보강한다.

## 5. 회귀 위험

| 위험 | 영향 | 방지 설계 / 검증 |
|---|---|---|
| 필터를 held union 뒤에 적용 | 보유 종목 청산 누락 | 반드시 union 전에 신규 풀만 필터 |
| Council 결과 뒤 BUY만 차단 | 제외 종목 시세 호출 지속 | 회전 전 스캔 풀에서 제거 |
| 다중 태그 중 일부만 비교 | 사용자가 끈 테마 종목 진입 | OFF 집합과 tags의 교집합 검사 |
| catalog 누락 | OFF인데 종목이 진입 | 배포 시 TOP402 100% coverage 강제, 런타임 경고 |
| fail-closed로 미분류 차단 | watchlist 신규 종목 전체 차단 가능 | 미분류 fail-open + 명시 경고 |
| 만료를 로컬 브라우저 시간으로 계산 | 자정 전후 상태 불일치 | 서버 `Asia/Seoul` 기준 계산 |
| 기존 runtime 설정 전체 overwrite | 손절/예산 등 원치 않는 변경 | 테마 전용 PATCH + lock 안의 부분 갱신 |
| 설정 파일 부분 쓰기 | 모든 override 복구 실패 | atomic temp-write/replace 권고 |
| 테마 토글 중 회전 offset 변화 | 일시적 중복/지연 조회 | 새 풀 길이 modulo, 다음 순회 내 수렴 |
| 테마 OFF가 SELL까지 막음 | 손절 보호 상실 | 보유 OFF 종목 STOP_LOSS E2E 필수 |
| 기존 동적 ThemeSignal과 혼동 | 예측 신호가 고정 분류를 변경 | catalog와 signal 모델/API/UI 완전 분리 |
| 다중 backend worker | 캐시별 상태 불일치 | 현 단일 프로세스 명시, 확장 시 DB/shared store |

### 필수 회귀 테스트

Backend:

1. catalog가 TOP402를 정확히 100% 덮는지
2. 삼성전자→반도체 등 대표 golden mapping
3. 다중 테마 종목은 하나의 OFF 테마만 있어도 제외되는지
4. 오늘만 OFF가 KST 자정 전 유효, 자정부터 자동 해제되는지
5. 해제까지 OFF가 재시작 복원 후 유지되는지
6. auto/watchlist/override에서 동일하게 적용되는지
7. OFF 테마 미보유 종목은 `market_input_fn` 호출조차 되지 않는지
8. **OFF 테마 보유 종목은 held union으로 호출되고 STOP_LOSS SELL이 제출되는지**
9. OFF 테마 보유 종목의 TAKE_PROFIT/트레일링/장마감 청산도 유지되는지
10. 수동 보유 원장 보호(`SELL_NOT_BOT_OWNED`)가 그대로인지
11. RiskManager/PermissionGate/OrderExecutor 모듈 diff 0 및 기존 테스트 통과
12. 기존 회전 cap/전체 순회/보유 항상 스캔 테스트 통과

기존 회귀 기반으로 특히 다음 테스트 파일을 확장하는 것이 적합하다.

- `backend/tests/test_kis_paper_sell_holding_guard.py`
- `backend/tests/test_ownership_filter.py`
- `backend/tests/test_runtime_config.py`
- 신규 `backend/tests/test_theme_catalog.py`
- 신규 `backend/tests/test_theme_filter_routes.py`

Frontend:

1. catalog 로딩/빈 상태/오류 상태
2. OFF 시 기간 선택과 PATCH payload
3. 서버 성공 응답 후에만 토글 반영
4. 오늘만 만료 표시
5. “신규 진입만 차단 · 청산 유지” 문구 고정
6. 다른 OFF 테마 때문에 여전히 제외되는 중복 상태 표시

## 6. 예상 변경 규모

설계대로 구현할 경우의 대략적인 규모다. catalog 402개 데이터 행은 코드 LOC와 분리해서 본다.

| 영역 | 파일 | 예상 변경 |
|---|---|---:|
| catalog | 신규 `data/market/theme_catalog.json` | 402종목 데이터 |
| catalog loader/filter | 신규 `backend/app/theme_filter/catalog.py`, `service.py` | 140~220 LOC |
| 런타임 상태 | `backend/app/core/runtime_config.py` 확장 | 70~120 LOC |
| API | 신규 `backend/app/api/routes_theme_filter.py`, `main.py` 등록 | 80~130 LOC |
| 스캔 연결 | `backend/app/kis_paper/driver_bridge.py` | 10~25 LOC |
| UI | 신규 `ThemeFilterCard.jsx`, 설정 화면 배치 | 150~230 LOC |
| API client | `frontend/src/services/backend/client.js` | 5~15 LOC |
| Backend 테스트 | 기존 3개 확장 + 신규 2개 | 220~350 LOC |
| Frontend 테스트 | 신규 카드 테스트 | 100~180 LOC |
| 선택적 생성기 | `scripts/build_theme_catalog.py` | 100~180 LOC |

예상 합계:

- 생성기 제외 제품 코드: 약 **455~740 LOC**
- 테스트: 약 **320~530 LOC**
- 정적 catalog: **402종목 매핑**
- 기존 보호계층: **0 LOC**
- DB migration: **0개**

`ThemeFilterCard`를 기존 `RuntimeConfigCard` 안에 직접 넣으면 파일 수는 줄지만 컴포넌트가 이미 여러 설정을 담당하고 있어 회귀 범위가 커진다. 별도 카드와 전용 API가 더 안전하다.

## 7. 구현 순서

1. taxonomy ID와 TOP402 catalog를 작성하고 coverage validator를 먼저 고정
2. 순수 함수 `symbol → themes`, `symbols - disabled themes` 구현
3. 런타임 OFF 상태·KST 만료·전용 API 구현
4. `_scan_universe_symbols()`의 회전 전 신규 풀에만 연결
5. OFF 테마 보유 종목 STOP_LOSS E2E를 먼저 통과
6. UI 카드 연결
7. 기존 스캔 회전·보유 union·SELL·원장 보호 전체 회귀 실행

3a 완료 조건은 “수동 catalog + 수동 토글 + 신규 진입만 차단 + 청산 유지”까지다. 미국 테마, 뉴스/트렌드 기반 자동 OFF, 자동 재분류, 동적 `ThemeSignal` 결합은 모두 3b 이후 별도 opt-in으로 남긴다.
