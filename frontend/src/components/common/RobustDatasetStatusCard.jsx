/**
 * RobustDatasetStatusCard — robust 분봉 데이터셋 수집/품질/메타데이터 상태 (read-only).
 *
 * KIS-INTRADAY-ROBUST-DATASET-COLLECTION-01. 4상태: 데이터 없음(empty) / 로딩 / 완료 / 실패.
 * 표시: 수집/품질 status, 종목군 분포, 실제 기간/거래일, 시간분할/regime/1분봉, ready 여부,
 * warnings, 다음 권장 작업. invariant(테스트 lock): 수집/품질검증/메타데이터 전용 —
 * 주문/실전/자동매매 시작/EXE 빌드/적용 버튼 0개 (새로고침/복사만), input/textarea 0개.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _Q_COLOR = { PASS: "#166534", WARN: "#a16207", FAIL: "#b91c1c", NOT_COLLECTED: "#64748b" };
const _Q_BG = { PASS: "#dcfce7", WARN: "#fef9c3", FAIL: "#fee2e2", NOT_COLLECTED: "#f1f5f9" };

export function RobustDatasetStatusCard({
  apiClient = backendApi,
  testId = "robust-dataset-status-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.robustDatasetStatus !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.robustDatasetStatus()) || null;
      setReport(r);
      setState(r && r.available !== false ? "complete" : "empty");
    } catch {
      setReport(null);
      setState("error");
    }
  }, [apiClient]);

  useEffect(() => { refresh(); }, [refresh]);

  const copyReport = useCallback(() => {
    try {
      if (navigator?.clipboard?.writeText) {
        navigator.clipboard.writeText(JSON.stringify(report || {}, null, 2));
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }
    } catch { /* noop */ }
  }, [report]);

  const q = report?.data_quality_status || "—";
  const byGroup = report?.symbols_by_group || {};
  const warnings = Array.isArray(report?.warnings) ? report.warnings : [];
  const groupCounts = Object.keys(byGroup).map((g) => `${g} ${(byGroup[g] || []).length}`).join(" · ");

  return (
    <div data-testid={testId}>
      <Card accentColor="#0ea5e933">
        <SectionLabel>🗂️ robust 분봉 데이터셋 (수집·품질·메타데이터)</SectionLabel>

        <div data-testid="robust-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 카드는 <b>데이터 수집·품질검증·메타데이터 전용</b>입니다. 백테스트/주문/실전 전환/
          EXE 빌드 0건 · 자동 적용 없음 · 수익 보장 없음. 기존 데이터는 삭제하지 않고 별도 경로에 저장합니다.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="robust-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>상태 새로고침</button>
          <button type="button" data-testid="robust-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "상태 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="robust-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            ⏳ 데이터셋 상태를 불러오는 중입니다…
          </div>
        ) : null}
        {state === "error" ? (
          <div data-testid="robust-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
            상태를 불러올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="robust-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 robust 데이터셋이 수집되지 않았습니다. CLI <code>collect_robust_intraday_dataset.py</code> 수집 후
            <code>validate_robust_intraday_dataset.py --write-latest</code> 실행 시 표시됩니다 (설치 오류 아님).
            {Object.keys(byGroup).length > 0 ? (
              <div data-testid="robust-empty-groups" style={{ marginTop: 4 }}>
                예정 종목군: {groupCounts}
              </div>
            ) : null}
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="robust-quality" style={{
              padding: "8px 10px", borderRadius: 6, marginBottom: 8,
              background: _Q_BG[q] || "#f1f5f9", border: "1px solid var(--c-border)",
            }}>
              <div style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)",
                            color: _Q_COLOR[q] || "var(--c-text)" }}>
                데이터 품질: <span data-testid="robust-quality-status">{q}</span>
              </div>
              <div data-testid="robust-summary" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
                종목 {report.symbol_count ?? 0} · {groupCounts}
              </div>
              <div data-testid="robust-period" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 2 }}>
                기간 {report.actual_period ?? "—"} · 거래일 {report.trading_days ?? 0}
              </div>
              <div data-testid="robust-meta" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 2 }}>
                시간분할 <b>{report.time_split_status ?? "—"}</b> · regime <b>{report.regime_label_status ?? "—"}</b> ·
                1분봉 <b>{report.one_minute_availability ?? "—"}</b>
              </div>
              <div data-testid="robust-ready" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginTop: 2 }}>
                robust 백테스트 준비: <b>{String(report.ready_for_robust_backtest)}</b>
              </div>
              <div data-testid="robust-live" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginTop: 2 }}>
                실전매매 권고 <b>{String(report.live_trading_recommendation)}</b> · 실제주문 허용 <b>{String(report.real_order_allowed)}</b>
              </div>
            </div>

            {warnings.length > 0 ? (
              <div data-testid="robust-warnings" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
                경고: {warnings.slice(0, 6).join(" · ")}
              </div>
            ) : null}
            {report.next_recommended_task ? (
              <div data-testid="robust-next" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 2 }}>
                다음 권장 작업: {report.next_recommended_task}
              </div>
            ) : null}
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default RobustDatasetStatusCard;
