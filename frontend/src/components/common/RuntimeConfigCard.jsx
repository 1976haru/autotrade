import { useEffect, useState } from "react";

import { backendApi } from "../../services/backend/client";
import { fmtKRW, nowKstHm } from "../../utils/format";

const BUDGET_STEP = 100_000; // 10만 단위
const PCT_STEP = 0.5;        // C1: 손절/익절 0.5% 단위
const DAILY_STEP = 1_000_000; // T2: 일일 매수 한도 100만 단위
const BUDGET_PRESETS = [10_000_000, 20_000_000, 30_000_000, 50_000_000];
const _round1 = (v) => Math.round(v * 10) / 10;   // 0.1% 정밀도 보존

export function RuntimeConfigCard({ config, onSaved, api = backendApi }) {
  const mcMeta = config?.max_concurrent_positions;
  const budMeta = config?.per_stock_budget;
  const slMeta = config?.stop_loss_pct;     // C1: 손절 %
  const tpMeta = config?.take_profit_pct;   // C1: 익절 %
  // T2: 일일 매수 한도 — 신 백엔드는 {value,min,max} 객체, 구 백엔드는 평수(plain int).
  const dlRaw = config?.daily_buy_limit_krw;
  const dlMeta = (dlRaw && typeof dlRaw === "object") ? dlRaw : null;
  const totalMeta = config?.total_paper_budget_krw;
  const entryLimits = config?.paper_entry_limits;
  const effMc = mcMeta?.value;
  const effBud = budMeta?.value;
  const effSl = slMeta?.value;
  const effTp = tpMeta?.value;
  const effDl = dlMeta ? dlMeta.value : Number(dlRaw ?? 10_000_000);
  // 종목 풀 확장 — universe_size(100/200/400) + universe_mode(auto/watchlist).
  //   meta 없으면(구 백엔드) 컨트롤 미표시(추가 전 동작 무변경).
  const uszMeta = config?.universe_size;
  const umMeta = config?.universe_mode;
  const effUsz = uszMeta?.value;
  const effUm = umMeta?.value;
  const USZ_OPTS = uszMeta?.options ?? [100, 200, 400];
  const UM_OPTS = umMeta?.options ?? ["auto", "watchlist"];

  const MC_MIN = mcMeta?.min ?? 1, MC_MAX = mcMeta?.max ?? 10;
  const BUD_MIN = budMeta?.min ?? 100_000, BUD_MAX = budMeta?.max ?? 10_000_000;
  const SL_MIN = slMeta?.min ?? 0.5, SL_MAX = slMeta?.max ?? 10;
  const TP_MIN = tpMeta?.min ?? 0.5, TP_MAX = tpMeta?.max ?? 20;
  const DL_MIN = dlMeta?.min ?? 3_000_000, DL_MAX = dlMeta?.max ?? 10_000_000;

  const [mc, setMc] = useState(effMc ?? 5);
  const [bud, setBud] = useState(effBud ?? 1_000_000);
  const [sl, setSl] = useState(effSl ?? 2);
  const [tp, setTp] = useState(effTp ?? 3.5);
  const [dl, setDl] = useState(effDl ?? 10_000_000);
  const [total, setTotal] = useState(totalMeta?.value ?? 10_000_000);
  const [usz, setUsz] = useState(effUsz ?? 100);
  const [um, setUm] = useState(effUm ?? "auto");
  const [note, setNote] = useState(null); // { kind: "ok"|"err", text }
  const [saving, setSaving] = useState(false);

  // 서버 실효값이 바뀌면(최초 로드/다른 경로 변경) 스테퍼 동기화.
  useEffect(() => { if (effMc != null) setMc(effMc); }, [effMc]);
  useEffect(() => { if (effBud != null) setBud(effBud); }, [effBud]);
  useEffect(() => { if (effSl != null) setSl(effSl); }, [effSl]);
  useEffect(() => { if (effTp != null) setTp(effTp); }, [effTp]);
  useEffect(() => { if (effDl != null) setDl(effDl); }, [effDl]);
  const effTotal = totalMeta?.value;
  useEffect(() => { if (effTotal != null) setTotal(effTotal); }, [effTotal]);
  useEffect(() => { if (effUsz != null) setUsz(effUsz); }, [effUsz]);
  useEffect(() => { if (effUm != null) setUm(effUm); }, [effUm]);

  const clampMc = (v) => Math.max(MC_MIN, Math.min(MC_MAX, v));
  const clampBud = (v) => Math.max(BUD_MIN, Math.min(BUD_MAX, Math.round(v / BUDGET_STEP) * BUDGET_STEP));
  const clampSl = (v) => Math.max(SL_MIN, Math.min(SL_MAX, _round1(v)));
  const clampTp = (v) => Math.max(TP_MIN, Math.min(TP_MAX, _round1(v)));
  const clampDl = (v) => Math.max(DL_MIN, Math.min(DL_MAX, Math.round(v / DAILY_STEP) * DAILY_STEP));
  const clampTotal = (v) => Math.max(totalMeta.min, Math.min(totalMeta.max, v));

  const stDirty = effSl != null && effTp != null && (sl !== effSl || tp !== effTp);
  const dlDirty = dlMeta != null && dl !== effDl;
  const uniDirty = (uszMeta != null && usz !== effUsz) || (umMeta != null && um !== effUm);
  const totalDirty = totalMeta != null && total !== totalMeta.value;
  const dirty = (effMc != null && effBud != null && (mc !== effMc || bud !== effBud)) || stDirty || dlDirty || totalDirty || uniDirty;
  const dailyApplied = entryLimits?.daily_buy_krw ?? effDl;
  const totalApplied = entryLimits?.total_budget_krw ?? totalMeta?.value ?? dailyApplied;
  const buyCount = entryLimits?.daily_buy_count ?? 10;
  const orderCap = entryLimits?.max_order_krw ?? effBud;
  const positionCap = entryLimits?.max_positions ?? effMc;
  const turnoverCap = Math.min(dailyApplied, buyCount * orderCap);
  const entryCap = Math.min(turnoverCap, totalApplied, positionCap * orderCap);

  const save = async () => {
    setSaving(true);
    setNote(null);
    try {
      const payload = { max_concurrent_positions: mc, per_stock_budget: bud };
      if (slMeta && tpMeta) { payload.stop_loss_pct = sl; payload.take_profit_pct = tp; }
      if (dlDirty) { payload.daily_buy_limit_krw = dl; }
      if (totalDirty) { payload.total_paper_budget_krw = total; }
      if (uszMeta) { payload.universe_size = usz; }
      if (umMeta) { payload.universe_mode = um; }
      const res = await api.runtimeConfigPut(payload);
      if (res.daily_buy_limit_krw?.value != null) setDl(res.daily_buy_limit_krw.value);
      if (res.total_paper_budget_krw?.value != null) setTotal(res.total_paper_budget_krw.value);
      onSaved?.(res); // 서버 재확인 실효값만 신뢰.
      // 손절/익절이 바뀌면 *보유 종목에도* 적용(C1). 그 외(종목수·투자금)는 다음 매수부터.
      setNote({
        kind: "ok",
        text: stDirty
          ? `적용됐어요 (${nowKstHm()}) — 손절/익절은 보유 종목에도 적용돼요`
          : `저장했어요 (${nowKstHm()})`,
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
            {/* V1: 스테퍼 버튼 기호(−/+)와 값 부호 겹침 방지 — 값은 양수 %, '손절' 라벨이 손실 방향을 의미. */}
            <span data-testid="rtcfg-sl-value" style={valBox}>{_round1(sl)}%</span>
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
            {/* V1: 값은 양수 %, '익절' 라벨이 이익 방향을 의미(스테퍼 +/− 와 부호 겹침 방지). */}
            <span data-testid="rtcfg-tp-value" style={valBox}>{_round1(tp)}%</span>
            <button type="button" data-testid="rtcfg-tp-inc" style={stepBtn}
              onClick={() => setTp((v) => clampTp(v + PCT_STEP))} disabled={tp >= TP_MAX}>+</button>
          </div>
        </div>
      )}

      {/* T2: 일일 매수 한도 스테퍼 (신 백엔드 meta 있을 때만) */}
      {dlMeta && (
        <div style={rowStyle}>
          <span style={{ fontSize: 14, color: "#2a1418", fontWeight: 700 }}>일일 매수 한도</span>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <button type="button" data-testid="rtcfg-dl-dec" style={stepBtn}
              onClick={() => setDl((v) => clampDl(v - DAILY_STEP))} disabled={dl <= DL_MIN}>−</button>
            <span data-testid="rtcfg-dl-value" style={valBox}>{fmtKRW(dl)}원</span>
            <button type="button" data-testid="rtcfg-dl-inc" style={stepBtn}
              onClick={() => setDl((v) => clampDl(v + DAILY_STEP))} disabled={dl >= DL_MAX}>+</button>
          </div>
        </div>
      )}
      {dlMeta && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {BUDGET_PRESETS.filter((value) => value >= DL_MIN && value <= DL_MAX).map((value) => (
            <button key={value} type="button" aria-pressed={dl === value}
              onClick={() => setDl(value)} disabled={saving}>
              {fmtKRW(value / 10_000)}만원
            </button>
          ))}
        </div>
      )}
      {totalMeta && (
        <div style={rowStyle}>
          <span style={{ fontSize: 14, color: "#2a1418", fontWeight: 700 }}>총 PAPER 운용 예산</span>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <button type="button" aria-label="총 PAPER 운용 예산 줄이기" style={stepBtn}
              onClick={() => setTotal((v) => clampTotal(v - DAILY_STEP))} disabled={total <= totalMeta.min || saving}>−</button>
            <span style={valBox}>{fmtKRW(total)}원</span>
            <button type="button" aria-label="총 PAPER 운용 예산 늘리기" style={stepBtn}
              onClick={() => setTotal((v) => clampTotal(v + DAILY_STEP))} disabled={total >= totalMeta.max || saving}>+</button>
          </div>
        </div>
      )}

      {/* 종목 풀 확장 — universe_mode(자동/관심종목) + universe_size(100/200/400). */}
      {umMeta && (
        <div style={rowStyle}>
          <span style={{ fontSize: 14, color: "#2a1418", fontWeight: 700 }}>종목 선택</span>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {UM_OPTS.map((opt) => (
              <button key={opt} type="button" data-testid={`rtcfg-um-${opt}`}
                onClick={() => setUm(opt)}
                style={{
                  padding: "8px 12px", borderRadius: 10, border: "1px solid rgba(40,20,24,.25)",
                  background: um === opt ? "#2a1418" : "rgba(255,255,255,.6)",
                  color: um === opt ? "#fff" : "#2a1418", fontSize: 13.5, fontWeight: 800,
                  cursor: "pointer", fontFamily: "inherit",
                }}>
                {opt === "auto" ? "자동(시총상위)" : "관심종목"}
              </button>
            ))}
          </div>
        </div>
      )}
      {uszMeta && um === "auto" && (
        <div style={rowStyle}>
          <span style={{ fontSize: 14, color: "#2a1418", fontWeight: 700 }}>종목 풀 크기</span>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {USZ_OPTS.map((opt) => (
              <button key={opt} type="button" data-testid={`rtcfg-usz-${opt}`}
                onClick={() => setUsz(opt)}
                style={{
                  padding: "8px 12px", borderRadius: 10, border: "1px solid rgba(40,20,24,.25)",
                  background: usz === opt ? "#2a1418" : "rgba(255,255,255,.6)",
                  color: usz === opt ? "#fff" : "#2a1418", fontSize: 13.5, fontWeight: 800,
                  cursor: "pointer", fontFamily: "inherit",
                }}>
                {opt}종목
              </button>
            ))}
          </div>
        </div>
      )}

      <div data-testid="rtcfg-affordable" style={{
        marginTop: 8, fontSize: 12.5, fontWeight: 700, lineHeight: 1.5, borderRadius: 8,
        padding: "8px 10px",
        background: "rgba(40,20,24,.06)", color: "rgba(40,20,24,.7)",
      }}>
        <div>현재 적용 중인 일일 매수 한도 {fmtKRW(dailyApplied)}원</div>
        {totalMeta && <div>현재 적용 중인 총 PAPER 운용 예산 {fmtKRW(totalApplied)}원</div>}
        {totalApplied < dailyApplied && (
          <div>총 운용 예산이 더 낮아요. 신규 매수는 보유 평가액과 주문 비용을 포함해 {fmtKRW(totalApplied)}원 안에서 가능해요.</div>
        )}
        {entryLimits && (dailyApplied < effDl || totalApplied < totalMeta?.value) && (
          <div>저장된 한도를 모두 적용하려면 자동매매 시작 화면에서 새 한도를 승인해주세요.</div>
        )}
        <div>
          주문당 최대 {fmtKRW(orderCap)}원 · 동시 보유 최대 {positionCap}종목 · 일일 매수 최대 {buyCount}회
        </div>
        <div>
          한도 기준 신규 보유에 투입 가능한 금액은 0~{fmtKRW(entryCap)}원이에요.
          보유 종목을 매도해 다시 매수할 경우 일일 누적 상한 추정치는 {fmtKRW(turnoverCap)}원이에요.
          실제 매수는 현재 보유, 남은 일일 한도·횟수, 주가, 수수료, KIS 주문 가능 현금에 따라 달라져요.
        </div>
      </div>

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
