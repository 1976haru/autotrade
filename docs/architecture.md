# Architecture

## 목표

기존 React UI는 관제·설정·승인 화면으로 유지하고, 실제 자동매매 엔진은 backend에 둔다.

```text
frontend/PWA
  ↓ REST API
backend/FastAPI
  ↓
RiskManager → PermissionGate → OrderExecutor
  ↓
BrokerAdapter
  ├─ MockBrokerAdapter
  ├─ KisBrokerAdapter
  ├─ KiwoomRestBrokerAdapter
  └─ FuturesBrokerAdapter (Phase 8 이후)
```

## 핵심 모듈

| 모듈 | 역할 |
|---|---|
| core/modes | 운용모드와 허용 기능 정의 |
| brokers | 브로커별 API 어댑터 |
| risk | 손실한도, 노출한도, 긴급정지 |
| execution | 주문 실행과 감사 로그 |
| market | 시세 수집, 캔들 생성, freshness 검사 |
| strategies | 전략 신호 계산 |
| backtest | 과거 데이터 검증 |
| ai | 리포트, 후보 제안, 로그 분석 |

## AI 자동매매 확장 원칙

AI 자동실행은 최종 단계에서만 허용한다. 코드 레벨에서는 `ENABLE_AI_EXECUTION=false`를 기본값으로 유지한다.
