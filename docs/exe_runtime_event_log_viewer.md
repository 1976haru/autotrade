# EXE 오류/이벤트 로그 뷰어 (#56 / 7-04)

EXE 장중 문제가 발생했을 때 사용자가 원인을 빠르게 파악할 수 있도록
RuntimeEvent / AgentDecision(AI 판단) / KIS 주문 이벤트 / 오류를 **한 화면**에서
확인하는 read-only 로그 뷰어. 최근 100건, 필터, 복사 제공. **secret / account /
API key 원문은 절대 표시하지 않는다** (free-text 자동 마스킹).

## 구성

- **Backend endpoint**: `GET /api/system/logs?source=&severity=&q=&limit=`
  (`app/system/log_viewer.py::collect_recent_logs`) — RuntimeEvent +
  AgentDecisionEpisode + OrderAuditLog 을 단일 모양으로 병합.
- **UI**: Settings 탭 `RuntimeEventLogViewer` — source/severity 필터 + 검색 +
  새로고침 + 복사.

## 표준 로그 항목

```
{ timestamp, source, severity, reason_code, message,
  symbol, action, broker_order_no, episode_id }
```

- **source**: `RUNTIME_EVENT`(backend runtime/error/sidecar event) /
  `AGENT_DECISION`(AI 판단 episode: action·reason_code·episode_id) /
  `KIS_ORDER`(OrderAuditLog: side·decision·broker_order_no).
- **severity**: `DEBUG / INFO / WARN / ERROR / CRITICAL`. exception/error 는
  RUNTIME_EVENT 의 ERROR/CRITICAL 로 표시.

## 민감정보 마스킹 (redaction)

backend 가 `message` / `reason_code` 같은 free-text 를 `redact_text()` 로
마스킹한다 — sk-/sk-ant-/ghp_/xox/Bearer/JWT/KIS app key/access_token=/
계좌번호(8-2)/신용카드/주민번호 패턴을 `[REDACTED]` 로 치환. 로그를 *드롭하지
않고* 값만 가린다. 구조화 식별자(`broker_order_no` / `episode_id` / `symbol` /
`action`)는 secret 이 아니므로 그대로 표시. UI 복사 직전에도 `containsSecretDeep`
로 2차 스캔해 적중 시 클립보드에 쓰지 않는다.

## 필터 / 기능

- **source 필터**: ALL / RUNTIME_EVENT / AGENT_DECISION / KIS_ORDER
- **severity 필터**: ALL / INFO / WARN / ERROR / CRITICAL
- **keyword 검색**: message / reason_code / symbol / action 대상
- **새로고침**, **복사**(sanitize 후, 한 줄 텍스트)
- 최근 **100건** 상한 (서버가 강제)

## 복사 형식 (예)

```
[2026-05-24T09:02:00+00:00] AGENT_DECISION INFO 005930 HOLD NO_SIGNAL ep=ep-1 - 조건에 맞는 신호 없음
```

복사 전 sanitize + `containsSecretDeep` 스캔 → secret/account 미포함.

## EXE 실행 후 확인 절차

1. EXE 실행, Settings(설정) 탭을 연다.
2. 하단 "📜 최근 이벤트 로그" 카드 확인.
3. **source 필터**로 RuntimeEvent / AI 판단 / KIS 주문 중 원하는 소스만 보기.
4. **severity 필터** ERROR/CRITICAL 로 오류만 보기 → backend 연결 실패,
   시장데이터 없음, KIS 자격 미설정, 주문 거절 원인 확인.
5. **검색**으로 사유 코드(예: `NO_MARKET_DATA`, `REJECTED`) 빠르게 필터.
6. **복사** 버튼으로 문제 상황을 클립보드에 담아 공유(민감정보 자동 제외).
7. 화면·복사 내용에 API key / Secret / 계좌번호 원문이 **없는지** 확인.

## 안전 invariant (테스트로 lock)

- read-only `systemLogs` GET 만 — 주문/POST/broker/OrderExecutor/route_order
  호출 0건.
- DB read-only SELECT 만 (write 0건).
- Secret/API key/계좌번호/access_token 원문 0건 (free-text 마스킹).
- 매수/매도/실거래/Place Order/**주문 재시도·재전송 버튼 0개**.
- 입력 form 은 검색 1개뿐 (주문 입력 0개).
- `is_live_authorization=false` / `contains_secret=false` 불변.

## 관련 파일

- `backend/app/system/log_viewer.py` (`collect_recent_logs`, `redact_text`)
- `backend/app/api/routes_system.py` (`GET /api/system/logs`)
- `backend/tests/test_runtime_event_log_viewer.py`
- `frontend/src/components/common/RuntimeEventLogViewer.jsx` (+`.test.jsx`)
- `frontend/src/components/tabs/Settings.jsx` (mount),
  `frontend/src/services/backend/client.js`
