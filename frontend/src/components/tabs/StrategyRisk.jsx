import { useState } from "react";

import { STRATEGIES } from "../../config/strategies";
import { RISK_POLICY_FIELDS } from "../../config/riskPolicy";
import { Btn, Card, SectionLabel, Toggle, Slider } from "../common";
import { DecisionDialog } from "../common/DecisionDialog";
import { fmtKRW } from "../../utils/format";


// 199: extended for the broader RiskPolicy surface — pct / seconds / list types
// the original 6-field card never needed.
function _formatPolicyValue(value, kind) {
  if (value == null)    return "—";
  if (kind === "krw")   return `${fmtKRW(value)}원`;
  if (kind === "bool")  return value ? "ON" : "OFF";
  if (kind === "pct")   return `${(Number(value) * 100).toFixed(1)}%`;
  if (kind === "seconds") return `${value}s`;
  if (kind === "list") {
    if (!Array.isArray(value) || value.length === 0) return "(전체)";
    return value.length <= 4 ? value.join(", ") : `${value.length}개`;
  }
  return String(value);
}


// 199: Compare arrays element-wise so an empty list parses as DEFAULT, not
// always-OVERRIDDEN (=== between two distinct empty arrays is false).
export function isPolicyValueOverridden(value, defaultValue) {
  if (Array.isArray(value) && Array.isArray(defaultValue)) {
    return value.length !== defaultValue.length
      || value.some((v, i) => v !== defaultValue[i]);
  }
  return value !== defaultValue;
}


export function PolicyRow({ label, value, envVar, isOverridden }) {
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline",
                     fontSize: 9, color: "#475569", marginBottom: 2, gap: 4 }}>
        <span>{label}</span>
        <span style={{ color: "#334155", fontFamily: "monospace", fontSize: 8 }}>{envVar}</span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontSize: 11, color: "#94a3b8", fontWeight: 700 }}>{value}</span>
        <span style={{
          fontSize: 8,
          fontWeight: 700,
          letterSpacing: "0.06em",
          color:      isOverridden ? "#fbbf24" : "#475569",
          background: isOverridden ? "#fbbf2422" : "transparent",
          border: isOverridden ? "1px solid #fbbf2455" : "1px solid #1a3a5c",
          padding: "1px 5px",
          borderRadius: 3,
        }}>
          {isOverridden ? "OVERRIDDEN" : "DEFAULT"}
        </span>
      </div>
    </div>
  );
}


export function EmergencyStopHistoryRow({ event }) {
  const color = event.enabled ? "#ef4444" : "#22c55e";
  const label = event.enabled ? "ON"      : "OFF";
  return (
    <div style={{ padding: "5px 0", borderBottom: "1px solid #05121f",
                   display: "flex", justifyContent: "space-between", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{
          fontSize: 9, fontWeight: 700, letterSpacing: "0.04em", color,
          padding: "1px 5px", borderRadius: 3,
          border: `1px solid ${color}55`, background: `${color}15`,
        }}>
          {label}
        </span>
        <span style={{ fontSize: 10, color: "#94a3b8" }}>
          {new Date(event.created_at).toLocaleString("ko-KR")}
        </span>
        {/* 153: reason_code는 사고 분석 시 가장 빠르게 보이는 정보 — inline 배지. */}
        {event.reason_code && (
          <span data-testid="reason-code-badge" style={{
            fontSize: 8, fontWeight: 700, letterSpacing: "0.04em",
            color: "#a78bfa", padding: "1px 4px", borderRadius: 3,
            border: "1px solid #a78bfa55", background: "#a78bfa15",
          }}>
            {event.reason_code}
          </span>
        )}
      </div>
      <div style={{ fontSize: 9, color: "#64748b", textAlign: "right" }}>
        {event.decided_by ? `by ${event.decided_by}` : ""}
        {event.note ? ` · ${event.note}` : ""}
      </div>
    </div>
  );
}


// 153: enum from app.risk.emergency_reasons. 백엔드 라우트 /api/risk/emergency-stop/
// reasons 호출로도 가져올 수 있지만 modal은 정적 dropdown — 9개 변동성 낮음.
const _EMERGENCY_STOP_REASONS = [
  { value: "manual_operator",          label: "수동 (운영자)" },
  { value: "daily_loss_limit",         label: "일일 손실 한도" },
  { value: "data_stale",               label: "시세 stale" },
  { value: "broker_error",             label: "broker 오류" },
  { value: "repeated_order_failure",   label: "주문 연속 실패" },
  { value: "abnormal_slippage",        label: "비정상 슬리피지" },
  { value: "agent_warning",            label: "AI 에이전트 경고" },
  { value: "margin_risk",              label: "선물 증거금 위험" },
  { value: "futures_liquidation_risk", label: "선물 강제청산 임박" },
];


export function EmergencyStopHistoryCard({ history }) {
  return (
    <Card>
      <SectionLabel>긴급 정지 이력 (최근 50건)</SectionLabel>
      <div style={{ fontSize: 9, color: "#334155", marginBottom: 6, lineHeight: 1.5 }}>
        런타임 플래그는 백엔드 재시작 시 OFF로 리셋되지만, 이력은 영구 저장되어
        "누가 언제 어떤 사유로 토글했는지"가 재시작 후에도 유지됩니다.
      </div>
      {history.length === 0 ? (
        <div style={{ color: "#1e3a5c", fontSize: 11, textAlign: "center", padding: 12 }}>
          기록된 토글 없음
        </div>
      ) : history.map((e) => <EmergencyStopHistoryRow key={e.id} event={e} />)}
    </Card>
  );
}


export function EmergencyStopConfirmModal({
  targetEnabled, busy, onConfirm, onCancel, defaultDecidedBy = "",
}) {
  const action = targetEnabled ? "긴급 정지 활성화" : "긴급 정지 해제";
  const accent = targetEnabled ? "#ef4444"          : "#22c55e";
  // 153: reason_code dropdown 자체 상태. 빈 문자열은 null로 변환해 backend로 전달.
  const [reasonCode, setReasonCode] = useState("");

  const reasonField = (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 10, color: "#64748b", marginBottom: 4 }}>
        사유 코드 (reason_code) — 사고 분석 시 사용
      </div>
      <select
        data-testid="emergency-stop-reason-select"
        value={reasonCode}
        onChange={(e) => setReasonCode(e.target.value)}
        disabled={busy}
        style={{
          width: "100%", padding: "5px 6px",
          background: "#0c2035", color: "#7dd3fc",
          border: "1px solid #1a3a5c", borderRadius: 4,
          fontSize: 11,
        }}
      >
        <option value="">— 미지정 —</option>
        {_EMERGENCY_STOP_REASONS.map((r) => (
          <option key={r.value} value={r.value}>{r.label}</option>
        ))}
      </select>
    </div>
  );

  return (
    <DecisionDialog
      title={action}
      accent={accent}
      cancelLabel="취소"
      confirmLabel="확인"
      description="감사 추적을 위해 운영자명/사유/코드를 남겨주세요. 코드는 사고 분석 시 reason별 집계에 사용됩니다."
      notePlaceholder="예: vol spike, circuit-breaker"
      busy={busy}
      defaultDecidedBy={defaultDecidedBy}
      extraFields={reasonField}
      extraPayload={() => ({ reason_code: reasonCode || null })}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}


export function BackendPolicyCard({ riskPolicy, operatorName = "" }) {
  const { policy, loading, error, emergencyStop, busy, toggleEmergency } = riskPolicy;
  const [confirmOpen, setConfirmOpen] = useState(false);

  return (
    <Card accentColor={emergencyStop ? "#ef444455" : "#7dd3fc22"}>
      <SectionLabel>백엔드 리스크 정책</SectionLabel>

      {error && (
        <div style={{ color: "#f87171", fontSize: 11, marginBottom: 8 }}>{error}</div>
      )}

      {loading ? (
        <div style={{ color: "#475569", fontSize: 11, padding: 8 }}>로딩 중…</div>
      ) : policy ? (
        <>
          <div style={{ fontSize: 9, color: "#334155", marginBottom: 8, lineHeight: 1.5 }}>
            값은 backend env 설정의 라이브 스냅샷입니다. OVERRIDDEN 배지는 해당 env가
            기본값과 다름을 의미하므로, 운영자가 적용한 변경이 실제로 반영됐는지 즉시
            확인할 수 있습니다.
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {RISK_POLICY_FIELDS.map((f) => {
              const v = policy[f.key];
              return (
                <PolicyRow
                  key={f.key}
                  label={f.label}
                  envVar={f.envVar}
                  value={_formatPolicyValue(v, f.kind)}
                  isOverridden={isPolicyValueOverridden(v, f.defaultValue)}
                />
              );
            })}
          </div>
        </>
      ) : (
        <div style={{ color: "#475569", fontSize: 11 }}>정책 정보 없음</div>
      )}

      <div style={{
        marginTop: 14, paddingTop: 12, borderTop: "1px solid #0c2035",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <div>
          <div style={{
            fontSize: 11, fontWeight: 700,
            color: emergencyStop ? "#ef4444" : "#94a3b8",
          }}>
            긴급 정지 {emergencyStop ? "● ACTIVE" : "○ OFF"}
          </div>
          <div style={{ fontSize: 9, color: "#475569", marginTop: 2 }}>
            ON 시 RiskManager가 모든 신규 주문 차단
          </div>
        </div>
        <Btn
          color={emergencyStop ? "#22c55e" : "#ef4444"}
          onClick={() => setConfirmOpen(true)}
          disabled={busy}
          small
        >
          {emergencyStop ? "해제" : "긴급 정지"}
        </Btn>
      </div>

      {confirmOpen && (
        <EmergencyStopConfirmModal
          targetEnabled={!emergencyStop}
          busy={busy}
          defaultDecidedBy={operatorName}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={async (decision) => {
            const result = await toggleEmergency(decision);
            if (result?.ok !== false) setConfirmOpen(false);
            return result;
          }}
        />
      )}
    </Card>
  );
}


export function StrategyRisk({ strategyOn, toggle, strategyParams, updateParam, risk, updateRisk, riskPolicy, operatorName }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <BackendPolicyCard riskPolicy={riskPolicy} operatorName={operatorName} />
      <EmergencyStopHistoryCard history={riskPolicy.history || []} />

      <div style={{ fontSize: 11, color: "#475569", marginBottom: 2, marginTop: 8 }}>
        복수 전략 동시 활성화 → 신호 합류(Confluence) 시 진입
      </div>

      {/* 전략 카드 */}
      {Object.values(STRATEGIES).map((s) => {
        const isOn = strategyOn[s.id];
        return (
          <Card key={s.id} accentColor={isOn ? s.color + "55" : undefined}>
            {/* 헤더 */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <span style={{ fontSize: 22 }}>{s.icon}</span>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 13, color: isOn ? s.color : "#64748b" }}>
                    {s.name}
                  </div>
                  <div style={{ fontSize: 10, color: "#475569" }}>
                    {s.desc} · 승률 ~{s.winRate}%
                  </div>
                </div>
              </div>
              <Toggle value={isOn} onChange={() => toggle(s.id)} color={s.color} />
            </div>

            {/* 파라미터 (ON일 때만 표시) */}
            {isOn && (
              <div style={{ marginTop: 12, borderTop: "1px solid #0c2035", paddingTop: 12 }}>
                <div style={{ fontSize: 10, color: "#475569", marginBottom: 8, fontStyle: "italic" }}>
                  → {s.detail}
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4 }}>
                  {Object.entries(s.params).map(([pk, pv]) => (
                    <Slider
                      key={pk}
                      label={pv.label}
                      value={strategyParams[s.id]?.[pk] ?? pv.default}
                      min={pv.min} max={pv.max} step={pv.step}
                      onChange={(v) => updateParam(s.id, pk, v)}
                    />
                  ))}
                </div>
                <div style={{ display: "flex", gap: 12, fontSize: 10, color: "#334155", marginTop: 4 }}>
                  <span>⏰ {s.bestTime}</span>
                  <span>🎯 {s.bestTarget}</span>
                </div>
              </div>
            )}
          </Card>
        );
      })}

      {/* 리스크 설정 */}
      <div style={{ marginTop: 10 }}>
        <SectionLabel>━━ 리스크 관리</SectionLabel>
      </div>

      <Card accentColor="#ef444422">
        {[
          { label: "일일 최대 손실 (원)", key: "maxDailyLoss", min: 50_000,    max: 2_000_000, step: 50_000 },
          { label: "종목당 투자 한도 (원)", key: "maxPerTrade",  min: 200_000,  max: 5_000_000, step: 100_000 },
          { label: "최대 보유 종목 수",    key: "maxPositions", min: 1,        max: 20,        step: 1 },
          { label: "연속 손실 정지 (회)",  key: "pauseOnStreak",min: 2,        max: 10,        step: 1 },
          { label: "최대 낙폭 서킷브레이커 (%)", key: "maxDrawdown", min: 2, max: 20, step: 0.5 },
        ].map(({ label, key, ...rest }) => (
          <Slider key={key} label={label} value={risk[key]} onChange={(v) => updateRisk(key, v)} {...rest} />
        ))}
      </Card>

      <Card accentColor="#f59e0b22">
        <SectionLabel>강제 청산 시간</SectionLabel>
        <div style={{ display: "flex", gap: 8 }}>
          {["15:00", "15:10", "15:20", "15:25"].map((t) => (
            <button
              key={t}
              onClick={() => updateRisk("forceCloseAt", t)}
              style={{
                flex: 1, padding: "7px 0", borderRadius: 4,
                border: `1px solid ${risk.forceCloseAt === t ? "#f59e0b" : "#1a3a5c"}`,
                background: risk.forceCloseAt === t ? "#f59e0b" : "transparent",
                color:      risk.forceCloseAt === t ? "#010a14" : "#64748b",
                cursor: "pointer", fontFamily: "inherit", fontSize: 12, fontWeight: 700,
              }}
            >{t}</button>
          ))}
        </div>

        <div style={{ marginTop: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 11, color: "#64748b" }}>트레일링 스탑</span>
          <Toggle
            value={risk.trailingStop}
            onChange={(v) => updateRisk("trailingStop", v)}
            color="#f59e0b"
          />
        </div>
        <div style={{ marginTop: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 11, color: "#64748b" }}>서킷브레이커</span>
          <Toggle
            value={risk.circuitBreaker}
            onChange={(v) => updateRisk("circuitBreaker", v)}
            color="#f59e0b"
          />
        </div>
      </Card>
    </div>
  );
}
