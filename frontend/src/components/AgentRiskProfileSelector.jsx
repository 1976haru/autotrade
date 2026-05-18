/**
 * #4-RiskProfileUI: AI Paper 운용 성향 선택 컴포넌트.
 *
 * 3 라디오 카드 (보수적 / 안정적 / 공격적) 중 1 개 선택. 기본값 BALANCED.
 *
 * CLAUDE.md 절대 원칙 (테스트로 lock):
 *   1. 본 카드는 *advisory selector* — 실거래 / 주문 / 정책 변경 0건.
 *   2. broker / 주문 / route_order 호출 0건 — 선택값 props 콜백만.
 *   3. "지금 매수" / "지금 매도" / "Place Order" / "실거래 시작" /
 *      "ENABLE_LIVE_TRADING" 라벨 button 0개.
 *   4. AGGRESSIVE 선택 시에도 *실거래 안전장치 우회 불가* 문구 영구 노출.
 *   5. secret / API key / 계좌번호 입력 form 0건.
 *
 * props:
 *   - value         : 선택된 프리셋 ("CONSERVATIVE" | "BALANCED" | "AGGRESSIVE")
 *   - onChange(p)   : 선택 변경 콜백 (string p)
 *   - disabled      : 라디오 비활성화 (start 직후 등)
 *
 * 사용:
 *   const [profile, setProfile] = useState("BALANCED");
 *   <AgentRiskProfileSelector value={profile} onChange={setProfile} />
 */


export const RISK_PROFILES = [
  {
    value: "CONSERVATIVE",
    label: "보수적",
    summary: "진입 적게, 손실 방어 우선",
    detail: (
      "confidence 임계 높음 · risk_flag 허용 적음 · " +
      "1회 거래 손실 한도 0.5% · 1 종목 비중 10% · 동시 후보 2종목"
    ),
    color: "#3b82f6",
    bg:    "#eff6ff",
    border: "#bfdbfe",
  },
  {
    value: "BALANCED",
    label: "안정적 (기본값)",
    summary: "수익과 위험 균형",
    detail: (
      "confidence 임계 0.40 · risk_flag 1개까지 허용 · " +
      "1회 손실 한도 1.0% · 1 종목 비중 20% · 동시 후보 3종목"
    ),
    color: "#22c55e",
    bg:    "#f0fdf4",
    border: "#bbf7d0",
  },
  {
    value: "AGGRESSIVE",
    label: "공격적",
    summary: "후보 더 많이 허용 · 단 안전장치 우회 불가",
    detail: (
      "confidence 임계 0.30 · risk_flag 2개까지 허용 · " +
      "1회 손실 한도 2.0% · 1 종목 비중 30% · 동시 후보 5종목"
    ),
    color: "#f59e0b",
    bg:    "#fffbeb",
    border: "#fde68a",
  },
];


export const DEFAULT_RISK_PROFILE = "BALANCED";


function _ProfileCard({ profile, selected, onSelect, disabled }) {
  const active = selected === profile.value;
  return (
    <button
      type="button"
      data-testid={`risk-profile-card-${profile.value}`}
      data-selected={active ? "true" : "false"}
      onClick={() => { if (!disabled) onSelect(profile.value); }}
      disabled={disabled}
      style={{
        flex: "1 1 0",
        minWidth: 0,
        textAlign: "left",
        padding: "10px 12px",
        borderRadius: 8,
        border: `2px solid ${active ? profile.color : "var(--c-border)"}`,
        background: active ? profile.bg : "var(--c-surface)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.6 : 1,
        boxShadow: active ? `0 0 0 2px ${profile.color}33` : "none",
        transition: "border-color 150ms, background 150ms",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 4,
        }}
      >
        <span
          data-testid={`risk-profile-radio-${profile.value}`}
          aria-checked={active}
          role="radio"
          style={{
            display: "inline-block",
            width: 14,
            height: 14,
            borderRadius: "50%",
            border: `2px solid ${active ? profile.color : "#94a3b8"}`,
            background: active ? profile.color : "transparent",
            flex: "0 0 14px",
          }}
        />
        <strong
          style={{
            fontSize: 13,
            color: active ? profile.color : "var(--c-text-1)",
          }}
        >
          {profile.label}
        </strong>
      </div>
      <div
        style={{ fontSize: 12, color: "var(--c-text-2)", marginBottom: 4 }}
      >
        {profile.summary}
      </div>
      <div
        style={{
          fontSize: 11,
          color: "var(--c-text-3)",
          lineHeight: 1.4,
        }}
      >
        {profile.detail}
      </div>
    </button>
  );
}


function _AggressiveSafetyNote() {
  return (
    <div
      data-testid="risk-profile-aggressive-warning"
      style={{
        marginTop: 6,
        padding: "6px 10px",
        background: "#fef2f2",
        border: "1px solid #fecaca",
        borderRadius: 6,
        fontSize: 11,
        color: "#991b1b",
      }}
    >
      ⚠ <strong>공격적</strong> 모드도 <strong>실거래 안전장치를 우회하지
      않습니다</strong>. ENABLE_LIVE_TRADING=false 기본값 유지, KIS 모의투자
      한정. 실제 LIVE 활성화는 별도 옵트인 PR + 운영자 명시 승인이 필요합니다.
    </div>
  );
}


export default function AgentRiskProfileSelector({
  value,
  onChange,
  disabled = false,
}) {
  const selected =
    value && RISK_PROFILES.some((p) => p.value === value)
      ? value
      : DEFAULT_RISK_PROFILE;

  const handleSelect = (next) => {
    if (typeof onChange === "function" && next !== selected) {
      onChange(next);
    }
  };

  return (
    <div data-testid="agent-risk-profile-selector">
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginBottom: 8,
        }}
      >
        <span
          data-testid="risk-profile-section-label"
          style={{
            fontSize: "var(--fs-xs)",
            color: "var(--c-text-3)",
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            fontWeight: "var(--fw-bold)",
          }}
        >
          AI 운용 성향
        </span>
        <span
          data-testid="risk-profile-paper-only-badge"
          style={{
            display: "inline-block",
            padding: "1px 6px",
            borderRadius: 3,
            background: "#bef264",
            color: "#0f172a",
            fontSize: 10,
            fontWeight: "var(--fw-bold)",
          }}
        >
          Paper 전용 · 실거래 아님
        </span>
        <span
          data-testid="risk-profile-default-note"
          style={{ fontSize: 11, color: "var(--c-text-3)" }}
        >
          기본값: 안정적
        </span>
      </div>

      <div
        role="radiogroup"
        aria-label="AI 운용 성향 선택"
        data-testid="risk-profile-radiogroup"
        data-selected={selected}
        style={{ display: "flex", gap: 8, flexWrap: "wrap" }}
      >
        {RISK_PROFILES.map((p) => (
          <_ProfileCard
            key={p.value}
            profile={p}
            selected={selected}
            onSelect={handleSelect}
            disabled={disabled}
          />
        ))}
      </div>

      {selected === "AGGRESSIVE" ? <_AggressiveSafetyNote /> : null}

      <div
        data-testid="risk-profile-footer-note"
        style={{
          marginTop: 8,
          fontSize: 11,
          color: "var(--c-text-3)",
          lineHeight: 1.4,
        }}
      >
        선택된 성향은 AI Paper 자동매매의 진입 기준 / position size /
        risk flag 허용 범위에 반영됩니다. 실거래 주문이 발생하지 않으며,
        is_order_signal=false / auto_apply_allowed=false /
        is_live_authorization=false 가 영구 유지됩니다.
      </div>
    </div>
  );
}
