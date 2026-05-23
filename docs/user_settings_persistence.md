# 사용자 설정 영구 저장 (P-16)

> Paper 자금 운용 기준을 **EXE 재실행 후에도 유지**되도록 OS 사용자 설정
> 폴더의 JSON 파일에 저장한다. **API key / Secret / 계좌번호 / 토큰은 절대
> 저장하지 않는다** — 그것들은 `.env` 만의 책임이며 본 저장소와 분리된다.

## 1. 무엇을 저장하는가

`paper_capital_settings.json` 의 `settings` 블록에는 **Paper 자금 기준 7종만**
저장된다.

| 키 | 의미 | 기본값 |
|---|---|---|
| `total_paper_capital` | Paper 시드머니 (KRW) | 10,000,000 |
| `per_symbol_allocation` | 종목당 투자금 (KRW) | 1,000,000 |
| `max_positions` | 최대 보유 종목 수 | 5 |
| `max_daily_buy_amount` | 일일 최대 매수금액 (KRW) | 3,000,000 |
| `max_symbol_weight_pct` | 종목별 최대 비중 (0~1) | 0.2 |
| `allow_additional_buy` | 동일 종목 추가매수 허용 | `false` |
| `risk_profile` | AI 운용 성향 | `BALANCED` |

## 2. 절대 저장하지 않는 것 (fail-closed)

- API key / App Secret
- 계좌번호 / 비밀번호
- access token / refresh token
- Anthropic / OpenAI / KIS key
- 실거래 허가 정보

저장 전 `scan_for_secrets()` 가 위 secret-like **key 이름** 과 **value 패턴**
(`sk-`, `sk-ant-`, `ghp_`, `Bearer …`, `PST…`, 한국 계좌번호 형태, JWT) 을
검사하고, 적중 시 `SecretInSettingsError` 로 **저장을 거부** 한다 (redaction
아님 — fail-closed). API 는 이때 `400 secret_in_settings_blocked` 를 반환한다.

또한 허용된 7개 키 외의 키는 저장 단계에서 무시된다(`validate_paper_capital_settings`
가 default + 허용 키만 통과).

## 3. 저장 위치 (`.env` 와 분리)

우선순위:

1. `AGENT_TRADER_CONFIG_DIR` 환경변수 (테스트 / 운영자 override)
2. OS 사용자 설정 폴더:
   - Windows: `%APPDATA%\Autotrade\config\paper_capital_settings.json`
   - macOS: `~/Library/Application Support/Autotrade/config/…`
   - Linux: `$XDG_CONFIG_HOME/Autotrade/config/…` 또는 `~/.config/Autotrade/config/…`
3. fallback: `backend/.runtime/config/…` (gitignore)

`.env` 는 프로젝트 루트/배포 폴더에 위치하며 secret 을 보관한다. 본 설정
파일은 **OS 사용자 폴더** 에 위치하여 물리적으로 분리된다.

## 4. 파일 구조

```json
{
  "version": 1,
  "updated_at": "2026-05-23T01:23:45.678901+00:00",
  "settings": {
    "total_paper_capital": 30000000,
    "per_symbol_allocation": 2000000,
    "max_positions": 8,
    "max_daily_buy_amount": 5000000,
    "max_symbol_weight_pct": 0.3,
    "allow_additional_buy": false,
    "risk_profile": "AGGRESSIVE"
  },
  "safety": {
    "is_live_authorization": false,
    "is_order_signal": false,
    "contains_secret": false
  }
}
```

원자적 쓰기: 임시 파일(`.tmp_*.json`)에 쓰고 `os.replace` 로 rename — 부분
쓰기로 파일이 깨지지 않는다. 파일이 깨졌으면 로드 시 `DEFAULT_CORRUPTED` 로
기본값 복구 + 경고.

## 5. Backend API

`app/api/routes_auto_paper.py` (`/api/auto-paper` prefix):

| 메서드 | 경로 | 동작 |
|---|---|---|
| `GET` | `/paper-capital-settings` | 저장값 로드 (`PERSISTED`) 또는 기본값 (`DEFAULT`) |
| `POST` | `/paper-capital-settings` | 검증 + secret scan + atomic 저장 |
| `POST` | `/paper-capital-settings/reset` | 저장 파일 삭제 + 기본값 |

모든 응답은 `is_live_authorization=false` / `is_order_signal=false` /
`contains_secret=false` / `safe_for_ui=true` 를 carry 한다. broker /
OrderExecutor / route_order 호출 0건.

> 기존 `POST /paper-capital-settings/validate` (P-15) 는 *검증/echo 전용* 으로
> 그대로 유지되며 파일을 쓰지 않는다.

## 6. Frontend 연결

`frontend/src/store/usePaperCapitalSettings.js` 의 hook 은 `api` 를 넘기면
backend 영속과 연결된다 (opt-in):

- mount: backend GET → 있으면 적용 + localStorage mirror, 실패 시 localStorage
  fallback.
- setField / setAll: localStorage 저장 + backend POST mirror (`saveStatus`:
  `saving → saved / error`).
- reset: localStorage 비움 + backend reset.

`PaperCapitalSettingsCard` 는 Settings 탭에서 `api={backendApi}` 로 렌더되어
하단에 저장 상태 ("저장됨 — 재실행 후에도 유지됩니다") + 저장 위치 라벨 +
".env 파일과 분리" 안내를 표시한다.

`AutoPaperLoopCard` 는 mount 시 backend 영속 값을 1회 로드해 localStorage 에
mirror 하므로, Settings 탭을 방문하지 않아도 시작 payload(`capital_settings`)
가 **재실행 후에도 유지된 자금 기준** 을 동봉한다.

## 7. 검증 규칙 (backend / frontend 동일)

- `total_paper_capital` ∈ [100,000, 10,000,000,000]
- `per_symbol_allocation` ∈ [10,000, …] 그리고 `<= total_paper_capital`
- `max_positions` ∈ [1, 100] (정수)
- `max_daily_buy_amount` ∈ [10,000, …] 그리고 `<= total_paper_capital`
- `max_symbol_weight_pct` ∈ (0, 1]
- `allow_additional_buy` boolean (default `false`)
- `risk_profile` ∈ {CONSERVATIVE, BALANCED, AGGRESSIVE} (그 외 → BALANCED)

검증 실패 필드는 적용하지 않고 default 로 두며 `errors` 라벨만 carry 한다
(잘못된 입력으로 기존 저장값을 덮어쓰지 않음).

## 8. CLAUDE.md 절대 원칙 매핑

- 본 모듈/엔드포인트는 broker / OrderExecutor / route_order / 외부 HTTP / AI
  SDK import 0건, DB write 0건.
- 안전 flag (`ENABLE_LIVE_TRADING` 등) 변경 0건 — 자금 *기준* 만 저장.
- AGGRESSIVE 성향도 **실거래 권한이 아니다** — `is_live_authorization=false`
  불변.
