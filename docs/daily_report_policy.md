# Daily Report 저장 / 보존 / 공유 정책 (#57)

## 저장 위치

- **운영 로그**: `reports/daily_YYYY-MM-DD.md` — 로컬 산출물.
- **`.gitignore`에 등록**: `reports/`는 git에 *지속 커밋되지 않는다*.
- **샘플 / 예시**: 필요 시 `docs/sample_daily_report.md`로 별도 보관 (PR로 검토).
- **`reports/.gitkeep`**: 디렉토리 placeholder만 유지 (실제 리포트는 미커밋).

## 보존 정책 (운영자 권고)

| 기간 | 권고 |
|---|---|
| 0-30일 | `reports/` 로컬 보관 — 일일 운영 / 디버깅 |
| 30-90일 | 외부 백업 (운영자 정책 — clouddrive / NAS) |
| 90일+ | aggregate 만 보관, raw markdown은 archive |

## 공유 정책

- **민감 정보 포함 가능성**: 리포트는 strategy 이름 / Agent 이름 / 거부 사유 등 *시스템 내부 식별자*를 포함한다. 외부 공유 전 운영자가 *수동* 검토.
- **API key / 계좌번호 / 잔고 절대값 미포함**: 본 Agent는 `OrderAuditLog` / `BacktestRun` 같은 *운영 audit*만 읽으며, broker 인증 정보 / 계좌 잔고 / 실 매매 가격을 raw로 dump하지 않는다.
- **외부 송신 자동화 0건**: 본 PR 시점 이메일 / 텔레그램 / 외부 webhook 송신 0건. 향후 옵트인 별도 PR.

## 본 리포트의 성격

본 리포트는 **투자 조언이 아니라 시스템 운영·검증·개선 자료**입니다:
- 종목 추천 / 매수 매도 신호 X
- 실 broker realized PnL과 다를 수 있는 *추정값* 포함 (reconciliation 모듈로 별도 검증 필요)
- 운영자 검토 → 별도 PR → 별도 백테스트 / paper / shadow → live 절차 필수

## 실행 권한 / 책임

| 행위 | 권한 / 책임 |
|---|---|
| CLI 실행 (`scripts/generate_daily_report.py`) | 운영자 |
| API `/preview` 호출 | 인증된 사용자 (read-only) |
| API `/generate` 호출 | 인증된 사용자 (파일 작성 권한 필요) |
| 리포트 외부 공유 | 운영자 책임 (민감 정보 검토 후) |
| 개선 제안 적용 | 운영자 + 별도 PR (자동 적용 금지) |

## 변경 시 동기화

- 본 정책 변경 → `docs/agent_design.md` 갱신 + PR로 검토
- `.gitignore` 변경 → 명시적 PR (실수로 raw 리포트가 커밋되지 않도록)

## 관련 문서

- [`daily_report_agent.md`](daily_report_agent.md) — Agent 구현 정책
- [`agent_design.md`](agent_design.md) — 전체 Agent 분리 정책
- `CLAUDE.md` — 절대 원칙 4번 (API key / Secret 절대 커밋 금지)
