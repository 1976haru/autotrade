import { Card, SectionLabel, StatBox } from "../common";
import { fmtKRW, fmtPct, pnlColor } from "../../utils/format";
import { useEmergencyStopAudits, useOrderAudits } from "../../store/useAuditLogs";
import { formatPendingAge } from "./Approvals";
import { flattenApprovalAttempts, setEventKindFilter } from "./AuditLog";


// 060 hardening made emergency_stop a hard kill-switch. The downside: if an
// operator forgets it on, the system silently rejects every order. This
// banner fires when it's been on long enough that "leftover from earlier
// incident" is more likely than "intentionally still on right now."
const _STUCK_THRESHOLD_MS = 30 * 60 * 1000;

// 046 history is sorted desc (newest first). When emergency_stop is currently
// on, history[0] should be the matching ON event — its created_at is when the
// stop turned on. If history[0] is OFF for some reason (transient state /
// older ON event off-window), return null rather than guessing.
export function emergencyStopOnSince(emergencyStop, history) {
  if (!emergencyStop || !history || history.length === 0) return null;
  const latest = history[0];
  return latest.enabled ? latest.created_at : null;
}


export function EmergencyStopStuckBanner({ since, now = Date.now(), onClick }) {
  if (!since) return null;
  const elapsed = now - new Date(since).getTime();
  if (elapsed < _STUCK_THRESHOLD_MS) return null;

  return (
    <button
      type="button"
      onClick={onClick}
      data-testid="emergency-stop-stuck-banner"
      style={{
        background: "#fbbf2422",
        border: "1px solid #fbbf2466",
        borderRadius: 6,
        padding: "8px 12px",
        color: "#fbbf24",
        textAlign: "left",
        fontFamily: "inherit",
        fontSize: 11,
        cursor: onClick ? "pointer" : "default",
        width: "100%",
      }}
    >
      <div style={{ fontWeight: 700 }}>
        🛑 긴급 정지 {formatPendingAge(since, now)}째 ON
      </div>
      <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>
        모든 신규 주문이 차단됩니다. 의도적이라면 무시하세요.
      </div>
    </button>
  );
}


const _DAY_MS = 24 * 60 * 60 * 1000;

// 시간 필터 + 카운팅을 컴포넌트에서 분리해 vi.setSystemTime 없이도 단위 테스트
// 가능하도록. NEEDS_APPROVAL은 BottomNav 배지/StatusSummaryCard와 중복되지만,
// 24h 활동 요약에서는 "어제 N건이 결재 단계로 갔는지" 자체가 의미 있는 신호라
// 별도로 카운트한다. attempts(079)는 created_at 대신 `at` 필드를 쓴다.
export function computeActivity24h(orders, stops, attempts = [], now = Date.now()) {
  const since = now - _DAY_MS;
  const within         = (r) => new Date(r.created_at).getTime() >= since;
  const withinAttempt  = (r) => new Date(r.at).getTime() >= since;
  const recentOrders   = orders.filter(within);
  const recentStops    = stops.filter(within);
  const recentAttempts = attempts.filter(withinAttempt);
  return {
    orders:   recentOrders.length,
    approved: recentOrders.filter((r) => r.decision === "APPROVED").length,
    rejected: recentOrders.filter((r) => r.decision === "REJECTED").length,
    pending:  recentOrders.filter((r) => r.decision === "NEEDS_APPROVAL").length,
    stops:    recentStops.length,
    stopsOn:  recentStops.filter((r) => r.enabled).length,
    stopsOff: recentStops.filter((r) => !r.enabled).length,
    attempts: recentAttempts.length,
  };
}


// 운영자가 대시보드 진입 즉시 봐야 하는 3가지 위험/상태 신호.
// alarm=true면 강조 색상으로 시선을 잡고, 핀의 클릭은 해당 탭으로 점프.
export function StatusPin({ icon, label, value, alarm, accent, onClick, testId }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      style={{
        flex: 1,
        padding: "8px 10px", borderRadius: 6,
        cursor: onClick ? "pointer" : "default",
        background:  alarm ? `${accent}15` : "#020e1c",
        border:      `1px solid ${alarm ? `${accent}99` : "#0c2035"}`,
        color:       alarm ? accent : "#94a3b8",
        fontFamily:  "inherit",
        textAlign:   "left",
        display:     "flex",
        alignItems:  "center",
        gap:         8,
      }}
    >
      <span style={{ fontSize: 16 }}>{icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 9, color: alarm ? accent : "#475569", marginBottom: 1 }}>
          {label}
        </div>
        <div style={{ fontSize: 11, fontWeight: 700,
                       color: alarm ? accent : "#64748b",
                       overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {value}
        </div>
      </div>
    </button>
  );
}


export function StatusSummaryCard({
  emergencyStop, pendingCount, stalePendingCount = 0, running, onJumpTab,
}) {
  const _jump = onJumpTab || (() => {});

  // 결재 핀은 두 단계로 색을 escalate: 평상 amber, stale(10분+)이 하나라도
  // 끼면 빨강. "3건 적체" vs "3건 + 1건 방치"를 첫 화면에서 구분하기 위해
  // 핀 라벨에도 "(N stale)"을 부가한다.
  const hasPending = pendingCount > 0;
  const hasStale   = stalePendingCount > 0;
  const pendingValue = hasPending
    ? (hasStale ? `${pendingCount}건 (${stalePendingCount} stale)` : `${pendingCount}건`)
    : "없음";

  return (
    <div style={{ display: "flex", gap: 8 }}>
      <StatusPin
        icon="🛑"
        label="긴급 정지"
        value={emergencyStop ? "ACTIVE" : "OFF"}
        alarm={!!emergencyStop}
        accent="#ef4444"
        onClick={() => _jump("strat")}
        testId="status-pin-emergency-stop"
      />
      <StatusPin
        icon="🔐"
        label="승인 대기"
        value={pendingValue}
        alarm={hasPending}
        accent={hasStale ? "#ef4444" : "#f59e0b"}
        onClick={() => _jump("approve")}
        testId="status-pin-pending-approvals"
      />
      <StatusPin
        icon="🤖"
        label="봇"
        value={running ? "RUNNING" : "STOPPED"}
        alarm={running}
        accent="#22c55e"
        onClick={() => _jump("bot")}
        testId="status-pin-bot"
      />
    </div>
  );
}


// Activity24hCard 내부 행을 버튼으로 만들기 위한 공용 스타일.
// AuditLog 탭으로 점프 + kind filter를 미리 세팅해 도착 시 자동 적용되도록 한다.
const _DRILLDOWN_BUTTON_STYLE = {
  background:   "transparent",
  border:       "none",
  padding:      "4px 0",
  display:      "flex",
  alignItems:   "baseline",
  gap:          8,
  width:        "100%",
  textAlign:    "left",
  fontFamily:   "inherit",
  color:        "inherit",
};

export function Activity24hCard({ onJumpTab, approvals = { pending: [], history: [] } }) {
  const orders = useOrderAudits();
  const stops  = useEmergencyStopAudits();
  const attempts = flattenApprovalAttempts(approvals.pending, approvals.history);
  const a = computeActivity24h(orders.items, stops.items, attempts);
  const loading = orders.loading || stops.loading;
  const error   = orders.error || stops.error;

  const _drillDown = (kind) => {
    if (!onJumpTab) return;
    setEventKindFilter(kind);
    onJumpTab("audit");
  };

  return (
    <Card>
      <SectionLabel>최근 24시간</SectionLabel>

      {error && (
        <div style={{ color: "#f87171", fontSize: 11, marginBottom: 6 }}>{error}</div>
      )}

      {loading ? (
        <div style={{ color: "#475569", fontSize: 11, padding: 8, textAlign: "center" }}>
          로딩 중…
        </div>
      ) : (
        <>
          <button
            type="button"
            onClick={() => _drillDown("order")}
            data-testid="activity-orders-row"
            style={{ ..._DRILLDOWN_BUTTON_STYLE, marginBottom: 4,
                      cursor: onJumpTab ? "pointer" : "default" }}
          >
            <span style={{ fontSize: 11, color: "#94a3b8" }}>주문</span>
            <span style={{ fontSize: 14, fontWeight: 700, color: "#7dd3fc" }}>{a.orders}건</span>
            <span style={{ fontSize: 10, color: "#475569" }}>
              {" · "}
              <span style={{ color: "#22c55e", fontWeight: 700 }}>승인 {a.approved}</span>
              {" · "}
              <span style={{ color: "#ef4444", fontWeight: 700 }}>거부 {a.rejected}</span>
              {" · "}
              <span style={{ color: "#f59e0b", fontWeight: 700 }}>대기 {a.pending}</span>
            </span>
          </button>
          <button
            type="button"
            onClick={() => _drillDown("stop")}
            data-testid="activity-stops-row"
            style={{ ..._DRILLDOWN_BUTTON_STYLE,
                      cursor: onJumpTab ? "pointer" : "default" }}
          >
            <span style={{ fontSize: 11, color: "#94a3b8" }}>긴급정지 토글</span>
            <span style={{
              fontSize: 14, fontWeight: 700,
              color: a.stops > 0 ? "#fbbf24" : "#475569",
            }}>{a.stops}건</span>
            {a.stops > 0 && (
              <span style={{ fontSize: 10, color: "#475569" }}>
                {" · "}
                <span style={{ color: "#ef4444", fontWeight: 700 }}>ON {a.stopsOn}</span>
                {" · "}
                <span style={{ color: "#22c55e", fontWeight: 700 }}>OFF {a.stopsOff}</span>
              </span>
            )}
          </button>
          {a.attempts > 0 && (
            <button
              type="button"
              onClick={() => _drillDown("attempt")}
              data-testid="activity-attempts-row"
              style={{ ..._DRILLDOWN_BUTTON_STYLE, marginTop: 4,
                        cursor: onJumpTab ? "pointer" : "default" }}
            >
              <span style={{ fontSize: 11, color: "#94a3b8" }}>결재 시도 거부</span>
              <span style={{ fontSize: 14, fontWeight: 700, color: "#ef4444" }}>
                {a.attempts}건
              </span>
            </button>
          )}
        </>
      )}
    </Card>
  );
}


export function Dashboard({
  portfolio, bot, botControls, emergencyStop,
  emergencyStopSince,
  pendingCount = 0, stalePendingCount = 0,
  approvals,
  onJumpTab,
}) {
  const { totalAsset, totalPnL, totalPnLPct, cash, positions } = portfolio;
  const { stats, winRate, trades, running } = bot;
  const { start, stop } = botControls;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>

      {/* 긴급 정지가 오래 켜져 있을 때 reminder — 위험/상태 요약보다 먼저 노출 */}
      <EmergencyStopStuckBanner
        since={emergencyStopSince}
        onClick={() => onJumpTab && onJumpTab("strat")}
      />

      {/* 위험/상태 요약 */}
      <StatusSummaryCard
        emergencyStop={emergencyStop}
        pendingCount={pendingCount}
        stalePendingCount={stalePendingCount}
        running={running}
        onJumpTab={onJumpTab}
      />

      {/* KPI */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
        <Card>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>총 자산</div>
          <div style={{ fontSize: 14, fontWeight: 700 }}>{fmtKRW(Math.round(totalAsset))}원</div>
          <div style={{ fontSize: 10, color: "#334155", marginTop: 2 }}>현금 {fmtKRW(cash)}원</div>
        </Card>
        <Card>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>평가손익</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: pnlColor(totalPnL) }}>
            {totalPnL >= 0 ? "+" : ""}{fmtKRW(Math.round(totalPnL))}원
          </div>
          <div style={{ fontSize: 10, color: pnlColor(totalPnLPct), marginTop: 2 }}>
            {fmtPct(totalPnLPct)}
          </div>
        </Card>
        <Card>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>봇 누적</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: pnlColor(stats.pnl) }}>
            {stats.pnl >= 0 ? "+" : ""}{fmtKRW(stats.pnl)}원
          </div>
          <div style={{ fontSize: 10, color: "#334155", marginTop: 2 }}>승률 {winRate}%</div>
        </Card>
      </div>

      {/* 24시간 활동 요약 */}
      <Activity24hCard onJumpTab={onJumpTab} approvals={approvals} />

      {/* 봇 컨트롤 */}
      <Card accentColor={running ? "#22c55e33" : undefined}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
              width: 8, height: 8, borderRadius: "50%",
              background: running ? "#22c55e" : "#334155",
              boxShadow: running ? "0 0 8px #22c55e" : "none",
            }} />
            <span style={{ fontSize: 12, fontWeight: 700, color: running ? "#22c55e" : "#475569" }}>
              {running ? "BOT RUNNING" : "BOT STOPPED"}
            </span>
          </div>
          <button
            onClick={running ? stop : start}
            style={{
              padding: "7px 18px", borderRadius: 4, border: "none",
              cursor: "pointer", fontFamily: "inherit", fontWeight: 700, fontSize: 12,
              background: running ? "#ef4444" : "#22c55e",
              color: "#010a14",
            }}
          >
            {running ? "⏹ 정지" : "▶ 시작"}
          </button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", textAlign: "center" }}>
          <StatBox label="매매" value={stats.total} color="#7dd3fc" />
          <StatBox label="승"   value={stats.wins}  color="#22c55e" />
          <StatBox label="패"   value={stats.losses} color="#ef4444" />
          <StatBox label="승률" value={`${winRate}%`} color={+winRate >= 55 ? "#22c55e" : "#f59e0b"} />
        </div>
      </Card>

      {/* 포지션 */}
      <Card>
        <SectionLabel>LIVE POSITIONS</SectionLabel>
        {positions.length === 0 ? (
          <div style={{ color: "#1e3a5c", textAlign: "center", padding: 16, fontSize: 12 }}>
            보유 포지션 없음
          </div>
        ) : positions.map((p) => {
          const pnl = (p.cur - p.avg) * p.qty;
          const pp  = ((p.cur - p.avg) / p.avg) * 100;
          return (
            <div key={p.code} style={{
              display: "flex", justifyContent: "space-between",
              padding: "7px 0", borderBottom: "1px solid #05121f", fontSize: 12,
            }}>
              <div>
                <span style={{ color: "#7dd3fc", fontSize: 11 }}>{p.code}</span>
                <br /><span>{p.name}</span>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ color: pnlColor(pnl), fontWeight: 700 }}>
                  {pnl >= 0 ? "+" : ""}{fmtKRW(Math.round(pnl))}원
                </div>
                <div style={{ fontSize: 11, color: pnlColor(pp) }}>{fmtPct(pp)}</div>
              </div>
            </div>
          );
        })}
      </Card>

      {/* 최근 체결 */}
      <Card>
        <SectionLabel>RECENT TRADES {running && <span style={{ color: "#22c55e" }}>● LIVE</span>}</SectionLabel>
        {trades.length === 0 ? (
          <div style={{ color: "#1e3a5c", textAlign: "center", padding: 16, fontSize: 12 }}>
            전략 엔진 미연동 (백엔드 체결 스트림 대기)
          </div>
        ) : trades.slice(0, 8).map((t) => (
          <div key={t.id} style={{
            display: "flex", justifyContent: "space-between",
            padding: "5px 0", borderBottom: "1px solid #05121f", fontSize: 11,
          }}>
            <span style={{ color: "#334155" }}>{t.ts}</span>
            <span style={{ color: "#94a3b8" }}>{t.name}</span>
            <span style={{ color: pnlColor(t.pnl), fontWeight: 700 }}>
              {t.pnl >= 0 ? "+" : ""}{fmtKRW(t.pnl)}원
            </span>
          </div>
        ))}
      </Card>
    </div>
  );
}
