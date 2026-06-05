import { useCallback, useEffect, useState } from "react";

import { backendApi } from "../../services/backend/client";

// B3: 아침 브리핑 보드 — 밤사이 미국 시세 5종 + 최신 경제 헤드라인. 사실만(추천 0).
//   페이지 로드 + 수동 새로고침만(폴링 0). 실패 칸은 정직 표시(F4 패턴).
const _pct = (v) => `${v > 0 ? "▲" : v < 0 ? "▼" : ""}${Math.abs(v).toFixed(2)}%`;
const _color = (v) => (v > 0 ? "#c0392b" : v < 0 ? "#1f6feb" : "#2a1418");
const _num = (v) => (v == null ? "—" : v.toLocaleString("ko-KR"));

export function BriefingBoard({ api = backendApi }) {
  const [open, setOpen] = useState(true);          // 접기/펴기(세션 내 유지로 충분)
  const [markets, setMarkets] = useState(null);    // null=로딩
  const [headlines, setHeadlines] = useState(null);
  const [mFail, setMFail] = useState(false);
  const [hFail, setHFail] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const [m, h] = await Promise.allSettled([api.briefingMarkets(), api.briefingHeadlines()]);
    if (m.status === "fulfilled" && m.value) { setMarkets(m.value); setMFail(false); }
    else { setMFail(true); }
    if (h.status === "fulfilled" && h.value) { setHeadlines(h.value); setHFail(false); }
    else { setHFail(true); }
    setLoading(false);
  }, [api]);

  useEffect(() => { load(); }, [load]); // 로드 시 1회(폴링 아님)

  const hNow = () => new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });

  return (
    <div data-testid="briefing-board" style={{
      background: "rgba(255,255,255,.6)", border: "1px solid rgba(40,20,24,.1)", borderRadius: 14,
      padding: "10px 14px", marginBottom: 12,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <button type="button" data-testid="briefing-toggle" onClick={() => setOpen((o) => !o)}
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 13.5, fontWeight: 800, color: "#2a1418" }}>
          {open ? "▾" : "▸"} 오늘 아침 브리핑 <span style={{ fontSize: 11, fontWeight: 600, color: "rgba(40,20,24,.55)" }}>· 미국장 마감 기준 · KST</span>
        </button>
        <button type="button" data-testid="briefing-refresh" onClick={load} disabled={loading}
          style={{ fontSize: 12, border: "none", background: "transparent", cursor: "pointer", color: "rgba(40,20,24,.6)" }}>↻</button>
      </div>

      {open && (
        <div style={{ marginTop: 8 }}>
          {/* 1행: 미국 시세 5종 */}
          {mFail && !markets ? (
            <div data-testid="briefing-markets-fail" style={{ fontSize: 12.5, color: "rgba(40,20,24,.6)" }}>
              시세 불러오기 실패({hNow()})
            </div>
          ) : !markets ? (
            <div data-testid="briefing-markets-loading" style={{ fontSize: 12.5, color: "rgba(40,20,24,.55)" }}>불러오는 중…</div>
          ) : (
            <div data-testid="briefing-markets" style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
              {(markets.markets || []).map((m) => (
                <span key={m.key} data-testid={`briefing-mkt-${m.key}`} style={{ fontSize: 12.5, fontWeight: 700, color: "#2a1418" }}>
                  {m.label}{" "}
                  {m.available ? (
                    <>
                      <span>{_num(m.value)}</span>{" "}
                      <span style={{ color: _color(m.change_pct) }}>{_pct(m.change_pct)}</span>
                    </>
                  ) : (
                    <span style={{ color: "rgba(40,20,24,.5)" }}>—(실패 {m.asof_kst})</span>
                  )}
                </span>
              ))}
            </div>
          )}

          {/* 2행: 헤드라인 5개 */}
          <div style={{ marginTop: 9, borderTop: "1px solid rgba(40,20,24,.08)", paddingTop: 7 }}>
            <div style={{ fontSize: 11.5, fontWeight: 800, color: "rgba(40,20,24,.7)", marginBottom: 4 }}>
              최신 경제 헤드라인{headlines?.source && ` · ${headlines.source}`}{headlines?.stale && ` · ${headlines.asof_kst} 기준`}
            </div>
            {hFail && !headlines ? (
              <div data-testid="briefing-headlines-fail" style={{ fontSize: 12, color: "rgba(40,20,24,.6)" }}>헤드라인 불러오기 실패({hNow()})</div>
            ) : !headlines ? (
              <div data-testid="briefing-headlines-loading" style={{ fontSize: 12, color: "rgba(40,20,24,.55)" }}>불러오는 중…</div>
            ) : headlines.available === false || (headlines.headlines || []).length === 0 ? (
              <div data-testid="briefing-headlines-empty" style={{ fontSize: 12, color: "rgba(40,20,24,.6)" }}>헤드라인 불러오기 실패({headlines.asof_kst})</div>
            ) : (
              <div data-testid="briefing-headlines">
                {headlines.headlines.map((h, i) => (
                  <div key={i} data-testid={`briefing-hl-${i}`} style={{ fontSize: 12.5, padding: "2px 0", lineHeight: 1.4 }}>
                    <a href={h.link} target="_blank" rel="noreferrer" style={{ color: "#2563eb", textDecoration: "none" }}>{h.title}</a>
                    {h.published_kst && <span style={{ fontSize: 11, color: "rgba(40,20,24,.5)", marginLeft: 5 }}>{h.published_kst}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
