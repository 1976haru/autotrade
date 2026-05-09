# 모바일 접속 가이드 (Mobile Access Guide)

본 문서는 **에이전트 트레이더 v1**을 스마트폰으로 *관제*하기 위한 접속 절차를 정리한다. 스마트폰은 **본체가 아니라 리모컨** — 자동매매 로직은 *전부 PC backend에서* 동작.

## 사전 준비

PC에서 backend + frontend를 *외부 인터페이스에 listen*하도록 실행:

```bash
# Backend (FastAPI) — 모든 인터페이스에 listen
cd C:\trade\autotrade\backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend (Vite dev) — 모든 인터페이스에 listen
cd C:\trade\autotrade\frontend
npm run dev -- --host 0.0.0.0
```

**`--host 0.0.0.0`** 옵션이 *반드시* 필요 — 기본 `127.0.0.1`은 같은 PC에서만 접속 가능. 단, 이 옵션은 *LAN 노출*이므로 신뢰하는 Wi-Fi 환경에서만.

## 1. 집 안에서 접속 (LAN)

같은 Wi-Fi에 PC + 폰이 연결된 상태.

### 1.1 PC IP 확인 (Windows PowerShell)

```powershell
Get-NetIPAddress | Where-Object {$_.AddressFamily -eq "IPv4" -and $_.PrefixOrigin -eq "Dhcp"} | Select-Object IPAddress, InterfaceAlias
```

예시 출력:
```
IPAddress      InterfaceAlias
---------      --------------
192.168.0.49   Wi-Fi
```

### 1.2 폰 브라우저 접속

```
http://192.168.0.49:5173/
```

Frontend가 backend(`127.0.0.1:8000`)에 직접 연결되지 않을 경우, `frontend/.env`에 환경변수 추가:

```
VITE_BACKEND_URL=http://192.168.0.49:8000
```

또는 frontend가 같은 머신의 backend로 backend URL을 `window.location.hostname`으로 자동 도출하게 하는 후속 작업도 가능 (현재는 `127.0.0.1:8000` default).

### 1.3 방화벽 주의

Windows Defender 방화벽이 첫 실행 시 "사설 네트워크에서 허용" 팝업을 띄울 수 있음 — *허용*. 공용 네트워크 허용은 *체크하지 마세요* (카페 / 호텔 등에서 미허용).

## 2. 밖에서 접속 (Tailscale 권장)

**공유기 포트포워딩은 사용하지 마세요** — 실 broker 자격증명을 가진 backend가 인터넷에 직접 노출되어 보안 위험이 큽니다.

### 2.1 Tailscale 설치

1. PC: <https://tailscale.com/download> → Windows 설치 → Google / Microsoft 계정으로 로그인.
2. 폰: App Store / Google Play "Tailscale" 검색 → 설치 → 같은 계정 로그인.

### 2.2 PC의 Tailscale IP 확인

PC에서 `tailscale ip -4` 또는 Tailscale UI에서 확인. 예: `100.64.0.5`.

### 2.3 폰에서 접속

폰 Tailscale 앱이 *연결됨* 상태에서 브라우저:

```
http://100.64.0.5:5173/
```

**Tailscale 특징**:
- peer-to-peer 사설 메시 — *외부 인터넷에 직접 노출 0건*.
- 무료 plan: 100 device.
- 같은 계정에 등록된 디바이스끼리만 접근 — 외부 추측 / scan 불가능.

### 2.4 대안 (권장 X)

- ngrok / Cloudflare Tunnel: 외부 *공개* URL 생성 → URL 추측 / 인덱싱 위험. 본 시스템에는 *권장하지 않음*.
- 공유기 포트포워딩: **금지** (절대 사용 X).

## 3. 모바일 핵심 화면 / 액션

| 화면 | 모바일 OperatorPanel + BottomNav 5-탭으로 접근 |
|---|---|
| **🏠 홈** | 운용 모드 / Agent 판단 / 손익 / 긴급중단 / 승인 대기 |
| **🧠 에이전트** | AI 결정 / 전략 / 시장 regime |
| **🔐 승인** | LIVE_AI_ASSIST 큐 — 결재 / 거부 / 취소 |
| **🎯 리스크** | RiskManager 정책 / **🛑 Kill Switch (긴급중단 토글)** |
| **⋯ 더보기** | 자동봇 / 차트 / 백테스트 / 로그 / 엔진 / 설정 |

### 긴급중단 위치 (반드시 숙지)

- **모바일**: 🏠 홈 → OperatorPanel 카드 (가장 위) → 빨강 "🛑 긴급중단" 버튼.
- **PC**: 🎯 리스크 탭 → Kill Switch UI.

## 4. 로그인 / 인증

본 PR 시점에서는 **localhost / LAN / Tailscale 신뢰 네트워크 가정**으로 별도 인증 없이 동작. 다음 단계로 다음 옵션이 *backlog*에 있음:

- 데스크톱 앱 진입 시 **관리자 비밀번호** (local-only 검증, [`local_security_policy.md`](local_security_policy.md))
- backend `/api/*` 호출에 *내부* API key 헤더 (Tailscale 외부 추가 방어)
- WebAuthn / 패스키 (운영자 옵트인)

## 5. 자주 묻는 질문

### Q. 폰에서 backend(8000)에 직접 연결되지 않습니다

→ `frontend/.env`에 `VITE_BACKEND_URL=http://<PC-IP>:8000` 추가. Frontend는 빌드 시 / dev 시점에 이 값을 inline.

### Q. Tailscale은 데이터를 가져가나요?

→ peer-to-peer 메시 — Tailscale 서버는 *peer 좌표(coordination)*만 처리하고 *트래픽 자체는 직접 peer-to-peer 암호화*. 운영자 자격증명 / 거래 데이터는 Tailscale에 *전송되지 않음*.

### Q. 공용 Wi-Fi(카페)에서도 안전한가요?

→ Tailscale을 거치면 안전 (peer-to-peer 암호 터널). LAN 모드(같은 Wi-Fi)는 *공용 Wi-Fi에서 사용하지 마세요* — 같은 네트워크의 다른 디바이스가 접근 가능.

### Q. PC가 꺼지면 어떻게 되나요?

→ backend가 종료되어 자동매매 / 모바일 접속 모두 중단. PC를 *항상 켜두는 것*이 본 시스템의 전제. 클라우드 VPS 옵션은 별도 backlog.

## 관련 문서

- [`deployment_strategy.md`](deployment_strategy.md) — 전체 배포 / 운영 정책
- [`local_security_policy.md`](local_security_policy.md) — 로컬 보안 / 비밀번호 / Tailscale
- [`smartphone_operator_mode.md`](smartphone_operator_mode.md) — 모바일 운영 화면 정책
- [`frontend_integration.md`](frontend_integration.md) — VITE_BACKEND_URL / friendly error
- `CLAUDE.md` — 절대 원칙 4-5
