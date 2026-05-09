# 로컬 보안 정책 (Local Security Policy)

본 문서는 **에이전트 트레이더 v1**의 *로컬 운영 보안* 원칙을 정리한다. 본 시스템은 *외부 공개 SaaS가 아니라 개인 자동매매 도구*이므로, 보안 모델 또한 *로컬-우선 / 외부-차단*이 기본.

## 1. 핵심 원칙 (절대)

| 원칙 | 시스템 가드 |
|---|---|
| **`.env`는 *로컬 PC에만* 존재** | `.gitignore`에 `.env` / `.env.*` (단 `.env.example` 허용) — 운영자가 매 commit 전 `git diff` 확인 권고 |
| **API key / Secret / 계좌번호 / 패스워드 / 인증 토큰 frontend 미저장** | `VITE_*` 변수만 frontend inline. SMTP_PASSWORD / KIS_APP_SECRET 등은 frontend env에 *절대* 두지 않음 |
| **외부 공개 인터넷 노출 금지** | 공유기 포트포워딩 *금지*, 외부 SaaS 호스팅 *금지*, ngrok / Cloudflare Tunnel 같은 공개 URL 도구 *권장 X* |
| **외부 접근은 Tailscale 등 사설 메시 VPN으로** | peer-to-peer 암호 터널 — 외부 인덱싱 / scan 불가능 |
| **GitHub Pages는 *Demo UI 전용*** | backend 호출 0건, 실 broker 호출 0건, 실 데이터 0건 |
| **Secret-shape 입력은 차단 또는 advisory 경고** | backend `agent_memory::sanitize_text` (fail-closed) + frontend `HelpFeedbackPanel` (advisory) |
| **audit log 외부 송출 금지** | feedback modal은 metadata(version/mode/URL/UA/Time)만 자동 포함. raw audit는 자동 송신 X |

## 2. .env / Secret hygiene

### 2.1 .gitignore 검증

```
.env
.env.*
!.env.example
backend/notepad .env
.venv/
venv/
```

본 시점 모든 항목 등록됨 — 운영자가 `git status`에서 `.env`가 *추적되지 않는지* 매번 확인.

### 2.2 .env.example 검증

`backend/.env.example` 및 `frontend/.env.example`은 *placeholder만* 포함:
- `VITE_BACKEND_URL=http://127.0.0.1:8000` (공개 가능)
- `VITE_ENABLE_FUTURES_TAB=false` (공개 가능)
- `VITE_FEEDBACK_EMAIL=` (운영자가 입력하는 *공개 가능* 메일)
- `KIS_APP_KEY=` (placeholder, 실 값은 .env에)
- 등등

**금지**: 실 API key / Secret / 계좌번호를 `.env.example`에 *예시로라도* 작성하지 마세요.

### 2.3 운영자 일일 체크

- [ ] `.env`가 git에 추적되지 않는지 (`git ls-files | grep .env`로 확인 — 결과 0건)
- [ ] feedback / chat / git commit 메시지에 자격증명을 우연히 첨부하지 않았는지

## 3. 관리자 / 사용자 인증

### 3.1 본 PR 시점 (v1.0.0)

- **localhost / LAN / Tailscale**의 *신뢰 네트워크* 가정 — 별도 인증 없이 동작.
- 데스크톱 앱 모드(Tauri / Electron)에서는 OS 사용자 권한이 1차 격리.
- 같은 PC에 다른 사용자가 있는 환경에서는 추가 가드 필요 (아래).

### 3.2 다음 단계 옵션 (backlog)

- **관리자 비밀번호** (local-only 검증 — DB의 hash 비교):
  - 첫 실행 시 운영자가 설정.
  - hash + salt로 SQLite에 저장. raw 비밀번호 *0건* 저장.
  - 실 broker 토글 / 긴급중단 등 *민감 행위*에 재인증 요구.
- **WebAuthn / 패스키**: 모바일 / 데스크톱 양쪽 지원, 패스워드보다 안전.
- **backend 내부 API key**: localhost / LAN 외 추가 방어층.

## 4. Tailscale 권장 / 포트포워딩 금지

### 4.1 권장: Tailscale

| 이유 | 설명 |
|---|---|
| peer-to-peer 메시 | 외부 인터넷에 *직접 노출되지 않음* |
| 같은 계정 디바이스만 접근 | 외부 추측 / scan 불가능 |
| 무료 plan: 100 device | 개인 / 베타 사용에 충분 |
| 트래픽 암호화 | 공용 Wi-Fi에서도 안전 |

자세한 절차: [`mobile_access_guide.md`](mobile_access_guide.md) §2.

### 4.2 금지: 공유기 포트포워딩

- 실 broker 자격증명을 가진 backend가 *인터넷에 직접 노출*.
- 공격자가 IP 스캔 / 무차별 brute force / known CVE 시도.
- WAF 없이 노출 시 토큰 / 잔고 / 매매 권한 모두 위험.

### 4.3 금지: 외부 공개 SaaS / ngrok 등

- ngrok / Cloudflare Tunnel은 *외부 공개 URL*을 만든다 — URL 추측만으로 접근 가능.
- 본 시스템은 *개인 도구*이므로 SaaS 형태 배포는 다중 사용자 / 격리 / 인증 / 법적 책임이 추가로 필요.

## 5. Secret 검출 / sanitize 정책

### 5.1 Backend (Agent Memory #58)

[`backend/app/agents/agent_memory.py::sanitize_text`](../backend/app/agents/agent_memory.py)가 INSERT *전*에 9종 패턴 검사:

- API key (sk- prefix, Anthropic, OpenAI, generic 32+ char)
- KIS app_key / app_secret / access_token (label + 16+ char)
- 한국 계좌번호 (`2-4-8` 또는 `2-3` 또는 10-14 연속 숫자)
- 신용카드 (13-19 digits)
- 한국 주민등록번호 (`6-7` pattern)
- JWT (`eyJ...` 3-segment base64)
- 이메일 / 한국 휴대전화

**fail-closed** — 적중 시 `SecretLeakError` raise (저장 자체 차단). redaction 아님.

### 5.2 Frontend (Help/Feedback)

[`frontend/src/components/common/HelpFeedbackPanel.jsx`](../frontend/src/components/common/HelpFeedbackPanel.jsx)는 사용자 입력에서 *advisory* 경고만 — 자동 차단은 X (사용자 의도 가능성 고려). 입력 금지 안내 노출.

## 6. 데스크톱 앱 (Tauri / Electron) 보안 추가 가드

본 PR 시점 데스크톱 앱은 *backlog* — 패키징 시 다음 가드 필수:

| 가드 | 내용 |
|---|---|
| **WebView strict CSP** | `default-src 'self'; script-src 'self'`. 외부 origin script 차단 |
| **Tauri allowlist 최소화** | 필요한 IPC 명령만 노출. `fs.readFile` 같은 강력 권한은 path scope 제한 |
| **Code signing** | Windows EV / macOS Developer ID 인증서 — 다운로드 신뢰성 + tamper 방지 |
| **Update manifest signature** | Tauri updater pubkey 검증 — 임의 manifest 주입 방어 |
| **Auto-launch 권한 차단** | OS 시작 시 자동 실행 default off — 운영자 명시 옵트인 |

자세한 내용: [`auto_update_plan.md`](auto_update_plan.md) §3.

## 7. GitHub Pages — Demo UI 전용

- Pages는 *frontend 정적 빌드*만 호스팅. backend / DB / 자격증명 0건.
- `VITE_DEMO_MODE=true`로 빌드 — `friendlyErrorMessage`가 "GitHub Pages 데모…" 안내로 분기.
- Pages에서 backend로 호출하는 코드 경로 0건 (모든 API call이 friendly error로 fallback).

자세한 내용: [`frontend_integration.md`](frontend_integration.md) §5.

## 8. 운영 체크리스트 (월별)

- [ ] `.env`가 git에 추적되지 않는지 확인 (`git ls-files | grep -i env`)
- [ ] `.env.example`에 실 자격증명이 들어가지 않았는지 확인
- [ ] `git log -p` 최근 30일에 secret-shape이 들어가지 않았는지 (`git log -p | grep -E "sk-ant|app_secret="`)
- [ ] backend `.env` 파일 권한이 운영자만 읽을 수 있는지 (Windows: 우클릭 → 속성 → 보안)
- [ ] Tailscale ACL 점검 (불필요한 device 제거)
- [ ] Windows Defender / 백신 최신 정의

## 9. 사고 발생 시 (Incident Response)

| 상황 | 즉시 조치 |
|---|---|
| **API key 노출 의심** | 1) KIS / 증권사 web에서 즉시 *키 폐기 / 재발급*. 2) `.env` 갱신. 3) 영향 범위 (audit log) 검토. |
| **계좌 무단 사용 의심** | 1) 증권사 고객센터 신고 + 거래 정지. 2) 시스템 *긴급중단* (Kill Switch). 3) backend 종료 후 audit log 확인. |
| **시스템 무단 접근 의심 (LAN)** | 1) 공유기 비밀번호 변경. 2) Wi-Fi 게스트 네트워크 분리. 3) Tailscale 도입 검토. |
| **Tailscale 계정 탈취** | 1) Tailscale 비밀번호 변경 + 2FA 활성화. 2) "device list"에서 모르는 device 제거. 3) `.env` 갱신. |

## 관련 문서

- [`deployment_strategy.md`](deployment_strategy.md) — 전체 배포 정책
- [`mobile_access_guide.md`](mobile_access_guide.md) — Tailscale 설정
- [`beta_distribution_plan.md`](beta_distribution_plan.md) — 베타테스터 secret 미공유 정책
- [`auto_update_plan.md`](auto_update_plan.md) — Update manifest signature
- [`agent_memory.md`](agent_memory.md) — backend sanitize fail-closed
- [`help_and_feedback.md`](help_and_feedback.md) — frontend secret advisory
- `CLAUDE.md` — 절대 원칙 4-5 (Secret 미저장 / frontend는 관제 전용)
