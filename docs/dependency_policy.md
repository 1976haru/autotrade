# Dependency Policy — Agent Trader v1

> 본 문서는 frontend + backend 의 의존성 관리 정책을 정의한다. 본 PR (#88)
> 시점에는 *정책 명시* 만이며, 실제 lock 파일 도입은 후속 PR (P0-2,
> [`docs/status/next_steps.md`](status/next_steps.md)) 에서 진행한다.

## 1. 현재 상태

| 영역 | lock 파일 | 상태 |
|---|---|---|
| **frontend** | `frontend/package-lock.json` | ✅ 존재 + `npm ci` 사용 (CI) |
| **backend**  | (없음) | ⚠ 후속 P0-2 작업 — `requirements.lock.txt` 또는 `uv.lock` 도입 예정 |
| **desktop (Tauri)** | `src-tauri/Cargo.lock` | 후속 P1-1 — Rust 빌드 활성화 시점에 생성 |

## 2. backend `requirements.txt` 정책

### 2.1 현재 (본 PR 시점)

```text
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.8
pydantic-settings>=2.4
httpx>=0.27
sqlalchemy>=2.0
alembic>=1.13
yfinance>=0.2.40
anthropic>=0.40
pytest>=8.0
ruff>=0.6
```

- 패키지 1줄
- 주석은 별도 줄
- minor 이상 `>=` 만 핀 — major 상한 *없음*

### 2.2 권장 (P2-5 작업 — 후속 PR)

```text
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
pydantic>=2.8,<3.0
sqlalchemy>=2.0,<3.0
# ...
```

- major 상한 `<X.0` 명시 — 자동 major bump 차단
- 새 major 출시 시 별도 PR 로 호환성 검증 후 `<X+1.0` 으로 변경

### 2.3 lock 파일 도입 (P0-2 — Paper 운영 *전* 필수)

권장 도구:
- **`uv`** (가장 빠르고 modern, 권장) — `uv pip compile requirements.txt -o requirements.lock.txt`
- 또는 **`pip-tools`** — `pip-compile requirements.txt -o requirements.lock.txt`

CI 변경:
```yaml
- run: pip install -r backend/requirements.lock.txt  # << lock 우선
```

운영자 변경:
```bash
cd backend
pip install -r requirements.lock.txt   # 운영 / staging
pip install -r requirements.txt        # 개발 (호환성 검증 용)
```

## 3. frontend `package-lock.json` 정책

### 3.1 현재 (good baseline)

- `package-lock.json` 이 git 에 commit ✅
- CI 가 `npm ci` 사용 (lock 그대로) ✅
- `npm install` 은 *운영자가 의도적으로 lock 갱신* 시에만

### 3.2 권장

- 새 dependency 추가는 별도 PR — `package-lock.json` 변경 diff 가 review 가능
  해야 함
- 보안 패치 (`npm audit fix`) 는 별도 PR + 회귀 테스트 통과 후 머지
- major version bump 도 별도 PR (React 19→20 같은 변화는 통합 테스트 필수)

## 4. CI / Build 안정성

| CI workflow | 설치 방식 | Cache |
|---|---|---|
| `backend-ci.yml` | `pip install -r requirements.txt` (lock 도입 전) | `actions/setup-python@v5` 기본 캐시 |
| `frontend-ci.yml` | `npm ci` | `cache: 'npm'` + `cache-dependency-path: frontend/package-lock.json` |
| `desktop-release.yml` (#86) | `cargo install tauri-cli --version "^2" --locked` + `npm ci` | `actions/cache@v4` (cargo) |

## 5. 보안 업데이트 정책

- **CVE 발견 시**: 별도 PR `chore(deps): bump <pkg> <old> -> <new>` + CVE
  링크 + 회귀 테스트 통과 확인 후 머지
- **자동 도구**:
  - GitHub Dependabot 활성화 권장 (별도 PR)
  - `npm audit` / `pip-audit` 주기 점검
- **Anthropic / OpenAI SDK 업데이트**: API breaking change 가능 — 반드시
  통합 테스트 추가 후 머지

## 6. Paper 운영 *전* freeze 정책

`#72 Paper Gate` 가 PASS 되려면 28일 운영 데이터 필요. 그동안 의존성이 바뀌면
*같은 코드 결과* 라도 다른 라이브러리 버전에서 산출된 것이라 게이트 검증의
의미가 약해진다. 따라서:

1. P0-2 완료 (lock 파일 생성) 후
2. 운영 시작 시점에 `requirements.lock.txt` / `package-lock.json` 을 **freeze
   tag** 로 별도 branch / tag 보존
3. Paper 운영 28일 동안 *해당 lock 만* 사용 — security patch 외 변경 0건
4. Gate PASS 후 새 baseline lock 으로 갱신 가능

## 7. 본 PR (#88) 의 변경 범위

본 PR 은 *문서만 추가* — `requirements.txt` / `package.json` / 어떤 dependency
파일도 *변경하지 않는다*. 본 정책은 후속 PR (P0-2, P1, P2-5) 의 가이드.

## 8. 참고

- [`docs/status/next_steps.md`](status/next_steps.md) §P0/P1/P2 — 본 정책 항목들의 실제 작업 우선순위
- [`docs/status/known_risks.md`](status/known_risks.md) §2 — 의존성 관련 위험
- [`docs/system_hygiene_report.md`](system_hygiene_report.md) — 본 PR 점검 결과
- [uv 공식 문서](https://docs.astral.sh/uv/) — 외부 참조
- [pip-tools 공식 문서](https://pip-tools.readthedocs.io/) — 외부 참조
