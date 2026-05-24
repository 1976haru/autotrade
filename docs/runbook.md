# Agent Trader 장애 대응 Runbook — 장중 오류별 원인/대응표

> 장중 오류가 보이면 이 문서를 펴고 **"먼저 볼 화면 → 의미 → 즉시 조치 →
> 전달할 정보"** 순서로 대응하세요. 코딩을 몰라도 따라 할 수 있습니다.
> 기본 사용법은 [`docs/user_manual.md`](user_manual.md) 를 먼저 보세요.

---

## 1. 이 문서의 목적

- 장중 KIS **모의투자** 자동매매 중 오류가 났을 때 **빠르게 원인과 조치**를
  찾도록 합니다.
- 오류별로 **확인 위치 · 즉시 조치 · Claude Code 에 전달할 정보**를 표준화합니다.
- 이 프로그램은 모의투자/Paper 검증용이며 **수익을 보장하지 않습니다.** 실전
  전환은 별도 승인이 필요합니다.

## 2. 가장 먼저 해야 할 5단계

장중 오류가 보이면 **먼저** 아래 순서로 확인하세요.

1. **Settings 탭 → "🖥️ EXE 연결 상태"** — Backend/Sidecar/DB 연결 확인.
2. **"🏷️ 앱 버전 / 빌드 정보"** — 실행 중인 commit 확인.
3. **"🩺 EXE Preflight Smoke Test"** — PASS/WARN/FAIL 확인(또는 재실행).
4. **"📜 최근 이벤트 로그"** — severity 를 ERROR/WARN 로 필터해 원인 확인.
5. **KIS 모의투자 화면(MTS/HTS)** — 주문번호 / 주문내역 / 체결 대조.

> 5단계 후에도 원인을 모르면 §16 **문제 보고 양식**으로 정리해 전달하세요.

## 3. 장중 즉시 중단해야 하는 상황

아래 중 **하나라도** 보이면 자동매매를 계속 지켜보지 말고 **즉시 중단/확인**하세요.

- 화면이나 로그에 **secret / 계좌번호 원문**이 보임.
- `ENABLE_LIVE_TRADING` 가 true 로 보임 (반드시 false 여야 함).
- `ENABLE_AI_EXECUTION` 가 true 로 보임 (반드시 false 여야 함).
- `KIS_IS_PAPER` 가 false 로 보임 (반드시 true 여야 함).
- KIS **모의가 아니라 실전 계좌** 주문내역에 주문이 보임.
- 보유 수량보다 많은 **SELL** 이 시도됨.
- `broker_order_type` 이 `KIS_PAPER` 가 아님.
- 같은 종목 **중복 BUY** 가 반복됨.
- 프로그램 포트폴리오 수량이 KIS 모의 계좌와 **크게 다름**.
- **주문 실패가 반복**됨.
- `DB_FAIL` 또는 `BACKEND_FAIL` 이 **반복**됨.

> 위 안전값(LIVE/AI/FUTURES 는 OFF, KIS_IS_PAPER 는 ON)을 직접 바꾸지 마세요.
> 이상이 보이면 설정을 만지기 전에 먼저 §16 양식으로 보고하세요.

## 4. 오류 확인 위치

| 화면 | 무엇을 보는가 |
|---|---|
| Settings → EXE 연결 상태 | Backend API / Sidecar / Diagnostics / DB / KIS Paper |
| Settings → 앱 버전/빌드 정보 | 버전 / commit / build time / dirty |
| Settings → Preflight Smoke Test | PASS/WARN/FAIL 항목별 상태 |
| Settings → 최근 이벤트 로그 | RuntimeEvent / AI 판단 / KIS 주문 (severity 필터) |
| Settings → KIS 모의 자동매매 설정 상태 | READY / BLOCKED, 자격 구성 여부 |
| Agent 탭 → Decision Episode | 판단 → 주문 → 체결 → 사유(reason_code) |
| Dashboard → Paper 포트폴리오 | 모의 현금 / 보유 종목 / 매수불가 사유 |
| KIS 모의투자 화면(MTS/HTS) | 주문내역 / 미체결 / 체결 / 잔고 |

## 5. Backend / Sidecar 장애

| 코드/증상 | 의미 | 먼저 볼 화면 | 즉시 조치 |
|---|---|---|---|
| `BACKEND_FAIL` | backend API 응답 실패 | EXE 연결 상태 / 이벤트 로그 | EXE 재시작 → 수십 초 대기 → Preflight 재실행 |
| `SIDECAR_STOPPED` | backend sidecar 프로세스 중지 | EXE 연결 상태 (Sidecar) | EXE 재시작, 로그 확인 |
| `DB_FAIL` | DB 연결/마이그레이션 문제 | Preflight (db_status) | Preflight 결과 복사 → 재시작 → DB 파일 위치 확인 |
| Backend "연결 실패" 지속 | sidecar 미기동/포트 충돌 | EXE 연결 상태 | EXE 완전 종료 후 재실행, 다른 인스턴스 종료 |

## 6. KIS 자격 / 설정 장애

| 코드/증상 | 의미 | 즉시 조치 |
|---|---|---|
| `CREDENTIALS_MISSING` | KIS 자격 4종 중 누락 | `backend/.env` 의 `KIS_APP_KEY`/`KIS_APP_SECRET`/`KIS_ACCOUNT_NO`/`KIS_PRODUCT_CODE` **존재 여부(present)** 만 확인 후 EXE 재시작 |
| `BLOCKED_BY_KIS_READINESS` | KIS 준비 상태 미충족 | Settings → KIS 상태 카드 / auto env 조합 확인 |
| KIS Paper "BLOCKED" | 자격 미구성 또는 flag 어긋남 | 자격 4종 / `KIS_IS_PAPER=true` / 안전 flag 확인 |

> **금지**: `KIS_APP_SECRET` / `KIS_ACCOUNT_NO` **원문을 복사해서 보내지 마세요.**
> "구성됨/미구성", "missing_credentials 키 이름" 만 전달하면 됩니다.

## 7. 시장 데이터 장애

| 코드/증상 | 의미 | 즉시 조치 |
|---|---|---|
| `NO_MARKET_DATA` | 시세 데이터가 들어오지 않음 | 장 시간 확인 → 네트워크 확인 → 시세 provider 설정 확인 |
| `MARKET_CLOSED` | 장 시간이 아님 | 장 열린 날 다시 확인 (**휴일/장마감엔 정상**) |
| `PRICE_STALE` | 현재가가 오래됨 | 시세 갱신/네트워크 확인, 잠시 후 재확인 |

> `MARKET_CLOSED` / `NO_MARKET_DATA` 는 장이 닫힌 날엔 **오류가 아니라 정상**입니다.

## 8. 주문 / 체결 장애

| 코드/증상 | 의미 | 즉시 조치 |
|---|---|---|
| `ORDER_REJECTED` | KIS 모의 주문 거절 | KIS 모의 화면 주문내역 + 이벤트 로그 확인 → 주문가능수량/가격/장상태 확인 |
| `KIS_PAPER_SUBMITTED` 인데 KIS 화면에 주문 없음 | 프로그램 기록과 KIS 화면 불일치 가능 | `broker_order_no` / `ODNO` / 로그 / KIS 화면 대조 |
| `FILL_POLLING_FAIL` | 체결 조회 실패 | KIS 모의 계좌 주문내역 직접 확인, fill polling 상태 확인 |
| `SUBMITTED` 인데 계속 미체결 | 접수됐지만 체결 안 됨 | KIS 화면에서 미체결 확인, 가격/호가 확인 |

## 9. 포트폴리오 / 잔고 불일치

| 코드/증상 | 의미 | 즉시 조치 |
|---|---|---|
| `PORTFOLIO_DRIFT` | 프로그램 포트폴리오 ↔ KIS 모의 계좌 불일치 | Paper 포트폴리오 카드 / KIS 잔고 / 체결 로그 비교 |
| 수량이 KIS 모의와 크게 다름 | 체결 누락/중복 의심 | §3 즉시 중단 기준 — 보고 후 확인 |

## 10. 리스크 차단 / 매수불가 사유 (대부분 정상 안전장치)

| 코드 | 의미 | 조치 |
|---|---|---|
| `NO_SIGNAL` | 조건에 맞는 신호 없음 | 정상일 수 있음 |
| `DUPLICATE_POSITION_BUY_BLOCKED` | 이미 보유 중 추가매수 차단 | 정상 안전장치 |
| `DAILY_BUY_LIMIT_EXCEEDED` | 일일 매수한도 초과 | Paper 자금/한도 설정 확인 |
| `INSUFFICIENT_PAPER_CASH` | Paper 현금 부족 | Paper 자금 설정 확인 |
| `RISK_FLAGS_EXCEEDED` | 위험 플래그 초과로 HOLD | 무리한 진입 차단 (정상일 수 있음) |
| `EXIT_PLAN_INVALID` | 손절/익절 계획 무효로 BUY 차단 | Decision Episode 의 `exit_plan` 확인 |

## 11. 이벤트 로그 확인 방법

1. Settings → "📜 최근 이벤트 로그" 카드.
2. **severity = ERROR / CRITICAL** 로 필터해 오류만 봅니다.
3. **source 필터**로 RuntimeEvent / AI 판단 / KIS 주문 분리.
4. **검색**으로 `NO_MARKET_DATA`, `ORDER_REJECTED` 등 사유 코드 빠르게 찾기.
5. **복사** 버튼으로 10~30줄 복사 — 민감정보는 자동으로 가려집니다.

## 12. Preflight Smoke Test 결과 해석

- **PASS** : 기본 작동 정상.
- **WARN** : 운영 가능. 장 닫힌 날 `MARKET_CLOSED` 등은 WARN 이 정상.
- **FAIL** : 항목 확인 필요. 특히 `safety_flags` / `db_status` FAIL 은 즉시 점검.

## 13. 업데이트 / 버전 문제

| 코드/증상 | 의미 | 즉시 조치 |
|---|---|---|
| `UPDATE_FAILED` | 업데이트 실패 | 현재 버전/commit 확인 → 수동 재설치 또는 이전 버전 확인 |
| `VERSION_MISMATCH` | frontend ↔ backend commit 불일치 | 최신 EXE 재설치 또는 빌드 재확인 |

## 14. 보안 / secret 노출 의심

| 코드/증상 | 의미 | 즉시 조치 |
|---|---|---|
| `SECRET_EXPOSURE_SUSPECTED` | 민감정보 노출 의심 | 캡처/공유 **전에** secret 을 가림 → 로그 공유 중단 → 원문이 어디 남았는지(파일/메신저) 삭제 확인 |

> 화면·로그에 secret/계좌 원문이 보이면 그 자체가 §3 즉시 중단 사유입니다.

## 15. 오류별 대응 표 (요약)

| 코드/증상 | 의미 | 먼저 볼 화면 | 즉시 조치 | 전달할 정보 |
|---|---|---|---|---|
| `NO_MARKET_DATA` | 시세 없음 | 이벤트 로그 / Preflight | 장 시간·네트워크·provider 확인 | 발생 시각·symbol·로그·Preflight |
| `MARKET_CLOSED` | 장 시간 아님 | 이벤트 로그 | 장 열린 날 재확인 (정상 가능) | 장 상태·시각 |
| `CREDENTIALS_MISSING` | 자격 누락 | KIS 상태 카드 | .env 4종 present 확인 | missing 키 이름(원문 금지) |
| `BLOCKED_BY_KIS_READINESS` | 준비 미충족 | KIS 상태 카드 | 자격/모의설정/auto env 확인 | KIS readiness·flag |
| `ORDER_REJECTED` | 주문 거절 | KIS 화면·로그 | 수량/가격/장상태 확인 | 사유·broker_order_no·시각 |
| `FILL_POLLING_FAIL` | 체결조회 실패 | KIS 주문내역 | 직접 체결 확인·polling 상태 | symbol·주문번호·로그 |
| `PORTFOLIO_DRIFT` | 잔고 불일치 | 포트폴리오·KIS 잔고 | 수량 비교 | 프로그램/ KIS 수량 차이 |
| `BACKEND_FAIL` | backend 실패 | EXE 연결 상태 | 재시작·Preflight | Preflight·로그·시각 |
| `SIDECAR_STOPPED` | sidecar 중지 | EXE 연결 상태 | 재시작 | 로그·시각 |
| `DB_FAIL` | DB 문제 | Preflight | 재시작·DB 위치 확인 | Preflight db_status |
| `PRICE_STALE` | 현재가 오래됨 | 이벤트 로그 | 시세 갱신 확인 | symbol·시각 |
| `UPDATE_FAILED` | 업데이트 실패 | 앱 버전 카드 | 재설치/롤백 확인 | 현재 commit |
| `VERSION_MISMATCH` | commit 불일치 | 앱 버전 카드 | 최신 재설치 | FE/BE commit |
| `SECRET_EXPOSURE_SUSPECTED` | 노출 의심 | (해당 화면) | 가림·공유 중단·삭제 | **원문 금지** |

## 16. 문제 보고 양식

아래를 복사해 채운 뒤 전달하세요. **secret / 계좌번호 / token 은 절대
붙여넣지 마세요.**

```
[장애 보고 양식]
- 발생 시각:
- 장 상태: 장중 / 장마감 / 휴일 / 모름
- 실행 EXE commit:
- 앱 버전:
- Settings EXE 연결 상태:
- KIS Paper readiness(READY/BLOCKED):
- Preflight 결과(PASS/WARN/FAIL):
- 오류 코드:
- 관련 종목:
- broker_order_no / ODNO:
- KIS 모의 화면 주문 여부:
- 최근 이벤트 로그(10~30줄, 복사 버튼 사용):
- Decision Episode 내용:
- 내가 누른 버튼 / 한 행동:
- secret/account 원문 포함 여부: 포함하지 않음
```

## 17. Claude Code 에 전달하면 안 되는 정보

**절대 전달 금지** (이것들이 보이면 가리고 보내세요):

- `KIS_APP_SECRET` 원문 / `KIS_APP_KEY` 원문 / `KIS_ACCOUNT_NO` 원문
- access token / refresh token / Bearer token
- 주민등록번호 / 실제 계좌번호 / 비밀번호
- 원본 `.env` 파일 전체 내용

**전달해도 되는 것**:

- 자격 구성 여부(present = true / false), `missing_credentials` 의 **키 이름**
- `reason_code`, 사유 메시지(마스킹된 것)
- `broker_order_no` (KIS **모의** 주문번호인지 확인)
- 앱 commit / 버전
- Preflight PASS / WARN / FAIL
- redaction(마스킹)된 이벤트 로그

## 18. 최종 복구 후 확인 체크리스트

- [ ] Backend API "연결됨"
- [ ] Sidecar "실행 중"
- [ ] DB OK
- [ ] KIS Paper READY (또는 사유가 명확한 BLOCKED)
- [ ] secret / 계좌번호 미노출
- [ ] 실거래 OFF (LIVE 플래그는 OFF 유지)
- [ ] `KIS_IS_PAPER` true
- [ ] Preflight FAIL 없음
- [ ] 최근 이벤트 로그에 같은 ERROR 반복 없음
- [ ] KIS 모의 화면과 프로그램 상태 일치

---

### 관련 문서

- 초보자 매뉴얼: [`docs/user_manual.md`](user_manual.md)
- Backend/Sidecar 상태: [`docs/exe_backend_sidecar_status_check.md`](exe_backend_sidecar_status_check.md)
- 앱 버전/빌드: [`docs/exe_build_version_check.md`](exe_build_version_check.md)
- Preflight Smoke Test: [`docs/exe_preflight_smoke_test.md`](exe_preflight_smoke_test.md)
- 최근 이벤트 로그 뷰어: [`docs/exe_runtime_event_log_viewer.md`](exe_runtime_event_log_viewer.md)
- KIS 자격 입력 점검: [`docs/kis_paper_credentials_setup_check.md`](kis_paper_credentials_setup_check.md)
