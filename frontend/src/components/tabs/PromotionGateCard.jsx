import { useState } from "react";

import { Btn, Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


/**
 * Strategy Promotion Gate 카드 (#27)
 *
 * Backtest 결과 + 운영 데이터를 입력으로 단계별 승격 가능 여부를 *판단만* 한다.
 * 실제 모드 변경 / LIVE flag / AI Execution 활성화 버튼은 절대 추가하지 않는다
 * (CLAUDE.md 절대 원칙).
 */
const _STAGES = [
  "BACKTEST",
  "LIVE_SHADOW",
  "PAPER",
  "LIVE_MANUAL_APPROVAL",
  "LIVE_AI_ASSIST",
  "LIVE_AI_EXECUTION",
];


function _nextStage(stage) {
  const idx = _STAGES.indexOf(stage);
  if (idx < 0 || idx >= _STAGES.length - 1) return null;
  return _STAGES[idx + 1];
}


export function PromotionGateCard({ run }) {
  const [current, setCurrent] = useState("BACKTEST");
  const [target,  setTarget]  = useState("LIVE_SHADOW");
  const [humanApproved, setHumanApproved] = useState(false);
  const [result, setResult] = useState(null);
  const [busy,   setBusy]   = useState(false);
  const [err,    setErr]    = useState("");

  const handleEvaluate = async () => {
    setBusy(true); setErr(""); setResult(null);
    try {
      // run에서 가능한 필드만 자동 채움 — 나머지는 보수적 default.
      const payload = {
        strategy_name: run?.strategy || "manual",
        current_stage: current,
        target_stage:  target,
        trade_count:        run?.trades?.length ?? 0,
        expectancy:         run?.expectancy ?? 0,
        profit_factor:      run?.profit_factor ?? null,
        max_drawdown:       run?.max_drawdown ?? 0,
        max_consecutive_losses: run?.max_consecutive_losses ?? 0,
        win_rate:           run?.win_rate ?? 0,
        initial_cash:       run?.initial_cash ?? 10_000_000,
        cost_adjusted:      Boolean(run?.config),
        slippage_adjusted:  Boolean(run?.config),
        human_approved:     humanApproved,
      };
      const out = await backendApi.evaluatePromotion(payload);
      setResult(out);
    } catch (e) {
      setErr(e.message || "평가 실패");
    } finally {
      setBusy(false);
    }
  };

  const handleStageChange = (val) => {
    setCurrent(val);
    const nxt = _nextStage(val);
    if (nxt) setTarget(nxt);
  };

  const flagColor = ({
    PASS: "#22c55e", CAUTION: "#fbbf24", FAIL: "#ef4444", BLOCKED: "#94a3b8",
  })[result?.decision] || "#94a3b8";

  return (
    <Card>
      <div data-testid="promotion-gate-card">
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
          <SectionLabel>🛡 전략 승격 게이트</SectionLabel>
          <span data-testid="promotion-gate-not-order-badge"
                style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
                  padding: "2px 6px", borderRadius: 3,
                  background: "#7f1d1d33", color: "#fca5a5",
                  border: "1px solid #ef444466",
                }}>
            AI 추천만으로 승격 불가 · 사람 승인 + 코드 기준 모두 필요
          </span>
        </div>

        <div style={{
          fontSize: 11, color: "#94a3b8", lineHeight: 1.5, marginBottom: 8,
          padding: "6px 8px", background: "#0c2035", borderRadius: 3,
        }}>
          단계별 코드 기준(거래 수 / 기대값 / Profit Factor / MDD / Walk-forward / Monte
          Carlo / 데이터 품질 / Paper·Shadow 운영)을 평가합니다.{" "}
          <b>본 카드는 판단만</b> — 실제 모드 변경 / LIVE 활성화는 별도 옵트인 PR.
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr",
                       gap: 6, marginBottom: 8 }}>
          <div>
            <div style={{ fontSize: 10, color: "#475569", marginBottom: 3 }}>현재 단계</div>
            <select value={current} onChange={(e) => handleStageChange(e.target.value)}
                    style={_selectStyle}>
              {_STAGES.slice(0, -1).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <div style={{ fontSize: 10, color: "#475569", marginBottom: 3 }}>목표 단계</div>
            <select value={target} onChange={(e) => setTarget(e.target.value)}
                    style={_selectStyle}>
              {_STAGES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>

        <label style={{ display: "flex", alignItems: "center", gap: 6,
                         marginBottom: 8, fontSize: 11, color: "#94a3b8" }}>
          <input type="checkbox" checked={humanApproved}
                 onChange={(e) => setHumanApproved(e.target.checked)} />
          사람 승인됨 (LIVE 단계 진입에 필수)
        </label>

        <Btn small onClick={handleEvaluate} disabled={busy}>
          {busy ? "평가 중…" : "승격 게이트 평가"}
        </Btn>

        {err && (
          <div style={{ fontSize: 11, color: "#fca5a5", marginTop: 6 }}>{err}</div>
        )}

        {result && (
          <div data-testid="promotion-gate-result"
               style={{ marginTop: 10, paddingTop: 10,
                        borderTop: "1px solid #0c2035" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
              <span data-testid="promotion-gate-decision"
                    style={{
                      fontSize: 12, fontWeight: 700, padding: "4px 10px",
                      borderRadius: 4, color: flagColor,
                      background: `${flagColor}22`,
                      border: `1px solid ${flagColor}66`,
                    }}>
                {result.decision}
              </span>
              <span style={{ fontSize: 10, color: "#475569" }}>
                {result.current_stage} → {result.target_stage}
              </span>
            </div>

            {result.failed_criteria.length > 0 && (
              <div data-testid="promotion-gate-failed"
                   style={{ marginBottom: 6, fontSize: 11 }}>
                <div style={{ color: "#ef4444", fontWeight: 700, marginBottom: 3 }}>
                  실패 기준 ({result.failed_criteria.length})
                </div>
                {result.failed_criteria.map((c, i) => (
                  <div key={i} style={{ color: "#fca5a5", marginBottom: 2,
                                        paddingLeft: 8, borderLeft: "2px solid #ef444466" }}>
                    {c}
                  </div>
                ))}
              </div>
            )}

            {result.cautions.length > 0 && (
              <div data-testid="promotion-gate-cautions"
                   style={{ marginBottom: 6, fontSize: 11 }}>
                <div style={{ color: "#fbbf24", fontWeight: 700, marginBottom: 3 }}>
                  주의 ({result.cautions.length})
                </div>
                {result.cautions.map((c, i) => (
                  <div key={i} style={{ color: "#fde68a", marginBottom: 2,
                                        paddingLeft: 8, borderLeft: "2px solid #fbbf2466" }}>
                    {c}
                  </div>
                ))}
              </div>
            )}

            {result.warnings.length > 0 && (
              <div data-testid="promotion-gate-warnings"
                   style={{ marginBottom: 6, fontSize: 11, color: "#fde68a" }}>
                {result.warnings.map((w, i) => (
                  <div key={i}>⚠ {w}</div>
                ))}
              </div>
            )}

            {result.required_actions.length > 0 && (
              <div data-testid="promotion-gate-actions"
                   style={{ marginBottom: 6, fontSize: 11, color: "#7dd3fc" }}>
                <div style={{ fontWeight: 700, marginBottom: 3 }}>필요 조치</div>
                {result.required_actions.map((a, i) => (
                  <div key={i} style={{ marginBottom: 2 }}>→ {a}</div>
                ))}
              </div>
            )}

            {result.passed_criteria.length > 0 && (
              <details style={{ fontSize: 10, color: "#475569", marginTop: 6 }}>
                <summary style={{ cursor: "pointer" }}>
                  통과 기준 ({result.passed_criteria.length})
                </summary>
                <div style={{ marginTop: 4, paddingLeft: 8 }}>
                  {result.passed_criteria.map((c, i) => (
                    <div key={i} style={{ color: "#22c55e", marginBottom: 2 }}>✓ {c}</div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}


const _selectStyle = {
  background: "var(--c-surface)", color: "var(--c-text)",
  border: "1px solid var(--c-border-strong)",
  borderRadius: 4, padding: "6px 8px", fontSize: 11, width: "100%",
};
