# 손절 미작동(STOP_LOSS 0건) 수정 패치 — 1단계 설계 + diff 초안 (read-only)

> 작성 2026-06-25. **read-only 설계 단계 — 코드 0 변경.** 적용은 승인 후.
> ★청산/손절·게이트 로직이므로 *진단으로 확정된 결함만 최소 수정*, 새 동작 안 만듦.
> 진단 근거: 06-25 STOP_LOSS sell_reason **0건**, 보유 9종목 -1.3~-4.5% 미청산.

---

## 0. 확정된 인과사슬 (수정 대상)

```
KIS inquire-balance 가 보유분에 pchs_avg_pric=0 반환(간헐)
  └→ driver_bridge:723  if _avg>0 and _cur>0  → False
       └→ _position = None  (PositionContext 미생성)
            └→ agent_council:610  if position is not None and held_position → skip
                 └→ infer_position_sell_reason() 미호출 → 강제 STOP_LOSS 안 뜸 (결함 A)
                      └→ 청산은 vote(VWAP_BREAKDOWN)로만 발생, sell_reason≠STOP_LOSS
                           └→ auto_permission:234  is_risk_exit=False (STOP/TP 아님)
                                └→ confidence/quality 게이트 적용 → conf<0.6/qs<60 → 차단 (결함 B)
```
증거: 000990 14:17~14:36 SELL 4건 모두 `submitted=False`, `LOW_QUALITY_SCORE/LOW_CONFIDENCE`, 저장 position=**None**.

---

## 1. ★결함 A 수정안 — avg=0 이어도 강제손절 평가하게

### 핵심: avg_price 복구 (출처 우선순위)
`_kis_held_map`(driver_bridge:564-580)에서 KIS avg_price=0 인 보유분의 avg 를 복구한다. **3단 fallback**:

1. **last-known-good 스냅샷 avg (1순위, 가장 신뢰)** — `_HELD_SNAPSHOT`(이미 avg_price 저장, line 576)에서 *비-0* avg 가 있으면 사용. KIS 자신이 직전에 보고한 값 = 동일 출처, 정확. 대부분 틱은 avg 정상(positions/live 18:50 에 162800 정상 표시) → 간헐 0 만 메움.
2. **DB 체결단가 가중평균 (2순위)** — 스냅샷에도 없으면(예: 재기동 직후) `OrderAuditLog`(trade_reason='kis_paper_auto', executed, REJECTED 제외)의 BUY 체결가로 *가중평균* 산출. `_today_kis_paper_buy_state`(line 461)가 이미 `avg_fill_price or limit_price`·수량을 읽음 → 재사용. **단, 윈도우를 오늘→보유생성일까지 확장**해야 캐리오버(003230 06-23, 207940 06-15) 커버.
   - churn(매수-매도-재매수) 시: net 보유 수량에 대응하는 *가중평균 진입가* = Σ(BUY_qty×px) − (SELL 차감) 의 잔여 lot 가중평균. **근사치**(부분매도 시 어느 lot 잔존인지 모호) — 손절 트리거는 ±소폭 이동만 → 허용 가능.
3. **복구 실패 시 (avg 어디에도 없음)**: **현 보수적 동작 유지**(position=None, 강제손절 안 함) — 단 **결함 B 수정이 안전망**: vote 청산(하락 시 VWAP 등)이 게이트에 안 막혀 청산됨(defense-in-depth).

### ★fallback avg 신뢰성 / STOP 가드
- 1순위 스냅샷 avg = KIS 원값 → 신뢰. 2순위 DB avg = 실제 체결가 → 신뢰하나 churn 근사.
- **오발동 방지 가드**: 복구 avg 가 현재가와 *비현실적으로 괴리*(예: |avg−cur|/cur > 50%)면 그 avg 로 강제손절 **안 함**(데이터 오염 의심 → vote 경로로). → 틀린 가격 손절 위험 차단(STOP 조건).
- avg 출처를 `held_map[sym]["avg_price_source"]` 로 carry 해 audit/로그에 남김(추적성).

### diff 초안 (A) — `kis_paper/driver_bridge.py`
```python
# _kis_held_map 내부, avg 추출부 (line 575-576 교체)
-        _avg = int(getattr(p, "avg_price", 0) or 0)
-        out[sym] = {"hldg": hldg, "ord_psbl": max(0, ordp), "avg_price": _avg}
+        _avg = int(getattr(p, "avg_price", 0) or 0)
+        _avg_src = "kis"
+        if _avg <= 0:                                   # 결함 A: KIS avg=0 복구
+            snap = _HELD_SNAPSHOT.get(sym) or {}
+            _snap_avg = int(snap.get("avg_price", 0) or 0)
+            if _snap_avg > 0:
+                _avg, _avg_src = _snap_avg, "snapshot"   # 1순위: 직전 KIS 값
+            # 2순위(DB 가중평균)는 별도 헬퍼 _recover_avg_from_audit(sym) 로 — 호출부에서 주입
+        out[sym] = {"hldg": hldg, "ord_psbl": max(0, ordp),
+                    "avg_price": _avg, "avg_price_source": _avg_src}
```
```python
# driver_bridge:723 — 복구 avg 사용 + 괴리 가드 (강제손절 평가 진입 조건 완화)
-                if _avg > 0 and _cur > 0:
+                # 결함 A: 복구된 avg 도 허용. 단 비현실적 괴리(>50%)면 강제손절 보류.
+                _avg_ok = _avg > 0 and (abs(_avg - _cur) / _cur <= 0.5 if _cur > 0 else False)
+                if _avg_ok and _cur > 0:
```
> ★주의: **DB 가중평균(2순위)은 별도 함수로** 추가하고 호출부에서 db 주입(`_kis_held_map`은 broker만 받음). 1순위 스냅샷만으로도 간헐 avg=0 의 대부분을 커버 — 단계적 적용 권장(스냅샷 먼저, DB는 2차 PR).

---

## 2. ★결함 B 수정안 — 보유 청산 SELL 을 진입 게이트에서 면제

### 현황
`auto_executor.py:240-244` `is_risk_exit = SELL and sell_reason ∈ {STOP_LOSS, TAKE_PROFIT}`.
`auto_permission.py:234` `_quality_exempt = is_risk_exit and side=="SELL"` → **236-239(confidence/quality)만** 우회. **emergency_stop(211)·order window(220)·notional·dup 은 우회 안 함**(확인 완료).

### 수정: 면제를 *모든 보유 청산 SELL* 로 확대
```python
# auto_executor.py:240-244 교체
-        is_risk_exit=(
-            str(decision.side).upper() == "SELL"
-            and str(getattr(decision, "sell_reason_code", "") or "").upper()
-            in ("STOP_LOSS", "TAKE_PROFIT")
-        ),
+        # 결함 B: 보유 청산 SELL 은 *리스크 축소* → 진입용 confidence/quality 게이트 면제.
+        #   naked SELL 은 council(held_position=False→HOLD)·bridge(SELL_NO_HELD_POSITION)
+        #   에서 이미 차단되므로, 여기 면제는 *실보유 청산*에만 적용된다.
+        is_risk_exit=(
+            str(decision.side).upper() == "SELL"
+            and (
+                bool(getattr(decision, "held_position", False))
+                or str(getattr(decision, "sell_reason_code", "") or "").upper()
+                in ("STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP", "MARKET_CLOSE_EXIT")
+            )
+        ),
```
- `held_position` 은 `KisPaperAutoDecision`에 이미 carry 됨(council:281, executor DTO:79). build_permission_input 가 `decision.held_position` 읽기만 추가.

### ★오청산 위험 점검
| 위험 | 평가 |
|---|---|
| naked SELL(미보유 매도) 유발? | **불가** — council `held_position=False→HOLD`(agent_council:560-562) + bridge `SELL_NO_HELD_POSITION`(driver_bridge:788) 이중 차단. 면제는 게이트 통과만 완화, SELL 생성 안 함. |
| 보유보다 많이 팖? | **불가** — qty=min(hldg, ord_psbl)(driver_bridge:766) + council `position_quantity` cap. |
| emergency_stop/장외 시간에 청산? | **불가** — is_risk_exit 는 confidence/quality(236-239)만 우회. emergency_stop(211)·window(220) 그대로. |
| 약한 SELL vote 로 조기청산(미실현이익 포기)? | **잔존 위험(허용)** — 보유 청산은 손실/노출 *축소*. CLAUDE 원칙 "수익보다 손실방어 우선" 과 정합. 진짜 차단 대상은 *진입(BUY)* 의 무분별, 청산 아님. |
- **보수 옵션**: `held_position` 확대가 너무 넓다고 보면, reason_code 집합만 4종(+TRAILING_STOP, MARKET_CLOSE_EXIT)으로 확대하고 결함 A(STOP_LOSS 정상발동)에 의존. 단 A 복구 실패 케이스(avg 전무)는 못 구제 → **A+B(held_position) 병행이 defense-in-depth 로 권장**.

---

## 3. 부수 — sell_reason.py 필드명 정합 (잠재버그)

`sell_reason.py:190` `_stop_loss_triggered` 가 `pos.get("stop_loss_price")` 를 읽지만 `PositionContext.to_dict()` 키는 `resolved_stop_loss_price`(position_context.py:210). 현재는 `explicit_reason_code=pos_sell_reason`(agent_council:674)로 가려져 *비활성*이나, 라벨 정확도 위해 정합.
```python
# sell_reason.py:190
-    slp = _f(pos.get("stop_loss_price"))
+    slp = _f(pos.get("stop_loss_price") or pos.get("resolved_stop_loss_price"))
# (line 214 trailing 도 동일: trailing_stop_price or resolved_trailing_stop_price)
```
- 영향: 라벨링만(STOP_LOSS reason_code 정확). 트리거 동작 무변경. 저위험.

---

## 4. ★회귀 테스트 설계

신규 `tests/test_stoploss_avg_recovery.py` + 기존 council/permission 테스트 보강:

| # | 시나리오 | 기대 |
|---|---|---|
| T1 | avg=0 보유 + 스냅샷 avg=10000 + cur=9700(-3%) | PositionContext 생성 → infer **STOP_LOSS** → is_risk_exit True → **submitted** |
| T2 | avg=0 보유 + 스냅샷 없음 + DB BUY 가중평균=10000 + cur -3% | DB avg 복구 → STOP_LOSS 발동 |
| T3 | avg=0 + 복구 전무 + 하락 vote(VWAP) SELL | (B) held_position 면제 → 게이트 통과 **submitted** (defense-in-depth) |
| T4 | **정상 avg>0 보유 + cur -2%** | 기존대로 STOP_LOSS 발동 (**회귀 안 깨짐**) |
| T5 | 정상 avg>0 + cur +0.5%(미breach) | 강제손절 None + vote HOLD → 주문 0 (회귀: 약한 신호 주문 안 만듦) |
| T6 | 미보유(naked) SELL | council HOLD / bridge SELL_NO_HELD_POSITION → 차단(면제가 naked 안 뚫음) |
| T7 | emergency_stop ON + 청산 SELL | 여전히 차단(is_risk_exit 가 emergency 우회 안 함) |
| T8 | 복구 avg 가 cur 대비 >50% 괴리 | 강제손절 보류(오발동 가드) → vote 경로로 |
| T9 | sell_reason 라벨: STOP_LOSS 발동 시 reason_code=="STOP_LOSS" | 부수 필드명 정합 검증 |

---

## 5. ★보호계층 0줄 / 청산 안전 확인

- [x] **RiskManager / PermissionGate / OrderExecutor / route_order — 0 변경.** 면제(auto_permission)는 *사전 advisory 필터*이지 보호 4계층 아님. 면제된 SELL 도 route_order→RiskManager 재평가 그대로 통과.
- [x] **새 SELL 생성 0** — 수정은 (A) avg 복구로 *이미 보유한* 종목의 강제손절 *평가*를 살리고, (B) *council 이 이미 결정한* SELL 의 게이트 통과만 완화. 없던 매도를 만들지 않음.
- [x] **naked SELL 불가** — council+bridge 이중 차단 유지.
- [x] **과면제 차단** — is_risk_exit 는 confidence/quality 만 완화(검증: auto_permission:234-239), emergency_stop·window·notional·dup·daily-cap 불변.
- [x] **오발동 가드** — 복구 avg 의 >50% 괴리 시 강제손절 보류.
- [x] 주문 0건(설계 단계), broker 미접촉.

### STOP 점검
- 보호 4계층 핵심판단 변경 요구? → **아니오**(advisory 게이트·데이터층만). 통과.
- 청산 면제가 오청산? → naked/과매도/시간외 모두 기존 가드 유지, 잔존위험은 손실축소형. 통과(보수옵션 명시).
- avg fallback 틀린가격 손절? → 스냅샷(원값) 우선 + 괴리 가드. 통과.

---

## 6. 다음 단계 (승인 후)
1. **B(held_position 면제) + A 1순위(스냅샷 avg) 먼저** — 최소·고신뢰 수정. T1·T3·T4·T6·T7 테스트.
2. dry-run/단위테스트로 STOP_LOSS 정상 발동 + 회귀 확인.
3. **A 2순위(DB 가중평균) + 부수(필드명)** 는 2차 PR(윈도우 확장·churn 근사 검증 후).
4. 그 다음 실장중 검증.

**우선순위**: 결함 B(면제 확대)가 *가장 작고 안전하며 즉효* — 결함 A 가 미완이어도 vote 청산이 안 막혀 손실 포지션이 빠져나옴. A(avg 복구)는 강제 STOP_LOSS 를 *정확한 임계*로 되살리는 근본 수정. **둘 다 필요**(B=안전망, A=근본).
