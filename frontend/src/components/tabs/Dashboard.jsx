import { Card, SectionLabel, StatBox } from "../common";
import { fmtKRW, fmtPct, formatPendingAge, pnlColor } from "../../utils/format";
import { useEmergencyStopAudits, useOrderAudits } from "../../store/useAuditLogs";
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


// 097 supporting helper — Dashboard가 봇 idle 경고를 위해 24h 주문 카운트만
// 쓰는 가벼운 경로. computeActivity24h를 그대로 쓰면 stops/byMode 등을 다 도는
// 비용이 매 렌더 발생해 분리.
function _count24h(orders, now = Date.now()) {
  const since = now - _DAY_MS;
  return orders.filter((r) => new Date(r.created_at).getTime() >= since).length;
}

// 시간 필터 + 카운팅을 컴포넌트에서 분리해 vi.setSystemTime 없이도 단위 테스트
// 가능하도록. NEEDS_APPROVAL은 BottomNav 배지/StatusSummaryCard와 중복되지만,
// 24h 활동 요약에서는 "어제 N건이 결재 단계로 갔는지" 자체가 의미 있는 신호라
// 별도로 카운트한다. attempts(079)는 created_at 대신 `at` 필드를 쓴다.
//
// 093: byMode — 24h 안의 주문을 mode별로 분포. 092에서 처리 내역 모드 필터가
// 1급 시민이 됐으니 dashboard에서도 "오늘 SIMULATION 5 · PAPER 2 · MANUAL 3"
// 같은 미니 분포가 같이 있어야 모드별 규모 비교가 한눈에 가능하다.
// count가 0인 mode는 키를 생략 — 운영자가 쓰지 않는 mode는 안 보이는 편이
// 시선 노이즈가 적다.
export function computeActivity24h(orders, stops, attempts = [], now = Date.now()) {
  const since = now - _DAY_MS;
  const within         = (r) => new Date(r.created_at).getTime() >= since;
  const withinAttempt  = (r) => new Date(r.at).getTime() >= since;
  const recentOrders   = orders.filter(within);
  const recentStops    = stops.filter(within);
  const recentAttempts = attempts.filter(withinAttempt);
  const byMode = {};
  for (const r of recentOrders) {
    if (!r.mode) continue;  // defensive — fixtures could miss it
    byMode[r.mode] = (byMode[r.mode] || 0) + 1;
  }
  return {
    orders:   recentOrders.length,
    approved: recentOrders.filter((r) => r.decision === "APPROVED").length,
    rejected: recentOrders.filter((r) => r.decision === "REJECTED").length,
    pending:  recentOrders.filter((r) => r.decision === "NEEDS_APPROVAL").length,
    byMode,
    stops:    recentStops.length,
    stopsOn:  recentStops.filter((r) => r.enabled).length,
    stopsOff: recentStops.filter((r) => !r.enabled).length,
    attempts: recentAttempts.length,
  };
}


// 093: 6 운용모드의 짧은 라벨 + 색상. 표시 순서는 위험도 오름차순 — 시뮬부터
// LIVE까지 자연스럽게 읽히도록. 092가 처리 내역에서 LIVE_MANUAL_APPROVAL +
// LIVE_AI_ASSIST 만 다뤘다면, 여기는 주문 audit 전체라 6개 모드 모두 등장 가능.
export const MODE_DISPLAY = [
  { id: "SIMULATION",           label: "SIM",     color: "#64748b" },
  { id: "PAPER",                label: "PAPER",   color: "#7dd3fc" },
  { id: "LIVE_SHADOW",          label: "SHADOW",  color: "#94a3b8" },
  { id: "LIVE_MANUAL_APPROVAL", label: "MANUAL",  color: "#22c55e" },
  { id: "LIVE_AI_ASSIST",       label: "AI 보조", color: "#a78bfa" },
  { id: "LIVE_AI_EXECUTION",    label: "AI 자동", color: "#f59e0b" },
];


// "byMode 객체를 위 정렬 순서로 평탄화 + 0건 모드는 생략 + 알 수 없는 mode는
// 끝에 회색으로 모음"의 순수 함수. UI 행과 별도 단위 테스트가 가능하도록 분리.
export function formatModeBreakdown(byMode) {
  const known = MODE_DISPLAY
    .filter((m) => (byMode[m.id] || 0) > 0)
    .map((m) => ({ id: m.id, label: m.label, color: m.color, count: byMode[m.id] }));
  const knownIds = new Set(MODE_DISPLAY.map((m) => m.id));
  const unknown = Object.entries(byMode)
    .filter(([id, count]) => count > 0 && !knownIds.has(id))
    .map(([id, count]) => ({ id, label: id, color: "#475569", count }));
  return [...known, ...unknown];
}


// 097: 봇 RUNNING이지만 최근 24h 주문이 0건이면 신호 stuck/dead 의심.
// 평상 RUNNING(초록)과 STOPPED(회색) 사이에 노란 idle 단계를 추가해, 봇이
// 잘 돌고 있는 줄 알았는데 사실은 시그널이 안 뜨는 채로 굴러가는 상황을
// 첫 화면에서 알아챌 수 있도록.
export function botIdleSignal(running, ordersIn24h) {
  if (!running) return "off";
  if ((ordersIn24h || 0) === 0) return "idle";
  return "running";
}

// 봇 핀의 세 시각 상태. 071/058 다른 핀과 같이 alarm/accent 두 prop으로
// StatusPin에 흘려보낸다.
export const BOT_SIGNAL_DISPLAY = {
  off:     { value: "STOPPED",        color: "#94a3b8", alarm: false },
  running: { value: "RUNNING",        color: "#22c55e", alarm: true  },
  idle:    { value: "RUNNING (24h 0건)", color: "#fbbf24", alarm: true  },
};


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
  // 097: 봇 RUNNING + 24h 주문 0건이면 idle 의심으로 escalate. 기본값은 1로
  // 둬서 "데이터 모름" 호출자(예: 097 이전 시점에 만들어진 wrapper)가 idle
  // 경고를 잘못 트리거하지 않도록 — 명시적으로 0을 넘긴 경우만 idle 분기.
  ordersIn24h = 1,
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

  // 097: 봇 핀의 세 단계 — STOPPED(회색) / RUNNING(초록) / idle(노랑).
  // 같은 testId(`status-pin-bot`)를 유지해 058~077의 기존 회귀가 깨지지 않도록.
  const _botSignal = botIdleSignal(running, ordersIn24h);
  const _botDisplay = BOT_SIGNAL_DISPLAY[_botSignal];

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
        value={_botDisplay.value}
        alarm={_botDisplay.alarm}
        accent={_botDisplay.color}
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

// 093: 주문 행 바로 아래 sub-line. 모드별 개수가 0보다 큰 칩만 표시 — 분포가
// 단일 모드라면 한 칩만 나오는 게 자연스럽고, 다양한 모드가 섞이면 비교가 한눈에.
// 표시할 모드가 하나도 없으면 (orders=0인 평소) 행 자체를 렌더하지 않는다.
export function ModeBreakdownRow({ byMode }) {
  const cells = formatModeBreakdown(byMode);
  if (cells.length === 0) return null;
  return (
    <div data-testid="activity-mode-breakdown"
         style={{ display: "flex", flexWrap: "wrap", gap: 6, padding: "0 0 4px 36px",
                  fontSize: 9, marginBottom: 4 }}>
      {cells.map((c) => (
        <span key={c.id}
              data-testid={`activity-mode-cell-${c.id}`}
              style={{
                color: c.color,
                fontWeight: 700,
                padding: "1px 6px",
                borderRadius: 3,
                border: `1px solid ${c.color}55`,
                background: `${c.color}15`,
              }}>
          {c.label} {c.count}
        </span>
      ))}
    </div>
  );
}


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
          <ModeBreakdownRow byMode={a.byMode} />
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

  // 097: 봇 핀 idle 경고용 24h 주문 수 — useOrderAudits를 한 번 더 호출하는
  // 비용은 같은 5s 폴링이 두 인스턴스가 되는 정도로 미미. 데이터 일관성을
  // 위해 Activity24hCard와 hook을 lift up하는 refactor는 별도 PR.
  const _orderAudits = useOrderAudits();
  const _ordersIn24h = _count24h(_orderAudits.items);

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
        ordersIn24h={_ordersIn24h}
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
