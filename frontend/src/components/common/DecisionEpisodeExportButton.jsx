/**
 * P-31: decision_episode 분석 데이터 내보내기 버튼 — read-only.
 *
 * CSV(엑셀) / JSONL(파이썬·학습) export 를 트리거하고 생성 파일 경로를 표시한다.
 * **실전 / 매수 / 매도 / LIVE 활성화 버튼이 아니며**, export 데이터에는 실제
 * 계좌정보 / API key 가 포함되지 않는다 (주문 신호 아님).
 */

import { useCallback, useState } from "react";

import { Card, SectionLabel } from "./index.jsx";
import { backendApi } from "../../services/backend/client";

export function DecisionEpisodeExportButton({
  apiClient = backendApi,
  testId = "decision-episode-export",
} = {}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  const run = useCallback(async (format) => {
    if (typeof apiClient.agentExportDecisionEpisodes !== "function") return;
    setBusy(true); setError(""); setResult(null);
    try {
      const r = await apiClient.agentExportDecisionEpisodes({ format });
      setResult(r?.result || null);
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [apiClient]);

  return (
    <Card accentColor="#64748b33">
      <div data-testid={testId}>
        <SectionLabel>⬇️ 분석용 데이터 내보내기</SectionLabel>

        <div data-testid="export-disclaimer"
             style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8 }}>
          실제 계좌정보와 API key는 포함되지 않습니다. 이 파일은 주문 신호가 아닙니다.
          CSV는 엑셀 분석용, JSONL은 파이썬/학습 데이터 분석용입니다.
        </div>

        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          <button type="button" data-testid="export-csv-btn" disabled={busy}
                  onClick={() => run("csv")}
                  style={{ padding: "6px 12px", borderRadius: 6, cursor: "pointer",
                           border: "1px solid var(--c-border)", background: "var(--c-surface-2)",
                           fontSize: "var(--fs-xs)" }}>
            CSV 내보내기
          </button>
          <button type="button" data-testid="export-jsonl-btn" disabled={busy}
                  onClick={() => run("jsonl")}
                  style={{ padding: "6px 12px", borderRadius: 6, cursor: "pointer",
                           border: "1px solid var(--c-border)", background: "var(--c-surface-2)",
                           fontSize: "var(--fs-xs)" }}>
            JSONL 내보내기
          </button>
        </div>

        {error && (
          <div data-testid="export-error"
               style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 4 }}>
            내보내기 오류: {error}
          </div>
        )}

        {result && (
          <div data-testid="export-result"
               style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
            {result.format?.toUpperCase()} 생성 완료 · {result.row_count}행
            <div data-testid="export-file-path"
                 style={{ color: "var(--c-text-3)", wordBreak: "break-all" }}>
              파일: {result.file_path}
            </div>
          </div>
        )}

        <div data-testid="export-footer"
             style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          분석/학습용 데이터입니다. 주문 신호가 아니며 실거래 권한이 아닙니다.
        </div>
      </div>
    </Card>
  );
}

export default DecisionEpisodeExportButton;
