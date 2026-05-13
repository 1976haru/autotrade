# Notification 정책 (체크리스트 #64)

## 1. 목적

장중 즉시 대응이 필요한 위험 이벤트를 운영자에게 빠르게 알리는 것이 본
기능의 1차 목적이다. 1차 채널은 **Telegram Bot**이며 (BotFather 기반),
Email / Web Push / Slack 등 다른 채널은 보안·운영 검토 후 후속 PR에서
추가된다.

## 2. 절대 원칙 (CLAUDE.md)

| 원칙 | 강제 위치 |
|---|---|
| Telegram Bot Token / chat_id는 **backend/.env**에만 저장 | git / docs / frontend / 응답 어디에도 노출 0건. `.env.example`은 빈 placeholder만. `.gitignore`에 `.env` 포함 |
| frontend에 Token / chat_id 입력 UI를 만들지 *않는다* | `NotificationStatusCard.test.jsx`가 `<input>` / `<textarea>` / `<select>` 0개임을 invariant로 검증 |
| 알림 실패가 주문 / 리스크 판단을 깨뜨리지 *않는다* | `NotificationService.notify`는 절대 raise하지 않음. `TelegramChannel.send`는 모든 예외를 SendResult로 carry. emergency-stop API의 알림 hook은 추가로 try/except로 감쌈 |
| 알림 timeout / retry 제한 | Telegram 요청 timeout 5초(기본), retry 최대 1회. 합산 최대 10초 — backend 응답을 지연시키지 않음 |
| 위험 알림 우선, 주문 성공 알림 후순위 | CRITICAL > WARN > INFO > DEBUG. `min_severity=INFO` 기본. ORDER_SUCCESS는 미구현 (필요 시 INFO 또는 별도 채널) |
| NotificationEvent에 Secret 패턴 포함 시 ValueError | `__post_init__`에서 message에 `kis_app_key` / `anthropic_api_key` / `telegram_bot_token` / `bearer ` / `sk-` 등 감지하면 fail-closed |
| broker / OrderExecutor / route_order 호출 0건 | `notifications` 패키지 + `routes_notifications` 모두 broker import 없음 |
| LIVE / AI / FUTURES flag 변경 0건 | 본 PR은 알림 인프라만 |

## 3. 1차 채널 — Telegram

### 3.1 BotFather로 Bot 생성

1. Telegram에서 `@BotFather` 대화 → `/newbot`
2. Bot 이름 / username 입력 → token 발급 (예: `123456789:AAH-XXX...`)
3. token은 **즉시 backend/.env**에 입력. 사진 캡처 / Slack / 이메일 금지.

### 3.2 chat_id 확인

1. Bot과 대화방을 만들고 메시지 한 번 전송 (예: "test")
2. `https://api.telegram.org/bot<TOKEN>/getUpdates` 접속
3. JSON에서 `chat.id` (정수 또는 음수) 복사 → backend/.env에 입력

### 3.3 backend/.env 설정

```bash
NOTIFICATIONS_ENABLED=true
NOTIFICATIONS_MIN_SEVERITY=INFO          # DEBUG / INFO / WARN / CRITICAL
NOTIFICATIONS_DEDUPE_WINDOW_SECONDS=60   # 같은 dedupe_key는 60초 안 한 번만
NOTIFICATIONS_ALWAYS_SEND_CRITICAL=true  # CRITICAL은 dedupe 우회
TELEGRAM_BOT_TOKEN=123456789:AAH-XXX...
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_TIMEOUT_SECONDS=5.0
TELEGRAM_MAX_RETRIES=1
```

### 3.4 테스트

```bash
# backend 재시작 후
curl -X POST http://127.0.0.1:8000/api/notifications/test
```

또는 frontend StrategyRisk 탭의 `🔔 알림 설정` 카드 → **🧪 테스트 알림 보내기**.

## 4. 우선순위 (Severity)

| 등급 | 정수 | 사용 예 |
|---|---|---|
| DEBUG    | 0  | 개발/디버깅 (기본 미발송) |
| INFO     | 10 | emergency_stop 해제 / 정상 알림 |
| WARN     | 20 | data_stale / approval_pending / repeated_rejection / daily_loss 70%+ |
| CRITICAL | 30 | emergency_stop 활성 / broker_error / margin_risk 92%+ / daily_loss 90%+ |

**CRITICAL은 항상 발송** (`always_send_critical=true` 기본) — dedupe도 우회.
**WARN / INFO는 dedupe 적용** — 같은 `dedupe_key`가 60초 안에 다시 와도 한 번만.

## 5. 알림 대상

| Kind | Builder | 기본 Severity | dedupe_key 패턴 |
|---|---|---|---|
| `emergency_stop` | `build_emergency_stop_event` | CRITICAL(on) / INFO(off) | `emergency_stop:{enabled}:{level}:{reason}` |
| `data_stale` | `build_data_stale_event` | WARN | `data_stale:{symbol}:{minute}` |
| `approval_pending` | `build_approval_pending_event` | WARN | `approval_pending:{id}` |
| `daily_loss_warning` | `build_daily_loss_warning_event` | INFO / WARN(70%+) / CRITICAL(90%+) | `daily_loss_warning:{pct//10}` |
| `broker_error` | `build_broker_error_event` | CRITICAL | `broker_error:{broker}:{op}` |
| `repeated_rejection` | `build_repeated_rejection_event` | WARN | `repeated_rejection:{count//threshold}` |
| `margin_risk` | `build_margin_risk_event` | WARN / CRITICAL(distance≤3% or used≥90%) | `margin_risk:{used//5}` |
| `risk_auditor_warn` | `build_risk_auditor_event` | INFO/WARN/CRITICAL(audit_level별) | `risk_auditor:{level}:{score//10}` |
| `daily_report` | (후속) | INFO | (후속) |
| `order_success` | (미구현) | INFO | (기본 미발송) |
| `test` | NotificationEvent 직접 | INFO | (없음) |

## 6. Secret 관리

- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`는 `backend/.env`에만.
- `.env`는 `.gitignore`에 등록. `.env.example`은 placeholder(빈 값)만.
- frontend는 token / chat_id 입력 input을 노출하지 *않는다*. 운영자가 값을
  변경하려면 backend 서버에 SSH로 접근해서 `.env` 수정 후 backend 재시작.
- 응답 / 로그 / audit row 어디에도 token 값을 기록하지 *않는다*. `service.
  status()`는 boolean(channel_configured)만 노출.
- token이 message text에 우연히 포함되는 사고를 막기 위해 `NotificationEvent.
  __post_init__`가 `kis_app_key` / `anthropic_api_key` / `bearer ` / `sk-` /
  `telegram_bot_token` 같은 패턴을 검사해 ValueError로 raise.

## 7. 운영 / 보안 주의

1. **알림 실패가 핵심 흐름을 막지 않는다** — `notify()`는 절대 raise하지
   않으며, `emergency-stop` API의 hook은 추가로 try/except로 감싼다. 알림
   결과는 응답 status에 영향이 없다.
2. **dedupe로 폭주 방지** — 같은 `dedupe_key`는 `dedupe_window_seconds` 안에
   한 번만. CRITICAL은 always_send_critical=true이면 dedupe 우회 (운영자가
   놓치면 안 되는 사건).
3. **rate-limit** — Telegram Bot API는 30 msg/sec, 그룹은 1 msg/sec 제한.
   본 PR은 dedupe + timeout으로 1차 보호. 정밀한 rate-limit은 후속 (Redis).
4. **quiet hours** — 야간 알림 억제는 후속. 본 PR은 24/7 발송 (CRITICAL은
   항상 발송 정책).
5. **escalation policy** — 5분 안에 운영자 응답 없으면 secondary channel로
   전달하는 흐름은 후속.
6. **multi-instance dedupe** — 본 PR은 in-memory dict. 여러 backend 인스턴스
   동시 운영 시 같은 알림이 N번 갈 수 있음 — Redis 기반 dedupe는 후속.

## 8. API contract

### `GET /api/notifications/status`
*read-only*. token / chat_id 미포함. response 키: `enabled`,
`channel`, `channel_configured`, `telegram_configured`,
`min_severity`, `min_severity_name`, `dedupe_window_seconds`,
`always_send_critical`, `notice`.

### `POST /api/notifications/test`
실 발송 (NotificationsEnabled=true + Telegram 구성 시) 또는 noop skip.
응답 키: `ok`, `channel`, `skipped_reason`, `error`. token 미포함.

### `POST /api/notifications/mock-event`
운영자가 시나리오를 직접 흘려 발송 흐름을 검증. `dry_run=true`(기본)이면
NoOpChannel로 강제 — 외부 API 호출 0건. body에 `kind` + builder별 인자.

## 9. Frontend

`StrategyRisk` 탭에 `NotificationStatusCard`가 표시된다:
- 활성 여부 / 채널 / Telegram 구성 / min severity / dedupe window
- 알림 종류 chip 8개 (긴급정지 / 손실한도 / 승인대기 / 데이터 지연 / API
  장애 / 주문 반복 거부 / 선물 margin / Risk Auditor)
- 🔐 "Token은 backend/.env에만 저장됩니다" 안내 (항상 노출)
- 🧪 "테스트 알림 보내기" 버튼 (enabled + telegram_configured 일 때만 활성)
- 테스트 결과 표시

**Token / chat_id 입력 input은 없다** — 테스트로 `<input>` / `<textarea>` /
`<select>` 0개를 invariant 검증.

## 10. 후속 과제 (out-of-scope for #64)

- Email channel (SMTP / SES)
- Web Push (VAPID 키 관리 / 권한 모델 / 영구화 — PWA는 #63에 안내)
- Telegram rate limit (현재 dedupe + timeout만)
- Redis 기반 dedupe (멀티 인스턴스)
- quiet hours / 시간대별 발송 정책
- escalation policy (운영자 응답 없으면 secondary)
- daily report 알림 자동화 (#57과 연동)
- approval_pending 자동 트리거 (PendingApproval 생성 hook — 현재는 mock-event로만 검증)

## 11. 절대 invariant (변경 금지)

1. `NotificationService.notify`는 raise하지 *않는다*.
2. `TelegramChannel.send`는 raise하지 *않는다* — 모든 예외 SendResult.error로 carry.
3. emergency-stop API의 알림 hook은 try/except로 감싸 응답을 깨지 않는다.
4. frontend는 token / chat_id 입력 UI 0개.
5. API 응답 / SW 캐시 / audit row 어디에도 token / chat_id 값 포함 0건.
6. NotificationEvent.message는 Secret 패턴 검사 통과한 경우만 생성.
7. CRITICAL 알림은 dedupe 우회 (always_send_critical=true 기본).
8. 주문 성공 알림은 기본 미구현 (필요 시 INFO 또는 별도 채널).

## 12. 관련 PR / 체크리스트

- #34 RiskManager 표준 진입점 — emergency_stop hard short-circuit
- #37 3-Level Kill Switch — emergency-stop API + level
- #54 Risk Auditor Agent — RISK_AUDITOR_WARN 사후 연결 후속
- #57 Daily Report Agent — DAILY_REPORT 알림 후속
- #62 Risk Control Panel — emergency-stop UI
- #63 PWA — 알림 inline 통합은 후속, 본 PR은 backend → Telegram만
- #64 Notifications (본 PR)
