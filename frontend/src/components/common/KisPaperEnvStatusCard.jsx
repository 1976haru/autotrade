/**
 * 0-04: KIS 모의 자동매매 환경(.env) 상태 카드 — Settings 탭, read-only.
 *
 * EXE 사용자가 PAPER / KIS 모의 자동매매가 안전하게 켜져 있는지 한눈에 확인하는
 * 카드. `GET /api/kis-paper/auto/status` 의 boolean / enum 값만 표시한다.
 *
 * 절대 invariant (테스트로 lock):
 *  - API key / Secret / 계좌번호 *값* 표시 0건 (구성됨/미구성 boolean 만).
 *  - LIVE 활성화 / Live 자금 승인 / ENABLE_* 토글 버튼 0개.
 *  - 입력 form(input/textarea/select) 0개.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const POLL_INTERVAL_MS = 10_000;

// (label, key, 안전값 판정 함수, 표시 변환)
function _StatusRow({ label, value, ok, testid }) {
  const color = ok ? "#166534" : "#b91c1c";
  const bg = ok ? "#dcfce7" : "#fef2f2";
  return (
    <div
      data-testid={testid}
      style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        padding: "5px 8px", borderRadius: 4, background: bg, marginBottom: 4,
        fontSize: "var(--fs-xs)",
      }}
    >
      <span style={{ color: "var(--c-text-2)" }}>{label}</span>
      <span style={{ fontWeight: "var(--fw-bold)", color }}>{value}</span>
    </div>
  );
}

function _onOff(v) {
  return v ? "ON" : "OFF";
}

export function KisPaperEnvStatusCard({
  apiClient = backendApi,
  testId = "kis-paper-env-status-card",
  pollIntervalMs = POLL_INTERVAL_MS,
} = {}) {
  const [s, setS] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    if (typeof apiClient.kisPaperAutoStatus !== "function") return;
    try {
      const r = await apiClient.kisPaperAutoStatus();
      setS(r || null);
      setError(null);
    } catch (err) {
      setError(err?.message || String(err));
    }
  }, [apiClient]);

  useEffect(() => {
    refresh();
    if (!pollIntervalMs || pollIntervalMs <= 0) return undefined;
    const t = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(t);
  }, [refresh, pollIntervalMs]);

  const mode = s?.default_mode ?? "—";
  const live = s?.enable_live_trading === true;
  const ai = s?.enable_ai_execution === true;        // 보통 응답에 없음 → undefined
  const kisPaper = s?.kis_is_paper !== false;
  const brokerKind = s?.paper_broker_kind ?? "—";
  const autoOn = s?.enable_kis_paper_auto_trading === true;
  const dryRun = s?.dry_run === true;
  const fillPolling = s?.fill_polling === true;
  const credsPresent = s?.credentials_present === true;
  const brokerOrderType = s?.broker_order_type ?? "—";
  const liveAuth = s?.is_live_authorization === true;
  // 4-01: 4종 per-credential 존재 여부 (값 원문 0건 — boolean 만).
  const appKeyPresent = s?.kis_app_key_present === true;
  const appSecretPresent = s?.kis_app_secret_present === true;
  const accountPresent = s?.kis_account_no_present === true;
  const productCodePresent = s?.kis_product_code_present === true
    || s?.product_code_present === true;
  const missingCreds = Array.isArray(s?.missing_credentials)
    ? s.missing_credentials : [];
  // 4-02: Background Tick + KIS 모의 자동주문 READY/BLOCKED 판정.
  const bgTick = s?.enable_ai_paper_background_tick === true;
  const autoReady = s?.kis_paper_auto_ready === true;

  return (
    <Card data-testid={testId}>
      <SectionLabel>🔧 KIS 모의 자동매매 설정 상태</SectionLabel>

      <div
        data-testid="kis-env-intro"
        style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8 }}
      >
        현재 설정은 Paper / KIS 모의투자 전용입니다. 실거래는 OFF입니다.
      </div>

      {/* 4-02: KIS 모의 자동주문 READY/BLOCKED 헤드라인. */}
      <div
        data-testid="kis-env-auto-ready"
        data-ready={String(autoReady)}
        style={{
          padding: "6px 10px", borderRadius: 6, marginBottom: 8,
          fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
          background: autoReady ? "#dcfce7" : "#fef3c7",
          color: autoReady ? "#166534" : "#92400e",
        }}
      >
        {autoReady
          ? "KIS 모의투자 자동주문 READY — 자격 구성 + 안전 flag 정상 (실거래 OFF)"
          : "KIS 모의투자 자동주문 BLOCKED — 자격 미구성 시 자동주문은 차단됩니다."}
      </div>

      {error && (
        <div data-testid="kis-env-error"
             style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6 }}>
          {error}
        </div>
      )}

      <_StatusRow label="운용모드 (DEFAULT_MODE)" value={mode}
                  ok={mode === "PAPER" || mode === "SIMULATION"}
                  testid="kis-env-mode" />
      <_StatusRow label="실거래 (ENABLE_LIVE_TRADING)" value={_onOff(live)}
                  ok={!live} testid="kis-env-live" />
      {s?.enable_ai_execution !== undefined && (
        <_StatusRow label="AI 자동실행 (ENABLE_AI_EXECUTION)" value={_onOff(ai)}
                    ok={!ai} testid="kis-env-ai" />
      )}
      <_StatusRow label="KIS 모의투자 (KIS_IS_PAPER)" value={_onOff(kisPaper)}
                  ok={kisPaper} testid="kis-env-kis-paper" />
      <_StatusRow label="Paper broker (PAPER_BROKER_KIND)" value={brokerKind}
                  ok={brokerKind === "KIS_PAPER" || brokerKind === "MOCK"}
                  testid="kis-env-broker-kind" />
      <_StatusRow label="KIS Paper Auto" value={_onOff(autoOn)}
                  ok={autoOn} testid="kis-env-auto" />
      {s?.enable_ai_paper_background_tick !== undefined && (
        <_StatusRow label="Background Tick (ENABLE_AI_PAPER_BACKGROUND_TICK)"
                    value={_onOff(bgTick)} ok={bgTick}
                    testid="kis-env-bg-tick" />
      )}
      <_StatusRow label="dry-run (KIS_PAPER_AUTO_ORDER_DRY_RUN)"
                  value={_onOff(dryRun)} ok={!dryRun} testid="kis-env-dry-run" />
      <_StatusRow label="Fill Polling (KIS_PAPER_FILL_POLLING)"
                  value={_onOff(fillPolling)} ok={fillPolling}
                  testid="kis-env-fill-polling" />
      <_StatusRow label="KIS 자격 구성"
                  value={credsPresent ? "구성됨" : "미구성"}
                  ok={credsPresent} testid="kis-env-credentials" />
      {/* 4-01: 4종 자격 per-credential 구성 여부 (값 원문 0건 — 구성됨/미구성만). */}
      <_StatusRow label="APP KEY (KIS_APP_KEY)"
                  value={appKeyPresent ? "구성됨" : "미구성"}
                  ok={appKeyPresent} testid="kis-env-app-key" />
      <_StatusRow label="APP SECRET (KIS_APP_SECRET)"
                  value={appSecretPresent ? "구성됨" : "미구성"}
                  ok={appSecretPresent} testid="kis-env-app-secret" />
      <_StatusRow label="ACCOUNT NO (KIS_ACCOUNT_NO)"
                  value={accountPresent ? "구성됨" : "미구성"}
                  ok={accountPresent} testid="kis-env-account-no" />
      <_StatusRow label="PRODUCT CODE (KIS_PRODUCT_CODE)"
                  value={productCodePresent ? "구성됨" : "미구성"}
                  ok={productCodePresent} testid="kis-env-product-code" />
      {missingCreds.length > 0 && (
        <div
          data-testid="kis-env-missing-credentials"
          style={{
            padding: "5px 8px", borderRadius: 4, background: "#fef2f2",
            marginBottom: 4, fontSize: "var(--fs-xs)", color: "#b91c1c",
          }}
        >
          미구성 자격: {missingCreds.join(", ")} — backend/.env 에 입력 후
          EXE 재시작하세요.
        </div>
      )}
      <_StatusRow label="broker_order_type" value={brokerOrderType}
                  ok={brokerOrderType === "KIS_PAPER"}
                  testid="kis-env-broker-order-type" />
      <_StatusRow label="is_live_authorization" value={String(liveAuth)}
                  ok={!liveAuth} testid="kis-env-live-auth" />

      <div
        data-testid="kis-env-footer"
        style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          lineHeight: 1.6,
        }}
      >
        자격정보 원문은 표시하지 않습니다. backend/.env에만 입력하세요.
        .env.example에는 실제 값을 넣지 마세요. 현재 화면은 KIS 모의투자 설정
        확인용입니다. 화면에는 API key / Secret / 계좌번호가 표시되지 않으며,
        현재 설정은 Paper / KIS 모의투자 전용이고 실거래는 OFF입니다.
      </div>
    </Card>
  );
}

export default KisPaperEnvStatusCard;
