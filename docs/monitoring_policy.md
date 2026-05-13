# Monitoring Policy — 체크리스트 #70

> 본 문서는 *시스템 안정성* 모니터링 정책이다. **수익률 모니터링이 아니다**.
> 장중 장애 조기 발견, API 오류 감지, 데이터 지연 감지, 주문 실패율 추적이
> 1차 목표다.

## 1. 목적

자동매매 시스템은 *돈이 걸려 있다*. 장중에 다음 같은 장애가 발생하면 운영자가
즉시 인지할 수 있어야 한다.

- 백엔드 프로세스 응답 불가 / DB 연결 끊김
- API 핸들러에서 5xx 비율 급증
- 시세 데이터 stale / WebSocket feed 끊김
- 주문 거부율 급증 (RiskManager / OrderGuard 차단)
- 결재 대기 큐 적체 (운영자 미인지)
- 긴급정지 토글 빈발 (flapping)

본 정책은 **수익률보다 시스템 안정성을 우선**한다. 모니터링이 알림을 보내거나
주문을 차단하지 *않는다* — 운영자가 인지하고 다음 행동(긴급정지, 결재, 알림
설정)을 결정한다.

## 2. 모니터링 대상

| 메트릭 | 출처 | OK 기준 | WARN | CRITICAL |
|---|---|---|---|---|
| `server` | process uptime | 항상 OK | — | — |
| `database` | `SELECT 1` ping | 성공 | — | 실패 |
| `api_error_rate` | `ApiMetricsRegistry` (in-memory ring) | < 5% | 5%~20% | ≥ 20% |
| `order_failure_rate` | `OrderAuditLog` REJECTED 비율, 24h, 최소 5건 | < 30% | 30%~60% | ≥ 60% |
| `approval_queue` | `PendingApproval` oldest age | 0건 또는 < 10분 | ≥ 10분 | ≥ 30분 |
| `risk_events` | `EmergencyStopEvent` 최근 60분 카운트 | < 3 | 3~7 | ≥ 8 |
| `data_freshness` | `freshness` 모듈 sample status carry | not stale | stale | (별도 평가 없음) |
| `notification` | `NotificationService.status()` carry | enabled & configured 또는 disabled | enabled & not configured | — |

표본 부족 / 측정 불가 시 `UNKNOWN`. UNKNOWN은 *시스템 OK*로 단정하지 않고
회색으로 표시한다.

## 3. 코드 구조

```text
backend/app/monitoring/
├─ __init__.py          # 패키지 invariants 명시
├─ types.py             # MetricStatus enum / Metric / AlertCandidate / MonitoringSnapshot
├─ api_metrics.py       # ApiMetricsRegistry (in-memory ring buffer)
├─ middleware.py        # ApiMetricsMiddleware (ASGI 응답 status_code 기록)
└─ service.py           # MonitoringService + notify_alerts helper

backend/app/api/
└─ routes_monitoring.py # /api/monitoring/{health,metrics,alerts}

frontend/src/
├─ store/useMonitoring.js
├─ components/tabs/MonitoringCard.jsx
└─ components/tabs/MonitoringCard.test.jsx
```

## 4. API

| Endpoint | 메서드 | 응답 | 비고 |
|---|---|---|---|
| `/api/monitoring/health` | GET | overall + metrics_summary + alert_count | 가벼운 liveness — uptime monitor / k8s probe용 |
| `/api/monitoring/metrics` | GET | 전체 `MonitoringSnapshot` JSON | 운영자 dashboard 표시용 |
| `/api/monitoring/alerts` | GET | WARN/CRITICAL alert candidates | *조회 only* — 송신 X |

세 endpoint 모두 **read-only**. broker 호출 / DB write / 안전 flag 변경 0건.

## 5. 알림 후보 기준

`MonitoringService._derive_alerts()`가 각 메트릭의 `status`를 보고 WARN /
CRITICAL인 경우 한 건씩 `AlertCandidate` 생성. OK / UNKNOWN은 후보 미생성.

### 알림 후보 → NotificationService 매핑

```python
kind_map = {
    "data_freshness":     NotificationKind.DATA_STALE,
    "approval_queue":     NotificationKind.APPROVAL_PENDING,
    "risk_events":        NotificationKind.RISK_AUDITOR_WARN,
    "order_failure_rate": NotificationKind.REPEATED_REJECTION,
    "api_error_rate":     NotificationKind.BROKER_ERROR,
    "database":           NotificationKind.BROKER_ERROR,
    "server":             NotificationKind.BROKER_ERROR,
    "notification":       NotificationKind.TEST,
}
```

`notify_alerts(service, alerts)` helper가 위 매핑으로 변환 후
`NotificationService.notify(event)` 위임. **호출자는 try/except 없이 사용 가능**
— helper가 raise하지 않음 (시스템 안정성 우선).

## 6. UI 표시

- **데스크탑**: `MonitoringCard`가 Dashboard 상단(MarketRegimeBadge 아래)에 노출.
  - Overall status badge (OK / WARN / CRITICAL / UNKNOWN) 색상 명확.
  - 8개 메트릭 행 (status badge + message).
  - 알림 후보 목록 (송신 버튼 없음 — *표시만*).
- **모바일**: 3개 요약 카드 (시스템 / 데이터 / 주문&리스크). 각 카드는 그룹
  내부의 가장 나쁜 status로 표시.

## 7. 절대 원칙 (코드 단 강제)

다음 invariant는 `tests/test_monitoring.py`의 정적 grep 가드로 강제된다.

1. `app/monitoring/*` 와 `app/api/routes_monitoring.py`는 broker / OrderExecutor /
   route_order import 0건.
2. monitoring 모듈은 DB write (INSERT/UPDATE/DELETE/db.add/db.commit/db.flush)
   0건.
3. `routes_monitoring.py`는 `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` /
   `ENABLE_FUTURES_LIVE_TRADING` / `emergency_stop` 변경 0건.
4. 응답에 Secret / API Key / Telegram Bot Token / 계좌번호 패턴 0건.
5. UI는 BUY/SELL/HOLD/긴급정지 토글/LIVE 활성화 버튼 0개.
6. `notify_alerts()` 는 raise 금지 — 알림 채널 실패해도 시스템 중단 X.

## 8. 임계치 변경 / 후속 PR

- 현재 임계치는 `MonitoringThresholds` dataclass의 코드 default.
- 환경변수 override (`MONITORING_API_ERROR_WARN` 등)는 #70 후속 PR.
- Prometheus / Grafana 통합도 후속 — 본 PR은 in-memory + JSON 응답만.

## 9. 운영 가이드

1. `/api/monitoring/health`를 외부 uptime monitor (Cronitor / UptimeRobot /
   k8s liveness probe)에 등록.
2. 모바일에서 Dashboard 첫 화면에서 모니터링 카드 색상으로 1차 인지.
3. CRITICAL 발견 시 다음 절차:
   - **database CRITICAL**: 백엔드 프로세스 / DB 파일 권한 / SQLite WAL 확인.
   - **api_error_rate CRITICAL**: `/api/audit/events` 로그에서 최근 에러 동시
     확인. broker / market provider down 가능성.
   - **order_failure_rate CRITICAL**: RiskManager / OrderGuard 차단 사유 확인.
     데이터 stale 또는 emergency_stop 켜져 있을 가능성.
   - **approval_queue CRITICAL**: 결재 탭에서 30분 이상 대기 row 처리.
   - **risk_events CRITICAL**: emergency_stop 토글 빈발 — 운영자 협의.

## 10. 참고

- 기존 데이터 freshness 모듈: [`market_data_freshness.md`](market_data_freshness.md) (있는 경우)
- 기존 audit log facade: [`audit_log_policy.md`](audit_log_policy.md)
- 기존 알림 정책: [`notification_policy.md`](notification_policy.md)
- 운영자 가이드: [`deployment_checklist.md`](deployment_checklist.md)
