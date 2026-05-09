# 에이전트 트레이더 v1 사용자 가이드 (A4 1장)

> **Agent Trader v1 · v1.0.0** — 시스템 사용 설명서. *투자 조언이 아닙니다.*

## 1. 이 프로그램은 무엇인가?

AI 에이전트가 시장 / 전략 / 리스크를 *분석*하고, 사용자(운영자)는 결과와 위험을 *확인*해 시작 / 일시정지 / 긴급중단 / 승인을 결정하는 **자동매매 관제 시스템**입니다. 본 시스템은 *수익을 보장하지 않으며*, 사용자 의사결정 부담을 줄이는 보조 도구입니다.

## 2. 지금 버전(v1)에서 가능한 것

- ✓ GitHub Pages **Demo 화면** 확인 (백엔드 없이도 UI 구조 체험)
- ✓ Mock / Virtual / Paper 모드 자동매매 *구조* 검증
- ✓ Agent 판단 요약 (시장 regime / 전략 / 리스크 / 승인) 확인
- ✓ 리스크 / 승인 / Audit Log 검토
- ✗ 실제 실거래는 **기본 비활성화** — 운영자 별도 옵트인 절차 필요

## 3. 사용자가 봐야 할 핵심 화면

| 탭 | 용도 |
|---|---|
| **홈 (대시보드)** | 운용 모드 / Agent 판단 / 손익 / 긴급중단 / 승인 대기 — 한 화면 |
| **에이전트** | AI 결정 hero / 전략 선택 / 시장 regime 등 |
| **승인** | LIVE_AI_ASSIST 큐 — 결재 / 거부 / 취소 |
| **리스크** | RiskManager 정책 / Kill Switch / shadow trade |
| **로그** | OrderAuditLog / 결재 history / Agent decisions |
| **설정** | 모드 / 운영자 / 안전 flag / 버전 / 사용자 가이드 / 도움말 |

## 4. 기본 사용 순서

1. 대시보드에서 **현재 상태** 확인 (운용 모드 / 백엔드 연결 / 긴급중단)
2. **Agent 판단** 확인 — Hero 카드 + 전략 chip
3. **리스크 상태** 확인 — Risk Auditor / 긴급정지 이력
4. **승인 대기** 항목 확인 (있다면 사유 / 사전검사 결과)
5. 필요 시 **시작 / 일시정지 / 긴급중단** 버튼 사용 (모바일은 OperatorPanel)
6. 장 종료 후 **Daily Report** 확인 (운영 / 검증 / 개선 자료)

## 5. 가장 중요한 주의사항

- 🚨 본 프로그램은 **수익 보장 도구가 아닙니다**.
- 🚨 실거래 전 **Paper / Shadow / Manual Approval** 검증이 필수입니다.
- 🚨 AI 판단은 *참고자료*이며, 최종 책임은 사용자에게 있습니다.
- 🚨 **긴급중단 버튼 위치를 반드시 숙지하세요** (모바일 OperatorPanel + 데스크톱 상단).
- 🚨 **API key / Secret / 계좌번호 / 비밀번호를 화면이나 git에 입력하지 마세요**. 시스템이 자동 차단하지만 1차 방어는 사용자입니다.

## 6. 운용 모드 설명

| 모드 | 설명 |
|---|---|
| `SIMULATION` | 가짜 데이터 + Mock Broker. 학습 / 화면 체험용 |
| `PAPER` | 실 시세 + 모의투자 (가상 자금). KIS 모의투자 계정 필요 |
| `LIVE_SHADOW` | 실 계좌 read-only. 주문 *X*, 추정 기록만 |
| `LIVE_MANUAL_APPROVAL` | 사람 승인 필요. 모든 주문이 큐를 거침 |
| `LIVE_AI_ASSIST` | AI 후보 + 사람 승인. 본 v1에서 핵심 흐름 |
| `LIVE_AI_EXECUTION` | 최종 단계. **기본 비활성** — 8개 옵트인 조건 |

## 7. 문제 발생 시

| 증상 | 대처 |
|---|---|
| "백엔드 연결 대기" 빨강 banner | 로컬: `cd backend && uvicorn app.main:app --reload`. Pages: 자동 demo 모드. |
| 데이터 지연 (시세 stale) | 시장 데이터 collector 재시작. 거래 일시정지. |
| 승인 실패 | 결재 시 RiskManager 재검증이 차단. 사유 확인 후 재시도. |
| 긴급중단 | 모바일 OperatorPanel 또는 PC StrategyRisk 탭의 Kill Switch. |
| 문의 / 개선사항 | 설정 탭의 **도움말 / 문의** 버튼 (mailto / 클립보드 복사). |

---

> 본 가이드는 *시스템 사용 설명*이며 **투자 조언이 아닙니다**. 자동매매 결정 / 자금 운용은 사용자 책임입니다.
> 자세한 정책: [`agent_design.md`](agent_design.md), [`smartphone_operator_mode.md`](smartphone_operator_mode.md), [`promotion_policy.md`](promotion_policy.md), [`risk_policy.md`](risk_policy.md).
