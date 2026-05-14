/**
 * Strategy display name helper (#82).
 *
 * `sma_crossover` 같은 internal id 를 *displayName + (internal_id)* 형태로 변환.
 * 절대 internal id 를 완전히 가리지 않는다 — 로그 / audit / 운영자 매핑 유지.
 *
 * 사용:
 *   const lookup = await fetchStrategyDisplayLookup();
 *   formatStrategyName("sma_crossover", lookup);
 *   // → "단기/장기 이동평균 교차 (sma_crossover)"
 *
 * 캐시:
 *   `useStrategyDisplayNames()` hook 은 module-level 캐시를 공유 — 한 번 fetch
 *   후 같은 데이터 재사용 (BACKEND_BEGINNER_REGISTRY_CACHE).
 *
 * 안전:
 *   - lookup 가 없거나 entry 가 누락이면 *internal id 그대로 반환* (graceful
 *     degradation).
 *   - 본 helper 는 API 응답을 변형하지 않는다 — 표시 용도 only.
 */

import { useEffect, useState } from "react";
import { backendApi } from "../services/backend/client";


// ---------- module-level cache ----------


let _CACHE = null;          // { strategy_id: {display_name, beginner_name, ...} }
let _IN_FLIGHT = null;      // 동시 요청 중복 방지


/**
 * Backend `/api/strategies/beginner-registry` 응답을 받아 lookup dict 로 변환.
 * 캐시되어 두 번째 호출부터는 같은 Promise 반환.
 */
export async function fetchStrategyDisplayLookup() {
  if (_CACHE) return _CACHE;
  if (_IN_FLIGHT) return _IN_FLIGHT;
  _IN_FLIGHT = backendApi.engineBeginnerRegistry()
    .then((entries) => {
      const map = {};
      for (const e of entries || []) {
        if (e && e.strategy_id) {
          map[e.strategy_id] = e;
        }
      }
      _CACHE = map;
      _IN_FLIGHT = null;
      return _CACHE;
    })
    .catch((err) => {
      _IN_FLIGHT = null;
      throw err;
    });
  return _IN_FLIGHT;
}


/** 테스트용 reset — 운영 코드에서는 호출하지 않는다. */
export function _resetStrategyDisplayLookupForTests() {
  _CACHE = null;
  _IN_FLIGHT = null;
}


/** 미리 알려진 fallback (네트워크 실패 / 캐시 부재 시 internal id 그대로 노출). */
const _NULL_LOOKUP = {};


/**
 * displayName + (internal_id) 포맷 변환.
 *
 * @param {string|null|undefined} strategyId — internal id (`sma_crossover` 등)
 * @param {Object<string, Object>|null} lookup — fetchStrategyDisplayLookup 결과
 * @param {Object} [opts]
 * @param {boolean} [opts.compact=false] — true 면 "displayName" 만 (괄호 없음).
 *                                          기본 false — internal id 항상 함께.
 * @returns {string}
 */
export function formatStrategyName(strategyId, lookup, opts = {}) {
  if (strategyId == null || strategyId === "") return "—";
  const safeId = String(strategyId);
  const dict = lookup || _NULL_LOOKUP;
  const entry = dict[safeId];
  if (!entry || !entry.display_name) {
    // graceful — internal id 그대로 (운영자가 항상 매핑 가능).
    return safeId;
  }
  if (opts.compact) {
    return entry.display_name;
  }
  return `${entry.display_name} (${safeId})`;
}


/**
 * 짧은 표시 — displayName 만, internal id 는 별도 element 로 표시할 때.
 */
export function strategyDisplayShort(strategyId, lookup) {
  return formatStrategyName(strategyId, lookup, { compact: true });
}


/**
 * React hook — 컴포넌트가 displayName lookup 을 안전하게 사용할 수 있도록.
 *
 * 다수의 row 가 동시에 hook 을 사용해도 module-level 캐시 + in-flight Promise
 * dedup 으로 backend 호출은 1회. 컴포넌트 당 state 도 lookup 1개만 — fetch
 * 실패는 graceful fallback(internal id) 으로 처리, error/loading 상태는 노출
 * 하지 않아 대량 row stress 환경에서 re-render 폭발을 피한다.
 *
 * @returns {{ lookup: Object, loading: boolean, error: string }}
 */
export function useStrategyDisplayNames() {
  const [lookup, setLookup] = useState(_CACHE);

  useEffect(() => {
    if (_CACHE) {
      if (lookup !== _CACHE) setLookup(_CACHE);
      return undefined;
    }
    let cancelled = false;
    fetchStrategyDisplayLookup()
      .then((data) => { if (!cancelled) setLookup(data); })
      .catch(() => { /* graceful — internal id 그대로 노출 */ });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { lookup, loading: lookup == null, error: "" };
}
