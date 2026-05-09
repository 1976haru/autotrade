# Agent Memory

본 문서는 [`AgentMemory`](../backend/app/db/models.py) 테이블 + [`agent_memory.py`](../backend/app/agents/agent_memory.py) 모듈의 정책 contract를 정의한다. Agent와 운영자가 과거 손실 원인 / 전략 변경 이력 / 위험 사례 / 운영자 메모를 *검색 가능*한 형태로 보관하는 학습 저장소.

## ⚠ 본 메모리는 *주문 신호가 아닙니다*.

검색 결과로 직접 BUY/SELL/HOLD 결정을 만들지 않으며, RiskManager / PermissionGate / OrderExecutor 우회에 사용 *X*. 모든 실 주문 흐름은 기존 sanctioned 경로(`route_order` → RiskManager → PermissionGate → OrderExecutor)를 거친다.

## 1. 목적

- **반복 실수 방지** — 과거 손실 / 위험 사례를 검색해 동일 패턴 회피
- **전략 개선 이력 축적** — 어떤 변경이 어떤 결과로 이어졌는지 추적
- **과거 손실 원인 검색** — strategy / symbol / tag 기반 빠른 조회
- **운영자 메모** — 시스템 자동 분석 외 운영자 도메인 지식 보관

## 2. 저장 대상 (`MemoryType` 8종)

| 값 | 의미 |
|---|---|
| `daily_report` | #57 Daily Report 요약 (markdown 앞 1000 chars) |
| `risk_incident` | #54 Risk Auditor 결과 / 장중 위험 사례 |
| `strategy_research` | #55 Strategy Researcher 분석 |
| `backtest_review` | 백테스트 결과 운영자 코멘트 |
| `agent_decision` | Agent 결정 회고 |
| `operator_note` | 운영자 자유 메모 |
| `loss_post_mortem` | 손실 사례 분석 |
| `lesson_learned` | 일반 교훈 / 주의점 |

## 3. 저장 *금지* 대상 (절대 invariant)

`sanitize_text()`가 INSERT 전 모든 텍스트에서 패턴 검사 — 적중 시 `SecretLeakError`로 *raise* (fail-closed). 저장 자체를 차단하므로 redaction이 아닌 *원천 거부*.

| 카테고리 | 차단 패턴 |
|---|---|
| API key | `sk-...` 20+ chars (Anthropic / OpenAI / generic) |
| App secret | `app_key=` / `app_secret=` / `access_token=` 라벨 + 16+ chars |
| 한국 계좌번호 | `2-4-8` / `2-3` 패턴 또는 10-14 자리 연속 숫자 |
| 신용카드 | 13-19 digit 패턴 (공백/하이픈 포함) |
| 한국 주민등록번호 | `6-7` 패턴 |
| JWT | `eyJ...` 3-segment base64 |
| 이메일 | 표준 RFC 5322 패턴 |
| 한국 휴대전화 | `010-XXXX-XXXX` 등 |

`sanitize_dict` / `sanitize_tags` 도 동일 패턴을 재귀적으로 검사. 캐치 못한 패턴은 운영자가 수동 검토 후 PR로 패턴 추가 (테스트로 lock).

## 4. 검색 정책

### keyword / tag 기반 (현재 PR)
- `keyword`: title / summary / lessons / next_action LIKE 검색
- `tag`: JSON 배열 contains (in-memory filter — 작은 집합 가정)
- `memory_type` / `source_kind` / `strategy` / `symbol` / `mode` / `severity`: 정확 일치 필터
- `include_archived`: default False

### vector / semantic search (후속 PR — 별도 옵트인)
- 본 PR 시점 미구현. embedding model / vector DB 도입은 별도 PR.
- 도입 시 personal info / API key가 embedding으로 *우회 저장되지 않는지* 추가 검증 필요.

## 5. Agent 활용 정책

| 행위 | 허용 / 금지 |
|---|---|
| 과거 사례를 운영자에게 *advisory*로 표시 | ✓ 허용 |
| 과거 사례를 다른 Agent의 입력으로 carry | ✓ 허용 (단, 검색 결과는 결정 신호 아님) |
| 검색 결과로 *직접* BUY/SELL/HOLD 결정 생성 | ✗ 금지 (RiskManager 우회 시도로 간주) |
| 검색 결과로 *직접* approval queue 등록 | ✗ 금지 (sanctioned `submit_candidate` 흐름만) |
| 검색 결과로 RiskManager 한도 동적 완화 | ✗ 금지 |
| 검색 결과로 strategy 코드 / 파라미터 자동 변경 | ✗ 금지 (운영자 검토 + 별도 PR 필수) |

## 6. 운영 예시

```bash
# 최근 VWAP 전략 손실 원인 검색
GET /api/agents/memory/search?strategy=vwap&memory_type=loss_post_mortem

# 삼성전자 관련 과거 손실 원인 검색
GET /api/agents/memory/search?symbol=005930&memory_type=loss_post_mortem

# Agent 과신으로 차단된 사례 검색
GET /api/agents/memory/search?tag=ai_overconfidence

# CRITICAL 이상 위험 사례
GET /api/agents/memory/search?severity=CRITICAL&include_archived=false
```

### 자동 ingest helpers
- `memory_from_daily_report_markdown(report_date, markdown, ...)` — Daily Report → AgentMemory
- `memory_from_strategy_research_report(strategy, run_id, audit_level, summary, ...)` — Strategy Researcher → AgentMemory
- `memory_from_risk_audit_report(audit_level, risk_score, summary, ...)` — Risk Auditor → AgentMemory

자동 저장은 *optional* — 기본은 운영자가 endpoint를 명시적으로 호출 (자동 cron 도입은 별도 PR).

## 7. 안전 원칙 (절대 invariant)

| 원칙 | 가드 |
|---|---|
| **broker / OrderExecutor / route_order import 0건** | 정적 grep 가드 |
| **주문 객체 0건** — `OrderRequest` / `AICandidate` / `ExecutionProposal` 생성 0건 | 정적 grep 가드 |
| **approval queue 등록 0건** — `submit_candidate(` / `route_order(` 호출 0건 | 정적 grep 가드 |
| **외부 AI / HTTP 호출 0건** | anthropic / openai / httpx / requests / urllib3 import 0건 |
| **민감정보 저장 0건** | `sanitize_text` fail-closed 가드 |
| **`is_order_signal=False` 불변** | dataclass `__post_init__` ValueError |
| **DELETE 미사용** | archive flag로 대체 (audit 보존) |
| **BUY/SELL/HOLD enum 값 0개** | `MemoryType` / `SourceKind` / `MemorySeverity`에 0개 (테스트로 lock) |

## 8. UI

[`frontend/src/components/tabs/AgentMemoryCard.jsx`](../frontend/src/components/tabs/AgentMemoryCard.jsx) — Agent / Settings / Reports 탭에 마운트.

**필수 표시**:
- "주문 신호 아님 · 과거 학습 기록" 배지 (prominent)
- 검색창 (keyword)
- memory type / strategy / symbol filter
- 최근 메모 list (severity 색상 + tags)
- 메모 상세 view (lessons / next_action)
- archive 버튼
- 운영 메모 추가 폼 + "API key / Secret / 계좌번호 / 개인정보 입력 금지" 안내
- disclaimer notice ("주문 신호 아님 / BUY/SELL/HOLD 결정 / 자동 주문 / 승인 큐 등록에 사용되지 않습니다")

**금지된 UI 요소** (테스트로 lock):
- BUY / SELL / HOLD 버튼
- "매수 실행" / "매도 실행" / "즉시 주문" / "Place Order" / "Submit Order" 버튼
- "승인 큐 보내기" / "승인 대기 보내기" 같은 결정 trigger 버튼

**Compact mode** (Dashboard 등에 임베드):
- 검색창만 + 최근 3개 항목만 표시
- 운영 메모 추가 폼 / type filter 숨김

## 9. 후속 과제 (별도 PR)

- **Vector embedding** — semantic search (도입 시 secret 패턴 재검토 필수)
- **Memory consolidation** — 중복 / 오래된 항목 통합
- **Reminder** — 특정 패턴 (예: AI overconfidence) 누적 시 운영자에게 알림
- **자동 ingest cron** — 장 마감 후 Daily Report → AgentMemory 자동 저장 (운영자 옵트인)
- **Frontend 통합** — Dashboard 카드 / 결재 카드에서 관련 memory 표시
- **타임라인 뷰** — strategy / symbol별 시간 추이 시각화
- **Export** — 운영자 검토용 markdown export
- **Memory link to audit row** — `source_id`로 양방향 링크 (현재는 단방향)

## 관련 문서

- [`agent_design.md`](agent_design.md) — Agent 분리 정책
- [`agent_architecture.md`](agent_architecture.md) — 6개 표준 Agent 역할 (#51)
- [`daily_report_agent.md`](daily_report_agent.md) — Daily Report (#57) — ingest 소스
- [`strategy_researcher_agent.md`](strategy_researcher_agent.md) — Strategy Researcher (#55) — ingest 소스
- [`risk_auditor_agent.md`](risk_auditor_agent.md) — Risk Auditor (#54) — ingest 소스
- `backend/app/db/models.py::AgentMemory` — DB 모델
- `backend/alembic/versions/20260524_0020_agent_memory.py` — migration
- `CLAUDE.md` — 절대 원칙 4번 (API Key / Secret / 계좌번호 절대 frontend / git 미저장)
