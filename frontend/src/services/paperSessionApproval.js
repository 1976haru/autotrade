function kstDate(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now).reduce((out, part) => {
    if (part.type !== "literal") out[part.type] = part.value;
    return out;
  }, {});
  return `${parts.year}-${parts.month}-${parts.day}`;
}

/** Desktop-only, explicit KIS PAPER order authorization for the current KST day. */
export async function ensurePaperSessionApproval({ api, capitalSettings, confirmFn = window.confirm }) {
  const invoke = window?.__TAURI_INTERNALS__?.invoke;
  if (typeof invoke !== "function") {
    return { ok: false, reason: "DESKTOP_SESSION_AUTH_REQUIRED" };
  }

  const targetDate = kstDate();
  const current = typeof api.paperSessionApprovalGet === "function"
    ? await api.paperSessionApprovalGet()
    : null;
  const reusable = current?.target_date === targetDate
    && ["SCHEDULED", "ACTIVE"].includes(current?.status);
  if (reusable) return { ok: true, reused: true, targetDate };

  const perSymbol = Number(capitalSettings?.perSymbolAllocation || capitalSettings?.per_symbol_krw || 0);
  const maxPositions = Number(capitalSettings?.maxPositions || capitalSettings?.max_positions || 0);
  const daily = Number(capitalSettings?.maxDailyBuyAmount || capitalSettings?.daily_buy_limit_krw || 0);
  const approved = confirmFn(
    `KIS 모의계좌 자동매매 승인 (${targetDate})\n\n`
    + "오늘 장중 실제 KIS PAPER 매수·매도 주문이 자동 전송될 수 있습니다.\n"
    + `종목당 목표 ${perSymbol.toLocaleString()}원, 최대 ${maxPositions}종목, `
    + `일일 최대 ${daily.toLocaleString()}원\n\n`
    + "LIVE 계좌 주문은 허용되지 않습니다. 승인하시겠습니까?",
  );
  if (!approved) return { ok: false, cancelled: true, reason: "PAPER_APPROVAL_CANCELLED" };

  await invoke("approve_paper_session", { targetDate, explicitAck: true });
  return { ok: true, reused: false, targetDate };
}

export { kstDate };
