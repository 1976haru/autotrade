# Agent Design

CLAUDE.md 절대 원칙 1번: **AI가 브로커 주문 API를 직접 호출하는 코드를 만들지 않는다.** 본 문서는 그 원칙 위에서 AI를 사용할 수 있는 위치를 정리한다.

## 핵심 분리

```text
┌─────────────────────────┐         ┌──────────────────────┐
│ AI (Anthropic / OpenAI) │         │ 결정론적 코드        │
│                         │         │ (Risk / Permission / │
│ 분석 / 후보 / 보고서    │         │  Executor)           │
│ 출력: 텍스트 + 점수     │  ───►   │                      │
│                         │         │ broker.place_order   │
│ 직접 broker 호출 금지   │         │ (실제 주문 발생)     │
└─────────────────────────┘         └──────────────────────┘
        ▲                                       ▲
        │                                       │
        │      ┌─────────────────────────┐      │
        └──────│ 사용자 (운영자)          │──────┘
               │ 승인 / 옵트인 / 모니터링│
               └─────────────────────────┘
```

AI는 항상 **운영자 또는 결정론적 코드의 입력 신호**로만 작동한다. AI 응답이 직접 broker에 도달하는 경로는 코드 어디에도 없다.

## 에이전트 카탈로그

| 에이전트 | 역할 | 주문 권한 | 현재 구현 | 위치 |
|---|---|---|---|---|
| Execution Recommender | 종목 분석 + 점수 + 진입가/목표가/손절가 제안 | 없음 | ✓ 구현 | `/api/ai/analyze` |
| Market Observer | 시장 상태 / 데이터 freshness / 변동성 감시 | 없음 | 🛑 미구현 | (별도 PR) |
| News/Trend Agent | 뉴스 / 트렌드 / 공시 키워드 요약 | 없음 | 🛑 미구현 | (별도 PR) |
| Strategy Researcher | 전략 후보 + 백테스트 개선안 제안 | 없음 | 🛑 미구현 | (별도 PR) |
| Risk Auditor | 일일 손실 / 중복 주문 / 위험 이벤트 점검 | 없음 | 🛑 미구현 | (별도 PR) |
| Daily Report Agent | 장 종료 후 성과 리포트 + audit 요약 | 없음 | 🛑 미구현 | (별도 PR) |
| Live AI Executor | 제한 조건 하 자동 실행 | 한도 내 가능 | 🛑 미구현 (default OFF) | (별도 PR, 옵트인) |

## 구현된 흐름: Execution Recommender

`POST /api/ai/analyze`:

```text
운영자 요청 (ticker + extra context)
  ↓
AnalyzeRequest (FastAPI)
  ↓
AiClient (anthropic SDK) → Anthropic API
  ↓
응답 텍스트 + 추출된 score JSON
  ↓
AiAnalysisLog (DB) — 모든 호출/오류/미설정 기록
  ↓
AnalyzeResponse: {text, score, model, can_execute_order=False}
```

핵심 안전 장치:
- **`can_execute_order` 항상 False** — 응답 모델 default. 어떤 분기도 True를 set하지 않음.
- **DI 분리** — `get_ai_client()`만 AiClient를 반환하고, broker DI에는 연결 안 됨.
- **Audit** — 키 미설정/오류 포함 모든 호출이 `AiAnalysisLog`에 기록.
- **Lazy import** — anthropic SDK가 미설치이거나 키가 없어도 import는 성공.

자세한 안전 가드는 [`risk_policy.md`](risk_policy.md), 운영자 흐름은 [`promotion_policy.md`](promotion_policy.md) 5단계(AI Assist) 참조.

## AI → 주문 경로 (계획)

현재 `Execution Recommender`는 텍스트와 점수만 반환한다. 향후 `LIVE_AI_ASSIST` 단계에서 점수가 충분히 높으면 운영자에게 후보로 제시하는 흐름이 추가될 예정. 코드 측면에서:

```text
AI 분석 → 점수 / 진입가 / 수량 추출
  ↓
OrderRequest 생성 (변환 어댑터)  ← 별도 PR
  ↓
route_order(requested_by_ai=True, mode=LIVE_AI_ASSIST)
  ↓
RiskManager → NEEDS_APPROVAL (LIVE_AI_ASSIST 모드 분기)
  ↓
PendingApproval 큐 → 운영자 승인
  ↓
OrderExecutor → broker.place_order
```

`requested_by_ai=True` 플래그는 `RiskManager`의 AI 가드를 활성화한다. `enable_ai_execution=False`(기본)이면 LIVE_AI_EXECUTION 모드에서도 거부.

## AI 자동실행 조건 (`LIVE_AI_EXECUTION`)

CLAUDE.md "기본 비활성화" 원칙. 모두 충족해야 활성화 가능:

1. **모드** — `DEFAULT_MODE=LIVE_AI_EXECUTION`
2. **운영자 옵트인** — `ENABLE_AI_EXECUTION=true` 명시 설정
3. **실거래 가드** — `ENABLE_LIVE_TRADING=true` (LIVE_* 가드 통과)
4. **사전 단계 검증 완료** — Backtest → Shadow → Paper → Live Manual → AI Assist 순으로 통과
5. **보수적 한도** — RiskPolicy의 `max_order_notional` / `max_daily_loss` / `max_positions` 매우 타이트하게 설정
6. **감사 로그** — 모든 자동 주문이 `OrderAuditLog`에 기록 (자동 강제됨)
7. **선물 분리** — 선물은 본 단계에서도 자동실행 금지. 별도 옵트인.
8. **모니터링 대시보드** — 운영자가 실시간 관찰 가능한 환경

자세한 단계 매트릭스는 [`promotion_policy.md`](promotion_policy.md).

## 위반시 정책

다음 변경은 PR 리뷰에서 거절된다:

- AI 응답이 broker.place_order에 직접 도달하는 경로
- `can_execute_order=True`를 set하는 분기
- `requested_by_ai`를 사용자 의도와 무관하게 False로 하드코딩 (AI 가드 우회)
- AI 호출 결과를 audit에 기록하지 않는 흐름
- `LIVE_AI_EXECUTION` 모드를 운영자 옵트인 없이 활성화
- AI 응답을 신뢰하여 RiskPolicy 한도를 동적으로 완화

## 향후 작업

- **News/Trend Agent**: 외부 뉴스 API + 키워드 추출 → AI 요약. 별도 모듈 `app/agents/news.py`.
- **Strategy Researcher**: BacktestRun audit 분석 → 파라미터 튜닝 제안. AI 응답을 운영자가 새 backtest로 검증.
- **Risk Auditor**: OrderAuditLog 패턴 분석 → 이상 거래 / 중복 신호 / 한도 초과 임박 알림.
- **Daily Report Agent**: 장 종료 후 audit 집계 + AI 요약. 알림 채널 연동.
- **AI Assist 자동 후보**: AI 응답을 OrderRequest로 변환해 PermissionGate 큐에 자동 push.

각 항목은 별도 PR로 추가되며, 본 문서를 동시에 갱신한다.

## 관련 문서

- [`agent_architecture.md`](agent_architecture.md) — 6개 표준 Agent 역할 contract (#51) — Observer / Analyst / Risk Auditor / Strategy Researcher / Report Writer / Execution Recommender
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 + 환경 플래그 매트릭스
- [`risk_policy.md`](risk_policy.md) — RiskManager AI 가드 단계
- [`architecture.md`](architecture.md) — 가드 체인 전체 구조
- `CLAUDE.md` — 절대 원칙 1번 (AI 직접 호출 금지)
