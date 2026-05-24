# EXE Backend / Sidecar / 진단 상태 확인 (#53 / 7-01)

EXE 실행 시 사용자가 **정상/오류 상태를 혼동하지 않도록** Backend API 연결,
sidecar 실행, diagnostics, DB, KIS Paper 준비 상태를 **분리**해서 표시한다.
특히 "Backend 연결됨" 과 "연결 실패" 가 **동시에** 표시되는 모순을 코드 단에서
차단한다.

## 핵심 설계 — 모순 방지

- **단일 진실은 frontend 의 fetch 결과**다. `GET /api/system/exe-status` 가
  응답하면 `backend_api_reachable=true`, fetch 가 실패/timeout 이면 `false`.
- `backend_api_reachable=false` 이면 `normalizeExeStatus()` 가
  `diagnostics_status` / `db_status` / `kis_paper_readiness` 를 **모두
  `UNKNOWN`(확인 불가)** 으로 강등한다. → "연결 실패인데 진단 정상" 같은 모순이
  원천적으로 발생할 수 없다.
- 연결 상태 라벨("연결됨"/"연결 실패")은 **단일 boolean** 에서만 파생되므로
  두 라벨이 동시에 표시될 수 없다 (`exeStatus.test.js` 의 XOR 테스트로 lock).

## 상태 모델

| 필드 | 값 | 의미 |
|---|---|---|
| `backend_api_reachable` | true / false | Backend API health 응답 성공 여부 (frontend fetch 결과) |
| `sidecar_status` | RUNNING / STARTING / STOPPED / UNKNOWN | EXE sidecar 프로세스 상태 (web/dev=UNKNOWN) |
| `diagnostics_status` | OK / DEGRADED / FAIL / UNKNOWN | 진단 상태 |
| `db_status` | OK / FAIL / UNKNOWN | DB 준비 상태 |
| `kis_paper_readiness` | READY / BLOCKED / UNKNOWN | KIS 모의투자 preflight (안전 flag 기준) |
| `checked_at` | ISO8601 | 마지막 확인 시각 |
| `last_error_message` | string / null | 원인별 오류 메시지 |
| `is_live_authorization` | false (불변) | 실거래 권한 아님 |
| `contains_secret` | false (불변) | secret 미포함 |

## Backend endpoint

`GET /api/system/exe-status` (read-only)

```json
{
  "backend_api_reachable": true,
  "sidecar_status": "UNKNOWN",
  "diagnostics_status": "OK",
  "db_status": "OK",
  "kis_paper_readiness": "READY",
  "checked_at": "2026-05-24T00:00:00+00:00",
  "last_error_message": null,
  "is_live_authorization": false,
  "contains_secret": false
}
```

- broker / OrderExecutor / route_order 호출 0건, DB write 0건.
- 응답은 boolean / enum / timestamp 만 — Secret / API key / 계좌번호 원문 0건
  (모든 하위 평가가 boolean 만 산출).
- `sidecar_status` 는 backend 가 `AUTOTRADE_DESKTOP_SIDECAR` env 마커로 자체
  보고하며, frontend 가 desktop 감지 + reachable 로 추가 refine 한다.

## UI 위치

Settings 탭 상단 (버전 카드 바로 아래) — `BackendSidecarStatusCard`
(`data-testid="backend-sidecar-status-card"`).

표시 항목:

1. Backend API: "Backend API 연결됨" / "Backend API 연결 실패"
2. Sidecar: "Sidecar 실행 중" / "시작 중" / "중지" / "확인 불가"
3. Diagnostics: "진단 상태 정상" / "일부 문제" / "실패" / "확인 불가"
4. DB: "DB 정상" / "DB 실패" / "DB 확인 불가"
5. KIS Paper: "KIS 모의투자 준비 상태: READY / BLOCKED / 확인 불가"
6. 마지막 확인 시각 / 오류 메시지

연결 실패 시 단일 안내 배너: "Backend가 연결되지 않아 진단 상태를 확인할 수
없습니다." 가 표시되고 진단·DB·KIS 행은 모두 "확인 불가" 로 강등된다.

## EXE 실행 후 확인 절차

1. EXE 실행 후 Settings(설정) 탭을 연다.
2. 상단 "EXE 연결 상태" 카드에서 **Backend API 연결됨** 확인.
3. (데스크톱) **Sidecar 실행 중** 확인.
4. **Diagnostics 정상** 확인.
5. **DB 정상** 확인.
6. **KIS Paper: READY / BLOCKED** 확인 — BLOCKED 면 자격/안전 flag 점검
   (KisPaperEnvStatusCard 참고).
7. Backend 연결 실패 시: "Backend API 연결 실패" + "확인 불가" 표시가 정상
   동작인지 확인 (연결됨과 동시 표시되면 안 됨).
8. 화면에 API key / Secret / 계좌번호 원문이 **표시되지 않는지** 확인.
9. 장이 닫힌 날에는 데이터 관련 진단이 `MARKET_CLOSED` / `NO_MARKET_DATA`
   로 나오는 것이 정상일 수 있다 (연결 상태와는 별개).

## 안전 invariant (테스트로 lock)

- 연결됨 / 연결 실패 동시 표시 0건.
- 매수 / 매도 / 실거래 시작 / Place Order / ENABLE_* 토글 버튼 0개.
- 입력 form(input/textarea/select) 0개 — secret 입력/표시 surface 0건.
- broker / OrderExecutor / route_order 호출 0건, 안전 flag 변경 0건.

## 관련 파일

- backend: `backend/app/api/routes_system.py` (`GET /api/system/exe-status`)
- backend test: `backend/tests/test_exe_status_api.py`
- frontend helper: `frontend/src/utils/exeStatus.js` (+ `.test.js`)
- frontend card: `frontend/src/components/common/BackendSidecarStatusCard.jsx`
  (+ `.test.jsx`)
- mount: `frontend/src/components/tabs/Settings.jsx`
