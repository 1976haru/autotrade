# 장 열리기 전 사전 검증 게이트 (BUILD-02A)

최종 EXE 빌드 전 / 장중 KIS 모의 API 리허설 전에, **장이 열리기 전에도 확인 가능한
모든 항목** 을 자동 점검해 하나의 사전 검증 리포트로 묶는다.

> ⚠️ 본 작업은 장중 KIS 모의 *실주문* 테스트 전 *준비 상태* 확인이다. **실제 KIS
> API 를 호출하지 않으며**, 실전/모의 주문을 보내지 않고, 실전 기능을 켜지 않는다.
> KIS 자격은 **present 여부만** 확인하고 원문 값은 절대 출력하지 않는다. **실전
> 승인이 아니며 수익을 보장하지 않는다.**

관련 코드:
- `backend/app/system/premarket_readiness_gate.py` — fast/full mode 사전 검증
- endpoint `GET /api/system/premarket-readiness` (read-only, fast mode)
- CLI `scripts/run_premarket_readiness_gate.py` (fast / full / `--dry-run`)
- 프론트엔드 `frontend/src/components/common/PremarketReadinessGateCard.jsx` (Settings 탭)
- 테스트: `test_premarket_readiness_gate.py`, `test_run_premarket_readiness_gate_script.py`,
  `PremarketReadinessGateCard.test.jsx`

## 1. 목적

빌드/리허설 직전, 개별 모듈이 아니라 *준비 상태 전체* 를 한 번에 확인한다. 장이 닫혀
있어도 검증 가능한 항목만 모아 `premarket_ready` / `build_ready_for_offline` /
`ready_for_market_open_rehearsal` 을 산출한다.

## 2. BUILD-01 vs BUILD-02A vs BUILD-02B

| 단계 | 범위 | KIS API |
|---|---|---|
| **BUILD-01** | 전체 흐름 정합성(Universe→…→Live safety)을 fake/offline 로 1회 점검 | 호출 0 |
| **BUILD-02A**(본 문서) | 장 열리기 전 가능한 *전 영역* 사전 검증(테스트/빌드/문서/정합성/자격 present) | 호출 0 |
| **BUILD-02B**(다음) | 장중 *실제 KIS 모의 API* 현재가/주문/체결 polling 리허설 | KIS 모의 호출 O |

BUILD-02A 는 BUILD-01 을 한 섹션(PROGRAM_INTEGRITY)으로 *포함* 하고, 그 위에
환경/자격/문서/리포트 스크립트/테스트·빌드 명령 가용성을 더한다.

## 3. fast / full mode

- **fast** (기본, API + CLI): 전부 *in-process*. 기존 gate 함수 재사용, subprocess 0.
  - 섹션: ENV_READINESS / KIS_CREDENTIALS / PAPER_LIVE_SEPARATION / UNIVERSE_FALLBACK /
    PORTFOLIO_SOURCE / AGENT_CARDS / PROGRAM_INTEGRITY / PREFLIGHT_SMOKE / DOCS_RUNBOOK /
    REPORT_SCRIPTS.
- **full** (CLI 전용): fast + 외부 명령 *plan* 실행 → BACKEND_QUALITY(`ruff check` /
  `pytest -m "not slow"` / `security_scan`) + FRONTEND_QUALITY(`npm run lint` /
  `npm test` / `npm run build`). 명령 실패 시 해당 섹션 **FAIL**(감추지 않음).
  `--dry-run` 은 명령 plan 만 표시(실행 안 함). API 는 full mode 를 실행하지 않는다.

## 4. 섹션별 점검 설명

- **ENV_READINESS** — ENABLE_LIVE/AI/FUTURES=false + KIS_IS_PAPER=true (하나라도 위반 FAIL).
- **KIS_CREDENTIALS** — present 여부만(원문 0건). 미설정/미상 → **WARN**(offline 빌드 가능,
  장중 리허설 전 입력 필요).
- **PAPER_LIVE_SEPARATION** — `resolve_kis_endpoint` 로 Paper/Live 분리 + fallback 금지(#71).
- **UNIVERSE_FALLBACK** — 관심종목 없으면 기본 50 fallback, count>0(#54).
- **PORTFOLIO_SOURCE** — PAPER_SIMULATED source + 0원 fallback 금지(#55).
- **AGENT_CARDS** — 설명/성과/품질/피드백 카드 backing 함수 정상(#49/#50/#51/#52), 주문 버튼 0개.
- **PROGRAM_INTEGRITY** — BUILD-01 전체 흐름 build_ready.
- **PREFLIGHT_SMOKE** — EXE preflight(#63). 런타임 DB 필요 → fast 모듈 단독은 SKIP,
  API endpoint 가 DB 주입해 PASS/WARN/FAIL.
- **DOCS_RUNBOOK** — 필수 문서 존재 + 문제 보고 양식 + Claude Code 전달 금지 안내 +
  금지 문구(단언형) 없음(부정/인용은 허용).
- **REPORT_SCRIPTS** — 백테스트/Walk-forward/스트레스/정합성 스크립트 가용(full mode 실행).
- **BACKEND/FRONTEND_QUALITY** (full) — ruff/pytest/security_scan, npm lint/test/build.

### PASS / WARN / FAIL 의미

- **PASS** — 정상.
- **WARN** — 빌드 환경에서 정상인 경고(예: KIS 자격 미설정). offline 빌드 허용.
- **FAIL** — 차단 사유. `premarket_ready=False`.

### KIS 자격 미설정 WARN 해석

KIS 모의 자격이 없어도 **offline 빌드/검증은 가능** 하다(WARN). 다만 장중 실제 KIS
모의 API 리허설(BUILD-02B)에는 자격이 필요하므로 `ready_for_market_open_rehearsal` 은
False 가 된다. 자격을 입력하면 PASS + rehearsal=True.

## 5. 최종 판정

- `premarket_ready` = FAIL 0건(WARN 허용).
- `build_ready_for_offline` = premarket_ready AND PROGRAM_INTEGRITY PASS.
- `ready_for_market_open_rehearsal` = build_ready_for_offline AND KIS 자격 present.

## 6. 실행

```bash
# fast (in-process)
python scripts/run_premarket_readiness_gate.py --markdown reports/prebuild/premarket.md

# full (외부 명령 실행 — CLI 전용, 시간 소요)
python scripts/run_premarket_readiness_gate.py --mode full

# full plan 만 (실행 안 함)
python scripts/run_premarket_readiness_gate.py --mode full --dry-run

# API (fast only, read-only)
GET /api/system/premarket-readiness
```

산출물(`--output` 미지정 시 `reports/prebuild/`): `premarket_readiness_*.json` + `.md`.
exit code: 0 premarket_ready / 1 FAIL / 2 오류. `reports/` 는 `.gitignore` 등록.

UI: Settings 탭 `PremarketReadinessGateCard` — overall/premarket_ready/rehearsal +
섹션 PASS/WARN/FAIL + KIS WARN 안내 + BUILD-02B 안내. 버튼은 "점검 새로고침" /
"결과 복사" 만(주문/실전/승인 버튼 0개).

## 7. 다음 단계 (BUILD-02B, 장중)

KIS 모의 자격 입력 후, 장중에 실제 KIS 모의 현재가 조회 / 모의 주문 / 체결 polling
리허설을 진행한다(KIS_IS_PAPER=true, 실거래 OFF). 이는 본 사전 검증과 별도 단계다.

## 8. 안전 가드 (정적 grep + dataclass 불변)

- `premarket_readiness_gate.py` / CLI: broker / OrderExecutor / 단일 주문 라우터 /
  paper_trader / KIS 어댑터 / anthropic / openai / httpx / requests import 0건, broker
  주문/취소/route 호출 0건(KIS 실제 API 0건), DB write 0건, secret/계좌 출력 0건.
- `is_live_authorization` / `broker_order_sent` / `order_created` / `contains_secret`
  항상 False. 안전 flag default · `.env` 변경 0건.
- 본 점검은 **실전을 켜지 않으며 실전 전환 승인을 수행하지 않는다.** 수익 보장 아님.
