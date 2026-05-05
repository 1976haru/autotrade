# Agent Design

## 에이전트 역할

| Agent | 역할 | 주문 권한 |
|---|---|---:|
| Market Observer | 시장 상태, 데이터 freshness, 변동성 감시 | 없음 |
| News/Trend Agent | 뉴스·트렌드·공시 키워드 정리 | 없음 |
| Strategy Researcher | 전략 후보와 백테스트 개선안 제안 | 없음 |
| Risk Auditor | 손실한도, 중복주문, 위험 이벤트 점검 | 없음 |
| Execution Recommender | 주문 후보와 근거 제시 | 직접 주문 불가 |
| Daily Report Agent | 장 종료 후 성과 리포트 | 없음 |

## AI 자동실행 조건

- `LIVE_AI_EXECUTION` 모드
- `ENABLE_AI_EXECUTION=true`
- RiskManager 승인
- 감사 로그 저장
- 전략 승격 기준 충족
- 선물은 별도 승인 전 자동실행 금지
