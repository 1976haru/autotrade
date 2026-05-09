import { Card, SectionLabel } from "../common";
import { FuturesOrderAuditCard } from "./FuturesOrderAuditCard";
import { FuturesMarginRiskCard } from "./FuturesMarginRiskCard";

// 50: Futures UI는 *Simulation Only / Read-only / 위험 안내* 화면이다.
// 본 파일은 두 개의 export를 노출:
//   - `Futures`              — feature flag(`FEATURES.futuresTab=true`)일 때 렌더
//   - `FuturesDisabledNotice` — flag false에서 forced URL 접근 시 안전 안내 화면
//
// 절대 invariant:
//   - 실제 선물 broker API 호출 0건 (broker 호출 트리거 코드 미존재)
//   - 모든 주문 버튼 disabled — onClick 실제 동작 없음
//   - "실제 주문 가능"처럼 보이는 green primary CTA 없음
//   - backend `ENABLE_FUTURES_LIVE_TRADING=False` invariant와 *별개* — 본
//     UI는 그 위에 추가되는 노출 정책 레이어
//
// 자세한 정책: docs/futures_ui.md (#50).


// ====================================================================
// 0. Forced-access disabled notice (feature flag false)
// ====================================================================

export function FuturesDisabledNotice() {
  return (
    <div data-testid="futures-disabled-notice"
         style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Card accentColor="#94a3b855">
        <div style={{ fontSize: 14, fontWeight: 700, color: "#e2e8f0",
                       marginBottom: 6 }}>
          🪙 선물 기능 비활성화 — UI 노출 차단됨
        </div>
        <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.7 }}>
          Futures 탭은 기본값으로 navigation에 노출되지 않습니다. 본 화면은
          URL/state로 강제 접근했을 때 표시되는 *안전 안내*입니다.
        </div>
        <div style={{ marginTop: 8, fontSize: 10, color: "#64748b",
                       lineHeight: 1.6 }}>
          본 프로젝트는 주식 자동매매 연구 플랫폼으로, 선물 기능은{" "}
          <strong style={{ color: "#94a3b8" }}>Simulation Only / Read-only</strong>
          {" "}화면입니다. 활성화는 환경변수{" "}
          <code style={{ color: "#7dd3fc" }}>VITE_ENABLE_FUTURES_TAB=true</code>
          {" "}+ 운영자 명시 검토 후에만 노출됩니다.
        </div>
      </Card>

      <_ConfusionPreventionBanner />
    </div>
  );
}


// ====================================================================
// 1. 주식/선물 혼동 방지 banner (Futures 화면 최상단 고정)
// ====================================================================

function _ConfusionPreventionBanner() {
  return (
    <div data-testid="futures-confusion-banner">
      <Card accentColor="#a78bfa55">
        <div style={{ fontSize: 12, fontWeight: 700, color: "#a78bfa",
                       marginBottom: 4 }}>
          ⚠ 이 화면은 주식 자동매매 화면이 *아닙니다*.
        </div>
        <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.7 }}>
          선물은 별도 리스크 체계(레버리지 / 증거금 / 강제청산 / 만기 / 롤오버)로
          관리됩니다. 현재는 가상 선물 시뮬레이션 / 감사 정보만 표시합니다 —
          실제 선물 주문은 발생하지 않습니다.
        </div>
      </Card>
    </div>
  );
}


// ====================================================================
// 2. Disabled banner (Simulation Only / Read-only badges)
// ====================================================================

function _Badge({ label, color, testId }) {
  return (
    <span data-testid={testId} style={{
      fontSize: 9, fontWeight: 700, color,
      padding: "2px 8px", borderRadius: 3,
      border: `1px solid ${color}66`, background: `${color}15`,
      whiteSpace: "nowrap",
    }}>
      {label}
    </span>
  );
}


function _DisabledBanner() {
  return (
    <div data-testid="futures-disabled-banner">
      <Card accentColor="#ef444455">
        <div style={{ fontSize: 13, fontWeight: 700, color: "#ef4444",
                       marginBottom: 6 }}>
          🚫 선물 기능은 현재 비활성화되어 있습니다
        </div>
        <div style={{ fontSize: 11, color: "#94a3b8",
                       lineHeight: 1.7, marginBottom: 8 }}>
          이 화면은 가상 시뮬레이션 / 감사 정보 확인용입니다. 실제 선물 주문은
          실행되지 않습니다.
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          <_Badge label="Simulation Only" color="#a78bfa"
                   testId="futures-badge-simulation-only" />
          <_Badge label="Read-only" color="#7dd3fc"
                   testId="futures-badge-readonly" />
          <_Badge label="FUTURES_LIVE OFF" color="#ef4444"
                   testId="futures-badge-live-off" />
          <_Badge label="실제 주문 0건" color="#94a3b8"
                   testId="futures-badge-zero-orders" />
        </div>
      </Card>
    </div>
  );
}


// ====================================================================
// 3. Risk warning (선물 고유 위험)
// ====================================================================

function _RiskWarning() {
  const items = [
    { icon: "⚡", text: "레버리지 — 손실이 증거금을 초과할 수 있다" },
    { icon: "💰", text: "증거금 — initial / maintenance / margin call" },
    { icon: "🚨", text: "강제청산 — broker가 자동으로 포지션을 청산할 수 있다" },
    { icon: "📅", text: "만기 / 롤오버 — 근월물 만기 시 차월물로 수동 전환 필요" },
    { icon: "🌙", text: "야간 / 해외 리스크 — 24시간 시간대 + 환율" },
    { icon: "🤖", text: "AI 자동매매는 선물에서 더 엄격한 권한이 필요하다" },
  ];
  return (
    <div data-testid="futures-risk-warning">
      <Card accentColor="#f59e0b55">
        <SectionLabel>⚠ 선물 고유 위험</SectionLabel>
        <ul style={{ margin: 0, padding: "4px 0 0 18px",
                      fontSize: 11, color: "#fbbf24", lineHeight: 1.9 }}>
          {items.map((it, i) => (
            <li key={i}>
              <span style={{ marginRight: 6 }}>{it.icon}</span>
              <span style={{ color: "#e2e8f0" }}>{it.text}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}


// ====================================================================
// 4. Safety matrix
// ====================================================================

function _MatrixRow({ label, value, ok }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between",
      padding: "5px 0", borderBottom: "1px solid #05121f",
      fontSize: 11,
    }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span style={{ color: ok ? "#22c55e" : "#94a3b8", fontWeight: 700,
                      fontFamily: "monospace" }}>
        {value}
      </span>
    </div>
  );
}


function _SafetyMatrix() {
  return (
    <div data-testid="futures-safety-matrix">
      <Card>
        <SectionLabel>다층 안전 가드 (모두 적용 중)</SectionLabel>
        <_MatrixRow label="ENABLE_FUTURES_LIVE_TRADING"
                      value="false (default)" ok={false} />
        <_MatrixRow label="실제 선물 주문"
                      value="blocked" ok={false} />
        <_MatrixRow label="AI 선물 실행"
                      value="blocked" ok={false} />
        <_MatrixRow label="MockFuturesBroker"
                      value="enabled" ok={true} />
        <_MatrixRow label="FuturesRiskManager"
                      value="enabled (#48 margin/leverage/liq rules)" ok={true} />
        <_MatrixRow label="Manual approval required"
                      value="future phase" ok={false} />
        <div style={{ marginTop: 8, fontSize: 10, color: "#64748b",
                       lineHeight: 1.6 }}>
          한 층이 풀려도 다른 층이 선물 주문을 막습니다.
          ENABLE_FUTURES_LIVE_TRADING + 실제 broker adapter + 운영자 명시 opt-in
          + 9-step blocker 체크리스트가 모두 통과해야 활성화됩니다.
        </div>
      </Card>
    </div>
  );
}


// ====================================================================
// 5. Disabled order area
// ====================================================================

function _DisabledOrderArea() {
  return (
    <div data-testid="futures-disabled-orders">
      <Card accentColor="#33415555">
        <SectionLabel>주문 — 전체 비활성</SectionLabel>
        <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 10,
                       lineHeight: 1.7 }}>
          선물 실거래 주문은 별도 승인 전까지 사용할 수 없습니다. 본 영역의
          모든 버튼은 클릭해도 broker로 주문이 가지 않습니다 (onClick 실제
          동작 없음).
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button data-testid="futures-disabled-buy"
                  disabled style={_disabledBtnStyle()}>
            매수 개시 — 비활성
          </button>
          <button data-testid="futures-disabled-sell"
                  disabled style={_disabledBtnStyle()}>
            매도 개시 — 비활성
          </button>
          <button data-testid="futures-disabled-close"
                  disabled style={_disabledBtnStyle()}>
            청산 — 비활성
          </button>
        </div>
      </Card>
    </div>
  );
}


function _disabledBtnStyle() {
  return {
    flex: 1, padding: "8px 16px", borderRadius: "var(--r-md)",
    border: "none", cursor: "not-allowed",
    background: "var(--c-surface-3)", color: "#475569",
    fontWeight: "var(--fw-bold)", fontSize: "var(--fs-sm)",
    fontFamily: "inherit", letterSpacing: "0.02em",
    opacity: 0.6,
  };
}


// ====================================================================
// 6. Activation checklist
// ====================================================================

function _ActivationChecklist() {
  const steps = [
    "주식 MVP 완료 (LIVE_MANUAL_APPROVAL + LIVE_AI_ASSIST 무사고)",
    "Paper / Shadow 검증 (4주+ 무중단)",
    "Futures simulation stress 통과",
    "futures_scope.md 확인 (국내/해외선물 1차 시장 하나만 선택)",
    "Margin / leverage / liquidation risk 검증 (#48)",
    "FuturesAIExecutionGate 추가 (#45 위에 futures-specific 한도)",
    "운영자 명시 opt-in PR + 9-step blocker 체크리스트 통과",
    "별도 PR — 본 PR(#50)은 UI 노출 정책만 정리",
  ];
  return (
    <div data-testid="futures-activation-checklist">
      <Card>
        <SectionLabel>활성화 로드맵 (모든 단계 별도 옵트인 PR)</SectionLabel>
        <ol style={{ margin: 0, padding: "4px 0 0 22px",
                      fontSize: 11, color: "#94a3b8", lineHeight: 1.9 }}>
          {steps.map((s, i) => (
            <li key={i}>
              <span style={{ marginRight: 6, color: "#475569" }}>❌</span>
              <span style={{ color: "#e2e8f0" }}>{s}</span>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}


// ====================================================================
// Main Futures component (feature flag true 시 렌더)
// ====================================================================

export function Futures() {
  return (
    <div data-testid="futures-tab"
         style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* 1. 혼동 방지 banner — 최상단 고정 */}
      <_ConfusionPreventionBanner />

      {/* 2. 비활성 상태 + 안전 배지 */}
      <_DisabledBanner />

      {/* 3. 선물 고유 위험 안내 */}
      <_RiskWarning />

      {/* 4. 다층 안전 가드 매트릭스 */}
      <_SafetyMatrix />

      {/* 5. 마진/레버리지/강제청산 사전 평가 (#48 — read-only) */}
      <FuturesMarginRiskCard />

      {/* 6. 가상 선물 주문 audit (#194/169 — read-only) */}
      <FuturesOrderAuditCard />

      {/* 7. 비활성 주문 영역 — 시각적으로 disabled 명시 */}
      <_DisabledOrderArea />

      {/* 8. 활성화 로드맵 */}
      <_ActivationChecklist />

      <div style={{ fontSize: 10, color: "#1e3a5c", lineHeight: 1.6,
                     padding: "0 4px" }}>
        ⚠ 선물은 레버리지·강제청산·만기 등 추가 위험이 있어 주식보다 엄격한 한도와
        검증을 적용합니다. 실거래 자금 유입은 운영자가 명시적으로 승인한 시점부터만
        가능합니다 (현재 비활성).
      </div>
    </div>
  );
}
