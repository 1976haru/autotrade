import { useEffect, useState } from "react";

import { backendApi } from "../../services/backend/client";
import { Card, SectionLabel } from "./index";
import { formatThemeBriefingLine, themeBriefingStaleNote } from "../../utils/themeBriefing";


// briefing: 테마 브리핑 2단계(전일 미국 테마 ETF 등락) — 선택적, 정보 표시만.
//   미전달(undefined) 시 기존 동작과 완전히 동일(신규 fetch·렌더 0).
export function ThemeFilterCard({ apiClient = backendApi, briefing = null }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [durations, setDurations] = useState({});

  useEffect(() => {
    let active = true;
    apiClient.themeFilterGet()
      .then((value) => {
        if (!active) return;
        setStatus(value);
        setDurations(Object.fromEntries(
          (value?.themes || []).map((t) => [t.id, t.duration || "today"]),
        ));
        setError("");
      })
      .catch(() => { if (active) setError("테마 설정을 불러오지 못했습니다."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiClient]);

  const toggle = async (theme) => {
    setBusy(theme.id);
    setError("");
    try {
      const enabled = !theme.enabled;
      const body = { enabled };
      if (!enabled) body.duration = durations[theme.id] || "today";
      const value = await apiClient.themeFilterPatch(theme.id, body);
      setStatus(value);
      setDurations((prev) => ({
        ...prev,
        ...Object.fromEntries(
          (value?.themes || []).map((t) => [t.id, t.duration || prev[t.id] || "today"]),
        ),
      }));
    } catch {
      setError("테마 설정 저장에 실패했습니다. 기존 상태를 유지합니다.");
    } finally {
      setBusy("");
    }
  };

  return (
    <Card>
      <SectionLabel>🏷 오늘의 테마 필터</SectionLabel>
      <div data-testid="theme-filter-safety" style={{
        padding: "9px 10px", borderRadius: 8, background: "#eff6ff",
        color: "#1e3a8a", fontSize: 12, fontWeight: 700, lineHeight: 1.5,
      }}>
        OFF는 신규 진입만 차단합니다. 이미 보유한 종목의 손절·익절·청산은 계속됩니다.
      </div>

      {themeBriefingStaleNote(briefing) && (
        <div data-testid="theme-briefing-stale-note" style={{
          marginTop: 8, padding: "6px 10px", borderRadius: 8, background: "#fef3c7",
          color: "#7a4a00", fontSize: 11, fontWeight: 700,
        }}>
          ⚠️ {themeBriefingStaleNote(briefing)}
        </div>
      )}

      {loading && <div data-testid="theme-filter-loading" style={{ marginTop: 10 }}>불러오는 중…</div>}
      {error && <div data-testid="theme-filter-error" style={{
        marginTop: 10, color: "var(--c-danger)", fontSize: 12,
      }}>{error}</div>}

      {!loading && status && (
        <>
          <div style={{ marginTop: 10, color: "var(--c-text-3)", fontSize: 11 }}>
            현재 신규 진입 제외 {status.effective_blocked_symbol_count ?? 0}종목
            {" · "}재시작 없이 다음 스캔부터 반영
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 7, marginTop: 9 }}>
            {(status.themes || []).map((theme) => (
              <div key={theme.id} data-testid={`theme-row-${theme.id}`} style={{
                display: "grid", gridTemplateColumns: "minmax(90px,1fr) auto auto",
                alignItems: "center", gap: 8, padding: "8px 9px",
                border: "1px solid var(--c-border)", borderRadius: 8,
              }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 800, color: "var(--c-text)" }}>
                    {theme.label}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--c-text-3)", marginTop: 2 }}>
                    {theme.mapped_symbol_count}종목
                    {!theme.enabled && theme.duration === "today" && theme.expires_at_kst
                      ? ` · 오늘만 (${theme.expires_at_kst.slice(11, 16)} 해제)` : ""}
                    {!theme.enabled && theme.duration === "until_enabled" ? " · 해제까지" : ""}
                  </div>
                  {briefing && (
                    <div data-testid={`theme-briefing-${theme.id}`} style={{ fontSize: 10, color: "var(--c-text-3)", marginTop: 2 }}>
                      {formatThemeBriefingLine(briefing.byId[theme.id], briefing.sessionDateUs)}
                    </div>
                  )}
                </div>
                <select
                  data-testid={`theme-duration-${theme.id}`}
                  aria-label={`${theme.label} OFF 기간`}
                  value={durations[theme.id] || "today"}
                  disabled={!theme.enabled || busy === theme.id}
                  onChange={(e) => setDurations((prev) => ({
                    ...prev, [theme.id]: e.target.value,
                  }))}
                  style={{ padding: "6px 5px", borderRadius: 6, border: "1px solid var(--c-border)" }}
                >
                  <option value="today">오늘만</option>
                  <option value="until_enabled">해제까지</option>
                </select>
                <button
                  type="button"
                  role="switch"
                  aria-checked={theme.enabled}
                  data-testid={`theme-toggle-${theme.id}`}
                  disabled={busy === theme.id}
                  onClick={() => toggle(theme)}
                  style={{
                    minWidth: 54, padding: "7px 9px", borderRadius: 999,
                    border: "none", cursor: busy === theme.id ? "wait" : "pointer",
                    background: theme.enabled ? "#16a34a" : "#94a3b8",
                    color: "#fff", fontSize: 11, fontWeight: 800,
                  }}
                >
                  {busy === theme.id ? "…" : theme.enabled ? "ON" : "OFF"}
                </button>
              </div>
            ))}
          </div>
          {status.unmapped_symbol_count > 0 && (
            <div data-testid="theme-filter-unmapped" style={{
              marginTop: 9, color: "#9a3412", fontSize: 11,
            }}>
              미분류 {status.unmapped_symbol_count}종목은 fail-open으로 차단하지 않습니다.
            </div>
          )}
        </>
      )}
    </Card>
  );
}

export default ThemeFilterCard;
