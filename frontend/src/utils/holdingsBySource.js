// 설계 B 조각 1 — 보유를 봇/직접 2섹션으로 분리 + 섹션별 수익률(순수 함수, 부작용 0).
//
// positions/live 응답의 각 항목에 backend 가 붙인 source(BOT/MANUAL/MIXED/UNTAGGED)와
// bot_qty/manual_qty 를 사용. ★조각 1: 표시 전용 — 봇 격리(MANUAL 차감)는 미작동
// (응답 bot_isolation_active=false). 그 경고는 호출자가 별도 배너로 표시.

// 한 종목을 봇/직접 lot 으로 쪼갠다(MIXED 는 양쪽에 분할 표시).
function _splitOne(p) {
  const qty = Number(p.quantity || 0);
  const bot = Math.max(0, Number(p.bot_qty ?? (p.source === "BOT" ? qty : 0)));
  const man = Math.max(0, Number(p.manual_qty ?? (qty - bot)));
  const avg = Number(p.avg_price || 0);
  const mkt = Number(p.market_price || 0);
  const pnlOf = (q) => (avg && mkt ? (mkt - avg) * q : null);
  const rows = [];
  if (bot > 0) rows.push({ ...p, _section: "BOT", _qty: bot, _eval_pnl: pnlOf(bot) });
  if (man > 0) rows.push({ ...p, _section: "MANUAL", _qty: man, _eval_pnl: pnlOf(man) });
  // 둘 다 0(데이터 부족)이면 보수적으로 직접 보유 측에.
  if (rows.length === 0) rows.push({ ...p, _section: "MANUAL", _qty: qty, _eval_pnl: pnlOf(qty) });
  return rows;
}

function _sectionStats(rows) {
  let cost = 0, value = 0, pnl = 0;
  for (const r of rows) {
    const q = Number(r._qty || 0);
    const avg = Number(r.avg_price || 0);
    const mkt = Number(r.market_price || 0);
    if (avg && mkt) { cost += avg * q; value += mkt * q; pnl += (mkt - avg) * q; }
  }
  const retPct = cost > 0 ? Math.round((pnl / cost) * 1000) / 10 : null;
  return { rows, count: rows.length, eval_pnl_krw: pnl, return_pct: retPct };
}

/**
 * positions(라이브 보유) → { bot, manual } 두 섹션(각 rows + 합계 수익률).
 * @param {Array} positions positions/live 의 positions 배열
 * @returns {{bot, manual}}
 */
export function splitHoldingsBySource(positions) {
  const bot = [], manual = [];
  for (const p of (positions || [])) {
    for (const r of _splitOne(p)) {
      (r._section === "BOT" ? bot : manual).push(r);
    }
  }
  return { bot: _sectionStats(bot), manual: _sectionStats(manual) };
}

export const SOURCE_BADGE = Object.freeze({
  BOT:      { label: "봇 운용", icon: "🤖", color: "#22c55e" },
  MANUAL:   { label: "직접 보유", icon: "👤", color: "#3b82f6" },
  MIXED:    { label: "봇+직접", icon: "🤖👤", color: "#a855f7" },
  UNTAGGED: { label: "출처 미상", icon: "❓", color: "#94a3b8" },
});
