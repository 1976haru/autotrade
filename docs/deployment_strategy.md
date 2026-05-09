# 배포 전략 (Deployment Strategy)

본 문서는 **에이전트 트레이더 v1**의 배포 / 접속 / 업데이트 / 보안 운영 방식을 정리한다. 핵심은:

1. **개인용 로컬 실행이 기본** — 데스크톱이 자동매매 본체.
2. **스마트폰은 관제 리모컨** — 본체가 아니다.
3. **외부 공개 웹서비스로 배포하지 않는다** — 공유기 포트포워딩 *금지*.
4. **외부 접근이 필요하면 Tailscale 같은 사설 메시 VPN** 사용.

## 1. 운영 구조 (Local-First)

```
┌──────────────────────────────────┐    ┌────────────────────────────┐
│  데스크톱 PC (자동매매 본체)        │    │  스마트폰 (관제 리모컨)       │
│  ┌────────────────────────────┐  │    │                            │
│  │ FastAPI backend (port 8000) │  │    │  Browser / PWA             │
│  │  - RiskManager              │  │    │  http://<PC-IP>:5173       │
│  │  - PermissionGate           │  │ ←──┤  (LAN) 또는                 │
│  │  - OrderExecutor            │  │    │  http://<Tailscale-IP>:5173│
│  │  - Mock / KIS broker        │  │    │  (외부)                     │
│  └────────────────────────────┘  │    │                            │
│  ┌────────────────────────────┐  │    │  *주문/리스크 로직은 본체 PC*  │
│  │ Vite dev server (port 5173) │  │    │  스마트폰에서 처리하지 않음     │
│  └────────────────────────────┘  │    └────────────────────────────┘
│  ┌────────────────────────────┐  │
│  │ SQLite DB (로컬 파일)        │  │
│  └────────────────────────────┘  │
│  ┌────────────────────────────┐  │
│  │ .env (API Key 로컬 저장)    │  │  ← *외부로 절대 송출 X*
│  └────────────────────────────┘  │
└──────────────────────────────────┘
```

**중요**: 데이터(.env / DB / audit logs)는 *모두 로컬 PC에만* 저장. 외부 서비스(클라우드 / SaaS)에 자동 업로드하는 경로 *0건*.

## 2. 운영 모드별 위치

| 환경 | backend 실행 | frontend | 주문 흐름 | 데이터 출처 banner |
|---|---|---|---|---|
| **로컬 PC 단독** | `uvicorn app.main:app --reload` | `npm run dev` (5173) | Mock / KIS 모의 / 실거래 (운영자 옵트인) | backend (녹색) |
| **로컬 PC + 같은 Wi-Fi 폰** | `uvicorn ... --host 0.0.0.0` | `npm run dev -- --host 0.0.0.0` | 동일 (PC backend) | backend (녹색) |
| **외부에서 폰 (Tailscale)** | 위와 동일 | 위와 동일 | 동일 (PC backend) | backend (녹색) |
| **GitHub Pages** | 없음 | 빌드된 정적 UI | 주문 *불가능* (backend 없음) | demo (보라) |

## 3. 접속 방법

### 3.1 같은 Wi-Fi (LAN)

PC에서 IP 확인:
```powershell
# PowerShell
Get-NetIPAddress | Where-Object {$_.AddressFamily -eq "IPv4"}
```

폰에서 접속: `http://<PC-IPv4>:5173/`

### 3.2 외부에서 접속 (Tailscale 권장)

**포트포워딩은 *금지*** — 공유기 외부 노출은 보안 위험이 너무 크다 (실 broker 자격증명이 노출된 backend가 인터넷에 직접 노출).

대신 **Tailscale**(무료 사설 메시 VPN):

1. PC에 Tailscale 설치 + 로그인.
2. 폰에 Tailscale 앱 설치 + 같은 계정 로그인.
3. PC의 Tailscale IP 확인 (예: `100.64.0.5`).
4. 폰 브라우저: `http://100.64.0.5:5173/`.

**Tailscale 장점**:
- 외부 인터넷에 *직접 노출되지 않음* (peer-to-peer 사설 망).
- 자격증명(API key)이 *PC 외부로 송출되지 않음*.
- 무료 (개인 plan: 100 device).

대안: `ngrok` / `Cloudflare Tunnel` 등은 **외부 공개 URL을 만드는 도구**이므로 본 시스템에는 *권장하지 않음* (URL 추측 만으로 접근 가능 / 외부 인덱싱 위험).

### 3.3 GitHub Pages (UI Demo만)

`https://1976haru.github.io/autotrade/` — backend가 없으므로 **자동매매 X / 실 broker X**. 화면 구조 / Demo Mode 체험만 가능.

## 4. PC 운영 주의사항

### 4.1 PC가 켜져 있어야 한다

- 본 시스템은 *PC에서 backend가 동작* — PC를 끄면 자동매매 / API 접근 *모두 중단*.
- 장중 운영자가 외출 중이라도 PC backend는 켜져 있어야 함 (또는 Cloud / VPS 운영은 별도 PR).

### 4.2 절전 / 슬립 / 모니터 OFF

- Windows: 제어판 → 전원 옵션 → "디스플레이 끄기" / "절전 모드 시작" 모두 *없음*으로 설정 권고.
- 또는 PowerShell: `powercfg /change standby-timeout-ac 0`
- 모니터만 꺼지는 건 OK (디스플레이 OFF는 backend에 영향 X).

### 4.3 Windows Update / 재부팅

- Windows Update가 자동 재부팅하면 *backend가 종료*된다.
- 장중 시간(09:00-15:30 KST)을 *active hours*로 설정해 자동 재부팅 회피:
  - 설정 → Windows Update → 활성 시간 설정.
- 그래도 누적 업데이트 / 보안 패치로 비정기 재부팅 가능 — 매일 장 시작 전 backend 상태 확인 권고.

### 4.4 ISP 통신 장애

- 인터넷이 끊기면 broker API 호출 불가능.
- backend는 데이터 freshness 가드(stale price ≥ 60s → hard reject) + emergency_stop 자동 발동을 별도로 검토 가능.

## 5. 보안 / 절대 금지

| 행위 | 금지 사유 |
|---|---|
| **공유기 포트포워딩** | 실 broker 자격증명을 가진 backend가 인터넷에 *직접 노출* — bot scan / brute force 표적 |
| **외부 공개 웹서비스로 배포** | 본 시스템은 *개인 자동매매 도구*. SaaS화 시 다중 사용자 / 인증 / 격리 / 법적 책임 등 별도 구조 필요 |
| **다른 사람과 .env 공유** | API Key / Secret이 운영자 *개인 자격증명* — 절대 공유 X |
| **GitHub에 .env 커밋** | `.gitignore`에 등록되어 있으나 운영자 실수 방지 위해 매 commit 전 `git diff` 확인 권고 |
| **Slack / 메일에 raw audit log 첨부** | 잔고 / 계좌 / 매매 기록이 외부 서비스에 노출 |

## 6. 운영자 일일 체크리스트

- [ ] backend 실행 중인지 확인 (`http://127.0.0.1:8000/api/status`)
- [ ] 운용 모드 / 안전 flag 상태 확인 (Settings 탭)
- [ ] 긴급중단 OFF 상태인지 확인 (Hero / Risk 탭)
- [ ] 데이터 freshness 정상인지 확인 (시세 timestamp)
- [ ] PC 절전 모드 비활성화 / Windows Update 재부팅 일정 확인

## 관련 문서

- [`mobile_access_guide.md`](mobile_access_guide.md) — 스마트폰 접속 가이드
- [`beta_distribution_plan.md`](beta_distribution_plan.md) — 베타테스터 배포 계획
- [`auto_update_plan.md`](auto_update_plan.md) — 자동 업데이트 계획
- [`local_security_policy.md`](local_security_policy.md) — 로컬 보안 정책
- `CLAUDE.md` — 절대 원칙 4 (Secret 미저장) + 5 (frontend는 관제 전용)
