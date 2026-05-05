# Broker Selection

| 구분 | 1차 적용 | 장점 | 주의점 |
|---|---|---|---|
| MockBroker | 즉시 적용 | 키 없이 개발 가능, 테스트 쉬움 | 실제 시장 체결과 다름 |
| KIS Open API | 2차 | 공식 샘플과 Python 예제 풍부 | 모의투자/실전 서버 구분 필요 |
| Kiwoom REST API | 3차 | 국내주식, 조건검색 확장 여지 | 기존 OCX OpenAPI+와 혼동 금지 |
| FuturesBrokerAdapter | Phase 8 이후 | 선물 확장 | 증거금·레버리지·강제청산 위험 큼 |

## 결정

- MVP: MockBroker + KIS 모의투자
- 이후: Kiwoom REST 추가
- 선물: 주식 MVP 안정화 후 별도 모듈

## 참고

- https://apiportal.koreainvestment.com/
- https://github.com/koreainvestment/open-trading-api
- https://openapi.kiwoom.com/
