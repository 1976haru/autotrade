# 테마 고도화 설계 — 대분류 확장 + 아침 브리핑 + shadow 기록

> 범위: 설계와 현행 데이터 분석만 수행한다. 코드·DB·주문·설정 변경 없음.
> 운영 universe는 **300종목 그대로** 유지한다. 테마 분류만 확장한다.
> 미국 지표는 표시·측정 전용이며 자동 ON/OFF, 추천, 주문 입력으로 사용하지 않는다.

## 0. 결론

- 운영 대분류를 20개로 확장한다.
- active TOP300의 현재 `기타` 136개를 **5개**로 축소한다.
- 5개는 사업이 분산된 지주사이므로 억지로 한 테마에 넣지 않는다.
- 다중 태그는 유지하며 TOP300 기준 약 35개 종목이 2개 이상 태그를 가진다.
- 아침 브리핑은 미국 지표의 전일 등락과 데이터 상태만 표시한다.
- 미국 기준값과 같은 날 한국 테마 실성과는 별도 shadow 테이블에 기록한다.
- 4주 후 상관·방향 일치·표본 커버리지를 검증한다. 그 전후 모두 거래 로직은 동일하다.

## 1. 범위와 현재 상태

현재 런타임은 `universe_size=300`을 지원하고 TOP402 catalog의 앞 300개를 사용한다.
본 설계에서 바꾸지 않는 항목:

- universe 크기 300
- 회전 scan cap
- 신규 진입 필터 위치
- 보유종목 union
- 손절·익절·청산
- `RiskManager`, `PermissionGate`, `OrderExecutor`, `order_router`

현재 `theme_catalog.json`에는 내부적으로 13개 태그가 있으나, TOP402 전체에서
`other`가 207개이고 active TOP300에서는 136개다. 현재 generic
`industrial` 27개와 `consumer` 18개도 운영자가 바로 이해하기에는 범위가 넓다.

추가로 catalog 정비 전에 고쳐야 할 데이터 품질 문제가 있다.

- 확장 302종목 이름 일부가 CP949/UTF-8 mojibake로 저장되어 있다.
- `082920 비츠로셀`이 bio, `166090 하나머티리얼즈`가 bio로 들어간 오분류가 있다.
- v2 생성기는 KRX/FDR의 정상 UTF-8 이름 snapshot을 기준으로 이름을 재생성해야 한다.

## 2. 기타 207개 분석

`기타`는 실제로 무관한 한 묶음이 아니라 다음 그룹이 섞인 결과다.

| 발견 그룹 | 대표 종목 |
|---|---|
| 전력·원자력·전선 | 한국전력, 한전KPS, 산일전기, 일진전기, 제룡전기, 비에이치아이 |
| 통신·네트워크 | SK텔레콤, KT, LG유플러스, RFHIC, 케이엠더블유, 쏠리드 |
| 게임 | 크래프톤, 시프트업, 더블유게임즈, NHN |
| 로봇·AI·자동화 | 레인보우로보틱스, 두산로보틱스, 로보티즈, 고영, 루닛, 클로봇 |
| 반도체 부품·장비 | 솔브레인, 파두, 피에스케이, 심텍, 코미코, 원익QnC, 네패스 |
| 바이오·제약 | 삼천당제약, 에이비엘바이오, 펩트론, 보로노이, 올릭스, 종근당 |
| 화장품 | 한국콜마, 실리콘투, 달바글로벌, 코스맥스, 코스메카코리아 |
| 음식료 | 삼양식품, 농심, 오뚜기, 동서, 롯데칠성, 롯데웰푸드 |
| 철강·소재 | 고려아연, 현대제철, 세아베스틸지주, LS머트리얼즈, 코스모신소재 |
| 건설·인프라 | 삼성물산, GS건설, HDC현대산업개발, KCC, 한샘 |
| 유통·소비 | 이마트, 롯데쇼핑, BGF리테일, 현대백화점, GS리테일 |
| 운송·물류·여행 | HMM, 대한항공, 팬오션, CJ대한통운, 아시아나항공 |
| 방산·우주 | 한화, 쎄트렉아이, STX엔진, SNT다이내믹스, 엠앤씨솔루션 |
| 조선 기자재 | HD현대중공업, 대한조선, HJ중공업, 세진중공업, 성광벤드 |
| 복합 지주사 | LG, SK, 두산, 롯데지주, 원익홀딩스 |

마지막 복합 지주사 5개는 자회사 구성이 여러 테마에 걸쳐 있고 순수 지표 민감도가
낮다. 이들을 임의로 한 테마에 넣으면 분류 수치는 좋아지지만 측정 정확도는 나빠진다.
따라서 최종 `기타`로 유지한다.

## 3. 확장 taxonomy

### 3.1 TOP300 예상 분포

아래 수치는 현재 TOP402 순서의 앞 300개를 그대로 두고, KRX 업종 기준 +
수동 overlay 초안을 적용한 결과다. 다중 태그가 있으므로 합계는 300보다 크다.

| 순번 | theme_id | 표시명 | TOP300 태그 수 |
|---:|---|---|---:|
| 1 | `semiconductor` | 반도체 | 43 |
| 2 | `bio` | 바이오·제약 | 34 |
| 3 | `automobile` | 자동차·부품 | 14 |
| 4 | `finance` | 금융 | 32 |
| 5 | `secondary_battery` | 2차전지 | 24 |
| 6 | `internet` | 인터넷·플랫폼 | 17 |
| 7 | `defense` | 방산·항공우주 | 15 |
| 8 | `shipbuilding` | 조선·기자재 | 12 |
| 9 | `power_nuclear` | 원자력·전력·전선 | 19 |
| 10 | `entertainment_media` | 엔터·미디어 | 8 |
| 11 | `cosmetics` | 화장품 | 8 |
| 12 | `food_beverage` | 음식료 | 10 |
| 13 | `steel_materials` | 철강·비철·소재 | 10 |
| 14 | `construction_infra` | 건설·인프라 | 13 |
| 15 | `telecom_network` | 통신·네트워크 | 6 |
| 16 | `gaming` | 게임 | 6 |
| 17 | `robot_ai` | 로봇·AI·자동화 | 8 |
| 18 | `retail_consumer` | 유통·소비 | 21 |
| 19 | `energy_chemical` | 에너지·화학 | 22 |
| 20 | `transport_logistics` | 운송·물류·여행 | 10 |
| - | `other` | 기타 | **5** |

정리:

- 기존 TOP300 `기타`: 136개
- 확장 후 TOP300 `기타`: **5개**
- 흡수: 131개
- 다중 태그 종목: 약 35개
- 남는 기타: `003550 LG`, `034730 SK`, `000150 두산`,
  `004990 롯데지주`, `030530 원익홀딩스`

catalog는 TOP402 superset을 계속 보관할 수 있다. 같은 규칙을 402개 전체에 적용하면
기타는 동일한 5개이며, 운영 scan은 계속 앞 300개만 사용한다.

### 3.2 분류 규칙

1. KRX 공식 업종 snapshot을 base로 사용한다.
2. 운영 대분류 변환표로 primary theme을 만든다.
3. 시장 통용 공급망/사업구조는 수동 overlay로 secondary theme을 추가한다.
4. 종목당 `primary_theme` 1개와 `themes[]` 1개 이상 구조를 유지한다.
5. OFF 테마와 `themes[]` 중 하나라도 겹치면 기존처럼 신규 진입만 제외한다.
6. 지주사는 이름이 아니라 실제 주요 매출/자산 구조 근거가 있을 때만 태그한다.
7. 매핑 변경은 `taxonomy_version=kr-theme-v2`와 review 날짜를 기록한다.

대표 다중 태그:

- 한화에어로스페이스: 방산·항공우주 + 조선 기자재 관련 노출 검토
- LS ELECTRIC: 원자력·전력 + 산업 자동화
- 에코프로머티: 2차전지 + 철강·비철·소재
- 서진시스템: 통신·네트워크 + 원자력·전력(ESS)
- 비츠로셀: 2차전지로 수정
- 하나머티리얼즈: 반도체로 수정

### 3.3 기존 런타임 OFF 상태 호환

기존 core ID는 그대로 유지한다. `bio`의 표시명만 “바이오·제약”으로 넓히고
`defense`, `entertainment` 등 기존 ID도 alias로 읽는다.

generic `industrial`/`consumer`가 OFF로 저장되어 있을 수 있으므로 조용히 무시하면 안 된다.

- `industrial` OFF → 전력·소재·건설·로봇·조선·방산 child OFF로 보수적 변환
- `consumer` OFF → 화장품·음식료·유통소비·엔터미디어 child OFF로 변환
- 변환 후 UI에 “taxonomy v2 전환으로 기존 broad OFF를 세부 OFF로 이전” 표시
- 운영자가 검토 후 개별 ON 가능

## 4. 아침 테마 브리핑

### 4.1 표시 원칙

- 전일 미국장 종가 기준 값과 등락률만 표시
- `상승/보합/하락/데이터 없음`은 수치 구간 label일 뿐 매매 판단이 아님
- “추천”, “매수”, “제외”, “쉬세요”, “유망” 문구 금지
- 한국 테마 ON/OFF를 자동 변경하지 않음
- 미국 proxy가 없으면 `매핑 없음`을 명시
- proxy가 한국 테마와 완전히 같지 않음을 `DIRECT/PARTIAL/NONE`으로 표시

### 4.2 미국 proxy 매핑

| 한국 대분류 | 미국 지표/ETF | 품질 | 비고 |
|---|---|---|---|
| 반도체 | `^SOX` | DIRECT | PHLX Semiconductor Index |
| 바이오·제약 | `IBB` | PARTIAL | 바이오 중심, 전통 제약 비중 차이 |
| 자동차·부품 | `DRIV` | PARTIAL | EV·자율주행 편향 |
| 금융 | `XLF` | DIRECT | 미국 금융 섹터 |
| 2차전지 | `LIT` | DIRECT | 리튬 채굴~배터리 생산 |
| 인터넷·플랫폼 | `FDN` | DIRECT | 미국 상장 인터넷 기업 |
| 방산·항공우주 | `ITA` | DIRECT | 미국 항공우주·방산 |
| 조선·기자재 | 매핑 없음 | NONE | 미국 shipping ETF는 운송사 중심이라 조선 대용 금지 |
| 원자력·전력·전선 | `URA` + `XLU` | PARTIAL | 원자력 공급망 + 유틸리티 2개 병기 |
| 엔터·미디어 | `XLC` | PARTIAL | 통신·인터랙티브 미디어도 포함 |
| 화장품 | 매핑 없음 | NONE | 순수·유동성 충분한 미국 proxy 부재 |
| 음식료 | `PBJ` | DIRECT | 미국 음식료 기업 |
| 철강·비철·소재 | `SLX` | PARTIAL | 철강 중심, 비철/첨단소재 일부 미포함 |
| 건설·인프라 | `PAVE` | PARTIAL | 미국 인프라·장비·엔지니어링 |
| 통신·네트워크 | `IYZ` | DIRECT | 통신 서비스 + 통신 장비 |
| 게임 | `ESPO` | DIRECT | 글로벌 게임·eSports |
| 로봇·AI·자동화 | `BOTZ` | DIRECT | 로봇·AI·자동화 |
| 유통·소비 | `XRT` | DIRECT | 미국 retail |
| 에너지·화학 | `XLE` + `XLB` | PARTIAL | 에너지 + 소재 composite, 순수 화학 아님 |
| 운송·물류·여행 | `IYT` | PARTIAL | 항공·철도·트럭, 여행/해운 차이 |

공식 정의 근거 예:

- [Nasdaq SOX](https://indexes.nasdaq.com/Index/Overview/sox)
- [iShares ITA](https://www.ishares.com/us/products/239502/ishares-us-aerospace-defense-etf)
- [Global X LIT](https://www.globalxetfs.com/funds/LIT)
- [Global X URA](https://www.globalxetfs.com/funds/URA)
- [Global X BOTZ](https://www.globalxetfs.com/funds/BOTZ)
- [State Street XLF](https://www.ssga.com/us/en/individual/etfs/state-street-financial-select-sector-spdr-etf-xlf)
- [State Street XRT](https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-retail-etf-xrt)
- [iShares IYZ](https://www.ishares.com/us/products/239523/IYZ)
- [iShares IYT](https://www.ishares.com/us/products/239501/ishares-transportation-average-etf)
- [Global X PAVE](https://www.globalxetfs.com/funds/pave)

### 4.3 데이터 구조와 fetch

현재 `market_briefing.py`는 5개 ticker를 개별 `fast_info`로 조회한다. 테마 proxy를
15개 이상 추가해 순차 조회하면 지연과 rate-limit 위험이 커진다.

권고:

- 신규 `theme_briefing.py`로 기존 5개 macro 지표와 분리
- ticker를 dedupe한 뒤 `yfinance.download()` 일봉 batch 1회
- 최근 완결된 미국 session의 close/previous close로 등락률 계산
- cache TTL 12시간, 자동 polling 없음
- ticker별 `OK/MISSING/STALE/FETCH_ERROR`, 전체 graceful-fail
- 다중 proxy는 각 값을 그대로 표시하고 shadow에서만 고정 equal-weight composite 사용
- 데이터의 실제 미국 session date를 저장하고 서버 조회시각을 가격 기준일로 오인하지 않음

API:

```text
GET /api/briefing/themes
```

응답 예:

```json
{
  "session_date_us": "2026-07-02",
  "themes": [
    {
      "theme_id": "semiconductor",
      "label": "반도체",
      "mapping_quality": "DIRECT",
      "proxies": [
        {"ticker": "^SOX", "change_pct": 1.24, "status": "OK"}
      ]
    },
    {
      "theme_id": "shipbuilding",
      "label": "조선·기자재",
      "mapping_quality": "NONE",
      "proxies": [],
      "status": "NO_MAPPING"
    }
  ],
  "used_for_order": false,
  "used_for_theme_toggle": false,
  "is_live_authorization": false
}
```

UI는 현재 `BriefingBoard`의 세 번째 영역으로 추가한다. 20개 행이므로 기본은
6개만 보이고 “전체 테마 보기”로 펼친다. 각 행은 `테마 / proxy / 전일 등락 /
기준일 / 품질`만 표시한다.

## 5. 테마 shadow 기록

기존 `ShadowTrade`는 LIVE_SHADOW 주문 후보 ledger다. 테마 미국 선행값과 한국
일성과를 그 테이블에 넣으면 `audit_id`, 주문수량, would-have decision 의미가 깨진다.
따라서 별도 테이블을 사용한다.

### 5.1 테이블

`theme_us_shadow_signal` — 미국장 종료 후/한국장 전 append:

| 컬럼 | 의미 |
|---|---|
| `id` | PK |
| `target_trade_date_kst` | 연결할 한국 거래일 |
| `theme_id`, `taxonomy_version` | 분류 버전 |
| `mapping_version` | 미국 proxy 매핑 버전 |
| `proxy_payload` | ticker별 session date/value/return/status |
| `composite_return_pct` | 고정 equal-weight 결과, 없으면 NULL |
| `us_bucket` | `RISING/FLAT/FALLING/NO_DATA/NO_MAPPING` |
| `threshold_version` | 예: ±0.50%, `us-bucket-v1` |
| `recorded_at` | 수집시각 |
| `used_for_order` | 영구 false |

`theme_kr_daily_outcome` — 한국장 마감 후 append:

| 컬럼 | 의미 |
|---|---|
| `trade_date_kst`, `theme_id`, `taxonomy_version` | join key |
| `member_count`, `priced_count`, `coverage_pct` | 품질 |
| `close_to_close_ew_return_pct` | 주 성과 |
| `open_to_close_ew_return_pct` | 갭 이후 장중 성과 |
| `median_return_pct` | outlier 완화 |
| `positive_member_ratio` | 상승 종목 비율 |
| `source_status` | `COMPLETE/PARTIAL/UNAVAILABLE` |
| `recorded_at` | 기록시각 |
| `used_for_order` | 영구 false |

unique key:

- US: `(target_trade_date_kst, theme_id, mapping_version)`
- KR: `(trade_date_kst, theme_id, taxonomy_version)`

두 테이블 모두 재실행 시 중복 insert를 막고, 기존 row를 거래 입력으로 읽지 않는다.

### 5.2 판정과 성과 정의

미국 bucket:

- `RISING`: composite return ≥ +0.50%
- `FALLING`: composite return ≤ -0.50%
- `FLAT`: 그 사이
- 데이터 부족: `NO_DATA`
- proxy 없음: `NO_MAPPING`

이는 측정용 구간화이며 추천/진입 차단 의미가 없다.

한국 실성과:

- 같은 테마 모든 가용 종목을 equal-weight
- 다중 태그 종목은 각 소속 테마에 각각 포함
- primary metric: 전일 종가→당일 종가
- secondary metric: 당일 시가→당일 종가
- 가격 coverage < 80%면 `PARTIAL`, 분석 기본 표본에서 제외
- 상·하한가/거래정지는 제거하지 않고 별도 data flag로 기록

### 5.3 4주 후 검증

최소 15개 paired session과 coverage 80% 이상일 때만 판정한다.

테마별:

1. 미국 composite와 한국 close-to-close 수익률의 Spearman correlation
2. `RISING/FALLING`과 한국 수익률 부호 일치율
3. bucket별 한국 평균·중앙값 수익률
4. open gap과 open-to-close 분해
5. 표본 수, missing률, stale률
6. bootstrap 95% CI

결과 label:

- `INSUFFICIENT`: 표본/coverage 부족
- `NO_RELATION`: CI가 0을 포함하거나 효과 미미
- `WEAK_RELATION`: 방향은 있으나 불안정
- `STABLE_RELATION`: 사전 정의 기준을 반복 충족

어떤 label도 자동 토글·주문·수량 조정으로 연결하지 않는다. 후속 자동화는 별도 승인,
별도 shadow 재검증, 별도 PR 대상이다.

### 5.4 실행 위치

- 아침 collector: 기존 background driver의 주문 tick과 분리된 일 1회 job
- 장 마감 outcome collector: 시장 종료 후 일 1회
- 실패는 다음 거래 tick이나 주문 loop를 중단하지 않음
- 보호 4계층 import 0
- 조회 API: `GET /api/theme-shadow/daily`, `GET /api/theme-shadow/summary?weeks=4`

필수 invariant:

1. `used_for_order=false`
2. `used_for_theme_toggle=false`
3. broker/order/risk/permission import 0
4. theme shadow 실패가 scan/청산을 중단하지 않음
5. 미국 `FALLING`이어도 거래는 평소대로

## 6. 회귀 위험과 검증

| 위험 | 방지 |
|---|---|
| taxonomy 변경으로 기존 OFF가 조용히 풀림 | legacy broad OFF를 child OFF로 보수적 이전 |
| 다중 태그로 차단 범위 급증 | UI에 unique 차단 종목 수와 중복 수 표시 |
| 미국 proxy를 추천처럼 해석 | 사실값·품질·기준일만 표시, 판단 문구 금지 |
| 미국 휴장/한국 개장 날짜 오연결 | `target_trade_date_kst` calendar resolver + session date 저장 |
| yfinance 다중 호출 폭증 | batch fetch + 12h TTL + polling 0 |
| shadow가 주문 경로에 유입 | 별도 모듈/테이블, 정적 import guard |
| 한국 테마 성과의 대형주 편향 | equal-weight + median + positive ratio 병기 |
| catalog 이름 mojibake | 정상 UTF-8 KRX/FDR snapshot으로 재생성 |

필수 테스트:

- active TOP300 수와 순서 불변
- 300개 모두 1개 이상 태그, 기타 정확히 5
- 대표 golden mapping과 오분류 수정
- legacy OFF migration
- mapped/no-mapping/stale/holiday 응답
- UI에 추천·매수·제외 문구 0
- 미국 bucket별 거래 결과가 완전히 동일한 dry-run
- shadow 두 테이블 idempotency와 join date
- 기존 OFF 신규진입 차단 + held 손절 청산 회귀
- 보호 4계층 diff/import 0

## 7. 예상 변경 규모

| 영역 | 예상 |
|---|---:|
| taxonomy v2 데이터/생성 규칙 | 180~280 LOC + 300종목 data diff |
| catalog 검증/legacy OFF migration | 80~140 LOC |
| 테마 브리핑 batch fetch/API | 180~280 LOC |
| 브리핑 UI | 100~170 LOC |
| shadow 모델 2개 + migration 1개 | 100~160 LOC |
| 아침/장마감 collector + calendar join | 220~360 LOC |
| shadow 조회·4주 집계 API | 140~220 LOC |
| backend/frontend 테스트 | 450~700 LOC |

제품 코드 합계는 약 **820~1,330 LOC**, 테스트는 **450~700 LOC**, DB migration은
1개다. `RiskManager`, `PermissionGate`, `OrderExecutor`, `order_router` 변경은 0줄이다.

권장 구현 순서:

1. taxonomy v2 + TOP300 coverage/golden test
2. 기존 runtime OFF migration + 기존 필터/청산 회귀
3. 브리핑 batch fetch와 표시 전용 UI
4. shadow DB/collector
5. 4주 summary는 데이터가 쌓인 후 활성화
