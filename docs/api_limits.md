# API Limits

초기 단계에서는 정확한 호출 제한값을 코드에 하드코딩하지 않는다. 브로커별 공식 문서를 확인한 뒤 아래 정책을 채운다.

| 브로커 | API | 제한 | 대응 |
|---|---|---|---|
| KIS | OAuth token | 확인 필요 | 토큰 캐시, 만료 전 갱신 |
| KIS | quote/order | 확인 필요 | SlidingWindowRateLimiter 적용 |
| Kiwoom REST | token/order/quote | 확인 필요 | 요청 큐, 재시도 제한 |

## 공통 정책

- 실패 시 무한 재시도 금지
- 주문 API는 일반 조회 API보다 강한 제한 적용
- 429/오류코드 발생 시 신규 주문 중지
- 재시도는 exponential backoff 적용
