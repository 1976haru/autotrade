/**
 * #55 / 7-03 — 포트폴리오 데이터 source 통일 카드 (read-only).
 *
 * Dashboard / Settings 에서 현금·총자산·포지션 값이 *어떤 source* 에서 왔는지와
 * 조회 상태를 함께 표시한다. 핵심:
 *  - 한 섹션의 현금/총자산/포지션은 *같은 source* 에서만 표시.
 *  - **API 실패 시 0원으로 표시하지 않는다** — "확인 불가" + 사유 표시.
 *  - Paper 모의 포트폴리오와 KIS 모의 계좌를 *섞지 않고* 별도 섹션으로 분리.
 *  - 조회 실패와 실제 0원을 구분.
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수/매도/실전 시작/Place Order 버튼 0개, 입력 form 0개.
 *  - account/secret 원문 표시 0건.
 *  - 실거래 계좌 잔고가 아니라는 안내.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const POLL_INTERVAL_MS = 0;

const _SOURCE_LABEL = {
  PAPER_SIMULATED: "Paper 모의 포트폴리오",
  KIS_PAPER_ACCOUNT: "KIS 모의 계좌",
  UNAVAILABLE: "조회 불가",
  MIXED_BLOCKED: "source 혼합 차단",
};

const _STATUS_LABEL = {
  OK: "정상",
  STALE: "오래됨",
  ERROR: "오류",
  CREDENTIALS_MISSING: "자격 미설정",
  API_UNAVAILABLE: "조회 실패",
  NOT_CONFIGURED: "미연결",
  UNKNOWN: "알 수 없음",
};

// 값을 숫자로 표시해도 되는 상태/소스인지.
function _isAvailable(snap) {
  return (
    snap?.value_available === true &&
    snap?.cash != null &&
    snap?.total_asset != null
  );
}

// 숫자면 'n원', null/undefined/실패면 '확인 불가' (0원으로 오해 금지).
function _krwOrUnknown(n, available) {
  if (!available || n == null || !Number.isFinite(Number(n))) return "확인 불가";
  return `${Number(n).toLocaleString("ko-KR")}원`;
}

function _countOrUnknown(n, available) {
  if (!available || n == null || !Number.isFinite(Number(n))) return "확인 불가";
  return `${Number(n)}종목`;
}

function _SnapshotSection({ snap, testPrefix }) {
  if (!snap) return null;
  const source = snap.source;
  const attempted = snap.attempted_source || source;
  const status = snap.status;
  const available = _isAvailable(snap);
  const failed = !available;
  const headerSource = source === "UNAVAILABLE" || source === "MIXED_BLOCKED"
    ? attempted
    : source;

  return (
    <div
      data-testid={`${testPrefix}-section`}
      data-source={source}
      data-status={status}
      style={{
        border: "1px solid var(--c-border)", borderRadius: "var(--r-sm)",
        padding: "8px 10px", marginBottom: 8,
        background: failed ? "#fef2f2" : "#f8fafc",
      }}
    >
      <div
        data-testid={`${testPrefix}-source`}
        style={{
          fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
          color: failed ? "#b91c1c" : "#166534", marginBottom: 4,
        }}
      >
        데이터 소스: {_SOURCE_LABEL[headerSource] || headerSource}
        {" "}({headerSource})
      </div>

      <div
        data-testid={`${testPrefix}-status`}
        style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}
      >
        상태: {_STATUS_LABEL[status] || status} ({status})
        {snap.reason_code ? ` · ${snap.reason_code}` : ""}
      </div>

      {failed ? (
        <div
          data-testid={`${testPrefix}-failure`}
          style={{
            padding: "5px 8px", borderRadius: 4, background: "#fee2e2",
            color: "#7f1d1d", fontSize: "var(--fs-xs)", marginBottom: 4,
          }}
        >
          조회 실패: 실제 잔고 0원이 아닙니다.
          {snap.message_ko ? ` ${snap.message_ko}` : ""}
        </div>
      ) : null}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
          gap: 6,
        }}
      >
        <div data-testid={`${testPrefix}-cash`}
             style={{ padding: "6px 8px", background: "#fff", borderRadius: 4 }}>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>현금</div>
          <div style={{ fontWeight: "var(--fw-bold)" }}>
            {_krwOrUnknown(snap.cash, available)}
          </div>
        </div>
        <div data-testid={`${testPrefix}-total-asset`}
             style={{ padding: "6px 8px", background: "#fff", borderRadius: 4 }}>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>총자산</div>
          <div style={{ fontWeight: "var(--fw-bold)" }}>
            {_krwOrUnknown(snap.total_asset, available)}
          </div>
        </div>
        <div data-testid={`${testPrefix}-positions`}
             style={{ padding: "6px 8px", background: "#fff", borderRadius: 4 }}>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>포지션</div>
          <div style={{ fontWeight: "var(--fw-bold)" }}>
            {_countOrUnknown(snap.position_count, available)}
          </div>
        </div>
      </div>

      <div
        data-testid={`${testPrefix}-last-updated`}
        style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 4 }}
      >
        마지막 갱신: {snap.last_updated || "—"}
      </div>
    </div>
  );
}

export function PortfolioSourceCard({
  apiClient = backendApi,
  testId = "portfolio-source-card",
  pollIntervalMs = POLL_INTERVAL_MS,
} = {}) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    if (typeof apiClient.portfolioSource !== "function") { setLoading(false); return; }
    try {
      const r = await apiClient.portfolioSource();
      setReport(r || null);
      setError(null);
    } catch (err) {
      setReport(null);
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

  const paper = report?.paper_simulated || null;
  const kis = report?.kis_paper_account || null;

  return (
    <div data-testid={testId}>
      <Card>
        <SectionLabel>💰 포트폴리오 데이터 소스</SectionLabel>

        <div data-testid="portfolio-source-note" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          현금 · 총자산 · 포지션 값은 같은 source 에서만 표시됩니다. 조회 실패는
          0원이 아니라 "확인 불가" 로 표시되며, 실제 (실전) 계좌 잔고가 아닙니다.
        </div>

        <div data-testid="portfolio-source-zero-warning" style={{
          padding: "5px 8px", borderRadius: 4, background: "#fffbeb",
          color: "#92400e", fontSize: "var(--fs-xs)", marginBottom: 8,
        }}>
          ⚠️ 조회 실패는 실제 잔고 0원이 아닙니다. "확인 불가" 와 "0원" 은 다릅니다.
        </div>

        {loading && report == null && !error ? (
          <div data-testid="portfolio-source-loading" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          }}>불러오는 중…</div>
        ) : null}

        {error ? (
          <div data-testid="portfolio-source-error" style={{
            padding: "6px 10px", borderRadius: 6, background: "#fef2f2",
            color: "#b91c1c", fontSize: "var(--fs-xs)",
          }}>포트폴리오 소스를 불러올 수 없습니다 (backend 연결 확인). 실제 잔고
            0원이 아닙니다.</div>
        ) : null}

        {report != null && !error ? (
          <>
            <div data-testid="portfolio-source-paper-label" style={{
              fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
              color: "var(--c-text-2)", margin: "4px 0",
            }}>
              ① Paper 모의 포트폴리오 (Paper 전용 · 실제 계좌 아님)
            </div>
            <_SnapshotSection snap={paper} testPrefix="portfolio-source-paper" />

            <div data-testid="portfolio-source-kis-label" style={{
              fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
              color: "var(--c-text-2)", margin: "4px 0",
            }}>
              ② KIS 모의 계좌 (KIS 모의투자 계좌 기준 · 실전 계좌 아님)
            </div>
            <_SnapshotSection snap={kis} testPrefix="portfolio-source-kis" />
          </>
        ) : null}

        <div data-testid="portfolio-source-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 화면은 포트폴리오 표시 전용입니다. 주문/거래 실행 기능이 아니며,
          자격정보(계좌번호/secret)는 표시되지 않습니다. broker 호출 0건.
        </div>
      </Card>
    </div>
  );
}

export default PortfolioSourceCard;
