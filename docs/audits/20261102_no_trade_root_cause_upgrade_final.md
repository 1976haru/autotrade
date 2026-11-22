# [20261102 NO TRADE ROOT CAUSE & UPGRADE FINAL]

감사 범위: 설치본 `2b6bb09`, 2026-11-02 05:00~15:45 KST. 운영 파일과 DB는 읽기 전용으로 조사했다. 기록이 없는 값은 `NOT_OBSERVABLE`로 표기한다.

## 최종 판정

- `NO_TRADE_EXPLAINED`
- `TRADE_GATE_BLOCKED`
- `REQUIRES_CODE_FIX` (종목당 배정액과 1회 주문 상한 불일치 및 진단 가시성)
- `STRATEGY_SIGNAL_MISSING`는 성립하지 않는다.

## 1. 오늘 거래 0건의 정확한 원인

Council BUY는 361건 발생했다. 357건은 `KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED`, 나머지 4건은 `KIS_PAPER_DRY_RUN_OK`였다. 운영값은 종목당 배정액 160만원(10:15 이전 기록에는 약 200만원 주문도 존재), KIS PAPER 1회 주문 상한 100만원, 주문 dry-run ON이었다. 따라서 신호 부재가 아니라 `금액 상한 불일치 + dry-run`이 실제 주문 0건의 직접 원인이다.

## 2~6. 감시·신호·차단 실측

| 항목 | 실측 |
|---|---:|
| 장전 워밍업 | 완료, 07:17:44 KST, 300/300 |
| 자동 감시 시작 | 09:00:00 KST |
| WAITING_MARKET → RUNNING | 확인 |
| 상태 머신 cycle | 545 |
| 정확한 scan tick | NOT_OBSERVABLE (설치본이 tick summary를 영속화하지 않음) |
| 실제 현재가 조회 | 2,040회, 고유 300종목 |
| 300종목 전체 순환 | 최소 6회 + 240종목; 정확한 논리 순환은 NOT_OBSERVABLE |
| Council BUY | 361건, 고유 125종목 |
| 주문 가능 판정 | 4건, 고유 1종목(402340) |
| 금액 상한 차단 | 357건, 고유 125종목 |
| dry-run 가상 주문 판정 | 4건 |
| 긴급정지 차단 | 0건 |
| PAUSED 차단 | 0건 |
| 체결 polling 미설정 차단 | 0건(단, polling 자체는 OFF) |
| 테마 제외 차단 | 0건(당일 유효 제외 0개) |
| 일일 주문 한도 차단 | 0건 |
| 현재가 API 오류로 평가 실패 | 14건(EGW00201, 14개 종목) |
| 실제 BUY / SELL / CANCEL 전송 | 0 / 0 / 0 |

DB `agent_decision_episode` 361행과 `order_audit_log` 0행, 일일 보고서의 BUY/SELL/CANCEL 0건을 교차 확인했다.

## 7~8. 오늘의 테마 필터

- Git `aedfd8a`에서 런타임 신규진입 필터, `f26ba66`에서 20개 분류, `65fc4b5`에서 홈 배치와 미국장 브리핑이 도입됐다. 삭제 이력은 없다.
- 현재 `kr-theme-v2`, 20개 테마, TOP402 402/402 매핑, 미매핑 0이다.
- 수동 ON/OFF, `today`/`until_enabled` 저장·만료, 차단 종목 수, 미국 ETF 프록시 등락 표시가 구현돼 있다.
- 적용 범위는 `NEW_ENTRY_ONLY`다. 보유종목 손절·익절·EOD 청산은 계속된다.
- 미국장 분석은 정보 표시 전용(`used_for_theme_toggle=false`)이며 자동으로 테마를 OFF하지 않는다.
- 최신 화면에서는 홈 좌측 접기 영역에 존재한다. 기능 복원용 별도 브랜치는 불필요하다.

## 9~10. GUI 진단 및 모드 분리

- 기존 종목별 차단 조회와 차단 사유 집계를 유지했다.
- 사용자 운영 모드를 `READ_ONLY`, `PAPER_DRY_RUN`, `PAPER_TRADING` 3개로 명시하고, 기존 내부 모드는 호환 필드로 유지했다.
- GUI에 운영 모드와 내부 모드를 함께 표시한다.
- `effective_gates`에 KIS PAPER 전용, dry-run, broker 주문 가능, smoke 제한을 노출한다.
- smoke는 별도 권한 상태가 아니라 제한된 `PAPER_TRADING` 하위형이다.
- 모드 판정은 긴급정지를 변경하지 않으며 실계좌 권한을 부여하지 않는다.

## 11. 테스트

- Backend: **8,785 PASS**, 25 slow deselected.
- Frontend: **3,376 PASS**.
- Rust: **3 PASS**.
- 집중 Mock: Backend 177 PASS, Frontend 13 PASS.
- 실제 KIS 주문 호출은 없었다.

## 12. KIS API 성능 및 오류

05:00~15:45 KST 실측 요청은 12,334건이다: HTTP 200 12,186, HTTP 500 148, 429 0, 503 0, 인증 오류 0. endpoint별로 분봉 8,956, 현재가 2,040, 잔고 1,225, 체결조회 108, 지수 4, tokenP 1이다. 로그가 명시적으로 EGW00201로 분류한 현재가 실패는 14건이다. 나머지 HTTP 500의 응답 분류는 `NOT_OBSERVABLE`이다.

tick P50/P95/P99와 자동 감속 동작 여부는 설치본에 tick duration/감속 이벤트가 영속화되지 않아 `NOT_OBSERVABLE`이다. 현재 backend working set은 약 367 MiB, private memory는 약 1.11 GiB이나 시계열이 없어 증가 추세는 `NOT_OBSERVABLE`이다.

## 13. 장마감·종료·원복

- 14:29:20 KST가 마지막 BUY episode이며 14:30 이후 신규 후보 주문 판정은 0건이다.
- 15:30:01 KST `RUNNING → MARKET_CLOSED` 확인.
- 15:40:23 KST 일일 보고서 생성 확인.
- EOD `FLAT_CONFIRMED`, 보유/미체결/청산 주문 0이다.
- 15:45 프로세스 정상 종료는 성립하지 않는다. UI 및 backend 프로세스는 05:37부터 계속 상주 중이며 강제 종료하지 않았다.
- 설정 자동 원복 이벤트는 NOT_OBSERVABLE. 프로그램이 설정을 바꾼 기록은 없다.

## 14~15. 운영 보호

이번 작업은 운영 `.env`, `runtime_overrides.json`, 운영 DB, 설치 EXE, 주문/취소를 변경하지 않았다. 설치 EXE 시각은 11월 1일 그대로다. 단, 운영 기록 자체에는 10:15 KST 긴급정지 `OFF` 이벤트와 runtime override 갱신이 존재한다. 이는 이번 감사가 수행한 변경이 아니며 사용자 요청의 `LEVEL_1 유지` 기대와 불일치한다. `.env`의 `WATCHDOG_ENABLED=true`도 요청 기대와 불일치한다. 승인 없이 수정하지 않았다.

## 16~17. 수정 브랜치·빌드

- 브랜치: `feat/kis-paper-intraday-flat-by-close-v1`
- 수정: 주문 수량을 `min(종목당 배정액, 1회 주문 상한)`으로 계산해 유효 신호가 구조적으로 금액 상한을 넘지 않게 함; 3개 운영 모드와 유효 게이트 표시.
- 최신 EXE 재빌드가 필요하다. 설치된 `2b6bb09`에는 이번 수정이 없다.

## 18. 실제 PAPER 시작 가능 판정

**현재는 시작 불가.** 이유: 설치본 미갱신, fill polling OFF, 긴급정지 상태가 요청 기대(LEVEL_1)와 불일치(현재 OFF), Watchdog 설정 기대 불일치, 실제 PAPER 전환 별도 승인 없음. 제안 한도(300/5/10/100만원/tick 1건/14:30/EOD/15:40)는 코드 기본·제안으로만 검증했으며 운영에 적용하지 않았다.

## 19. 남은 과제·예상 작업량

새 EXE 빌드 및 설치 전 Mock smoke, 운영 안전상태 재확인, 사용자 승인 후 설정 적용까지 약 1~2시간. 정확한 tick latency/순환시간/메모리 추세를 다음 거래일에 남기려면 tick summary 영속화가 추가로 필요하다(약 2~4시간).

## 20. 다음 행동 딱 1개

**이번 커밋으로 새 EXE를 빌드하기 전에, 사용자에게 PAPER_TRADING 운영 설정 적용 및 긴급정지 정책 상태 재설정 승인을 요청한다.**
