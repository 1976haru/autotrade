# 설계 — 트레일링 스탑 (이익 보호 청산)

> **상태: 설계만. 구현 코드 0줄.** 보호 파일(driver_bridge 판단부 / agent_council / 주문경로)
> 읽기만, 변경 0줄. 봇 미접촉. 구현은 별도 승인.
> 작성 2026-06-15 (Opus 4.8). 기존 트레일링 *수학*은 `position_context.py` 에 이미 존재 —
> 본 설계는 그것을 **라이브에 연결**(상태 영속 + 스캔 wiring)하는 부분만 다룬다.

---

## 0. 핵심 개념 + 예시

**문제**: 현재 하드 익절(+3%)은 +3% 도달 즉시 전량 매도 → 그 뒤 +8%까지 올라도 못 먹는다.
**트레일링 스탑**: +3%에서 *파는 대신* 익절을 **활성화(arm)** 하고, 이후 **장중 최고가를 추적**하다가
최고가 대비 **트레일링 폭(예 2%)만큼 꺾이면** 청산 → 추세를 더 타되 이익을 보호한다.

```
예시 (활성선 +3% / 트레일링 2% / 진입가 100,000):
  100,000 매수
  → 103,000 (+3%)   : 트레일링 활성. 최고가 추적 시작. (하드 익절이면 여기서 매도)
  → 108,000 (+8%)   : 최고가 갱신 108,000. 트레일가 = 108,000 × 0.98 = 105,840
  → 110,000 (+10%)  : 최고가 110,000. 트레일가 = 107,800
  → 107,000         : 110,000 대비 −2.7% 꺾임 < 107,800 → ★TRAILING_STOP 청산 (+7% 확보)
  하드 익절(+3%)이었으면 +3%만 먹었을 것. 트레일링은 +7% 확보.
```

핵심: **이익 *덜* 먹을 수는 있어도(최고점 −2%) 손실은 안 커진다** — 하드 손절(−1.5%)이 독립적으로 하방을 막는다.

---

## 1. 매 틱 로직 (고정 손절 독립 + 트레일링 익절)

매 스캔 틱, 보유 종목마다 (우선순위 = `position_context.infer_position_sell_reason`):

```
1) 고정 손절 (STOP_LOSS, 최우선·독립):
     cur ≤ entry × (1 − stop_loss_pct)   → SELL_STOP_LOSS
   ★트레일링과 무관하게 항상 평가. 트레일링이 켜져도 손절은 그대로.

2) 트레일링 익절 (TRAILING_STOP, 익절 모드일 때):
     a. 활성 판정: 최고가(hwm) > entry × (1 + activation_pct)   (예 +3% 도달했었나)
        - 미활성(아직 +3% 못 봄): 트레일링 미발동 → HOLD/전략판단
     b. 최고가 갱신: hwm = max(hwm, cur)   ← 매 틱 영속 저장 (§3)
     c. 트레일가: trail_price = hwm × (1 − trailing_pct)
     d. cur ≤ trail_price   → SELL_TRAILING_STOP

3) (장마감 강제청산 MARKET_CLOSE_EXIT — 기존, 별개)
```

**모드 토글**:
- `trailing` OFF (기본): 현재처럼 하드 익절(+3% 즉시 매도). `take_profit` 발동.
- `trailing` ON: 하드 익절을 **트레일링으로 대체**(+3% 활성 → 추적 → 2% 꺾임 청산). `take_profit`은 미발동, `TRAILING_STOP`이 익절 역할.
- → **익절 *방식*만 토글. 손절은 어느 모드든 −1.5% 고정 독립.**

기존 우선순위 `STOP_LOSS > TAKE_PROFIT > TRAILING_STOP`은 그대로 — 손절이 항상 이긴다.

---

## 2. ★상태 관리 — 최고가 영속 + 재시작 복구 + 정리 (가장 까다로운 부분)

트레일링은 **상태(최고가)가 있는** 유일한 청산 트리거다. 무상태인 손절/하드익절과 달리, hwm을
틱마다 갱신·**영속**해야 하고 watchdog 재시작에도 살아남아야 한다.

### 2-1. 영속 저장 방식 — **전용 DB 테이블** (권장)

```
table position_high_watermark:
  symbol            TEXT PK     -- 보유 종목
  entry_price       INT         -- 진입가(활성선 계산 기준)
  high_watermark    INT         -- 장중 최고가(매 틱 max 갱신)
  activated         BOOL        -- +activation_pct 도달해 트레일링 armed 됐나
  updated_at        DATETIME    -- 최근 갱신(신선도)
```
- **왜 테이블인가**: ① 원자적 upsert(틱마다 max 갱신) ② 재시작에도 *디스크 잔존* ③ 종목별 1행 ④
  runtime_overrides.json(설정)과 성격 분리(상태 ≠ 설정). 파일(JSON) 대안도 가능하나 동시성·원자성은 DB 우위.
- 매 틱: `hwm = max(stored.high_watermark, cur)` upsert. `activated`는 한 번 True 되면 유지(래칫).
- read-only 아님(write) — 단 *상태 기록 전용*, 주문/판단 로직 아님. (이력 추적용 별도 모듈.)

### 2-2. ★재시작 복구 (watchdog 재기동 대비)

backend 가 재시작하면 인메모리는 날아가지만 테이블은 남는다. 복구 절차:
```
on 보유 종목 스캔 (재시작 후 첫 틱):
  row = SELECT position_high_watermark WHERE symbol = ?
  if row 존재 and 신선(updated_at 오늘):
      hwm = max(row.high_watermark, cur)        -- 저장값 + 현재가
  else:                                          -- 행 없음/손상/전일치
      hwm = max(cur, entry × (1 + activation_pct))   -- ★폴백: 활성선(+3%)으로 재시드
      activated = (cur ≥ entry × (1 + activation_pct))
```
- **★폴백 = `max(현재가, 진입가 × 1.03)`**: 최고가 기록을 잃어도 **활성선 아래로는 안 내려간다**.
  보수적으로 재시드 → 재시작 직후 노이즈로 즉시 트레일링 청산되는 것을 막고(현재가가 활성선 위면 그 값),
  활성선 미달이면 아직 미활성으로 시작. **"기록 손실 = 손실 확대" 절대 아님**(손절은 독립).

### 2-3. 청산 시 정리

- 종목이 **SELL 체결(사유 무관)** 되면 → `DELETE position_high_watermark WHERE symbol = ?`.
  → 같은 종목 재매수 시 hwm이 새 진입가 기준으로 깨끗이 다시 시작(이전 추적 잔재 0).
- 장 마감 / 전일 행은 다음날 첫 스캔에서 stale 로 폴백 재시드(2-2).

---

## 3. 파라미터 (런타임 스테퍼)

| 파라미터 | 기본 | 범위 | 의미 |
|---|---|---|---|
| `trailing_enabled` | **false** | on/off | 익절 방식 토글(off=하드익절, on=트레일링) |
| `trailing_activation_pct` | **3.0%** | 1~10% | 트레일링 arm 되는 수익선(= 기존 익절선) |
| `trailing_pct` | **2.0%** | 1~5% | 최고가 대비 이만큼 꺾이면 청산 (휩쏘 주의 §6) |
| `stop_loss_pct` | **1.5%** | (기존) | 고정 손절 — 트레일링과 **독립·무관** |

- 런타임 오버라이드(`runtime_config.py`)에 `trailing_enabled`/`trailing_activation_pct`/`trailing_pct`
  화이트리스트 추가 → 홈 "설정 바꾸기" 스테퍼(C1/T2 패턴, effective getter). 봇이 매 사이클 fresh read.
- `position_context`는 이미 `trailing_stop_pct` 입력을 받는다 → 스캔이 effective 값으로 채우면 됨.

---

## 4. 안전 속성 (★"이익 덜 먹음"이지 "손실 커짐" 아님)

1. **익절만 건드린다**: 트레일링은 하드 익절을 *대체*할 뿐, **고정 손절(−1.5%)은 어느 모드든 독립·불변**.
   `infer_position_sell_reason` 우선순위 `STOP_LOSS > TAKE_PROFIT > TRAILING_STOP` — 손절이 항상 우선.
2. **활성은 수익 구간에서만**: hwm > entry(= +activation 도달) 일 때만 트레일링 armed
   (`resolve_trailing_stop_price`가 `TRAILING_STOP_NOT_IN_PROFIT`로 손실 구간 차단). 즉 **손실 중엔 트레일링이 개입 안 함** → 손절만 작동.
3. **최악의 경우**: 최고점에서 trailing_pct(2%) 만큼 덜 먹는다 → **기회이익 감소**이지 **손실 확대 아님**.
   재시작·기록손실 시에도 폴백이 활성선 아래로 안 내려가 손실 확대 경로 0.
4. **역방향 안전**: S1 검증과 동일 — 밴드 내/손실 구간 정상보유를 트레일링이 잘못 청산하지 않는다(활성 전엔 미발동).
5. **긴급정지 우선**: 트레일링 SELL도 route_order→RiskManager 긴급정지에 막힌다(기존 경로).

---

## 5. 3단계 도입

| 단계 | 내용 | 거래 영향 |
|---|---|---|
| **1. 측정(measure)** | hwm 영속 추적만. "트레일링이었다면 언제·얼마에 청산됐을지" vs 실제 하드익절 결과를 *로그로만* 비교. | 0 (하드익절 그대로) |
| **2. 섀도(shadow)** | 트레일링 SELL 결정을 *계산*하되 실행 안 함(shadow log). 하드익절 대비 누적 성과 비교(트레일링이 더 버는가?). | 0 |
| **3. 실거래(live)** | `trailing_enabled=true` → 트레일링이 하드익절 대체, 실제 청산. 운영자 토글 + 롤백. | 익절 방식 변경 |

→ 1·2단계로 **휩쏘·튜닝을 실데이터로 검증** 후 3단계. 손절은 전 단계 불변.

---

## 6. ★휩쏘 / 튜닝 주의

- **트레일링 폭이 너무 좁으면(예 0.5%)**: 정상 노이즈(장중 출렁임)에 즉시 청산 → **휩쏘**(추세 못 탐, 잦은 청산 + 비용↑).
- **너무 넓으면(예 5%)**: 최고점에서 많이 게워냄(이익 보호 약함).
- **시작값 2%**: KOSPI 대형주 장중 변동성 고려한 보수적 출발. 1·2단계 측정으로 종목군별 최적폭 튜닝.
- 비용 인식: 트레일링은 하드익절보다 **청산이 늦다**(추세 끝까지) → 큰 추세엔 유리, 횡보장엔 휩쏘로 불리. 측정으로 시장국면별 효과 확인.

---

## 7. 구현 STOP 지점 (착수 시)

| 위치 | 작업 | STOP? |
|---|---|---|
| `app/positions/high_watermark.py` (신규) | hwm 영속 테이블 upsert/조회/정리 + 재시작 폴백 | 상태기록 계층 — 무승인 가능(신규, 주문/판단 아님) |
| `runtime_config.py` | `trailing_*` 화이트리스트 + effective getter | 런타임설정 — 무승인(C1/T2 선례) |
| **`driver_bridge` 스캔** | PositionContext에 `high_watermark`/`trailing_stop_pct` 세팅 + hwm 갱신·정리 호출. **최고가→sell-reason(TRAILING_STOP) 연결** | **★driver_bridge 판단부 → STOP·보고** (S1과 동일 경계) |
| `position_context.py` | **변경 0** — resolve_trailing_stop_price/infer 이미 구현됨 | 읽기만 |
| 프론트 | 트레일링 토글/스테퍼 + "추적 중 최고 +X%" 표시 | 표시 계층 — 무승인 |

**핵심**: 트레일링 *수학*은 이미 있다(position_context). 구현은 ① hwm 영속 + ② 스캔이 hwm을 채워
council에 넘기는 것(= S1의 PositionContext 확장). ②가 driver_bridge 판단부라 **STOP·보고 후 착수**.

**선행조건**: S1(position 전달) 배포·검증 완료(2026-06-15 ✓) — 트레일링은 그 PositionContext에 hwm만 더하는 증분.

---

## 8. 핵심 결정 요약

- **영속 저장**: 전용 DB 테이블 `position_high_watermark`(symbol PK, hwm/entry/activated/updated_at). 매 틱 max upsert, 청산 시 DELETE.
- **재시작 복구**: 테이블에서 로드. 없음/stale → **폴백 `max(현재가, 진입가×(1+activation))` = max(cur, entry×1.03)** — 활성선 아래로 안 내려감(손실 확대 0).
- **안전**: 익절 방식만 토글, 손절 독립 불변. 활성은 수익 구간만. 최악 = 이익 덜 먹음.
- **도입**: 측정 → 섀도 → 실거래. 폭 2% 시작, 측정으로 휩쏘 튜닝.
- **STOP**: hwm→sell-reason 연결(driver_bridge 판단부)은 보고 후.
