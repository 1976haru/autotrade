# 스마트폰에서 Agent Trader v1 접속 — Tailscale 가이드

> PC 에서 실행 중인 Agent Trader v1 화면을 스마트폰에서도 *안전하게* 보는
> 방법. 공유기 설정 / 포트포워딩 / 외부 공개 IP 없이도 가능.

## 1. Tailscale 이란

- 같은 *Tailscale 계정* 에 로그인한 기기들끼리 *암호화된 사설 네트워크* 를
  자동으로 만들어 주는 무료 서비스.
- PC 와 스마트폰이 *각자 다른 와이파이 / 셀룰러* 에 있어도 마치 같은 LAN
  안에 있는 것처럼 통신할 수 있다.
- 회사 / 학교 네트워크에서도 (대부분) 동작.

> 다음 모두를 *대체* 한다 — 포트포워딩, 공유기 외부 노출, DDNS, VPN, ngrok.

---

## 2. 설치

### 2.1 PC (Windows)

1. https://tailscale.com/download/windows 접속
2. installer 다운로드 후 실행
3. 시스템 트레이 (시계 옆) 의 Tailscale 아이콘 클릭 → **"Log in"**
4. 브라우저가 열림 → Google / Microsoft / GitHub 계정 등으로 로그인
5. 로그인 후 트레이 아이콘에 ✅ 표시 + 본 PC 의 *Tailscale IP* (예:
   `100.x.y.z`) 가 표시됨

### 2.2 스마트폰

- **iPhone**: App Store 에서 "Tailscale" 검색 → 설치 → 같은 계정 로그인
- **Android**: Play Store 에서 "Tailscale" 검색 → 설치 → 같은 계정 로그인

**중요**: PC 와 스마트폰이 *반드시 같은 Tailscale 계정* 에 로그인해야 한다.

---

## 3. PC 의 Tailscale IP 확인

방법 1) PC 트레이의 Tailscale 아이콘 → 자신의 hostname 옆에 `100.x.y.z` 표시.

방법 2) PowerShell:
```powershell
tailscale ip -4
```

이 IP 를 메모해 둔다 — 스마트폰에서 사용할 주소다 (예: `100.115.92.10`).

---

## 4. Agent Trader v1 실행 (PC)

1. PC 의 바탕화면 아이콘으로 Agent Trader v1 을 실행
2. 앱이 정상적으로 켜진 것을 확인 (`설정` 탭 → "백엔드 정상" 표시)

> 스마트폰에서 접속하려면 PC 가 *켜져 있고* 앱이 *실행 중* 이어야 한다.

---

## 5. 스마트폰에서 접속

스마트폰의 Safari / Chrome 등 브라우저 주소창에:

```
http://<PC의-Tailscale-IP>:5173
```

예: `http://100.115.92.10:5173`

> 본 PR 시점 frontend dev 서버 port 는 `5173`. 정식 desktop 빌드가 backend
> 만 노출하도록 변경되면 port 가 달라질 수 있다 — 그 때는 운영자가 안내.

Agent Trader v1 의 모바일 화면이 열린다.

---

## 6. 접속이 안 될 때

순서대로 확인:

| # | 체크 | 확인 방법 |
|---|---|---|
| 1 | PC 가 켜져 있나? | 모니터에 화면 표시 / 절전 모드 해제 |
| 2 | Agent Trader v1 이 실행 중인가? | PC 작업 표시줄 확인 |
| 3 | PC 의 Tailscale 이 켜져 있나? | 트레이 아이콘 ✅ |
| 4 | 스마트폰의 Tailscale 이 켜져 있나? | 앱 열어 "Connected" 확인 |
| 5 | 같은 계정인가? | PC ↔ 스마트폰의 Tailscale 앱에 표시된 이메일 비교 |
| 6 | Tailscale IP 가 맞나? | `tailscale ip -4` 결과 재확인 |
| 7 | Windows 방화벽 차단인가? | §7 참고 |

---

## 7. Windows 방화벽 허용 (필요 시)

대부분 Tailscale 설치 시 자동 처리. 그래도 안 되면 수동:

1. Windows 검색 → **"Windows Defender 방화벽"** 열기
2. 좌측 **"앱 또는 기능을 Windows Defender 방화벽을 통해 허용"**
3. **"설정 변경"** 클릭
4. 목록에서 *Agent Trader v1* / *Tailscale* 찾기 → **개인** 체크박스 ✅
5. 없으면 **"다른 앱 허용"** → 실행 파일 (`C:\Users\<유저>\AppData\Local\Agent Trader v1\Agent Trader v1.exe`) 추가

---

## 8. 포트포워딩 — 절대 하지 마세요

🚫 **공유기 포트포워딩 / DMZ / UPnP 노출 모두 금지.**

이유:
- 인터넷 전체에 PC 가 노출 → 봇 / 공격자가 자동 스캔으로 매매 화면에 접근
  시도
- KIS Secret / 계좌번호가 외부 공격자에게 평문 가까이 노출
- Tailscale 은 *계정 인증 + 종단 간 암호화* 라 위 위험이 없음

같은 이유로 **ngrok / Cloudflare Tunnel 의 공개 URL 형식** 도 베타 단계에서는
사용하지 않는다 — Tailscale 만 권장.

---

## 9. Run Unattended (고급)

PC 가 잠금 / 절전 상태일 때도 Tailscale 이 살아 있게 하려면:

1. PC 트레이의 Tailscale → **"Preferences"**
2. **"Run unattended"** 토글 ON

> 단점: PC 가 24시간 켜져 있어야 의미가 있음. 노트북 배터리 / 전기 요금
> 고려.

이 옵션을 *반드시* 켤 필요는 없다. 평소엔 PC 가 켜진 상태로 스마트폰 접속만
하면 충분.

---

## 10. 보안 요약

- Tailscale 은 *계정 인증* + *종단 간 암호화* — 같은 계정에 로그인하지 않은
  기기는 절대 접속 불가.
- *공유 옵션* 으로 다른 사람의 기기를 자신의 네트워크에 초대할 수 있지만,
  **베타 단계에서는 자신의 기기만 사용** — 운영자 / 친구 기기도 초대 X.
- Tailscale 계정의 비밀번호는 password manager 로 강력하게 관리.

---

## 11. 참고

- [Tailscale 공식 사이트](https://tailscale.com/) — 외부
- [`docs/beta_tester_install_guide.md`](beta_tester_install_guide.md) —
  데스크톱 앱 설치
- [`docs/mobile_access_guide.md`](mobile_access_guide.md) — 기존 모바일 접속
  가이드 (본 문서가 Tailscale 측면을 보강)
- [`docs/desktop_packaging.md`](desktop_packaging.md) — 데스크톱 앱 설계
