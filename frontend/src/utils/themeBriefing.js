// 테마 브리핑 2단계 — 전일 미국 테마 ETF/지수 등락률 표시용 순수 함수.
// ★정보 표시 전용: 추천/판단 문구를 만들지 않는다. 숫자·화살표·기준일만 조합한다.

export function themeBriefingById(resp) {
  const byId = {};
  (resp?.themes || []).forEach((t) => { byId[t.theme_id] = t; });
  return {
    sessionDateUs: resp?.session_date_us || null, byId,
    // ★전체 fetch 실패 시 백엔드가 이전 성공 데이터를 stale=true로 그대로 돌려준다
    //   (값을 지우지 않음) — 화면에 "갱신 실패, 이전 기준" 안내만 얹는다.
    stale: !!resp?.stale, staleReason: resp?.stale_reason || null,
  };
}

// briefing 전체(개별 theme 아님)에 대한 갱신 상태 안내. stale=false면 빈 문자열.
export function themeBriefingStaleNote(briefing) {
  if (!briefing?.stale) return "";
  return "갱신 실패 · 이전 기준 표시 중";
}

const arrow = (pct) => (pct > 0 ? "↑" : pct < 0 ? "↓" : "→");
const signed = (pct) => `${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`;
const shortDate = (iso) => (iso ? iso.slice(5) : "");

// entry: { mapping_quality, proxies: [{ticker, change_pct, status}], status }
export function formatThemeBriefingLine(entry, sessionDateUs) {
  if (!entry || entry.mapping_quality === "NONE") return "—";
  const ok = (entry.proxies || []).filter((p) => p.status === "OK" && p.change_pct != null);
  if (ok.length === 0) return "데이터 없음";
  const parts = ok.map((p) => `${p.ticker} ${signed(p.change_pct)} ${arrow(p.change_pct)}`);
  const asOf = sessionDateUs ? ` (${shortDate(sessionDateUs)} 기준)` : "";
  return `전일 ${parts.join(" · ")}${asOf}`;
}
