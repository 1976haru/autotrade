# EXE 빌드 전 통합 검증 Gate (체크리스트 11-00, FinalPrebuildIntegratedGate)

> EXE 빌드 직전, 전체 프로그램을 한 번에 점검해 **"빌드해도 되는 상태인지"** 를 판정한다.
> **실전 승인 아님 · 주문 실행 아님 · 수익 보장 아님.** '빌드 가능'과 '전략 유망'은 다르다.

## 1~2. 목적 / 왜 필요한가

여러 검증(BUILD-01/02A/02B, FINAL-AUDIT, FINAL-UI-API, 전략·실데이터 검증, 안전 플래그,
security_scan)이 흩어져 있다. 빌드 직전에 **하나의 판정**으로 모으면 운영자가 "지금 EXE 를
만들어도 되는가"를 한눈에 알 수 있다.

## 3. 점검 섹션

REPOSITORY_STATUS · BACKEND_QUALITY(ruff+pytest) · FRONTEND_QUALITY(lint+test+build) ·
SECURITY_SECRET_SCAN · HEALTH_PREFLIGHT · CONFIG_ENV(안전 플래그) · DB · KIS_CREDENTIALS ·
KIS_PAPER_ORDER_PATH(live 차단) · UNIVERSE · PORTFOLIO · AGENT_PIPELINE ·
ORDER_QUALITY_FEEDBACK · BUILD-01/02A/02B · BACKTEST · WALK_FORWARD · STRESS ·
REAL_DAILY_DATA · INTRADAY_REAL_DATA · LIVE_SAFETY · UI_API · DOCS_RUNBOOK · EXE_BUILD_INPUTS.

## 4~6. 판정 의미

- **BUILD_READY** — EXE 빌드 진행 가능 (FAIL 0, WARN 0).
- **BUILD_READY_WITH_WARNINGS** — 빌드 가능하나 WARN 확인 필요 (FAIL 0, WARN ≥ 1).
- **BUILD_BLOCKED** — 빌드 보류 (FAIL ≥ 1).

**BLOCKED 조건(요약)**: backend pytest 실패 / frontend build 실패 / security findings /
secret 노출 / .env 추적 / LIVE·AI·FUTURES=true / KIS_IS_PAPER=false / KIS live 주문 경로 /
broker·OrderExecutor·route_order 직접 호출 / BUILD-01·02A·02B FAIL / UI·API manifest FAIL /
DB 실패 / Universe 0+fallback 없음 / Portfolio source mixed / EXE 빌드 필수 파일 누락 /
주문·실전·승인 버튼 발견 / (require-kis-credentials 모드에서) KIS 자격 missing.

**WARN 허용(빌드 차단 아님)**: KIS 자격 미설정 / MARKET_CLOSED / Paper 표본 부족 / 실제
분봉 전략 WORTH_MORE_RESEARCH / 닫기권고 open PR / Frontend flaky 과거 이력(로컬 green) /
KIS intraday collector endpoint 미확인 / Paper sample 0 / docs-only CI 미실행 /
src-tauri/Cargo.lock 미추적(릴리스 무관) / fast 모드 미실행 섹션.

## 7~10. KIS 자격 / Paper 표본 / 실제 분봉 전략 WARN 해석 + "빌드 가능 ≠ 전략 유망"

- **KIS_CREDENTIALS WARN**: 빌드는 가능하나 *장중 KIS 모의 리허설 전* `KIS_IS_PAPER=true` +
  자격 입력 필요. `--require-kis-credentials` 면 FAIL.
- **Paper sample 부족 WARN**: 실전 검토 불가 — Paper 100건/28일 표본 필요(빌드와 별개).
- **실제 분봉 전략 `WORTH_MORE_RESEARCH` WARN**: 전략 *운영 확장* 전 튜닝 권고일 뿐 **빌드
  차단 아님**. 빌드(프로그램 배포 가능 여부)와 전략 유망성(돈을 벌지)은 *다른 축* 이다.

## 11~14. 장중 리허설 전 확인 / 안전

장중 KIS 모의 리허설 전: KIS 자격 입력 → BUILD-02A/02B 재실행 → final-prebuild-gate 재확인.
`FinalPrebuildReport.is_live_authorization=False` / `broker_order_sent=False` /
`order_created=False` / `contains_secret=False` / `no_profit_guarantee=True` 불변(dataclass
가드). broker / OrderExecutor / route_order / KIS 주문 API 호출 0건. **실전 승인 아님 · 수익
보장 아님.**

### 문제 보고 양식

```
- 실행 명령: python scripts/run_final_prebuild_gate.py [--full]
- overall_status:
- BLOCKER 목록:
- 재현 가능 여부:
- (Claude Code 전달 금지: API key / app secret / 계좌번호 원문)
```

## 사용

```
# fast (subprocess 일부: ruff/security_scan/pytest collect — 무거운 전체 pytest/build 제외)
python scripts/run_final_prebuild_gate.py --markdown reports/final_prebuild/final_prebuild_gate.md
# full (frontend build 등 추가)
python scripts/run_final_prebuild_gate.py --full
# 장중 리허설 게이트로 KIS 자격 강제
python scripts/run_final_prebuild_gate.py --require-kis-credentials
```
exit: 0(BUILD_READY/_WITH_WARNINGS) / 1(BUILD_BLOCKED) / 2(오류).

API: `GET /api/system/final-prebuild-gate` (fast, read-only, **subprocess/무거운 테스트/KIS
호출 없음** — 안전 플래그 + 리포트 파일 + 정적 입력 합성, backend/frontend/security 섹션은
fast 에서 WARN). UI: Settings 탭 `FinalPrebuildGateCard`(전체 상태 + 빌드 가능 여부 + 섹션 +
BLOCKER/WARN + 다음 단계, 새로고침·복사만 — 매수/매도/실전/승인 버튼 0개).

## 실측 (2026-05-25, fast)

overall_status **BUILD_READY_WITH_WARNINGS** (PASS 13 / WARN 8 / FAIL 0) — ruff·security_scan
0건·pytest collection·안전 플래그 모두 정상 → **EXE 빌드 가능**, WARN 은 KIS 자격 미설정 /
Paper 표본 0 / 실제 분봉 전략 WORTH_MORE_RESEARCH / fast 미실행 섹션. → *"현재 상태는 EXE
빌드 가능하나 KIS 자격·모의매매 표본 부족 WARN이 있습니다."*
