import { useEffect, useState } from "react";

import { backendApi } from "../../services/backend/client";
import { fmtKRW, nowKstHm } from "../../utils/format";

// R3: 장중 런타임 설정 카드 — 동시진입 종목 수 / 종목당 투자금 2개 전용.
//   서버가 돌려준 실효값만 신뢰(낙관적 갱신 금지). 저장은 PUT /api/runtime-config.
const BUDGET_STEP = 100_000; // 10만 단위
const PCT_STEP = 0.5;        // C1: 손절/익절 0.5% 단위
const _round1 = (v) => Math.round(v * 10) / 10;   // 0.1% 정밀도 보존

export function RuntimeConfigCard({ config, onSaved, api = backendApi }) {
  const mcMeta = config?.max_concurrent_positions;
  const budMeta = config?.per_stock_budget;
  const slMeta = config?.stop_loss_pct;     // C1: 손절 %
  const tpMeta = config?.take_profit_pct;   // C1: 익절 %
  const dailyLimit = Number(config?.daily_buy_limit_krw ?? 3_000_000);
  const effMc = mcMeta?.value;
  const effBud = budMeta?.value;
  const effSl = slMeta?.value;
  const effTp = tpMeta?.value;

  const MC_MIN = mcMeta?.min ?? 1, MC_MAX = mcMeta?.max ?? 10;
  const BUD_MIN = budMeta?.min ?? 100_000, BUD_MAX = budMeta?.max ?? 10_000_000;
  const SL_MIN = slMeta?.min ?? 0.5, SL_MAX = slMeta?.max ?? 10;
  const TP_MIN = tpMeta?.min ?? 0.5, TP_MAX = tpMeta?.max ?? 20;

  const [mc, setMc] = useState(effMc ?? 5);
  const [bud, setBud] = useState(effBud ?? 1_000_000);
  const [sl, setSl] = useState(effSl ?? 2);
  const [tp, setTp] = useState(effTp ?? 3.5);
  const [note, setNote] = useState(null); // { kind: "ok"|"err", text }
  const [saving, setSaving] = useState(false);

  // 서버 실효값이 바뀌면(최초 로드/다른 경로 변경) 스테퍼 동기화.
  useEffect(() => { if (effMc != null) setMc(effMc); }, [effMc]);
  useEffect(() => { if (effBud != null) setBud(effBud); }, [effBud]);
  useEffect(() => { if (effSl != null) setSl(effSl); }, [effSl]);
  useEffect(() => { if (effTp != null) setTp(effTp); }, [effTp]);

  const clampMc = (v) => Math.max(MC_MIN, Math.min(MC_MAX, v));
  const clampBud = (v) => Math.max(BUD_MIN, Math.min(BUD_MAX, Math.round(v / BUDGET_STEP) * BUDGET_STEP));
  const clampSl = (v) => Math.max(SL_MIN, Math.min(SL_MAX, _round1(v)));
  const clampTp = (v) => Math.max(TP_MIN, Math.min(TP_MAX, _round1(v)));

  const stDirty = effSl != null && effTp != null && (sl !== effSl || tp !== effTp);
  const dirty = (effMc != null && effBud != null && (mc !== effMc || bud !== effBud)) || stDirty;
  // 충돌 경고(저장은 막지 않음): 종목당 금액 × 종목 수 > 일일 매수 한도.
  const conflict = bud * mc > dailyLimit;
  const affordable = Math.max(0, Math.floor(dailyLimit / Math.max(1, bud)));

  const save = async () => {
    setSaving(true);
    setNote(null);
    try {
      const payload = { max_concurrent_positions: mc, per_stock_budget: bud };
      if (slMeta && tpMeta) { payload.stop_loss_pct = sl; payload.take_profit_pct = tp; }
      const res = await api.runtimeConfigPut(payload);
      onSaved?.(res); // 서버 재확인 실효값만 신뢰.
      // 손절/익절이 바뀌면 *보유 종목에도* 적용(C1). 그 외(종목수·투자금)는 다음 매수부터.
      setNote({
        kind: "ok",
        text: stDirty
          ? `적용됐어요 (${nowKstHm()}) — 손절/익절은 보유 종목에도 적용돼요`
          : `적용됐어요 (${nowKstHm()}) — 다음 매수부터 새 설정으로 진행해요. 지금 보유 중인 종목은 그대로 둬요.`,
      });
    } catch {
      setNote({ kind: "err", text: `저장 실패(${nowKstHm()}) — 다시 시도해주세요` });
    } finally {
      setSaving(false);
    }
  };

  const stepBtn = {
    width: 40, height: 40, borderRadius: 10, border: "1px solid rgba(40,20,24,.25)",
    background: "rgba(255,255,255,.6)", color: "#2a1418", fontSize: 20, fontWeight: 800,
    cursor: "pointer", fontFamily: "inherit",
  };
  const valBox = { minWidth: 96, textAlign: "center", fontSize: 18, fontWeight: 800, color: "#2a1418" };
  const rowStyle = { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginTop: 8 };
  const _card = { background: "rgba(255,255,255,.5)", borderRadius: 12, padding: "12px 14px", marginTop: 12 };

  // F2: 서버값을 *모르는* 상태에선 임의값/기본값을 그리지 않는다(R3 — 서버값만 신뢰).
  //   null=로딩, _failed=조회 실패. 둘 다 [저장] 비활성(서버값 모른 채 저장 발사 금지).
  if (!config) {
    return (
      <div data-testid="rtcfg-card" style={_card}>
        <div style={{ fontSize: 13, fontWeight: 800, color: "#2a1418", marginBottom: 2 }}>설정 바꾸기 (장중 가능)</div>
        <div data-testid="rtcfg-loading" style={{ fontSize: 13, color: "rgba(40,20,24,.55)", marginTop: 6 }}>불러오는 중…</div>
      </div>
    );
  }
  if (config._failed || !mcMeta || !budMeta) {
    return (
      <div data-testid="rtcfg-card" style={_card}>
        <div style={{ fontSize: 13, fontWeight: 800, color: "#2a1418", marginBottom: 2 }}>설정 바꾸기 (장중 가능)</div>
        <div data-testid="rtcfg-fail" style={{ fontSize: 13, color: "rgba(40,20,24,.6)", marginTop: 6 }}>
          설정을 불러오지 못했어요({nowKstHm()})
        </div>
      </div>
    );
  }

  return (
    <div data-testid="rtcfg-card" style={_card}>
      <div style={{ fontSize: 13, fontWeight: 800, color: "#2a1418", marginBottom: 2 }}>
        설정 바꾸기 (장중 가능)
      </div>

      {/* 동시진입 종목 수 */}
      <div style={rowStyle}>
        <span style={{ fontSize: 14, color: "#2a1418", fontWeight: 700 }}>동시진입 종목</span>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <button type="button" data-testid="rtcfg-mc-dec" style={stepBtn}
            onClick={() => setMc((v) => clampMc(v - 1))} disabled={mc <= MC_MIN}>−</button>
          <span data-testid="rtcfg-mc-value" style={valBox}>{mc}개</span>
          <button type="button" data-testid="rtcfg-mc-inc" style={stepBtn}
            onClick={() => setMc((v) => clampMc(v + 1))} disabled={mc >= MC_MAX}>+</button>
        </div>
      </div>

      {/* 종목당 투자금 */}
      <div style={rowStyle}>
        <span style={{ fontSize: 14, color: "#2a1418", fontWeight: 700 }}>종목당 투자금</span>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <button type="button" data-testid="rtcfg-bud-dec" style={stepBtn}
            onClick={() => setBud((v) => clampBud(v - BUDGET_STEP))} disabled={bud <= BUD_MIN}>−</button>
          <span data-testid="rtcfg-bud-value" style={valBox}>{fmtKRW(bud)}원</span>
          <button type="button" data-testid="rtcfg-bud-inc" style={stepBtn}
            onClick={() => setBud((v) => clampBud(v + BUDGET_STEP))} disabled={bud >= BUD_MAX}>+</button>
        </div>
      </div>

      {/* C1: 손절 % (보유 종목에도 적용) */}
      {slMeta && (
        <div style={rowStyle}>
          <span style={{ fontSize: 14, color: "#2a1418", fontWeight: 700 }}>손절</span>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <button type="button" data-testid="rtcfg-sl-dec" style={stepBtn}
              onClick={() => setSl((v) => clampSl(v - PCT_STEP))} disabled={sl <= SL_MIN}>−</button>
            <span data-testid="rtcfg-sl-value" style={valBox}>-{_round1(sl)}%</span>
            <button type="button" data-testid="rtcfg-sl-inc" style={stepBtn}
              onClick={() => setSl((v) => clampSl(v + PCT_STEP))} disabled={sl >= SL_MAX}>+</button>
          </div>
        </div>
      )}

      {/* C1: 익절 % */}
      {tpMeta && (
        <div style={rowStyle}>
          <span style={{ fontSize: 14, color: "#2a1418", fontWeight: 700 }}>익절</span>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <button type="button" data-testid="rtcfg-tp-dec" style={stepBtn}
              onClick={() => setTp((v) => clampTp(v - PCT_STEP))} disabled={tp <= TP_MIN}>−</button>
            <span data-testid="rtcfg-tp-value" style={valBox}>+{_round1(tp)}%</span>
            <button type="button" data-testid="rtcfg-tp-inc" style={stepBtn}
              onClick={() => setTp((v) => clampTp(v + PCT_STEP))} disabled={tp >= TP_MAX}>+</button>
          </div>
        </div>
      )}

      {/* 충돌 경고 (저장은 막지 않음) */}
      {conflict && (
        <div data-testid="rtcfg-conflict" style={{
          marginTop: 8, fontSize: 12.5, fontWeight: 700, lineHeight: 1.5,
          borderRadius: 8, padding: "8px 10px", background: "rgba(245,170,30,.18)", color: "#7a4a00",
        }}>
          이 설정이면 하루에 약 {affordable}종목까지만 새로 살 수 있어요 (일일 매수 한도 {fmtKRW(dailyLimit)}원)
        </div>
      )}

      <button type="button" data-testid="rtcfg-save" onClick={save} disabled={!dirty || saving}
        style={{
          width: "100%", marginTop: 10, padding: "11px 0", borderRadius: 10, border: "none",
          background: (!dirty || saving) ? "rgba(40,20,24,.25)" : "#2a1418", color: "#fff",
          fontSize: 15, fontWeight: 800, cursor: (!dirty || saving) ? "default" : "pointer",
          fontFamily: "inherit",
        }}>
        {saving ? "저장 중…" : "저장"}
      </button>

      {note && (
        <div data-testid="rtcfg-note" style={{
          marginTop: 8, fontSize: 13, fontWeight: 700, lineHeight: 1.5, borderRadius: 8, padding: "8px 10px",
          background: note.kind === "ok" ? "rgba(21,160,95,.18)" : "rgba(192,57,43,.16)",
          color: note.kind === "ok" ? "#0f5132" : "#7a1d1d",
        }}>
          {note.text}
        </div>
      )}
    </div>
  );
}
