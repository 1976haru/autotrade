# Security Scan (#93)

> 본 문서는 *secret / 인증서 / 번들 누출* 을 사전 차단하는 정적 검사 도구의
> 정책 / 사용법 / 확장 가이드. 절대 원칙은 [`CLAUDE.md`](../CLAUDE.md) 와 동기화.

## 1. 한 줄 결론

- `scripts/security_scan.py` 가 `git ls-files` 추적 대상을 *read-only* 스캔.
- HIGH/MEDIUM 검출 1건이라도 있으면 exit 1 (CI 차단). LOW 는 informational.
- `backend/tests/_fake_secrets.py` 가 테스트용 fake placeholder 의 단일 진실.
- `backend/tests/test_repository_hygiene.py` 가 본 스캐너를 *매 CI 실행*마다 호출.
  → 새 secret 이 commit 되면 즉시 회귀 차단.

## 2. 검사 대상

### 2-1. Secret pattern (`scripts/security_scan.py::RULES`)

| Rule | Severity | 설명 |
|---|---|---|
| `openai_api_key` | HIGH | `sk-` + 30자 이상 알파넘 |
| `anthropic_api_key` | HIGH | `sk-ant-` + 30자 이상 |
| `github_pat` / `github_pat_v2` | HIGH | `ghp_` / `github_pat_` + 30자 이상 |
| `slack_token` | HIGH | `xox[abprs]-` + 20자 이상 |
| `telegram_bot_token` | HIGH | `<digits>:AA<base64-33+>` |
| `aws_access_key` | HIGH | `AKIA` + 16자 대문자/숫자 |
| `gcp_api_key` | HIGH | `AIza` + 35자 |
| `kis_personal_secret_token` | HIGH | `PST` + 20자 이상 대문자/숫자 |
| `jwt_token` | MEDIUM | JWT 3-part (각 부분 15+/15+/20+) |
| `bearer_long_token` | MEDIUM | `Bearer ` + 40자 이상 토큰 |
| `korean_bank_account` | MEDIUM | `XXXXXXXX-XX` (8~10자리 + dash + 2자리) |
| `credit_card` | HIGH | 16자리 신용카드 형식 |
| `kis_app_key_value` | HIGH | `.env` 의 `KIS_APP_KEY=` 가 비어 있지 않고 placeholder 도 아닌 값 |
| `kis_app_secret_value` | HIGH | 동일하게 `KIS_APP_SECRET=` 의 실제 값 |
| `anthropic_api_key_value` | HIGH | `ANTHROPIC_API_KEY=sk-ant-...` 실제 형식 값 |
| `live_trading_enabled_value` | HIGH | `ENABLE_LIVE_TRADING=true` |
| `ai_execution_enabled_value` | HIGH | `ENABLE_AI_EXECUTION=true` |
| `futures_live_enabled_value` | HIGH | `ENABLE_FUTURES_LIVE_TRADING=true` |
| `kis_paper_disabled_value` | HIGH | `KIS_IS_PAPER=false` |

### 2-2. 인증서 / 키 파일 (`FORBIDDEN_FILE_PATTERNS`)

다음 확장자가 `git ls-files` 결과에 포함되면 즉시 HIGH:

- `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.crt`, `*.cer`, `*.keystore`, `*.jks`, `*.pkcs12`
- `private_key*` / `private-key*` 형식

### 2-3. 번들 / 설치 파일

- `*.msi`, `*.nsis`, `*-setup.exe`, `*.dmg`, `*.pkg`
- `backend/dist/*` (PyInstaller 산출물)
- `src-tauri/binaries/*.exe` (Tauri sidecar 실제 EXE)

→ Secret 이 함께 번들될 위험 차단 + `.gitignore` 누락 시 즉시 탐지.

### 2-4. `.env` 실제 파일

`.env` / `backend/.env` / `frontend/.env` / `*.env.local` — `.env.example` /
`.env.staging.example` 만 OK.

## 3. False positive 처리

### 3-1. 디렉토리 단위 allowlist (`skip_globs`)

- `backend/tests/**` — fake secret fixture 가 정상.
- `frontend/src/**/*.test.*` / `frontend/src/**/__tests__/*` — frontend 테스트.
- `docs/**` — 패턴 예시 / 문서.
- `assets/**` — vite/webpack 빌드 산출물 (원본은 `frontend/src/` 에).
- `scripts/security_scan.py` 자체 (자기 매칭 방지).
- `docs/security_scan.md` (본 문서 자체).

### 3-2. 라인 단위 ignore 마커

```python
# 의도적 placeholder — security-scan: ignore
SAMPLE_ACCOUNT = "12345678-01"
```

```javascript
// security-scan: ignore (UI placeholder, not a real account)
{ placeholder: "12345678-01" }
```

라인에 `security-scan: ignore` 문자열이 있으면 스캐너가 해당 라인 skip.

### 3-3. Placeholder 패턴 (`.env.example`)

`.env.example` 에서 `KIS_APP_KEY=` 의 값이 다음이면 OK:
- 빈 값 (`=` 뒤 공백 또는 EOL)
- `여기에...` / `your-...` / `<...>` placeholder

## 4. 사용법

### 4-1. 로컬 실행

```bash
# stdout 으로 결과
python scripts/security_scan.py

# strict mode (LOW 도 차단)
python scripts/security_scan.py --strict

# JSON 출력 + 파일 저장
python scripts/security_scan.py --format json --output security_scan.json
```

### 4-2. CI 통합

`backend/tests/test_repository_hygiene.py::test_security_scan_script_exists_and_runs_clean`
가 매 CI 실행 시 본 스크립트를 호출하므로 별도 워크플로 step 불필요. 향후
별도 worker 분리가 필요하면 `.github/workflows/backend-ci.yml` 에 `python
scripts/security_scan.py --strict` 추가 가능.

### 4-3. Exit code

| Code | 의미 |
|---|---|
| `0` | 검출 0건 (clean) |
| `1` | HIGH / MEDIUM 검출 (strict 모드면 LOW 도 포함) |
| `2` | 실행 오류 (예: git 미설치) |

## 5. 테스트 fake secret 표준화

`backend/tests/_fake_secrets.py` 가 단일 진실:

```python
from tests._fake_secrets import (
    FAKE_KIS_APP_KEY,
    FAKE_KIS_APP_SECRET,
    FAKE_KIS_ACCOUNT_NO,
    FAKE_ANTHROPIC_API_KEY,
    FAKE_OPENAI_API_KEY,
    FAKE_TELEGRAM_BOT_TOKEN,
    FAKE_GITHUB_PAT,
    FAKE_JWT_TOKEN,
    # sanitizer 가 *catch* 해야 하는 secret-shaped sentinel:
    SECRET_SHAPED_FOR_SANITIZER_OPENAI,
    SECRET_SHAPED_FOR_SANITIZER_ANTHROPIC,
    SECRET_SHAPED_FOR_SANITIZER_KR_ACCOUNT,
)
```

규칙:
- 모든 fake placeholder 는 `FAKE-` / `PLACEHOLDER` / `0000` 마커 1개 이상 포함.
- 본 모듈은 `assert_all_placeholders_contain_fake_marker()` 로 self-check.
- `test_repository_hygiene::test_fake_secrets_module_has_clear_markers` 가 매번 검증.

기존 테스트의 inline fake secret 은 즉시 회귀 위험 없는 한 유지 — *신규
테스트*는 본 모듈 import 권장.

## 6. 절대 원칙 invariant 재확인 (#93 시점)

| invariant | 상태 |
|---|---|
| `KIS_IS_PAPER=true` (default) | ✅ `backend/app/core/config.py` + `.env.example` |
| `ENABLE_LIVE_TRADING=false` (default) | ✅ |
| `ENABLE_AI_EXECUTION=false` (default) | ✅ |
| `ENABLE_FUTURES_LIVE_TRADING=false` (default) | ✅ |
| 실거래 호출 0건 | ✅ KIS adapter `place_order(is_paper=False)` `NotImplementedError` |
| Secret commit 0건 | ✅ `security_scan.py` + `test_repository_hygiene` |
| Frontend secret 노출 0건 | ✅ 카드별 `input/textarea` 0개 + secret 패턴 textContent 0건 |
| 출금 기능 0건 | ✅ broker adapter 에 `withdraw` / `transfer` 메서드 0건 |

## 7. 확장 가이드

### 7-1. 새 secret 패턴 추가

`scripts/security_scan.py::RULES` 튜플에 `Rule(...)` 추가:

```python
Rule(
    name="my_new_secret",
    severity=Severity.HIGH,
    pattern=re.compile(r"\bMY-PREFIX-[A-Z0-9]{20,}\b"),
    description="새 secret 형식 (#XX)",
    skip_globs=_TEST_FIXTURE_GLOBS,
),
```

`pattern` 은 충분히 *길고 명확한* 형식이어야 한다 — false positive 최소화 위해.

### 7-2. 새 forbidden 파일 확장자

`FORBIDDEN_FILE_PATTERNS` 에 `(regex, Severity, description)` 추가.

### 7-3. 새 allowlist 디렉토리

특정 디렉토리가 test fixture 처럼 fake secret 을 허용한다면 해당 rule 의
`skip_globs` 에 추가. `_TEST_FIXTURE_GLOBS` 공통 튜플을 확장하는 것이 가장 안전.

## 8. 후속 backlog

- **JS bundle 원본 추적**: `assets/` 의 bundled JS 가 변경되면 *원본 `frontend/src/`*
  의 어느 라인에서 왔는지 sourcemap 으로 역추적해 ignore 마커 자동 적용.
- **pre-commit hook**: `.git/hooks/pre-commit` 에 `python scripts/security_scan.py
  --strict` 등록 (운영자 옵션).
- **entropy 기반 검출**: 패턴이 아닌 *Shannon entropy* 로 random-looking string
  탐지 (truffleHog 식). 본 PR 에는 미포함.
- **GitHub Action workflow 분리**: CI 정책이 더 엄격해지면 `security-scan.yml`
  별도 워크플로로 분리 + PR comment 첨부.

## 9. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
- [`docs/system_hygiene_report.md`](system_hygiene_report.md) — #88 시스템 hygiene 정책
- [`docs/dependency_policy.md`](dependency_policy.md) — 의존성 정책
- [`backend/tests/test_repository_hygiene.py`](../backend/tests/test_repository_hygiene.py) — 통합 검사 위치
- [`backend/tests/_fake_secrets.py`](../backend/tests/_fake_secrets.py) — fake placeholder 표준
- [`scripts/security_scan.py`](../scripts/security_scan.py) — 스캐너 entrypoint
