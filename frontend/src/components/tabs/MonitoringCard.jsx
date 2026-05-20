/**
 * 체크리스트 #70: Monitoring Dashboard Card.
 *
 * CLAUDE.md 절대 원칙:
 *   1. 본 카드는 broker / 주문 / route_order 호출 0건. read-only 표시만.
 *   2. Secret / API Key / 계좌번호를 *입력받지 않는다* — 응답에 들어올 수
 *      없도록 backend가 차단하지만, 추가 방어로 본 카드는 *value JSON을
 *      직접 노출하지 않고* status / message / 핵심 수치만 표시한다.
 *   3. "긴급정지 토글" / "주문 실행" / "LIVE 활성화" 같은 control 0개.
 *      운영자 행동 버튼은 다른 탭(Risk / Approvals)에서만.
 *
 * UI 구성:
 *   - 상단: overall status badge (OK/WARN/CRITICAL/UNKNOWN, 색상)
 *   - 모바일: 시스템 / 데이터 / 주문&리스크 3개 요약 칩
 *   - 데스크탑: 8개 메트릭 상세 (server / database / api_error_rate /
 *     order_failure_rate / approval_queue / risk_events / data_freshness /
 *     notification)
 *   - 하단: alert candidates 목록 (송신 X — 후보 표시만)
 *
 * 수익률 표시 없음 — 시스템 안정성 우선.
 */

import { useMemo } from "react";
import { Card, SectionLabel } from "../common";
import { MarketClosedNotice } from "../common/MarketClosedNotice";
import { useMonitoring } from "../../store/useMonitoring";
import { MarketPhase, currentMarketPhase } from "../../utils/marketHours";


const STATUS_COLOR = {
  OK:       "#22c55e",
  WARN:     "#f59e0b",
  CRITICAL: "#ef4444",
  UNKNOWN:  "#94a3b8",
};

const STATUS_LABEL = {
  OK:       "정상",
  WARN:     "주의",
  CRITICAL: "심각",
  UNKNOWN:  "측정 불가",
};

const METRIC_DISPLAY_NAME = {
  server:             "서버",
  database:           "데이터베이스",
  api_error_rate:     "API 오류율",
  order_failure_rate: "주문 실패율",
  approval_queue:     "승인 대기",
  risk_events:        "리스크 이벤트",
  data_freshness:     "데이터 지연",
  notification:       "알림",
};


function StatusBadge({ status, small = false }) {
  const color = STATUS_COLOR[status] || STATUS_COLOR.UNKNOWN;
  const label = STATUS_LABEL[status] || status || "—";
  return (
    <span
      data-testid={`status-badge-${status}`}
      style={{
        display: "inline-block",
        padding: small ? "2px 8px" : "4px 12px",
        borderRadius: 4,
        fontSize: small ? 10 : 12,
        fontWeight: 700,
        color,
        background: `${color}15`,
        border: `1px solid ${color}55`,
      }}
    >
      {label}
    </span>
  );
}


function MetricRow({ metric }) {
  const display = METRIC_DISPLAY_NAME[metric.name] || metric.name;
  return (
    <div
      data-testid={`metric-row-${metric.name}`}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 12px",
        borderBottom: "1px solid var(--c-border)",
        gap: 12,
      }}
    >
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontWeight: 600, color: "var(--c-text)", fontSize: 13 }}>
          {display}
        </div>
        <div style={{ color: "var(--c-text-3)", fontSize: 11, marginTop: 2 }}>
          {metric.message || "—"}
        </div>
      </div>
      <StatusBadge status={metric.status} small />
    </div>
  );
}


function MobileSummary({ snapshot }) {
  const groups = useMemo(() => {
    const findStatus = (names) => {
      const subset = (snapshot?.metrics || []).filter((m) => names.includes(m.name));
      if (subset.length === 0) return "UNKNOWN";
      const order = { CRITICAL: 4, WARN: 3, UNKNOWN: 2, OK: 1 };
      return subset.reduce((worst, m) =>
        (order[m.status] || 0) > (order[worst] || 0) ? m.status : worst,
      "OK");
    };
    return [
      { key: "system", label: "시스템 상태",
        status: findStatus(["server", "database", "api_error_rate"]) },
      { key: "data",   label: "데이터 상태",
        status: findStatus(["data_freshness"]) },
      { key: "trade",  label: "주문/리스크 경고",
        status: findStatus(["order_failure_rate", "approval_queue", "risk_events"]) },
    ];
  }, [snapshot]);

  return (
    <div
      data-testid="monitoring-mobile-summary"
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(3, 1fr)",
        gap: 6,
        marginBottom: 12,
      }}
    >
      {groups.map((g) => (
        <div
          key={g.key}
          data-testid={`mobile-summary-${g.key}`}
          style={{
            padding: "8px 6px",
            border: "1px solid var(--c-border)",
            borderRadius: 6,
            textAlign: "center",
            background: "var(--c-surface)",
          }}
        >
          <div style={{ fontSize: 10, color: "var(--c-text-3)", marginBottom: 4 }}>
            {g.label}
          </div>
          <StatusBadge status={g.status} small />
        </div>
      ))}
    </div>
  );
}


function AlertList({ alerts }) {
  if (!alerts || alerts.length === 0) {
    return (
      <div
        data-testid="monitoring-alerts-empty"
        style={{
          padding: 10, color: "var(--c-text-3)", fontSize: 12,
        }}
      >
        현재 알림 후보 없음 — 시스템 정상.
      </div>
    );
  }
  return (
    <div data-testid="monitoring-alerts-list">
      {alerts.map((a, idx) => (
        <div
          key={`${a.kind}-${idx}`}
          data-testid={`alert-row-${a.kind}`}
          style={{
            display: "flex", justifyContent: "space-between",
            alignItems: "center",
            padding: "8px 10px",
            borderTop: idx > 0 ? "1px solid var(--c-border)" : "none",
            gap: 10,
          }}
        >
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 12 }}>{a.title}</div>
            <div style={{ color: "var(--c-text-3)", fontSize: 11 }}>{a.message}</div>
          </div>
          <StatusBadge status={a.severity} small />
        </div>
      ))}
    </div>
  );
}


/**
 * MonitoringCard.
 *
 * Props:
 *   - snapshotOverride: 테스트용 — 정의 시 hook을 사용하지 않고 주입된 값 사용.
 *
 * 시스템이 응답하지 않을 때도 색상 회색 + "측정 불가" 안내로 fallback —
 * 모니터링 카드 자체가 에러로 흰 화면이 되면 안 된다.
 */
export function MonitoringCard({ snapshotOverride = null, marketPhase: marketPhaseProp = null }) {
  const monitoring = useMonitoring();
  const snapshot = snapshotOverride ?? monitoring.snapshot;
  const error    = snapshotOverride ? "" : monitoring.error;
  const loading  = snapshotOverride ? false : monitoring.loading;

  const overall  = snapshot?.overall || "UNKNOWN";
  const metrics  = snapshot?.metrics || [];
  const alerts   = snapshot?.alerts  || [];
  // fix/market-closed-state-distinction: 장 종료 / 휴장 시간엔 데이터가
  // *비어 있는 게 정상* — 에러 메시지를 friendly market-closed 안내로 대체.
  const marketPhase = marketPhaseProp || currentMarketPhase();
  const marketClosed = marketPhase !== MarketPhase.OPEN;

  return (
    <Card style={{ marginBottom: 12 }} accentColor={STATUS_COLOR[overall]}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>모니터링 (시스템 안정성)</SectionLabel>
        <StatusBadge status={overall} />
      </div>

      {error && marketClosed ? (
        <div data-testid="monitoring-error" style={{ marginBottom: 10 }}>
          <MarketClosedNotice
            phase={marketPhase}
            testId="monitoring-market-closed"
            detail="장 종료 / 휴장 시간에는 일부 메트릭이 비어 있을 수 있습니다. (backend 연결 자체는 별도 점검)"
          />
        </div>
      ) : error ? (
        <div
          data-testid="monitoring-error"
          style={{
            padding: 10, fontSize: 12,
            color: "var(--c-text-3)", background: "var(--c-surface-2)",
            borderRadius: 6, marginBottom: 10,
          }}
        >
          모니터링 데이터를 가져오지 못했습니다. 백엔드 상태를 확인하세요.
        </div>
      ) : null}

      {loading && !snapshot ? (
        <div data-testid="monitoring-loading" style={{
          padding: 16, color: "var(--c-text-3)", fontSize: 12, textAlign: "center",
        }}>
          모니터링 데이터 로딩 중…
        </div>
      ) : (
        <>
          <MobileSummary snapshot={snapshot} />

          <div data-testid="monitoring-metrics" style={{
            border: "1px solid var(--c-border)", borderRadius: 6,
            overflow: "hidden",
          }}>
            {metrics.map((m) => <MetricRow key={m.name} metric={m} />)}
          </div>

          <div style={{
            marginTop: 14, padding: 0,
            border: "1px solid var(--c-border)", borderRadius: 6,
          }}>
            <div style={{
              padding: "8px 10px",
              background: "var(--c-surface-2)",
              fontSize: 11, fontWeight: 700,
              color: "var(--c-text-2)",
            }}>
              알림 후보 (송신 X — 후보 표시만)
            </div>
            <AlertList alerts={alerts} />
          </div>

          <div style={{
            marginTop: 10, padding: "8px 10px",
            fontSize: 10, color: "var(--c-text-3)",
            background: "var(--c-surface-2)", borderRadius: 4,
          }}>
            * 수익률 모니터링이 아니라 *시스템 안정성* 모니터링입니다.
            장중 장애 조기 발견용이며, 알림은 NotificationService 설정에
            따라 별도로 결정됩니다.
          </div>
        </>
      )}
    </Card>
  );
}

export default MonitoringCard;
