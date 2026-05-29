/**
 * AutoRefreshFooter — 카드 하단 자동새로고침 UX (KIS-PAPER-FULL-LIFECYCLE E).
 *
 *  [✓ 자동 새로고침] [수동 새로고침] · "N초 전 업데이트" · (로딩…) · (에러: ...)
 *
 * 운영자가 한눈에:
 *  - 마지막 갱신 시각 ("N초 전")
 *  - 현재 자동 ON/OFF 상태 (체크박스)
 *  - 백엔드 오류 (간략 노출, 시크릿 없음)
 *  - 수동 새로고침 가능
 *
 * 안전 invariant (테스트로 lock):
 *  - 매수/매도/실전/Place Order/ENABLE_* 라벨 버튼 0개.
 *  - input 은 *체크박스 1개* (자동 새로고침 토글)만.
 */

import { useEffect, useState } from "react";

import { formatTimeAgo } from "../../hooks/useAutoRefresh";

export function AutoRefreshFooter({
  isAutoOn,
  setIsAutoOn,
  lastUpdatedAt,
  isRefreshing,
  lastError,
  manualRefresh,
  intervalMs,
  testId = "auto-refresh-footer",
}) {
  // 1초마다 "N초 전" 표시 갱신 (실시계).
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 1_000);
    return () => clearInterval(t);
  }, []);

  return (
    <div data-testid={testId} style={{
      display: "flex", alignItems: "center", gap: 8, marginTop: 6,
      fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
      paddingTop: 6, borderTop: "1px dashed var(--c-border)",
    }}>
      <label style={{ display: "inline-flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={!!isAutoOn}
          onChange={(e) => setIsAutoOn?.(!!e.target.checked)}
          data-testid={`${testId}-toggle`}
          aria-label="자동 새로고침"
        />
        <span>자동 새로고침 {isAutoOn ? "ON" : "OFF"}
          {isAutoOn && intervalMs ? ` (${Math.round(intervalMs / 1000)}s)` : ""}
        </span>
      </label>
      <span data-testid={`${testId}-last`} style={{ marginLeft: 4 }}>
        · {formatTimeAgo(lastUpdatedAt)}
      </span>
      {isRefreshing ? (
        <span data-testid={`${testId}-loading`} aria-live="polite">⏳</span>
      ) : null}
      {!isAutoOn ? (
        <button type="button" data-testid={`${testId}-manual`}
                onClick={() => { void manualRefresh?.(); }}
                style={{ fontSize: "var(--fs-xs)", padding: "1px 8px", marginLeft: 4,
                         borderRadius: 4, border: "1px solid var(--c-border)",
                         background: "var(--c-bg-2)", cursor: "pointer" }}>
          수동 새로고침
        </button>
      ) : null}
      {lastError ? (
        <span data-testid={`${testId}-error`}
              style={{ marginLeft: "auto", color: "#7f1d1d" }}>
          ⚠ {String(lastError).slice(0, 80)}
        </span>
      ) : null}
    </div>
  );
}

export default AutoRefreshFooter;
