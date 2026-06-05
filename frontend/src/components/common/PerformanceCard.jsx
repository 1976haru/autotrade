import { useCallback, useEffect, useState } from "react";

import { backendApi } from "../../services/backend/client";
import { fmtKRW, nowKstHm } from "../../utils/format";

// P3: 성과 카드 — 승률/손익비/순손익 + 봇 vs 지수. 읽기 전용.
//   탭 전환 / 수동 새로고침 시에만 조회(폴링 편승 금지). 정직성: 가짜 0% 금지.
const TABS = [
  { key: "daily", label: "오늘" },
  { key: "weekly", label: "이번 주" },
  { key: "monthly", label: "이번 달" },
  { key: "custom", label: "직접 설정" },
];

const _pct = (v) => `${v > 0 ? "+" : ""}${Number(v).toFixed(1)}%`;
const _pp = (v) => (v == null ? "" : ` (${v > 0 ? "+" : ""}${Number(v).toFixed(1)}%p)`);
const _signed = (v) => `${v > 0 ? "+" : ""}${fmtKRW(v)}원`;

export function PerformanceCard({ api = backendApi }) {
  const [period, setPeriod] = useState("daily");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    if (period === "custom" && (!from || !to)) return; // 범위 미입력 시 대기
    setLoading(true);
    setFailed(false);
    try {
      const res = await api.performanceGet({ period, from, to });
      setData(res);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [api, period, from, to]);

  // 탭/범위 변경 시에만 조회(폴링 아님).
  useEffect(() => { load(); }, [load]);

  const noData = data?.no_data;
  const smallSample = data?.small_sample;
  const market = data?.market;
  const cmp = data?.comparison;

  const winRateText = noData || data?.win_rate == null
    ? "—"
    : `${Math.round(data.win_rate * 100)}% (${data.win_count}승 ${data.loss_count}패)`;
  const payoffText = noData || data?.payoff_ratio == null ? "—" : Number(data.payoff_ratio).toFixed(1);
  const netText = noData || data?.net_pnl_krw == null ? "—" : _signed(data.net_pnl_krw);

  const Metric = ({ k, v, sub, testid }) => (
    <div style={{ flex: 1, textAlign: "center" }}>
      <div style={{ fontSize: 12, color: "rgba(40,20,24,.7)", fontWeight: 600 }}>{k}</div>
      <div data-testid={testid} style={{ fontSize: 16, fontWeight: 800, color: "#2a1418", marginTop: 3 }}>{v}</div>
      {sub && <div style={{ fontSize: 11, color: "rgba(40,20,24,.6)", marginTop: 1 }}>{sub}</div>}
    </div>
  );

  return (
    <div data-testid="perf-card" style={{
      background: "rgba(255,255,255,.5)", borderRadius: 12, padding: "12px 14px", marginTop: 12,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: "#2a1418" }}>
          성과 (비용 차감 후) {smallSample && !noData && (
            <span data-testid="perf-smallsample-badge" style={{ fontSize: 11, fontWeight: 700, color: "#7a4a00", marginLeft: 4 }}>· 표본 적음 · 참고용</span>
          )}
        </div>
        <button type="button" data-testid="perf-refresh" onClick={load} disabled={loading}
          style={{ fontSize: 12, fontWeight: 700, border: "1px solid rgba(40,20,24,.25)", borderRadius: 8, padding: "4px 9px", background: "transparent", color: "#2a1418", cursor: "pointer", fontFamily: "inherit" }}>
          {loading ? "조회 중…" : "↻ 새로고침"}
        </button>
      </div>

      {/* 기간 탭 */}
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        {TABS.map((t) => (
          <button key={t.key} type="button" data-testid={`perf-tab-${t.key}`} onClick={() => setPeriod(t.key)}
            style={{
              flex: 1, padding: "7px 0", borderRadius: 8, fontFamily: "inherit", fontSize: 12.5, fontWeight: 800,
              border: `1px solid ${period === t.key ? "#5b9dff" : "rgba(40,20,24,.2)"}`,
              background: period === t.key ? "rgba(91,157,255,.16)" : "transparent",
              color: period === t.key ? "#2563eb" : "rgba(40,20,24,.75)", cursor: "pointer",
            }}>
            {t.label}
          </button>
        ))}
      </div>

      {period === "custom" && (
        <div style={{ display: "flex", gap: 6, marginTop: 8, alignItems: "center" }}>
          <input data-testid="perf-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)}
            style={{ flex: 1, padding: "6px", borderRadius: 8, border: "1px solid rgba(40,20,24,.25)", fontFamily: "inherit" }} />
          <span style={{ color: "rgba(40,20,24,.6)" }}>~</span>
          <input data-testid="perf-to" type="date" value={to} onChange={(e) => setTo(e.target.value)}
            style={{ flex: 1, padding: "6px", borderRadius: 8, border: "1px solid rgba(40,20,24,.25)", fontFamily: "inherit" }} />
        </div>
      )}

      {/* 1행: 승률 / 손익비 / 순손익 */}
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <Metric k="승률" v={winRateText} testid="perf-winrate" />
        <Metric k="손익비" v={payoffText} testid="perf-payoff" />
        <Metric k="순손익" v={netText} sub={noData ? null : "비용 차감 후"} testid="perf-net" />
      </div>

      {noData && (
        <div data-testid="perf-nodata" style={{ marginTop: 8, fontSize: 12.5, color: "rgba(40,20,24,.65)", textAlign: "center" }}>
          청산 거래가 쌓이면 표시돼요
        </div>
      )}

      {/* 2행: 봇 vs 지수 */}
      <div style={{ marginTop: 10, fontSize: 13, color: "#2a1418", fontWeight: 700, lineHeight: 1.5 }}>
        {failed || !market || market.available === false ? (
          <span data-testid="perf-market-fail" style={{ color: "rgba(40,20,24,.6)" }}>
            시장 데이터 불러오기 실패({nowKstHm()})
          </span>
        ) : cmp ? (
          <span data-testid="perf-market">
            봇 {_pct(cmp.bot_return_pct)}
            {cmp.kospi_return_pct != null && <> · 코스피 {_pct(cmp.kospi_return_pct)}{_pp(cmp.vs_kospi_pp)}</>}
            {cmp.kosdaq_return_pct != null && <> · 코스닥 {_pct(cmp.kosdaq_return_pct)}{_pp(cmp.vs_kosdaq_pp)}</>}
          </span>
        ) : (
          <span data-testid="perf-market" style={{ color: "rgba(40,20,24,.6)" }}>
            {market.kospi_return_pct != null
              ? <>코스피 {_pct(market.kospi_return_pct)}{market.kosdaq_return_pct != null && <> · 코스닥 {_pct(market.kosdaq_return_pct)}</>}</>
              : "시장 비교 준비 중"}
          </span>
        )}
      </div>
    </div>
  );
}
