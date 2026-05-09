# 도움말 / FAQ / 문의·개선사항 정책

본 문서는 [`HelpFeedbackPanel`](../frontend/src/components/common/HelpFeedbackPanel.jsx) + `FaqCard` 컴포넌트의 정책 contract를 정의한다. 사용자가 사용 중 궁금한 점, 오류, 개선 요청을 운영자에게 안전하게 전달할 수 있게 한다.

## 1. 핵심 원칙 — Frontend는 Secret을 저장 / 송신하지 않는다

| 원칙 | 가드 |
|---|---|
| **SMTP / Mail API Secret을 frontend에 저장하지 않는다** | `.env.example`에 `VITE_FEEDBACK_EMAIL`만 — *공개 가능* 운영 메일 주소만 입력. SMTP_PASSWORD / MAIL_API_KEY 등은 절대 frontend env에 두지 않음 |
| **실제 SMTP 송신은 frontend에서 하지 않는다** | mailto 링크 (사용자 메일 앱이 발송) 또는 클립보드 복사 후 사용자가 직접 전달. 백엔드 SMTP는 후속 옵트인 PR |
| **사용자 입력에 Secret 같은 패턴이 감지되면 advisory 경고** | `_SECRET_HINTS` 정규식 (sk- prefix / 한국 계좌번호 / 주민번호 / api_key=label 등). 자동 차단은 X — *advisory*만 |
| **자동 수집 메타 정보에 Secret 0건** | App / Version / Mode / URL / UA / Time만 수집 (모두 공개 가능). API key / token / 계좌번호 자동 포함 0건 |
| **사용자에게 입력 금지 안내 명시** | 매 모달 상단에 노란 박스로 "API key / Secret / 계좌번호 / 비밀번호 입력 금지" 강조 |

## 2. 자동 수집되는 정보

`FeedbackModal`이 자동으로 메일 본문에 포함하는 메타:

```
- App: 에이전트 트레이더 v1
- Version: Agent Trader v1 v1.0.0
- Mode: SIMULATION  (현재 운용 모드)
- URL: https://1976haru.github.io/autotrade/  (현재 페이지)
- UA: Mozilla/5.0 ...  (브라우저 식별)
- Time: 2026-05-08T01:23:45.000Z  (UTC ISO)
```

**자동 수집 X 목록**:
- API key / Anthropic key / OpenAI key
- App secret / KIS app key
- 계좌번호 / 거래 패스워드 / 인증 토큰
- 잔고 절대값 (요약 / 추정만 backend에서 별도 송신)
- 사용자 비밀번호 / 본인인증 정보

사용자가 수동으로 본문에 입력하면 frontend는 *advisory*만 — 빨강 banner로 "Secret 같은 패턴이 감지되었습니다" 경고. 자동 차단은 하지 않음 (사용자 의도일 수도 있음).

## 3. 송신 방식

### 3.1 mailto (`VITE_FEEDBACK_EMAIL` 설정된 경우)

```
mailto:support@example.com?subject=[에이전트 트레이더 v1] ...&body=...
```

사용자의 OS 기본 메일 앱이 열리며 본문이 prefill. 사용자가 *직접* 송신.

### 3.2 클립보드 복사 (항상 가능)

`navigator.clipboard.writeText(draft)` — 사용자가 클립보드 내용을 다른 채널 (Slack / 카카오톡 / 운영자에게 직접 전달)에 붙여넣어 사용.

### 3.3 (후속 옵트인) Backend SMTP

본 PR에서는 *구현하지 않는다*. 운영자가 SMTP 서버를 갖춘 경우 별도 PR로 backend `/api/feedback/submit` endpoint 추가 + secret은 backend `.env`로만 관리.

## 4. 폼 필드

| 필드 | 종류 | 필수 |
|---|---|---|
| 분류 | select (사용법/오류/개선/AI/리스크/기타) | ✓ |
| 이름 / 별칭 | text | 선택 |
| 답장 받을 이메일 | email | 선택 |
| 제목 | text | ✓ |
| 내용 | textarea | ✓ |
| 심각도 | select (낮음/보통/높음/심각) | 기본: 보통 |
| 재현 방법 | textarea | 선택 |
| 개선 제안 | textarea | 선택 |

## 5. FAQ — 자주 묻는 질문 (8건)

`FaqCard`가 항상 펼쳐진 상태로 Settings 탭에 노출. 사용자가 모달을 열기 전에도 답을 먼저 확인할 수 있다.

| 질문 | 핵심 답변 요약 |
|---|---|
| 이 프로그램은 실제 돈이 나가나요? | 기본 비활성. SIMULATION/PAPER/VIRTUAL/SHADOW는 실 돈 X. LIVE_*는 운영자 옵트인 + 사람 승인 필요 |
| SIMULATION/PAPER/SHADOW 차이는? | SIM=가짜 데이터, PAPER=실 시세+가상 자금, SHADOW=실 계좌 read-only |
| AI가 직접 주문하나요? | 아니오. AI는 *제안*만. 모든 주문은 RiskManager → PermissionGate → OrderExecutor |
| 긴급중단(Kill Switch)은? | 즉시 모든 신규 주문 차단. 모바일 OperatorPanel / PC Risk 탭. 자동 청산 X |
| 백엔드 연결 대기란? | FastAPI backend 미응답. Local: uvicorn 실행. Pages: 자동 demo |
| GitHub Pages vs 로컬 차이? | Pages=UI만, 로컬=FastAPI+DB 모두 동작 |
| 모의투자 vs 실거래 차이? | PAPER=실 시세+가상 자금, LIVE=실 계좌+실 자금 |
| 실거래는 언제 가능? | promotion_policy.md 8개 조건 통과 + ENABLE_LIVE_TRADING=true 별도 PR |

## 6. UI 위치

| 위치 | 컴포넌트 |
|---|---|
| Settings 탭 → "버전 / 공지사항" | `VersionInfoCard` (PHASE 2) |
| Settings 탭 → "사용자 가이드" | `UserGuideCard` (PHASE 3) |
| Settings 탭 → "도움말 / 문의 / 개선 제안" | `HelpFeedbackPanel` (PHASE 4) |
| Settings 탭 → "자주 묻는 질문 (FAQ)" | `FaqCard` (PHASE 4) |

## 7. 향후 과제

- **Backend SMTP** — 운영자가 SMTP 서버 보유 시 별도 PR. secret은 backend `.env`로만 관리.
- **Slack / Telegram webhook** — 운영자 옵트인 (별도 PR).
- **GitHub Issues 자동 생성** — `gh api` 호출로 운영자 GitHub Issue 생성. PAT는 backend에서 관리.
- **시각적 첨부 / 스크린샷 자동 캡처** — html2canvas 등 (선택).
- **다국어 지원** — 현재 한국어 고정.
- **FAQ 검색** — 8건 이상으로 늘면 검색창 추가.

## 관련 문서

- [`user_guide_a4.md`](user_guide_a4.md) — 초보자 가이드 A4 1장
- [`frontend_integration.md`](frontend_integration.md) — API client / DataSourceBanner / friendly error
- [`agent_design.md`](agent_design.md) — Agent 분리 정책
- [`smartphone_operator_mode.md`](smartphone_operator_mode.md) — Mobile Operator
- `frontend/src/components/common/HelpFeedbackPanel.jsx` — 본 컴포넌트
- `frontend/.env.example::VITE_FEEDBACK_EMAIL` — 공개 메일 주소만
- `CLAUDE.md` — 절대 원칙 4-5 (Secret 미저장, frontend는 관제 전용)
