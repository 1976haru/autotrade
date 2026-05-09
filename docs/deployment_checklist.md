# 배포 / 접속 / 보안 체크리스트 (Deployment Checklist)

**에이전트 트레이더 v1** 운영자가 *직접 단계별로 점검*할 수 있는 체크리스트. 기능 개발이 아니라 **배포 / 접속 / 보안** 운영 정책을 0단계부터 12단계까지 따라가며 확인하는 문서.

> 본 문서는 *시스템 운영 점검 가이드*이며 **투자 조언이 아닙니다**.

## 용어 — 초보자용 한 줄 요약

| 용어 | 풀어서 |
|---|---|
| **Tailscale** | 우리 PC끼리만 통하는 *비밀 통로*. 같은 계정 디바이스끼리 사설 망으로 연결. 설치하면 외부 카페에서도 집 PC에 안전하게 접속 가능. 공유기 포트포워딩과 달리 *외부 인터넷에 IP가 노출되지 않음*. |
| **PWA** | "스마트폰 홈화면에 *앱처럼 추가*하는 웹사이트". 앱스토어 거치지 않고 브라우저로 설치. 본 시스템은 *관제 화면*에 PWA를 사용 (자동매매 본체는 PC). |
| **Tauri** | 데스크톱 앱을 만드는 도구. 같은 frontend 코드를 .exe / .dmg / .deb로 패키징. *가벼움* (Electron 대비 1/15 크기), Rust 기반으로 더 안전. 본 시스템 베타테스터 배포의 *1순위 후보*. |
| **Electron** | Tauri의 대안. 더 무겁지만 광범위한 생태계 (Slack / Discord / VS Code가 사용). |
| **자동 업데이트** | 앱 실행 시 새 버전이 있는지 자동 확인 → 사용자에게 알림 (Phase 2) → 자동 다운로드+설치 (Phase 3). 본 시스템은 *알림 단계부터* 시작. |
| **Code signing** | "이 .exe는 우리가 만든 게 맞다"는 *디지털 서명*. Windows SmartScreen / macOS Gatekeeper 경고 회피. 인증서 비용 ~$300/year. *후속*. |

---

## 단계별 체크리스트 (0 ~ 12)

### 0단계 — 배포 목표 확정

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 0.1 | 운영 형태 확정 | 본 시스템은 *개인 자동매매 도구*임을 자기 확인 | "외부 SaaS 아님 / 개인 + 베타테스터" 결정 | 다중 사용자 SaaS는 인증 / 격리 / 법적 책임이 추가로 필요 |
| 0.2 | 공개 범위 결정 | 누구에게 배포할지 — 나만 / 가까운 베타테스터 N명 | 명단 작성 (≤ 5명 권고) | 모르는 사람 / 공개 채널 배포 *금지* |
| 0.3 | repository 공개 정책 | 실 API key 들어가기 전 *private 전환* | github.com → Settings → 'Change visibility' → Private | 한 번 public commit한 secret은 git history에서 *완전 제거 어려움* — 사고 시 즉시 키 폐기 |
| 0.4 | 운영 모드 default 확인 | LIVE 활성화 시점 / 절차 결정 | `.env`에서 `DEFAULT_MODE=SIMULATION`, 모든 LIVE flag false | 활성화는 별도 옵트인 PR + promotion gate 8개 조건 통과 |

### 1단계 — 현재 배포 방식 이해

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 1.1 | 4 가지 환경 인지 | Local / LAN / Tailscale / GitHub Pages Demo | 각각 설명 1줄로 말로 정리 | Pages는 *UI 데모만* — 실거래 0건 |
| 1.2 | "본체 vs 리모컨" 모델 | PC=자동매매 본체, 폰=관제 리모컨 | 자동매매 로직이 어디서 동작하는지 명확히 인지 | 폰에서 주문 / 리스크 결정이 *처리되지 않음* (모두 PC backend) |
| 1.3 | 실 broker 호출 위치 | 오직 backend에서만 (frontend X) | `frontend/` 검색 → broker.place_order 호출 0건 확인 | frontend는 *관제 / 승인 / 설정 UI*. 실 거래 코드 미포함 |

### 2단계 — 내 데스크톱에서 안전하게 쓰기

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 2.1 | backend 실행 | `cd backend && uvicorn app.main:app --reload` | `http://127.0.0.1:8000/api/status` HTTP 200 | reload 모드는 코드 변경 시 자동 재시작 |
| 2.2 | frontend 실행 | `cd frontend && npm run dev` | `http://localhost:5173/` 접속 OK | 같은 PC에서만 접근 가능 (`--host` 옵션 안 줬을 때) |
| 2.3 | 운용 모드 확인 | 화면 상단 모드 badge → SIMULATION 표시 | Hero 카드에 "SIMULATION" / "Backend 연결됨" | 다른 모드라면 `.env`의 `DEFAULT_MODE` 점검 |
| 2.4 | 절전 / 슬립 비활성 | Windows 전원 옵션 → "절전 모드 시작 안 함" | `powercfg /change standby-timeout-ac 0` | 모니터 OFF는 OK (디스플레이 전원만 차단) |
| 2.5 | Windows Update 활성 시간 | 09:00-15:30 KST를 active hours로 설정 | 설정 → Windows Update → 활성 시간 | 누적 업데이트는 비정기 재부팅 가능 — 매일 장 시작 전 확인 |

### 3단계 — 스마트폰으로 집 안에서 보기 (LAN)

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 3.1 | backend `--host 0.0.0.0` | 외부 인터페이스에 listen | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | 신뢰하는 Wi-Fi에서만 — 공용 Wi-Fi에서 *금지* |
| 3.2 | frontend `--host 0.0.0.0` | 동일 | `npm run dev -- --host 0.0.0.0` | 출력 로그에 "Network: http://192.168.x.y:5173" 표시되면 OK |
| 3.3 | PC IP 확인 | PowerShell `Get-NetIPAddress` | `192.168.0.X` 같은 IPv4 주소 메모 | 공유기 재시작 시 IP가 바뀔 수 있음 — DHCP 고정 / 정적 IP 권고 |
| 3.4 | 폰 브라우저 접속 | `http://<PC-IP>:5173/` | Hero 카드 표시 + `/api/status` 호출 성공 | 방화벽 1회 허용 ("사설 네트워크에서 허용" 체크) |
| 3.5 | 긴급중단 위치 숙지 | 모바일 OperatorPanel 내 빨강 버튼 | 폰에서 즉시 누를 수 있는 위치 인지 | 실거래 운영 시 *반드시 숙지* |

### 4단계 — 밖에서 스마트폰으로 보기 (Tailscale)

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 4.1 | Tailscale 설치 (PC) | <https://tailscale.com/download> | Tailscale 우측 상단 아이콘 활성 | Google / Microsoft 계정 로그인 |
| 4.2 | Tailscale 설치 (폰) | App Store / Google Play | 같은 계정 로그인, "연결됨" 표시 | 폰 / PC 같은 계정이어야 peer 인식 |
| 4.3 | Tailscale IP 확인 | PC에서 `tailscale ip -4` | `100.X.Y.Z` 메모 | 본 IP는 *공개 인터넷에 노출되지 않음* |
| 4.4 | 폰 브라우저 접속 | `http://100.X.Y.Z:5173/` | 외부 통신망 (LTE)에서도 접속 OK | Wi-Fi 끄고 LTE에서 동작 확인 |
| 4.5 | **포트포워딩 / ngrok / Cloudflare Tunnel 사용 X** | 외부 공개 도구 *금지* | 공유기 admin 페이지에 "포트포워딩 룰 0건" | 공개 URL은 외부 추측 / scan / brute force 위험 |

### 5단계 — 베타테스터 배포 방식 결정

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 5.1 | 배포 방식 선택 | 3안: a) zip 수동, b) Tauri 데스크톱 앱, c) Docker | (a)부터 시작 → (b) 진행 → (c) 검토 | (b)는 인증서 / 빌드 파이프라인 시간 필요 |
| 5.2 | 운영자 `.env` *미공유* | 베타테스터에게 운영자 자격증명 0건 | 압축 / 빌드 산출물에서 `.env` 제외 확인 | git ls-files에 `.env` 0건 (이미 gitignored) |
| 5.3 | 베타테스터 자격증명 가이드 | 각자 KIS 모의투자 / API Key 발급 | 가이드 링크 + 발급 절차 문서 작성 | 운영자 `.env.example`에 placeholder만 |
| 5.4 | 모드 default 강제 | 압축물 / Tauri 빌드의 `.env.example`이 SIMULATION + 모든 LIVE flag false | 베타테스터 첫 실행 시 안전 모드 | LIVE 활성화는 별도 옵트인 절차 안내 |
| 5.5 | 배포 채널 결정 | GitHub Releases (private repo) | 운영자가 새 release 생성 → 베타테스터에게 직접 링크 전달 | 공개 Discord / 카카오톡 단톡 *금지* |

### 6단계 — Tauri 데스크톱 앱 준비 (1순위 후보)

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 6.1 | Tauri 학습 | 공식 docs 한 번 훑어보기 | https://tauri.app/start 읽음 | Rust 기본 개념 (cargo / target) 숙지 |
| 6.2 | Tauri 1.x → 2.x 결정 | 본 PR 시점 v2가 stable | v2 권장 | v1은 deprecated |
| 6.3 | dev 환경 빌드 | `npm create tauri-app` 또는 기존 frontend wrap | `tauri dev` 로 앱 창 뜸 | 빌드 시 Rust toolchain 필요 |
| 6.4 | production 빌드 | `tauri build` → `.exe` / `.dmg` 생성 | dist에 결과물 확인 | Windows 빌드는 Windows에서, macOS는 macOS에서 |
| 6.5 | Code signing (후속) | Windows EV / macOS Developer ID | 인증서 발급 + 빌드 파이프라인 통합 | 후속 — 베타 안정화 후 |
| 6.6 | Electron 대안 검토 | Tauri 적용 어려운 경우 | electron-builder PoC | bundle 크기 큼 (~150MB) |

### 7단계 — 자동 업데이트 준비

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 7.1 | Phase 1 (현재) — 수동 다운로드 | GitHub Releases에서 사용자가 직접 받음 | 운영자가 release 노트 작성 | 베타테스터가 새 버전 인지 못 할 수 있음 |
| 7.2 | Phase 2 — 업데이트 알림 | 앱 실행 시 GitHub API로 latest 조회 | 새 버전 banner 표시 (사용자 클릭으로 다운로드) | rate limit 60 req/hour — localStorage 6h 캐시 |
| 7.3 | release notes 단일 출처 | `frontend/src/config/releaseNotes.js` | GitHub Release `body`와 sync | 양쪽 일치하도록 운영자 절차 |
| 7.4 | Phase 3 — 자동 다운로드/설치 | Tauri updater 또는 Electron autoUpdater | pubkey 서명 검증 + code signing | 베타 안정화 후 옵트인 |
| 7.5 | 버전 일치 lint | package.json + appInfo.js + releaseNotes.js + git tag | CI workflow로 검증 | 본 시점 *backlog* — 별도 PR |

### 8단계 — 로그인 / 접근 제어

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 8.1 | 본 시점 인증 정책 | localhost / LAN / Tailscale의 *신뢰 네트워크 가정* | 별도 인증 없음 | 같은 PC 다중 사용자 환경에서는 추가 가드 필요 |
| 8.2 | 관리자 비밀번호 (옵션) | 데스크톱 앱 진입 시 prompt | hash + salt를 SQLite에 저장, raw 미저장 | 후속 — 옵트인 PR |
| 8.3 | 민감 액션 재인증 (옵션) | 실 broker 토글 / 긴급중단 시 재확인 | 비밀번호 / passkey 재입력 | UX 부담 vs 안전 균형 — 운영자 결정 |
| 8.4 | WebAuthn / 패스키 (후속) | 모바일 / 데스크톱 양쪽 지원 | 별도 PR | 패스워드보다 안전 |
| 8.5 | backend 내부 API key (후속) | localhost / LAN 외 추가 방어층 | 헤더 검증 | Tailscale로도 충분한 환경에서는 보류 |

### 9단계 — Secret / API Key 관리

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 9.1 | `.env` git 추적 0건 | `.gitignore`에 `.env` 등록 | `git ls-files \| grep -i env` → 결과 0건 | 매 commit 전 `git status` 확인 권고 |
| 9.2 | `.env.example` 실 값 미포함 | placeholder만 (e.g. `KIS_APP_KEY=`) | grep으로 32자리 이상 문자열 0건 | 예시로라도 실 키 *입력 금지* |
| 9.3 | frontend Secret 미저장 | `VITE_*`만 inline | `VITE_FEEDBACK_EMAIL`은 *공개 가능* 메일 (Secret 아님) | SMTP_PASSWORD / MAIL_API_KEY 절대 frontend 미저장 |
| 9.4 | feedback / chat 첨부 시 redaction | raw audit log 외부 송신 시 운영자 redaction | 자동 sanitize는 9 패턴(api_key / app_secret / 계좌번호 / 주민번호 / JWT 등) | `agent_memory::sanitize_text` fail-closed |
| 9.5 | 사고 발생 시 (Incident Response) | 키 노출 의심 → 즉시 *키 폐기 / 재발급* | KIS / Anthropic 콘솔에서 폐기 + `.env` 갱신 | `local_security_policy.md` §9 |

### 10단계 — 네트워크 / 보안

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 10.1 | 공유기 포트포워딩 *금지* | 외부 인터넷에 backend 직접 노출 X | 공유기 admin 페이지 → 포트포워딩 룰 0건 | scan / brute force 위험 |
| 10.2 | 외부 공개 호스팅 *금지* | ngrok / Cloudflare Tunnel / 공개 URL 도구 미사용 | 사용 중인 domain 0건 | URL 추측만으로 접근 가능 |
| 10.3 | Tailscale ACL | 같은 계정 디바이스끼리만 | Tailscale admin → device list 정리 | 모르는 device 발견 시 *즉시 제거* + 비밀번호 변경 |
| 10.4 | Wi-Fi 환경 분리 | 게스트 / IoT 네트워크와 운영 PC 분리 | 공유기에 게스트 SSID 별도 | 같은 Wi-Fi 다른 디바이스 접근 차단 |
| 10.5 | Windows Defender 활성 | 백신 실시간 보호 ON | Windows Security → 위협 보호 ON | 실 broker 자격증명 가진 PC는 일반 PC보다 더 신중 |
| 10.6 | OS 패치 최신 | Windows Update 정기 적용 | "최신 상태" 표시 | 보안 패치 누락 시 known CVE 위험 |

### 11단계 — 베타테스트 운영

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 11.1 | 베타테스터 명단 (≤ 5명) | 운영자가 직접 알고 있는 사람 | 명단 작성 + 사용자 가이드 전달 | 모르는 사람 *X* |
| 11.2 | onboarding 가이드 전달 | `docs/user_guide_a4.md` + `mobile_access_guide.md` 링크 | 베타테스터가 ack | KIS 모의투자 발급 가이드 별도 |
| 11.3 | feedback 채널 결정 | Slack / Discord / 이메일 / GitHub Issue (private) | 채널 1개로 통일 | feedback 시 자격증명 *절대 미공유* (frontend 가드 + 운영자 안내) |
| 11.4 | 베타 기간 ≥ 4주 | 실 시장 환경 노출 충분 | 시작일 기록 + 4주 후 점검 | 단기 (1-2주)는 다양한 시장 조건 미커버 |
| 11.5 | 정식 배포 진입 조건 | critical 이슈 0건 + Phase 2 알림 작동 | `beta_distribution_plan.md` §7 7개 항목 모두 체크 | LIVE 활성화는 *그 이후*에도 별도 옵트인 |

### 12단계 — 실거래 전 배포 최종 점검

| 항목 | 설명 | 내가 할 일 | 완료 기준 | 주의사항 |
|---|---|---|---|---|
| 12.1 | repository **private 전환** 확인 | 실 API key 입력 *전* private | github.com → Settings → Visibility = Private | 한 번 public이면 history에서 완전 제거 어려움 |
| 12.2 | `.env` 권한 확인 | 운영자만 읽기 가능 | Windows: 우클릭 → 속성 → 보안 | 다중 사용자 PC라면 별도 OS 사용자로 분리 |
| 12.3 | promotion gate 8개 조건 | `docs/promotion_policy.md` 모두 체크 | 모든 항목 ✓ | 1개라도 미충족이면 LIVE 활성화 X |
| 12.4 | 백테스트 → Shadow → Paper → Manual → AI Assist 검증 완료 | 단계별 audit 무사고 | StrategyResearcher #55 + RiskAuditor #54 결과 확인 | 단계 건너뛰기 금지 |
| 12.5 | code signing 인증서 (후속) | Windows EV / macOS Developer ID | 발급 + 빌드 파이프라인 통합 | 베타 안정화 후 |
| 12.6 | 일일 점검 루틴 확정 | backend 상태 / 모드 / 안전 flag / 긴급중단 / 데이터 freshness | 매일 장 시작 전 5분 | `deployment_strategy.md` §6 |
| 12.7 | 사고 대응 매뉴얼 인지 | API key 노출 / 무단 접근 / Tailscale 탈취 등 | `local_security_policy.md` §9 읽음 | 사고 시 *즉시* 키 폐기 / 거래 정지 |
| 12.8 | LIVE 활성화 *옵트인 PR* | `ENABLE_LIVE_TRADING=true` 변경은 *별도 PR* | PR description에 위 12개 단계 체크 결과 첨부 | default off는 *절대 변경 금지*, 활성화는 명시 옵트인만 |

---

## 절대 원칙 요약 (15)

1. 외부 공개 서버로 기본 운영하지 않는다.
2. **공유기 포트포워딩 금지**.
3. 스마트폰 외부 접속은 **Tailscale 우선**.
4. PC가 켜져 있어야 스마트폰에서 관제 가능.
5. 베타테스터는 *각자 자기 PC에 설치*.
6. 운영자 `.env`와 API Key를 *절대 공유하지 않는다*.
7. 실제 API 들어가기 전 repository **private 전환**.
8. **GitHub Pages는 UI Demo 전용** (실 데이터 / backend 미배치).
9. 실제 backend는 local PC 또는 private server에서만 실행.
10. 초기 업데이트는 *수동 / 알림 방식*, 자동 업데이트는 후속.
11. **Tauri**는 desktop packaging 1순위 후보.
12. **Electron**은 대안.
13. **PWA**는 스마트폰 홈화면 관제용.
14. 푸시 알림은 *보안 검토 후* 옵트인.
15. **LIVE / AI / FUTURES flag는 기본 false** (절대 변경 금지 default).

---

## 관련 문서

- [`deployment_strategy.md`](deployment_strategy.md) — 전체 배포 / 운영 정책
- [`mobile_access_guide.md`](mobile_access_guide.md) — 스마트폰 접속 (LAN / Tailscale) 절차
- [`beta_distribution_plan.md`](beta_distribution_plan.md) — 베타테스터 배포 계획
- [`auto_update_plan.md`](auto_update_plan.md) — Phase 1-2-3 단계별 업데이트
- [`local_security_policy.md`](local_security_policy.md) — 로컬 보안 정책
- [`promotion_policy.md`](promotion_policy.md) — LIVE 활성화 8개 조건
- [`user_guide_a4.md`](user_guide_a4.md) — 초보자 사용자 가이드
- [`smartphone_operator_mode.md`](smartphone_operator_mode.md) — 모바일 Operator 화면 정책
- [`frontend_integration.md`](frontend_integration.md) — Frontend API client / DataSourceBanner
- `CLAUDE.md` — 절대 원칙 1-6
