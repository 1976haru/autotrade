import { useCallback, useEffect, useState } from "react";

import { backendApi } from "../../services/backend/client";
import { fmtKRW } from "../../utils/format";

// S5: 기법별 성적표 — AI 운용 성향 카드 하단. 읽기 전용. 탭 전환/수동 새로고침만(폴링 0).
const TABS = [
  { key: "daily", label: "오늘" },
  { key: "weekly", label: "주" },
  { key: "monthly", label: "월" },
];
const LABELS = { ORB: "ORB", MOMENTUM: "모멘텀", VWAP: "VWAP", GAP: "갭" };
const PROFILE_KO = { conservative: "보수형", balanced: "안정형", aggressive: "공격형" };

export function TechniqueScorecard({ api = backendApi }) {
  const [period, setPeriod] = useState("daily");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      setData(await api.performanceByTechnique({ period }));
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [api, period]);

  useEffect(() => { load(); }, [load]); // 탭 변경 시에만 조회(폴링 아님)

  const noData = data?.no_data;
  const smallSample = data?.small_sample;

  return (
    <div data-testid="technique-card" style={{ marginTop: 14, borderTop: "1px solid rgba(40,20,24,.12)", paddingTop: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: "#2a1418" }}>
          기법별 성적
          {data?.active_profile && <span style={{ fontSize: 11, fontWeight: 600, color: "rgba(40,20,24,.6)", marginLeft: 5 }}>· 현재 {PROFILE_KO[data.active_profile] || data.active_profile} 기준</span>}
          {smallSample && !noData && <span data-testid="technique-smallsample" style={{ fontSize: 11, fontWeight: 700, color: "#7a4a00", marginLeft: 5 }}>· 표본 적음 · 참고용</span>}
        </div>
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          {TABS.map((t) => (
            <button key={t.key} type="button" data-testid={`technique-tab-${t.key}`} onClick={() => setPeriod(t.key)}
              style={{
                padding: "3px 9px", borderRadius: 7, fontFamily: "inherit", fontSize: 12, fontWeight: 800, cursor: "pointer",
                border: `1px solid ${period === t.key ? "#5b9dff" : "rgba(40,20,24,.2)"}`,
                background: period === t.key ? "rgba(91,157,255,.16)" : "transparent",
                color: period === t.key ? "#2563eb" : "rgba(40,20,24,.7)",
              }}>{t.label}</button>
          ))}
          <button type="button" data-testid="technique-refresh" onClick={load} disabled={loading}
            style={{ fontSize: 12, border: "none", background: "transparent", cursor: "pointer", color: "rgba(40,20,24,.6)" }}>↻</button>
        </div>
      </div>

      {failed ? (
        <div data-testid="technique-fail" style={{ fontSize: 12.5, color: "rgba(40,20,24,.6)", marginTop: 8 }}>불러오기 실패</div>
      ) : noData ? (
        <div data-testid="technique-nodata" style={{ fontSize: 12.5, color: "rgba(40,20,24,.6)", marginTop: 8 }}>
          — 거래가 쌓이면 표시돼요 (기법 기록은 오늘부터 쌓여요)
        </div>
      ) : (
        <>
          <div style={{ marginTop: 8 }}>
            {(data?.techniques || []).map((t) => (
              <div key={t.technique} data-testid={`technique-row-${t.technique}`}
                style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "5px 0", fontSize: 13 }}>
                <span style={{ fontWeight: 800, color: "#2a1418", width: 64 }}>{LABELS[t.technique] || t.technique}</span>
                <span style={{ color: "rgba(40,20,24,.7)", flex: 1, textAlign: "center" }}>
                  찬성 {t.trade_count}건{t.win_rate != null && ` · ${Math.round(t.win_rate * 100)}%(${t.win_count}승 ${t.loss_count}패)`}
                </span>
                <span style={{ fontWeight: 800, width: 96, textAlign: "right", color: t.net_contribution_krw > 0 ? "#c0392b" : t.net_contribution_krw < 0 ? "#1f6feb" : "#2a1418" }}>
                  {t.net_contribution_krw > 0 ? "+" : ""}{fmtKRW(t.net_contribution_krw)}원
                </span>
              </div>
            ))}
          </div>
          <div data-testid="technique-note" style={{ fontSize: 11, color: "rgba(40,20,24,.55)", marginTop: 6, lineHeight: 1.4 }}>
            한 거래에 여러 기법이 함께 찬성할 수 있어요 — 합계가 전체 거래 수와 다를 수 있어요.
          </div>
        </>
      )}
    </div>
  );
}
