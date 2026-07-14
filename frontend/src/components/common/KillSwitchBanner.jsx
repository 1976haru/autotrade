/**
 * 킬스위치(긴급정지) ON 상태를 화면 최상단에서 절대 놓치지 않게 하는 배너.
 *
 * incident: 07-13 크래시 대응으로 오퍼레이터가 킬스위치를 켠 뒤 끄는 걸
 * 잊어 07-14 하루 전체 주문이 전부 차단됐다. 근본 원인은 "지금 켜져 있다"를
 * 알려주는 화면이 없었다는 것 — 이 배너가 그 갭을 메운다.
 *
 * 절대 원칙:
 *   - 본 컴포넌트는 프론트 전용. 리스크 판단이나 자동 청산 로직은 만들지
 *     않는다 — [해제] 버튼은 기존 `POST /api/risk/emergency-stop` 한 곳만
 *     호출한다 (route_order/broker import 0건).
 *   - level === OFF면 아무것도 렌더하지 않는다 (배너 자체가 없음).
 *   - 접기/무시 컨트롤 없음 — 킬스위치가 켜져 있는 한 항상 최상단에 노출.
 */

import { useKillSwitchStatus } from "../../store/useKillSwitchStatus";
import { isMarketOpen } from "../../utils/marketHours";

const _MIN  = 60_000;
const _HOUR = 60 * _MIN;
const _DAY  = 24 * _HOUR;

/** "29시간 경과" / "1일 5시간 경과" / "3분 경과" — 정확한 경과시간 표기.
 *  emergencyStopOnSince/formatPendingAge의 "~전" 상대표현과는 의도가 달라
 *  (여기는 "얼마나 방치됐는지"가 핵심) 별도 포맷터로 둔다. */
export function formatElapsedDuration(sinceIso, now = Date.now()) {
  const elapsed = Math.max(0, now - new Date(sinceIso).getTime());
  if (elapsed < _MIN) return "방금 경과";
  if (elapsed < _HOUR) return `${Math.floor(elapsed / _MIN)}분 경과`;
  if (elapsed < _DAY) return `${Math.floor(elapsed / _HOUR)}시간 경과`;
  const days = Math.floor(elapsed / _DAY);
  const hours = Math.floor((elapsed % _DAY) / _HOUR);
  return hours > 0 ? `${days}일 ${hours}시간 경과` : `${days}일 경과`;
}

const _LEVEL_LABEL = {
  LEVEL_1: "LEVEL 1 — 신규 매수 중단",
  LEVEL_2: "LEVEL 2 — 미체결 취소 후보",
  LEVEL_3: "LEVEL 3 — 청산 후보 표시",
};

export function KillSwitchBanner({
  testId = "kill-switch-banner",
  operatorName,
  now = Date.now(),
}) {
  const { status, loading, error, busy, disable } = useKillSwitchStatus();

  // 조회 실패/로딩 중엔 조용히 숨김 — 오탐으로 화면을 막지 않는다. 실제 ON
  // 여부는 다음 폴링 tick에서 다시 반영된다.
  if (loading || error || !status || status.level === "OFF") return null;

  const marketHours = isMarketOpen(new Date(now));
  const levelLabel = _LEVEL_LABEL[status.level] || status.level;
  const activatedAt = status.active_since
    ? new Date(status.active_since).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })
    : null;

  const handleDisable = async () => {
    if (busy) return;
    await disable({
      decided_by: operatorName || undefined,
      note: "배너에서 해제",
    });
  };

  return (
    <div
      data-testid={testId}
      data-market-hours={marketHours ? "true" : "false"}
      role="alert"
      aria-live="assertive"
      style={{
        position: "sticky", top: 0, zIndex: 100,
        width: "100%", boxSizing: "border-box",
        padding: "10px 16px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 12, flexWrap: "wrap",
        background: marketHours ? "#b91c1c" : "#991b1b",
        color: "#fff",
        boxShadow: "0 2px 8px rgba(0,0,0,0.35)",
        animation: marketHours ? "ks-banner-pulse 1.4s ease-in-out infinite" : "none",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
        <div style={{ fontWeight: 800, fontSize: 15 }} data-testid={`${testId}-title`}>
          🔴 긴급정지 활성 중 — 모든 주문 차단됨
        </div>
        <div style={{ fontSize: 12, opacity: 0.92 }} data-testid={`${testId}-detail`}>
          {levelLabel}
          {activatedAt && (
            <span data-testid={`${testId}-since`}>
              {" · "}{activatedAt}부터, {formatElapsedDuration(status.active_since, now)}
            </span>
          )}
          {status.decided_by && <span>{" · "}{status.decided_by}</span>}
        </div>
      </div>

      <button
        type="button"
        data-testid={`${testId}-disable-btn`}
        onClick={handleDisable}
        disabled={busy}
        style={{
          flexShrink: 0,
          fontWeight: 700, fontSize: 13,
          padding: "6px 16px", borderRadius: 6,
          border: "1px solid rgba(255,255,255,0.7)",
          background: busy ? "rgba(255,255,255,0.2)" : "#fff",
          color: busy ? "#fff" : "#991b1b",
          cursor: busy ? "wait" : "pointer",
          fontFamily: "inherit",
        }}
      >
        {busy ? "해제 중..." : "해제"}
      </button>

      <style>{`
        @keyframes ks-banner-pulse {
          0%, 100% { filter: brightness(1); }
          50%      { filter: brightness(1.25); }
        }
      `}</style>
    </div>
  );
}

export default KillSwitchBanner;
