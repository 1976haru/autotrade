# CLAUDE.md — Auto Trader 작업 지침

## 프로젝트 정체성

이 프로젝트는 국내주식 단타 자동매매를 위한 **리스크 제한형 연구 플랫폼**이다. 초기 목적은 실거래 수익 자동화가 아니라, 데이터 수집·백테스트·모의투자·Shadow Mode·수동승인·AI 보조를 거쳐 검증 가능한 자동매매 시스템을 구축하는 것이다.

## 절대 원칙

1. AI가 브로커 주문 API를 직접 호출하는 코드를 만들지 않는다.
2. 모든 주문은 반드시 `RiskManager -> PermissionGate -> OrderExecutor` 순서를 거친다.
3. 기본 운용모드는 `SIMULATION` 또는 `PAPER`이며, `LIVE_AI_EXECUTION`은 기본 비활성화한다.
4. API Key, App Secret, 계좌번호, Anthropic/OpenAI Key는 절대 frontend에 저장하거나 커밋하지 않는다.
5. 프론트엔드는 관제·승인·설정 UI이며, 실제 증권사/AI API 호출은 backend에서만 수행한다.
6. 선물 기능은 주식 MVP 이후 별도 `FuturesBrokerAdapter`, `FuturesRiskManager`로 확장한다.

## 운용모드

- `SIMULATION`: 가짜 데이터와 MockBroker만 사용
- `PAPER`: 실제 또는 저장 시세 + 모의투자 주문
- `LIVE_SHADOW`: 실제 계좌/시세 기반 신호 기록, 주문 금지
- `LIVE_MANUAL_APPROVAL`: 실제 주문 전 사용자 승인 필요
- `LIVE_AI_ASSIST`: AI가 후보와 근거를 제안, 사용자가 승인
- `LIVE_AI_EXECUTION`: 제한된 조건에서 AI 실행 가능, 기본 비활성화

## 작업 방식

- 큰 기능은 작은 PR 단위로 쪼갠다.
- 새 기능은 테스트를 함께 추가한다.
- 금융 관련 로직은 수익률보다 손실 방어와 감사 로그를 우선한다.
- 랜덤 시뮬레이션 결과를 실제 성과로 표현하지 않는다.
- 실제 주문 코드 작성 전 MockBroker, 테스트, 실패 케이스를 먼저 구현한다.

## 우선 구현 순서

1. backend FastAPI skeleton
2. 운용모드 enum
3. BrokerAdapter interface
4. MockBroker
5. RiskManager
6. API routes: status, risk, broker mock
7. tests
8. frontend 랜덤 로직 제거 및 backend 연결
9. DB schema
10. BacktestEngine
