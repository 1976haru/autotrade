/**
 * 체크리스트 #60: AI Agent 모의매매 카드 (read-only).
 *
 * 본 카드는 GET /api/auto-trader/status 응답을 표시한다. UI는 다음을 보여준다:
 *   - 현재 운용 모드 + Paper / LIVE flag
 *   - 마지막 Agent 결정 (BUY / SELL / HOLD)
 *   - 판단 사유 / 사용된 전략 / confidence
 *   - 리스크 체크 결과 (4개)
 *   - 가상 보유 종목 / 가상 현금
 *   - Emergency Stop 토글 (in-memory)
 *
 * 절대 원칙:
 *   - 본 카드는 LIVE 주문을 만들지 않는다.
 *   - "즉시 매수" / "지금 주문" 같은 버튼은 제공하지 않는다.
 *   - run-once 버튼은 *모의매매 검증용*이며 backend가 모드/브로커 가드를 강제.
 */

import { useCallback, useEffect, useState } from "react";
import { Card, SectionLabel, Btn } from "../common";
import { friendlyErrorMessage } from "../../utils/errorMessage";
import { backendApi } from "../../services/backend/client";

const DECISION_COLOR = {
  BUY:  "#22c55e",
  SELL: "#ef4444",
  HOLD: "#94a3b8",
};

function decisionColor(action) {
  return DECISION_COLOR[action] ?? "#64748b";
}

function fmtKRW(value) {
  if (value == null) return "—";
  return Number(value).toLocaleString("ko-KR") + "원";
}


/** 사용자가 손쉽게 확인할 수 있게 — 가장 최근 plan + portfolio 요약. */
export function AutoTraderCard({ onRunOnce }) {
  const [status,     setStatus]     = useState(null);
  const [portfolio,  setPortfolio]  = useState(null);
  const [error,      setError]      = useState("");
  const [busy,       setBusy]       = useState(false);
  const [esBusy,     setEsBusy]     = useState(false);

  const load = useCallback(async () => {
    setBusy(true); setError("");
    try {
      const [s, p] = await Promise.all([
        backendApi.autoTraderStatus(),
        backendApi.autoTraderPortfolio().catch(() => null),
      ]);
      setStatus(s);
      setPortfolio(p);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (cancelled) return;
      await load();
    })();
    return () => { cancelled = true; };
  }, [load]);

  const toggleEmergency = async (enabled) => {
    setEsBusy(true); setError("");
    try {
      await backendApi.autoTraderEmergencyStop(enabled);
      await load();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setEsBusy(false);
    }
  };

  const lastReport = status?.lastReport ?? null;
  const latestPlan = lastReport?.plans?.[0] ?? null;
  const decision   = latestPlan?.decision ?? null;
  const port       = portfolio ?? lastReport?.portfolio ?? null;
  const isEmergency = status?.emergencyStop === true;
  const isLive     = status?.enableLiveTrading === true
                   || status?.enableAiExecution === true;

  return (
    <Card>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 6,
      }}>
        <SectionLabel>🤖 AI 모의매매 (Agent)</SectionLabel>
        <Btn onClick={load} disabled={busy} color="#7dd3fc" small>
          {busy ? "⟳" : "↻ 새로고침"}
        </Btn>
      </div>

      <div style={{
        fontSize: 11, color: "var(--c-text-3)", marginBottom: 6,
      }}>
        AI Agent가 전략 신호를 종합해 가상 매수/매도를 모의 실행합니다.
        실제 증권사 주문은 발생하지 않습니다.
      </div>

      {/* 안전 안내 */}
      <div data-testid="autotrader-paper-banner"
           style={{
             padding: "6px 10px", marginBottom: 8,
             background: isLive ? "#fef3c7" : "#ecfeff",
             border: `1px solid ${isLive ? "#fbbf24" : "#67e8f9"}`,
             borderRadius: 4, fontSize: 11,
             color: isLive ? "#92400e" : "#0e7490",
           }}>
        {isLive
          ? "⚠ LIVE flag 활성 — Backend가 LIVE 모드를 차단합니다."
          : "🛡 모의매매 모드 (LIVE 차단 활성)"}
        {status?.paperStatus?.mode && (
          <span style={{ marginLeft: 6, opacity: 0.7 }}>
            mode={status.paperStatus.mode}
          </span>
        )}
      </div>

      {error && (
        <div data-testid="autotrader-error"
             style={{
               color: "var(--c-danger)", fontSize: "var(--fs-sm)",
               marginBottom: 6, padding: "6px 10px",
               background: "#fef2f2", border: "1px solid #fecaca",
               borderRadius: 4,
             }}>
          {friendlyErrorMessage(error) || error}
        </div>
      )}

      {/* Emergency Stop 토글 */}
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", padding: "8px 10px",
        background: isEmergency ? "#fef2f2" : "#f1f5f9",
        border: `1px solid ${isEmergency ? "#ef4444" : "#cbd5e1"}`,
        borderRadius: 4, marginBottom: 8,
      }}>
        <div>
          <div style={{
            fontSize: 12, fontWeight: 700,
            color: isEmergency ? "#b91c1c" : "#334155",
          }}>
            🛑 Emergency Stop {isEmergency ? "ON" : "OFF"}
          </div>
          <div style={{ fontSize: 10, color: "#64748b" }}>
            ON 상태에서는 모든 가상 주문이 차단됩니다.
          </div>
        </div>
        <Btn
          onClick={() => toggleEmergency(!isEmergency)}
          disabled={esBusy}
          color={isEmergency ? "#22c55e" : "#ef4444"}
          small
        >
          {esBusy ? "..." : isEmergency ? "해제" : "긴급 정지"}
        </Btn>
      </div>

      {/* 마지막 결정 */}
      {decision ? (
        <div data-testid="autotrader-decision"
             style={{
               padding: 8, background: "#f8fafc",
               border: "1px solid #e2e8f0", borderRadius: 4,
               marginBottom: 8,
             }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <div data-testid="autotrader-action"
                 style={{
                   fontSize: 22, fontWeight: 800,
                   color: decisionColor(decision.action),
                   minWidth: 70,
                 }}>
              {decision.action}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#0f172a" }}>
                {decision.symbol}
                <span style={{
                  marginLeft: 8, fontSize: 11,
                  fontWeight: 400, color: "#475569",
                }}>
                  conf {decision.confidence}
                </span>
                <span style={{
                  marginLeft: 8, fontSize: 11,
                  fontWeight: 400, color: "#475569",
                }}>
                  qty {decision.positionSize}
                </span>
              </div>
              <div style={{
                fontSize: 11, color: "#475569", marginTop: 2,
                whiteSpace: "pre-line",
              }}>
                {decision.reason}
              </div>
            </div>
          </div>
          {/* used strategies */}
          {Array.isArray(decision.usedStrategies) && decision.usedStrategies.length > 0 && (
            <div style={{
              marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4,
            }}>
              {decision.usedStrategies.map((s) => (
                <span
                  key={s}
                  data-testid={`autotrader-strategy-${s}`}
                  style={{
                    fontSize: 10, padding: "2px 6px",
                    background: "#e0f2fe", color: "#0369a1",
                    borderRadius: 3,
                  }}
                >
                  {s}
                </span>
              ))}
            </div>
          )}
          {/* risk checks */}
          {decision.riskChecks && (
            <div data-testid="autotrader-risk-checks"
                 style={{
                   marginTop: 6, display: "grid",
                   gridTemplateColumns: "1fr 1fr", gap: 4,
                   fontSize: 10,
                 }}>
              {[
                ["maxPositionOk",    "보유 한도"],
                ["dailyLossLimitOk", "일일 손실"],
                ["cooldownOk",       "쿨다운"],
                ["cashAvailableOk",  "현금"],
              ].map(([key, label]) => {
                const ok = decision.riskChecks[key];
                return (
                  <div key={key}
                       style={{
                         padding: "2px 6px",
                         background: ok ? "#dcfce7" : "#fee2e2",
                         color:      ok ? "#15803d" : "#b91c1c",
                         borderRadius: 3,
                       }}>
                    {ok ? "✓" : "✗"} {label}
                  </div>
                );
              })}
            </div>
          )}
          {/* routing outcome */}
          {latestPlan?.routingDecision && (
            <div style={{
              marginTop: 6, fontSize: 10, color: "#475569",
            }}>
              <span style={{ fontWeight: 700 }}>라우팅: </span>
              {latestPlan.routingDecision}
              {latestPlan.executed && (
                <span style={{ marginLeft: 6, color: "#15803d" }}>
                  ✓ 가상 체결 ({latestPlan.fillQuantity}주
                  {latestPlan.fillPrice != null && ` @ ${fmtKRW(latestPlan.fillPrice)}`})
                </span>
              )}
              {latestPlan.blockedBy && (
                <span style={{ marginLeft: 6, color: "#b91c1c" }}>
                  차단: {latestPlan.blockedBy}
                </span>
              )}
            </div>
          )}
        </div>
      ) : (
        <div data-testid="autotrader-empty"
             style={{
               fontSize: 11, color: "#64748b", padding: 8,
               background: "#f8fafc", borderRadius: 4, marginBottom: 8,
             }}>
          아직 Agent 결정 없음. run-once를 호출하면 결정이 표시됩니다.
        </div>
      )}

      {/* portfolio */}
      {port && (
        <div data-testid="autotrader-portfolio"
             style={{
               display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
               gap: 6, marginBottom: 6,
             }}>
          {[
            ["가상 현금",   port.cash],
            ["가상 자산",   port.equity],
            ["보유 종목 수", Array.isArray(port.positions) ? port.positions.length : 0],
          ].map(([label, v]) => (
            <div key={label}
                 style={{
                   padding: 6, background: "#f1f5f9",
                   borderRadius: 4, textAlign: "center",
                 }}>
              <div style={{ fontSize: 9, color: "#64748b" }}>{label}</div>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#0f172a" }}>
                {typeof v === "number" ? v.toLocaleString("ko-KR") : v}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* run-once 호출 — 운영자가 mock 봉을 backend에 보내야 하므로 본 카드에서는
          외부 caller(onRunOnce)에게 위임. 미연결 시 안내만 노출. */}
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        {onRunOnce ? (
          <Btn
            onClick={onRunOnce}
            disabled={busy || isEmergency}
            color="#3b82f6"
            small
          >
            Run Once (모의)
          </Btn>
        ) : (
          <div style={{ fontSize: 10, color: "#94a3b8" }}>
            run-once 호출은 별도 UI에서 — 본 카드는 상태 표시 전용.
          </div>
        )}
        <div style={{ fontSize: 10, color: "#94a3b8" }}>
          최근 결정 {status?.recentReportCount ?? 0}건 캐시
        </div>
      </div>
    </Card>
  );
}

export { decisionColor as _decisionColor };
