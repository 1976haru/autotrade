/**
 * #54 / 7-02 — 기본 Universe 50개 상태 카드 (Settings 탭, read-only).
 *
 * 사용자 관심종목이 없어도 자동매매 후보군이 비지 않도록 기본 Universe 50개를
 * fallback 으로 쓰고, universe_source / count / symbols_preview / fallback_used /
 * reason 을 표시한다. 후보군이 0개면 사유를 명확히 보여 *조용히 멈추지 않게* 한다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수/매도/실전 시작/Place Order/종목 강제 주문 버튼 0개.
 *  - 입력 form(input/textarea/select) 0개.
 *  - account/secret 원문 표시 0건.
 *  - 기본 Universe 는 후보군 확보용 — 투자 추천이 아니라는 안내.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _SOURCE_LABEL = {
  USER_WATCHLIST: "사용자 관심종목",
  DEFAULT_UNIVERSE_50: "기본 Universe 50",
  FALLBACK_DEFAULT_UNIVERSE_50: "기본 Universe 50 (fallback)",
  EMPTY: "후보군 없음",
};

export function UniverseStatusCard({
  apiClient = backendApi,
  testId = "universe-status-card",
  pollIntervalMs = 0,
} = {}) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    if (typeof apiClient.universeStatus !== "function") { setLoading(false); return; }
    try {
      const r = await apiClient.universeStatus();
      setStatus(r || null);
      setError(null);
    } catch (err) {
      setStatus(null);
      setError(err?.message || String(err));
    } finally {
      setLoading(false);
    }
  }, [apiClient]);

  useEffect(() => {
    refresh();
    if (!pollIntervalMs || pollIntervalMs <= 0) return undefined;
    const t = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(t);
  }, [refresh, pollIntervalMs]);

  const source = status?.universe_source || null;
  const count = status?.universe_count ?? null;
  const preview = Array.isArray(status?.symbols_preview) ? status.symbols_preview : [];
  const fallback = status?.fallback_used === true;
  const reason = status?.reason_code || null;
  const message = status?.message_ko || "";
  const empty = count === 0;

  return (
    <div data-testid={testId}>
      <Card>
        <SectionLabel>📋 자동매매 후보군 (Universe)</SectionLabel>

        <div data-testid="universe-note" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          기본 Universe 는 후보군 확보용이며 투자 추천이 아닙니다. 사용자가
          관심종목을 등록하면 사용자 관심종목이 우선됩니다.
        </div>

        {loading && status == null && !error ? (
          <div data-testid="universe-loading" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          }}>불러오는 중…</div>
        ) : null}

        {error ? (
          <div data-testid="universe-error" style={{
            padding: "6px 10px", borderRadius: 6, background: "#fef2f2",
            color: "#b91c1c", fontSize: "var(--fs-xs)",
          }}>Universe 상태를 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {status != null && !error ? (
          <>
            <div data-testid="universe-source" data-source={source} style={{
              padding: "5px 8px", borderRadius: 4, marginBottom: 4,
              background: empty ? "#fef2f2" : (fallback ? "#fef3c7" : "#dcfce7"),
              fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
              color: empty ? "#b91c1c" : (fallback ? "#92400e" : "#166534"),
            }}>
              소스: {_SOURCE_LABEL[source] || source} ({source})
            </div>

            <div data-testid="universe-count" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              후보군: <strong>{count}개</strong>
            </div>

            {fallback ? (
              <div data-testid="universe-fallback" style={{
                fontSize: "var(--fs-xs)", color: "#92400e", marginBottom: 4,
              }}>
                사용자 관심종목 없음 → 기본 Universe 50개 사용 중 (fallback)
              </div>
            ) : null}

            {preview.length > 0 ? (
              <div data-testid="universe-preview" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
                fontFamily: "var(--font-mono, monospace)",
              }}>
                미리보기: {preview.join(", ")}
                {count > preview.length ? ` 외 ${count - preview.length}개` : ""}
              </div>
            ) : null}

            {empty ? (
              <div data-testid="universe-empty-reason" style={{
                padding: "5px 8px", borderRadius: 4, background: "#fef2f2",
                color: "#b91c1c", fontSize: "var(--fs-xs)", marginBottom: 4,
              }}>
                후보군이 없습니다: {reason} — {message || "자동 판단을 건너뜁니다."}
              </div>
            ) : (
              <div data-testid="universe-reason" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 4,
              }}>
                {message} ({reason})
              </div>
            )}

            {Array.isArray(status?.invalid_symbols_removed)
              && status.invalid_symbols_removed.length > 0 ? (
              <div data-testid="universe-invalid-removed" style={{
                fontSize: "var(--fs-xs)", color: "#92400e", marginBottom: 4,
              }}>
                유효하지 않아 제외: {status.invalid_symbols_removed.join(", ")}
              </div>
            ) : null}
          </>
        ) : null}

        <div data-testid="universe-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 화면은 후보군 상태 표시 전용입니다. 주문/매수/매도 기능이 아니며,
          자격정보는 표시되지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default UniverseStatusCard;
