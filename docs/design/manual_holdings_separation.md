# 설계 B — 수동 매수 + BOT/MANUAL 보유 분리

> **상태: 설계만. 구현 코드 0줄.** 보호 파일(driver_bridge 판단부)은 읽기만, 변경 0줄.
> 구현은 별도 승인 후. 작성 2026-06-13, Fable 5.

## 0. 목적

봇 단타와 **별개로**, 운영자가 직접 산 종목(호재·배당 장기보유)을 봇이 안 건드리게 격리한다.
- 봇: 5기법 council 단타(손절·익절·전략 청산).
- 수동: 운영자 직접 매수 → 봇의 손절·익절·전략 청산 **대상 아님**(직접 관리).
- 효과: 포트폴리오 분산 + 분산효과 측정(봇 운용 vs 직접 보유 수익률 분리 추적).

---

## 1. ★태깅 구조 — BOT vs MANUAL

### 1-1. 진실 소스 문제

KIS `get_positions` 는 **합산 보유만** 준다(봇·수동 구분 없음). 출처 구분은 **우리 DB**에서.

### 1-2. 태깅 필드: `OrderAuditLog.trade_reason` (기존 필드 재사용)

| 출처 | trade_reason | 현재 존재? |
|---|---|---|
| 봇 매수/매도 | `kis_paper_auto` | ✅ |
| 수동 매도(전량) | `manual_sell_all` | ✅ (오늘 NAVER 청산) |
| **수동 매수** | `manual_buy` | ❌ **신규 필요** |
| (수동 부분매도) | `manual_sell` | ❌ 신규 필요 |

> 이미 `_today_kis_paper_buy_state`(driver_bridge:370)가 `trade_reason == "kis_paper_auto"`
> 로 봇 보유를 *부분 격리* 중. 즉 **태깅 인프라는 이미 절반 존재** — MANUAL 태그만 추가.

### 1-3. 보유 출처 산출 (DB net 기반)

종목별 BOT-net 과 MANUAL-net 을 *별도 집계*:
```
bot_net(sym)    = Σ(BUY kis_paper_auto) − Σ(SELL kis_paper_auto)        [REJECTED 제외]
manual_net(sym) = Σ(BUY manual_buy)     − Σ(SELL manual_sell/all)        [REJECTED 제외]
kis_total(sym)  = get_positions 수량 (브로커 진실)
```

**정합성 불변식**:
```
bot_net(sym) + manual_net(sym) == kis_total(sym)     (이상적)
```
- 불일치 시(외부 앱에서 직접 거래·캐리오버 등) → **차액 = `UNTAGGED`**:
  `untagged(sym) = kis_total − bot_net − manual_net`.
  → **UNTAGGED 는 보수적으로 MANUAL 취급**(봇이 안 건드림). "출처 모르면 봇이 손대지 않는다"
  = 손실방어 안전측 기본값. (봇이 모르는 보유를 청산하는 사고 방지.)

### 1-4. 영속화

- 신규 테이블 0 — `OrderAuditLog.trade_reason` 만으로 산출(파생). 단 매 조회 집계 비용이 크면
  종목별 `holding_source` 캐시 뷰(읽기전용 집계) 추가 — *주문 기록이 진실*, 캐시는 파생.

---

## 2. ★봇 격리 — driver_bridge 보유 스캔에서 MANUAL 제외

### 2-1. 현재 흐름 (driver_bridge)

- `_kis_held_map(broker)` / `_kis_held_symbols` → KIS `get_positions`(합산) → `held_symbols`.
- 이 `held_symbols` 가 **청산 트리거 대상**(손절·익절·전략 SELL 은 보유 종목에만).
- 즉 *현재는 MANUAL 보유도 봇의 청산 대상* — 격리 필요.

### 2-2. 격리 설계 — 조회/캐시 계층에서 차감 (판단부 미접촉)

`_kis_held_map` 이 돌려주는 보유맵에서 **MANUAL/UNTAGGED 수량을 차감**해 *봇 관리 수량(BOT-managed)*만 남긴다:

```
bot_managed_qty(sym) = max(0, kis_total(sym) − manual_net(sym) − untagged(sym))
                     = max(0, bot_net(sym))        # 정합 시
held_map_for_bot     = { sym: {hldg: bot_managed_qty, ord_psbl: min(ord_psbl, bot_managed_qty)} }
```
- MANUAL-only 종목(bot_managed_qty=0) → `held_symbols_for_bot` 에서 **완전 제외** →
  손절·익절·전략 청산 트리거 안 됨. 봇은 그 종목을 "내 보유 아님"으로 인식.
- **이 차감은 `_kis_held_map`/`_kis_held_symbols`(조회/캐시 계층)에서 수행** — R-A 와 동일 계층.
  council·결정·가드(판단부)는 *차감된 봇-only 맵*을 받을 뿐, 로직 무변경.

> **STOP 경계**: 차감 *자체*는 조회 계층(무승인 가능, R-A·T2 선례). 단 `held_symbols` 가
> 흘러 들어가는 *가드/분기*(driver_bridge 판단부, line 562~612 일대)는 **읽기만** — 차감된 맵을
> 그대로 쓰면 분기 로직 0줄 변경. 만약 분기에 손대야 하면 그 지점만 STOP·보고.

### 2-3. ★R-A(보유 fallback) 상호작용 — 중요

R-A 는 `get_positions` 실패 시 *마지막 성공 스냅샷*(KIS 합산)으로 fallback 한다.
- **문제**: 스냅샷이 KIS *합산*이면 fallback 시 MANUAL 이 다시 섞임 → 봇이 MANUAL 청산 위험.
- **설계**: R-A 스냅샷을 **BOT-managed 차감 *후*의 맵**으로 저장(차감 → 스냅샷 순서).
  즉 스냅샷 자체가 "봇-only" 가 되어, fallback 시에도 MANUAL 안 섞임.
- 차감에 필요한 `manual_net`/`untagged` 는 *DB 집계*(get_positions 실패와 무관하게 항상 가능)
  → EGW 시에도 격리 유지. (DB는 로컬, KIS 호출 아님.)

---

## 3. 수동 매수 경로

### 3-1. 흐름

```
종목 검색(이름/코드) → 현재가 표시 → 수량 입력 → [직접 매수] 확인
  → route_order(source="manual", trade_reason="manual_buy")
  → RiskManager(긴급정지·PAPER 플래그·notional 한도만) → OrderExecutor → KIS 모의 BUY
```

### 3-2. 봇 제약 우회 / 안전장치 통과

| 게이트 | 수동 매수 |
|---|---|
| 봇 일일 매수 한도 / 동시진입 종목수 | **우회**(하루 직접 판단 — 봇 예산과 별개) |
| Agent Council(5기법 투표) | **우회**(전략 신호 무관 — 운영자 의도) |
| OrderGuard(중복·쿨타임) | 통과(수동도 중복 방지 적용 — 선택) |
| **긴급정지 (Kill Switch)** | **반드시 통과**(긴급정지 ON 이면 수동 매수도 차단) |
| **KIS_IS_PAPER / ENABLE_LIVE_TRADING** | **반드시 통과**(PAPER 강제 — 절대원칙 #3) |
| RiskManager notional/cash | 통과(계좌 잔고·1회 한도) |

> 수동 매도(`manual_sell_all`)가 이미 route_order 경유 → **수동 매수도 대칭**으로 route_order 경유.
> "AI 가 주문 API 직접 호출 안 한다"(절대원칙 #1)와 무관 — 운영자 명시 주문.

### 3-3. route_order 변경 범위

- `source="manual"` + `trade_reason="manual_buy"` carry. route_order 시그니처는 이미 source 받음
  (#40 OrderExecutor source carry) → **새 분기 불요, 태그 값만 추가**.
- **STOP 경계**: route_order 는 주문경로 4파일 → *읽기만 하고 변경 필요 시 STOP·보고*. 단 현재
  설계상 기존 source carry 재사용이라 **route_order 변경 0줄 예상**(태그는 호출자가 주입).

---

## 4. 화면 — 보유 2섹션

```
┌─ 🤖 봇 운용 (단타) ──────────────┐   ┌─ 👤 직접 보유 (장기) ──────────┐
│ 삼성전자  +1.2%  (봇 매수)        │   │ KT&G    +3.4%  (배당 보유)      │
│ SK하이닉스 −0.8% (손절선 -2%)     │   │ ...                            │
│ 소계: +0.4% / 평가 +12,000        │   │ 소계: +2.1% / 평가 +45,000     │
└──────────────────────────────────┘   └──────────────────────────────┘
   → 봇 손절·익절 대상              MANUAL: 봇 청산 대상 아님(직접 관리)
```
- 분류 소스: §1-3 의 `bot_net`/`manual_net`. (프론트 `classifyPositionSource` 의 BOT/EXISTING
  분류를 BOT/MANUAL/UNTAGGED 3-way 로 확장 — 표시 계층, 무승인.)
- 각 섹션 **수익률 분리 집계**(분산효과 측정). UNTAGGED 는 👤 섹션에 "(출처 미상)" 표기.

---

## 5. ★엣지 케이스 — 같은 종목을 봇·수동 둘 다 보유

가장 까다로운 부분. 명확한 규칙:

### 5-1. 수량은 태그별 독립 장부 (logical sub-ledger)

KIS 는 합산 100주만 알지만, 우리 DB 는 `bot_net=60 / manual_net=40` 로 분리 추적.
- 봇 청산 트리거는 **bot_managed_qty(=60)만** 대상 → 봇이 SELL 해도 60주까지만.
- 수동 매도는 manual_net(=40) 대상.

### 5-2. ★부분 매도 시 어느 태그에서 빠지나

**규칙: 태그별 FIFO + 주문 출처가 태그를 결정**(혼선 0):
- **봇 SELL**(`kis_paper_auto`) → **bot_net 에서만** 차감(FIFO: 봇이 산 lot 중 가장 오래된 것).
  manual_net 불변. → 봇은 자기가 산 것만 판다.
- **수동 SELL**(`manual_sell`) → **manual_net 에서만** 차감(FIFO: 수동 lot).
- 즉 **"판 주문의 출처 태그 = 빠지는 장부"**. 출처가 명확하므로 모호성 0.

### 5-3. KIS 수량 부족 충돌

봇이 60주 SELL 하려는데 KIS 합산이 50주뿐(외부 매도 등)이면?
- KIS `ord_psbl`(주문가능수량)로 캡(기존 R-A·S2 가드) → min(bot_managed, ord_psbl).
- 차액은 `untagged` 음수 → 정합성 경고 로그(운영자 확인). 봇은 보수적으로 **적은 쪽**만 주문.

### 5-4. 수동 매수가 봇 보유 위에 추가될 때

봇이 60주 보유 중 운영자가 같은 종목 40주 수동 매수 → bot_net=60, manual_net=40.
- 봇의 손절·익절은 **여전히 60주만** 대상(manual 40 은 봇 청산 제외).
- 중복매수 가드(`DUPLICATE_POSITION_BLOCKED`)는 *봇 관점*만 — 봇은 "이미 보유"로 신규매수 skip,
  수동은 가드 우회(운영자 의도). 단 manual_net 추가는 봇의 max_concurrent 카운트에 **미포함**
  (봇 슬롯과 별개).

---

## 6. driver_bridge 변경 범위 예측 (구현 시 STOP 지점)

| 위치 | 변경 | STOP? |
|---|---|---|
| `_today_kis_paper_buy_state` (370) | 이미 `kis_paper_auto` 필터 — manual_net/untagged 집계 헬퍼 *추가*(병렬 함수, 기존 무변경) | 조회 계층 — 무승인 |
| `_kis_held_map` / `_kis_held_symbols` (392/418) | BOT-managed 차감 로직 추가(MANUAL/UNTAGGED 빼기). **R-A 스냅샷도 차감 후 저장** | 조회/캐시 계층(R-A 선례) — **무승인 가능하나 손실방어 직결 → 보고 권장** |
| **scan 본문 가드 (562~612)** | `held_symbols`(차감된 봇-only) 그대로 사용 → **분기 로직 0줄 변경 목표**. 손대야 하면 그 지점만 STOP | **★읽기만 — 변경 필요 시 STOP·보고** |
| `route_order` 호출(수동 매수) | `source="manual"`, `trade_reason="manual_buy"` 태그 주입. route_order 자체 변경 0 예상 | **주문경로 — 읽기만, 변경 시 STOP** |
| `app/api/routes_*` (수동 매수 엔드포인트 신규) | 종목검색·매수. 긴급정지·PAPER 가드 통과 | 무승인 가능(신규 경로, route_order 경유) |
| 프론트 2섹션 + 수동매수 UI | 표시·입력 계층 | 무승인 가능 |

**필요 표본/선행조건**: 없음(표본 무관 — 기능 분리). 단 **태그 백필** 필요:
기존 보유(오늘까지의 캐리오버)는 trade_reason 이 없거나 kis_paper_auto → 과거분은 UNTAGGED 로
시작(보수적 MANUAL 취급) 또는 운영자가 1회 수동 분류. **신규 주문부터 정확 태깅.**

**선행 순서**:
1. `manual_buy`/`manual_sell` 태그 + route_order 태그 주입(수동 매수 경로) — 가장 작은 단위.
2. DB net 분리 집계(bot/manual/untagged) — 조회 계층.
3. `_kis_held_map` 차감 + R-A 스냅샷 차감 — **손실방어 직결, 보고 후**.
4. 프론트 2섹션 표시.
5. (마지막) scan 가드가 차감맵으로 정상동작 검증 — 분기 무변경 확인.

---

## 7. 미해결/오픈 질문 (구현 전 결정 필요)

1. 과거 캐리오버 보유의 태그: 일괄 UNTAGGED(보수적) vs 운영자 1회 분류? → **UNTAGGED 권장**(안전).
2. 수동 매수도 OrderGuard 중복/쿨타임 적용? → 적용 권장(오발주 방지), 단 봇 한도와는 별개.
3. UNTAGGED 를 MANUAL 로 볼지 BOT 로 볼지 → **MANUAL(봇이 안 건드림)** 확정 — 손실방어 안전측.
4. 수동 장기보유의 손절은 누가? → **운영자 직접**(봇 청산 제외가 목적). 단 "수동 보유 -X% 알림"
   advisory 는 제공 가능(주문 0).
