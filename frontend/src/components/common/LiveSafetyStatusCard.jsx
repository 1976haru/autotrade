/**
 * #70/#71/#72 — 실매매 안전 상태 카드 (Settings 탭, read-only).
 *
 * (70) 실매매 기본 OFF 정책, (71) KIS Paper/Live 경로 분리, (72) Live Capital
 * Review 상태를 표시한다. 모두 *표시 전용* 이며 실전을 켜지 않는다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 실전 켜기 / live order / approve live / 매수 / 매도 버튼 0개, 입력 form 0개.
 *  - secret/account 원문 표시 0건.
 *  - "실전매매 기본 OFF" / "KIS Paper 와 KIS Live 경로는 분리" / "Live Capital
 *    Review 는 주문 승인이 아닙니다" / "현재 실전 주문은 차단 상태" 문구 노출.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

function _Flag({ label, on, safeOff = true, testId }) {
  // safeOff=true: false 가 안전(초록), true 가 위험(빨강).
  const safe = safeOff ? !on : on;
  return (
    <span data-testid={testId} style={{
      display: "inline-block", padding: "2px 8px", borderRadius: 4, marginRight: 6,
      marginBottom: 4, fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
      background: safe ? "#dcfce7" : "#fee2e2", color: safe ? "#166534" : "#b91c1c",
    }}>
      {label}: {String(on)}
    </span>
  );
}

export function LiveSafetyStatusCard({
  apiClient = backendApi,
  testId = "live-safety-status-card",
} = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (typeof apiClient.liveSafetyStatus !== "function") return;
    try {
      setData((await apiClient.liveSafetyStatus()) || null);
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
    }
  }, [apiClient]);

  useEffect(() => { refresh(); }, [refresh]);

  const lp = data?.live_policy || null;
  const ke = data?.kis_endpoint || null;
  const rv = data?.live_capital_review || null;

  return (
    <div data-testid={testId}>
      <Card accentColor="#dc262633">
        <SectionLabel>🔒 실매매 안전 상태 (기본 OFF)</SectionLabel>

        <div data-testid="live-safety-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          실전매매 기본 OFF. KIS Paper 와 KIS Live 경로는 분리되어 있습니다.
          KIS_IS_PAPER=false 여도 explicit live gate 없이는 차단됩니다. Live Capital
          Review 는 주문 승인이 아닙니다. 현재 실전 주문은 차단 상태입니다.
        </div>

        {error ? (
          <div data-testid="live-safety-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>안전 상태를 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {/* (70) live policy */}
        {lp ? (
          <div data-testid="live-policy-block" style={{ marginBottom: 10 }}>
            <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              ① 실매매 기본 OFF 정책
            </div>
            <_Flag label="ENABLE_LIVE_TRADING" on={lp.enable_live_trading} testId="live-flag-live" />
            <_Flag label="ENABLE_AI_EXECUTION" on={lp.enable_ai_execution} testId="live-flag-ai" />
            <_Flag label="ENABLE_FUTURES_LIVE_TRADING" on={lp.enable_futures_live_trading} testId="live-flag-futures" />
            <_Flag label="KIS_IS_PAPER" on={lp.kis_is_paper} safeOff={false} testId="live-flag-paper" />
            <div data-testid="live-policy-gated" style={{
              fontSize: "var(--fs-xs)", color: lp.live_order_blocked ? "#166534" : "#b91c1c",
            }}>
              실전 경로: {lp.live_path_gated ? "차단(gated)" : "열림"} · {lp.reason_code}
            </div>
          </div>
        ) : null}

        {/* (71) kis endpoint separation */}
        {ke ? (
          <div data-testid="kis-endpoint-block" style={{ marginBottom: 10 }}>
            <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              ② KIS Paper / Live 경로 분리
            </div>
            <div data-testid="kis-endpoint-mode" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
              선택 경로: <strong>{ke.selected_mode}</strong> · 분리됨: {String(ke.paper_live_separated)}
              {ke.live_gate_required ? " · live gate 필요" : ""}
            </div>
            <div data-testid="kis-endpoint-reason" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
              {ke.message_ko || ke.reason_code}
            </div>
          </div>
        ) : null}

        {/* (72) live capital review */}
        {rv ? (
          <div data-testid="live-review-block" style={{ marginBottom: 8 }}>
            <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              ③ Live Capital Review (주문 승인 아님)
            </div>
            <div data-testid="live-review-status" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
              검토 상태: <strong>{rv.approval_status}</strong>
              {" · "}whitelist {rv.symbol_whitelist_count}개
              {" · "}max_notional {String(rv.max_order_notional_configured)}
              {" · "}daily_limit {String(rv.daily_live_limit_configured)}
            </div>
            <div data-testid="live-review-order" style={{ fontSize: "var(--fs-xs)", color: "#166534" }}>
              order_created={String(rv.order_created)} · broker_order_sent={String(rv.broker_order_sent)}
              {" · "}is_live_authorization={String(rv.is_live_authorization)}
            </div>
          </div>
        ) : null}

        <div data-testid="live-safety-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 화면은 안전 상태 표시 전용입니다. 실전을 켜는 버튼이 아니며, 자격정보
          (계좌번호/secret)는 표시되지 않습니다. 수익을 보장하지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default LiveSafetyStatusCard;
