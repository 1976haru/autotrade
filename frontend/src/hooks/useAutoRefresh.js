/**
 * useAutoRefresh — 카드 자동 새로고침 공용 훅 (KIS-PAPER-FULL-LIFECYCLE E).
 *
 * 기능:
 *  - setInterval 폴링 (`intervalMs`, default 5초)
 *  - 탭이 *비활성* (document.hidden === true) 시 폴링 일시정지 (`pauseOnHidden`).
 *    → 백엔드 부하 감소. 탭이 다시 활성화되면 즉시 1회 refresh 후 정상 재개.
 *  - 운영자 토글: ON/OFF 상태 (`isAutoOn`/`setIsAutoOn`), default ON.
 *    OFF 시 자동 폴링 정지, 수동 새로고침 (`manualRefresh`) 만 가능.
 *  - 마지막 갱신 시각 (`lastUpdatedAt`) 노출 — UI 가 "N초 전" 표시 가능.
 *  - `isRefreshing` 플래그 — 로딩 인디케이터용.
 *  - 백엔드 호출 실패 시 *예외를 흡수* — 마지막 데이터 유지, lastUpdatedAt 갱신 안 함.
 *    `lastError` 로 에러 메시지 노출 (UI 가 표시 선택).
 *
 * 안전:
 *  - 컴포넌트 언마운트 시 setInterval / addEventListener 정리 (memory leak 방지).
 *  - 최소 인터벌 1000ms 강제 (서버 부하 방지, 1초 미만은 1000ms 로 clamp).
 *  - refresh 가 async 든 sync 든 모두 처리.
 *  - test 친화적: `document` 미정의 환경(node SSR 등) 에서도 안전.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const _MIN_INTERVAL_MS = 1_000;   // 1초 미만 금지 (서버 부하).

export function useAutoRefresh(refresh, options = {}) {
  const {
    intervalMs = 5_000,
    pauseOnHidden = true,
    enabledByDefault = true,
  } = options;

  const interval = Math.max(_MIN_INTERVAL_MS, Number(intervalMs) || _MIN_INTERVAL_MS);

  const [isAutoOn, setIsAutoOn] = useState(enabledByDefault !== false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastError, setLastError] = useState(null);
  const [isVisible, setIsVisible] = useState(() => {
    try {
      return typeof document === "undefined" || document.visibilityState !== "hidden";
    } catch { return true; }
  });

  // refresh 콜백을 ref 에 캡쳐 — interval 콜백이 항상 최신 refresh 를 호출.
  const refreshRef = useRef(refresh);
  useEffect(() => { refreshRef.current = refresh; }, [refresh]);

  const doRefresh = useCallback(async () => {
    if (typeof refreshRef.current !== "function") return;
    setIsRefreshing(true);
    try {
      await refreshRef.current();
      setLastUpdatedAt(new Date());
      setLastError(null);
    } catch (err) {
      setLastError(err?.message || String(err));
    } finally {
      setIsRefreshing(false);
    }
  }, []);

  // visibility 추적.
  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const onVis = () => {
      const v = document.visibilityState !== "hidden";
      setIsVisible(v);
    };
    try {
      document.addEventListener("visibilitychange", onVis);
      return () => document.removeEventListener("visibilitychange", onVis);
    } catch {
      return undefined;
    }
  }, []);

  // 폴링 인터벌 — isAutoOn AND (visibility 활성 OR pauseOnHidden=false).
  useEffect(() => {
    if (!isAutoOn) return undefined;
    if (pauseOnHidden && !isVisible) return undefined;
    const t = setInterval(() => { void doRefresh(); }, interval);
    return () => clearInterval(t);
  }, [isAutoOn, isVisible, pauseOnHidden, interval, doRefresh]);

  // 탭이 다시 활성화되면 즉시 1회 refresh (지연 0 — pause 동안의 stale 데이터 빨리 갱신).
  const wasVisibleRef = useRef(isVisible);
  useEffect(() => {
    if (!isAutoOn) { wasVisibleRef.current = isVisible; return; }
    if (!wasVisibleRef.current && isVisible) { void doRefresh(); }
    wasVisibleRef.current = isVisible;
  }, [isAutoOn, isVisible, doRefresh]);

  return {
    isAutoOn,
    setIsAutoOn,
    lastUpdatedAt,
    isRefreshing,
    lastError,
    manualRefresh: doRefresh,
    isVisible,
    intervalMs: interval,
  };
}

/** "N초 전" / "방금 전" — last-updated 시각 표시 helper. */
export function formatTimeAgo(date, now = new Date()) {
  if (!date) return "—";
  const d = (date instanceof Date) ? date : new Date(date);
  const sec = Math.max(0, Math.floor((now.getTime() - d.getTime()) / 1000));
  if (sec < 3) return "방금 전";
  if (sec < 60) return `${sec}초 전`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}분 전`;
  return d.toLocaleTimeString();
}

export default useAutoRefresh;
