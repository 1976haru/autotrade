/**
 * Agent Strategy Choice Card (UI redesign — PHASE 5).
 *
 * "AI Agent가 4가지 핵심 전략 중 어떤 것을 골랐는가"를 한눈에 보여주는 카드.
 * 4개 전략을 동등 chip으로 나열하고 현재 선택된 전략을 강조 — 선택 이유와
 * 제외 이유를 사람이 읽을 수 있는 짧은 문구로 표시.
 *
 * 데이터 소스 (read-only):
 * - `backendApi.engineStatus()` — 현재 실행 중인 strategy + regime
 * - `backendApi.engineRegistry()` — 등록된 strategy 메타 (entry/exit 등)
 *
 * 절대 원칙: 본 카드는 *상태 표시 전용* — 전략 선택 / 변경 / 주문 발생 어떤
 * 부수효과도 만들지 않는다.
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "./index";
import { ErrorState, LoadingState, StatusBadge } from "./primitives";
import { MarketClosedNotice } from "./MarketClosedNotice";
import { backendApi } from "../../services/backend/client";
import { MarketPhase, currentMarketPhase } from "../../utils/marketHours";


// 체크리스트 상의 4개 핵심 전략. registry 키와 매핑 + 사용자 친화 라벨.
const FEATURED_STRATEGIES = [
  {
    key: "volume_breakout",
    label: "Volume Breakout",
    desc: "거래대금 급증 + 고점 돌파 1차 momentum",
  },
  {
    key: "pullback_rebreak",
    label: "Pullback Rebreak",
    desc: "1차 상승 후 거래량 fade 눌림 + 재돌파",
  },
  {
    key: "vwap_strategy",
    label: "VWAP Reclaim",
    desc: "VWAP 회복/이탈 보조 전략",
  },
  {
    key: "orb_vwap",
    label: "ORB + VWAP",
    desc: "장 초반 Opening Range + VWAP 결합",
  },
];


function friendlyError(err) {
  if (!err) return "알 수 없는 오류가 발생했습니다.";
  const msg = String(err.message || "").toLowerCase();
  if (msg.includes("failed to fetch") || msg.includes("networkerror")) {
    return "백엔드 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
  }
  return "전략 상태를 불러오는 중 오류가 발생했습니다.";
}


function StrategyChip({ entry, isSelected, isCandidate, registryEntry }) {
  // isSelected: 현재 운용 중. isCandidate: registry 등록되어 있어 선택 가능.
  const palette = isSelected
    ? { color: "var(--c-success)", bg: "rgba(34, 197, 94, 0.10)", border: "var(--c-success)" }
    : isCandidate
    ? { color: "var(--c-text-2)", bg: "var(--c-surface-2, #f8fafc)", border: "var(--c-border)" }
    : { color: "var(--c-text-3)", bg: "transparent", border: "var(--c-border)" };

  return (
    <div
      data-testid={`agent-strategy-chip-${entry.key}`}
      data-selected={isSelected ? "true" : "false"}
      data-candidate={isCandidate ? "true" : "false"}
      style={{
        padding: "10px 12px",
        borderRadius: 6,
        border: `1px solid ${palette.border}`,
        background: palette.bg,
        display: "flex",
        flexDirection: "column",
        gap: 3,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <span style={{
          fontSize: "var(--fs-sm)", fontWeight: 700, color: palette.color,
        }}>
          {entry.label}
        </span>
        {isSelected && (
          <span style={{
            fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 3,
            background: "var(--c-success)", color: "#fff",
          }}>
            ▶ 선택됨
          </span>
        )}
        {!isSelected && !isCandidate && (
          <span style={{ fontSize: 9, color: "var(--c-text-3)" }}>
            (등록 안됨)
          </span>
        )}
      </div>
      <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.4 }}>
        {entry.desc}
      </div>
      {registryEntry?.required_regime && (
        <div style={{ fontSize: 10, color: "var(--c-text-3)" }}>
          적합 시장: <b>{registryEntry.required_regime}</b>
        </div>
      )}
    </div>
  );
}


export function AgentStrategyChoiceCard({
  testId = "agent-strategy-choice-card",
  autoLoad = true,
  marketPhase: marketPhaseProp = null,
}) {
  const [state, setState] = useState({ requestKey: 0, error: null, status: null, registry: null });
  const [tick, setTick] = useState(0);
  // 테스트에서 marketPhaseProp 으로 주입 가능 — 미주입 시 client-side 계산.
  const marketPhase = marketPhaseProp || currentMarketPhase();
  const marketClosed = marketPhase !== MarketPhase.OPEN;

  useEffect(() => {
    if (!autoLoad) return;
    let cancelled = false;
    Promise.all([
      backendApi.engineStatus().catch(() => null),
      backendApi.engineRegistry().catch(() => null),
    ])
      .then(([status, registry]) => {
        if (cancelled) return;
        if (!status && !registry) {
          setState({
            requestKey: tick,
            error: new Error("Failed to fetch"),
            status: null, registry: null,
          });
          return;
        }
        setState({ requestKey: tick, error: null, status, registry });
      })
      .catch((err) => {
        if (!cancelled) setState({ requestKey: tick, error: err, status: null, registry: null });
      });
    return () => { cancelled = true; };
  }, [tick, autoLoad]);

  const isReady = state.requestKey === tick && (state.status || state.error);

  if (!isReady) {
    return (
      <Card>
        <LoadingState testId={`${testId}-loading`} title="AI 전략 상태 확인 중..." />
      </Card>
    );
  }

  if (state.error) {
    // fix/market-closed-state-distinction: 장 종료 / 휴장 시 fetch 가 실패
    // 했더라도 *오류*로 표시하지 않고 friendly market-closed banner 만
    // 노출. 정규장이 열려 있을 때만 ErrorState 를 노출해 실제 장애와
    // 휴장 상태를 구분.
    if (marketClosed) {
      return (
        <Card>
          <div data-testid={testId} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <SectionLabel>AI Agent 선택 전략</SectionLabel>
            <MarketClosedNotice
              phase={marketPhase}
              testId={`${testId}-market-closed`}
              detail="장 종료 / 휴장 시간에는 신규 전략 신호가 생성되지 않습니다. (backend 연결 자체는 별도 점검)"
              onRefresh={() => setTick((t) => t + 1)}
            />
          </div>
        </Card>
      );
    }
    return (
      <Card>
        <ErrorState
          testId={`${testId}-error`}
          title="AI 전략 선택 조회 실패"
          hint={friendlyError(state.error)}
          retryLabel="다시 시도"
          onRetry={() => setTick((t) => t + 1)}
        />
      </Card>
    );
  }

  // engineStatus shape can vary — defensively pull strategy name.
  const status = state.status || {};
  const registry = Array.isArray(state.registry) ? state.registry : [];
  const registryByKey = registry.reduce((acc, r) => {
    if (r?.name) acc[r.name] = r;
    return acc;
  }, {});

  // selected 전략 추정: status.strategies (LiveEngine 활성 list) 또는 단일
  // strategy 필드. shape이 다양하므로 여러 키를 시도.
  let selectedKeys = [];
  if (Array.isArray(status.strategies)) {
    selectedKeys = status.strategies
      .map((s) => (typeof s === "string" ? s : s?.name))
      .filter(Boolean);
  } else if (typeof status.strategy === "string") {
    selectedKeys = [status.strategy];
  } else if (typeof status.active_strategy === "string") {
    selectedKeys = [status.active_strategy];
  }

  const regime = status.regime || status.market_regime || null;

  return (
    <Card>
      <div data-testid={testId} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "baseline",
          gap: 8, flexWrap: "wrap",
        }}>
          <SectionLabel>AI Agent 선택 전략</SectionLabel>
          {selectedKeys.length > 0 ? (
            <StatusBadge status="success" testId={`${testId}-active-badge`}>
              {selectedKeys.length}개 활성
            </StatusBadge>
          ) : (
            <StatusBadge status="neutral" testId={`${testId}-active-badge`}>
              대기 중
            </StatusBadge>
          )}
        </div>

        {regime && (
          <div data-testid={`${testId}-regime`} style={{
            fontSize: "var(--fs-sm)", color: "var(--c-text-2)",
          }}>
            <span style={{ color: "var(--c-text-3)" }}>현재 시장 판단:</span>{" "}
            <b>{regime}</b>
          </div>
        )}

        <div
          data-testid={`${testId}-grid`}
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 8,
          }}
        >
          {FEATURED_STRATEGIES.map((entry) => {
            const reg = registryByKey[entry.key];
            const isSelected = selectedKeys.includes(entry.key);
            const isCandidate = !!reg;
            return (
              <StrategyChip
                key={entry.key}
                entry={entry}
                isSelected={isSelected}
                isCandidate={isCandidate}
                registryEntry={reg}
              />
            );
          })}
        </div>

        <div
          data-testid={`${testId}-rationale`}
          style={{
            padding: 8, borderRadius: 4,
            background: "var(--c-surface-2, #f8fafc)",
            border: "1px solid var(--c-border)",
            fontSize: "var(--fs-sm)", color: "var(--c-text-2)", lineHeight: 1.5,
          }}
        >
          {selectedKeys.length === 0 ? (
            <>
              <b>선택된 전략이 아직 없습니다.</b><br />
              AI Agent가 시장 데이터 수집 / 시장 국면 판단 후 적합한 전략을 자동
              선택합니다. 시장 시작 직후 또는 데이터 부족 상태에서는 잠시 대기할
              수 있습니다.
            </>
          ) : (
            <>
              <b>선택 이유:</b>{" "}
              현재 시장 국면({regime || "분석 중"})에서 가장 적합하다고 판단된
              전략은 <b>{selectedKeys.join(", ")}</b> 입니다. 각 전략의 entry/exit
              조건과 시장 적합도는 전략 탭에서 확인할 수 있습니다.
            </>
          )}
        </div>

        <div style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.4,
        }}>
          🔒 본 카드는 *상태 표시*만 합니다. 전략 활성화 / 변경은 전략 탭에서
          운영자가 명시 조작합니다.
        </div>

        <button
          type="button"
          data-testid={`${testId}-refresh`}
          onClick={() => setTick((t) => t + 1)}
          style={{
            alignSelf: "flex-end", fontSize: 11, fontWeight: 700,
            padding: "3px 10px", borderRadius: 3, cursor: "pointer",
            border: "1px solid var(--c-border)", background: "transparent",
            color: "var(--c-text-3)", fontFamily: "inherit",
          }}>
          새로고침
        </button>
      </div>
    </Card>
  );
}


export default AgentStrategyChoiceCard;
