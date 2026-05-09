import { useState } from "react";
import { BROKERS } from "../../config/brokers";
import { Card, SectionLabel, Btn, Inp } from "../common";
import { PageHeader } from "../common/primitives";
import { friendlyErrorMessage } from "../../utils/errorMessage";
import { useBackendStatus } from "../../store/useBackendStatus";
import { WatchlistsCard } from "./WatchlistsCard";
import {
  ReleaseNotesModal,
  VersionBadge,
} from "../common/VersionBadge";
import { APP_INFO, appVersionLine } from "../../config/appInfo";
import { latestReleaseNote } from "../../config/releaseNotes";


// 버전 / 공지 카드 — Settings 탭 상단에 노출. VersionBadge 클릭 시 release
// notes modal 재오픈.
export function VersionInfoCard() {
  const [open, setOpen] = useState(false);
  const note = latestReleaseNote();
  return (
    <Card data-testid="version-info-card">
      <SectionLabel>📦 버전 / 공지사항</SectionLabel>
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontWeight: 700, color: "var(--c-text)",
                         fontSize: "var(--fs-md)" }}>
            {APP_INFO.displayName}
          </div>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
                         fontFamily: "monospace", marginTop: 2 }}>
            {appVersionLine()}
          </div>
          {note && (
            <div style={{ fontSize: "var(--fs-xs)",
                           color: "var(--c-text-2)", marginTop: 4 }}>
              최근 업데이트: <strong>{note.title}</strong>
              <span style={{ color: "var(--c-text-3)", marginLeft: 6 }}>
                ({note.date})
              </span>
            </div>
          )}
        </div>
        <VersionBadge onClick={() => setOpen(true)}
                       testId="settings-version-badge" />
      </div>
      <ReleaseNotesModal open={open} onClose={() => setOpen(false)} />
    </Card>
  );
}


// 060 (emergency_stop hard-reject) and 061 (LIVE flag gating the queue)
// made several mode + flag combinations silently reject every order. The
// operator who sets DEFAULT_MODE=LIVE_MANUAL_APPROVAL but forgets to flip
// ENABLE_LIVE_TRADING=true sees nothing in the queue and might think the
// system is broken. This helper detects those combinations so Settings can
// surface a warning before the operator wonders why nothing is happening.
const _LIVE_MODES = new Set([
  "LIVE_MANUAL_APPROVAL", "LIVE_AI_ASSIST", "LIVE_AI_EXECUTION",
]);

export function computeModeWarning(status) {
  if (!status) return null;
  const { default_mode, enable_live_trading } = status;
  if (_LIVE_MODES.has(default_mode) && !enable_live_trading) {
    return {
      title: "현재 모든 주문이 REJECTED됩니다",
      detail: `DEFAULT_MODE=${default_mode}이지만 ENABLE_LIVE_TRADING=false라서 RiskManager가 큐 자체를 차단합니다. 운영자가 환경변수 ENABLE_LIVE_TRADING=true를 명시적으로 설정해야 합니다.`,
    };
  }
  return null;
}


export function ModeWarningBanner({ warning }) {
  if (!warning) return null;
  return (
    <Card accentColor="#ef444466"
          style={{ background: "#7f1d1d22" }}>
      <div data-testid="mode-warning-banner"
           style={{ fontSize: 12, color: "#fca5a5", fontWeight: 700, marginBottom: 6 }}>
        ⚠ {warning.title}
      </div>
      <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.5 }}>
        {warning.detail}
      </div>
    </Card>
  );
}


// 201: Full env-flag matrix mirrored from backend /api/status `safety_flags`.
// Each entry maps to CLAUDE.md "안전 플래그" table; the rendered badge
// highlights "안전" (default-position keeps risk gate engaged) vs "위험"
// (a non-default value that loosens a guard) so the operator can scan for
// any flag that's been flipped without checking env files.
const _SAFETY_FLAG_ROWS = [
  { key: "default_mode",                envVar: "DEFAULT_MODE",
    label: "기본 운용모드",   safeValue: "SIMULATION", kind: "string" },
  { key: "enable_live_trading",         envVar: "ENABLE_LIVE_TRADING",
    label: "실거래 활성",     safeValue: false,        kind: "bool" },
  { key: "enable_ai_execution",         envVar: "ENABLE_AI_EXECUTION",
    label: "AI 자동실행",     safeValue: false,        kind: "bool" },
  { key: "enable_futures_live_trading", envVar: "ENABLE_FUTURES_LIVE_TRADING",
    label: "선물 실거래",     safeValue: false,        kind: "bool" },
  { key: "kis_is_paper",                envVar: "KIS_IS_PAPER",
    label: "KIS 모의투자",    safeValue: true,         kind: "bool", inverted: true },
  { key: "market_data_provider",        envVar: "MARKET_DATA_PROVIDER",
    label: "시장 데이터",     safeValue: "mock",       kind: "string" },
  { key: "enable_fill_polling",         envVar: "ENABLE_FILL_POLLING",
    label: "체결 폴링",       safeValue: false,        kind: "bool" },
  { key: "stale_price_max_age_seconds", envVar: "STALE_PRICE_MAX_AGE_SECONDS",
    label: "시세 stale 한도", safeValue: 60,           kind: "seconds",
    safetyHint: "값이 작을수록 안전 — 기본 60초" },
];

function _formatFlagValue(value, kind) {
  if (value == null) return "—";
  if (kind === "bool")    return value ? "ON" : "OFF";
  if (kind === "seconds") return `${value}s`;
  return String(value);
}

export function SafetyFlagsCard({ status, loading, error }) {
  if (loading) {
    return (
      <Card>
        <SectionLabel>🛡 안전 플래그</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>🛡 안전 플래그</SectionLabel>
        <div style={{
          fontSize: "var(--fs-sm)", color: "var(--c-danger)",
          padding: "10px 14px", background: "#fef2f2",
          border: "1px solid #fecaca", borderRadius: "var(--r-md)",
        }}>{friendlyErrorMessage(error) || "안전 플래그 정보를 불러올 수 없어요."}</div>
      </Card>
    );
  }
  const flags = status?.safety_flags;
  if (!flags) {
    return (
      <Card>
        <SectionLabel>🛡 안전 플래그</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>
          백엔드가 safety_flags 블록을 반환하지 않았습니다 (구버전 API).
        </div>
      </Card>
    );
  }
  return (
    <Card data-testid="safety-flags-card">
      <SectionLabel>🛡 안전 플래그</SectionLabel>
      <div style={{ fontSize: 9, color: "#334155", marginBottom: 8, lineHeight: 1.5 }}>
        CLAUDE.md "안전 플래그" 표의 라이브 스냅샷. 위험 배지는 기본값에서 벗어나
        가드가 풀린 상태를 의미하며, 운영자가 의도한 변경인지 점검해야 합니다.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {_SAFETY_FLAG_ROWS.map((row) => {
          const v = flags[row.key];
          const isUnsafe = v !== row.safeValue;
          return (
            <div key={row.key}
                 data-testid={`safety-flag-${row.key}`}
                 style={{
                   display: "grid",
                   gridTemplateColumns: "1.4fr 0.9fr auto",
                   alignItems: "baseline", gap: 6,
                   padding: "6px 8px", background: "#0c2035", borderRadius: 3,
                 }}>
              <div>
                <div style={{ fontSize: 11, color: "#94a3b8", fontWeight: 700 }}>
                  {row.label}
                </div>
                <div style={{ fontSize: 8, color: "#334155", fontFamily: "monospace" }}>
                  {row.envVar}
                </div>
              </div>
              <div style={{ fontSize: 11, color: "#7dd3fc", fontFamily: "monospace" }}>
                {_formatFlagValue(v, row.kind)}
              </div>
              <span style={{
                fontSize: 9, fontWeight: 700, letterSpacing: "0.06em",
                padding: "2px 6px", borderRadius: 3,
                color:      isUnsafe ? "#ef4444"   : "#22c55e",
                background: isUnsafe ? "#7f1d1d33" : "#14532d33",
                border: `1px solid ${isUnsafe ? "#ef444466" : "#22c55e66"}`,
              }}>
                {isUnsafe ? "위험" : "안전"}
              </span>
            </div>
          );
        })}
      </div>
    </Card>
  );
}


export function Settings({ settings }) {
  const {
    brokerId, broker, tradeMode, apiKeys,
    connected, connecting, connMsg,
    switchBroker, switchMode, updateKey, connect,
    operatorName, setOperatorName,
  } = settings;
  const { status: backendStatus, loading: statusLoading, error: statusError } =
    useBackendStatus();
  const modeWarning = computeModeWarning(backendStatus);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <PageHeader
        title="설정"
        subtitle="브로커 / 운용 모드 / 운영자 / 안전 플래그 안내"
      />

      {/* 버전 / 공지 — VersionBadge 클릭으로 release notes 재오픈 */}
      <VersionInfoCard />

      {/* 위험한 mode + flag 조합 경고 — 가장 먼저 노출 */}
      <ModeWarningBanner warning={modeWarning} />

      {/* 201: 전체 환경변수 안전 플래그 — 위험 배지로 한눈에 점검 */}
      <SafetyFlagsCard
        status={backendStatus}
        loading={statusLoading}
        error={statusError}
      />

      {/* 거래 모드 */}
      <Card>
        <SectionLabel>거래 모드</SectionLabel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          {[
            ["sim",  "🔵 시뮬레이션", "#7dd3fc"],
            ["live", "🔴 실전 거래",  "#ef4444"],
          ].map(([mode, label, color]) => (
            <button
              key={mode}
              onClick={() => switchMode(mode)}
              style={{
                padding: 10, borderRadius: 4, cursor: "pointer",
                fontFamily: "inherit", fontWeight: 700, fontSize: 12,
                background: tradeMode === mode ? color + "22" : "transparent",
                border:     `1px solid ${tradeMode === mode ? color : "#1a3a5c"}`,
                color:      tradeMode === mode ? color : "#475569",
              }}
            >
              {label}
            </button>
          ))}
        </div>
        {tradeMode === "live" && (
          <div style={{ marginTop: 8, padding: "8px 10px", background: "#ef444415", borderRadius: 4, fontSize: 11, color: "#fca5a5" }}>
            ⚠ 실제 자금이 사용됩니다 — API 설정을 반드시 확인하세요
          </div>
        )}
      </Card>

      {/* 증권사 선택 */}
      <Card>
        <SectionLabel>증권사 선택</SectionLabel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 12 }}>
          {Object.values(BROKERS).map((b) => (
            <button
              key={b.id}
              onClick={() => { if (!b.disabled) switchBroker(b.id); }}
              disabled={b.disabled}
              style={{
                padding: "10px 8px", borderRadius: 4,
                cursor:     b.disabled ? "not-allowed" : "pointer",
                fontFamily: "inherit", textAlign: "left",
                background: brokerId === b.id ? b.color + "22" : "transparent",
                border:     `1px solid ${brokerId === b.id ? b.color : "#1a3a5c"}`,
                color:      b.disabled ? "#334155" : brokerId === b.id ? b.color : "#64748b",
                opacity:    b.disabled ? 0.5 : 1,
                fontSize:   12, fontWeight: 700,
              }}
            >
              <div>{b.name}</div>
              <div style={{ fontSize: 10, color: "#475569", marginTop: 2, fontWeight: 400 }}>{b.note}</div>
            </button>
          ))}
        </div>

        {/* API Key 입력 필드 */}
        {tradeMode !== "sim" && !broker.disabled && broker.fields?.map((f) => (
          <div key={f.key} style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>{f.label}</div>
            <Inp
              value={apiKeys[f.key] || ""}
              onChange={(v) => updateKey(f.key, v)}
              placeholder={f.placeholder || f.label}
              type={f.type}
            />
          </div>
        ))}

        <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 8 }}>
          <Btn color={broker.color || "#7dd3fc"} onClick={connect} disabled={connecting}>
            {connecting ? "연결 중..." : connected ? "✓ 재연결" : "API 연결"}
          </Btn>
          {connected && <span style={{ fontSize: 12, color: "#22c55e" }}>● 연결됨</span>}
          {connMsg && (
            <span style={{ fontSize: 11, color: connMsg.includes("성공") || connMsg.includes("시뮬") ? "#22c55e" : "#f87171" }}>
              {connMsg}
            </span>
          )}
        </div>
      </Card>

      {/* 백엔드 프록시 가이드 */}
      {tradeMode === "live" && (
        <Card accentColor="#f59e0b33">
          <SectionLabel>⚙ 실전 백엔드 프록시 설정</SectionLabel>
          <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.9 }}>
            국내 증권사 REST API는 브라우저 직접 호출 시 CORS 차단됩니다.<br />
            <span style={{ color: "#f59e0b" }}>① Python FastAPI / Node.js 로컬 프록시 서버 구축</span><br />
            <span style={{ color: "#f59e0b" }}>② API Key는 서버 .env 파일에만 보관 (보안)</span><br />
            <span style={{ color: "#f59e0b" }}>③ 앱 → localhost:8080/proxy/ 경유 호출</span><br />
            <span style={{ color: "#7dd3fc" }}>★ 권장: 한국투자증권(KIS) — REST API 문서 최충실</span>
          </div>
        </Card>
      )}

      {/* 18: 관심종목 (universe 후보군) */}
      <WatchlistsCard />

      {/* 운영자명 (감사 로그용) */}
      <Card>
        <SectionLabel>운영자명 (감사 로그용)</SectionLabel>
        <div style={{ fontSize: 10, color: "#475569", marginBottom: 8, lineHeight: 1.5 }}>
          긴급 정지 등 결재 모달의 <code>decided_by</code> 필드에 미리 채워집니다.
          이 기기에만 저장되며 (localStorage), 백엔드로는 토글 시점에만 전송됩니다.
        </div>
        <Inp
          value={operatorName || ""}
          onChange={setOperatorName}
          placeholder="예: ops1, trader-hsuhyun"
        />
      </Card>

      {/* PWA 설치 안내 */}
      <Card accentColor="#a855f733">
        <SectionLabel>📱 스마트폰 앱으로 설치 (PWA)</SectionLabel>
        <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.9 }}>
          <span style={{ color: "#a855f7", fontWeight: 700 }}>Android Chrome</span>: 우상단 메뉴 → 홈 화면에 추가<br />
          <span style={{ color: "#a855f7", fontWeight: 700 }}>iOS Safari</span>: 공유 버튼 → 홈 화면에 추가<br />
          → 설치 후 전체화면 앱으로 실행됩니다
        </div>
      </Card>

      {/* 버전 정보 */}
      <div style={{ textAlign: "center", fontSize: 10, color: "#1e3a5c", padding: "8px 0" }}>
        AI Auto Trader v2.1.0 · 모듈식 아키텍처 · Claude Sonnet 4
      </div>
    </div>
  );
}
