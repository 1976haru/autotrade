import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { DecisionEpisodeExportButton } from "./DecisionEpisodeExportButton";

function _api(result = { format: "csv", row_count: 42,
                         file_path: "exports/decision_episodes/decision_episodes_20260523_143000.csv",
                         contains_secret: false, is_order_signal: false,
                         is_live_authorization: false }) {
  return { agentExportDecisionEpisodes: vi.fn(async () => ({ result })) };
}

describe("<DecisionEpisodeExportButton>", () => {
  afterEach(cleanup);

  it("CSV / JSONL 버튼 + 안내 문구 표시", () => {
    render(<DecisionEpisodeExportButton apiClient={_api()} />);
    expect(screen.getByTestId("export-csv-btn").textContent).toMatch(/CSV/);
    expect(screen.getByTestId("export-jsonl-btn").textContent).toMatch(/JSONL/);
    const d = screen.getByTestId("export-disclaimer").textContent;
    expect(d).toMatch(/실제 계좌정보와 API key는 포함되지 않습니다/);
    expect(d).toMatch(/주문 신호가 아닙니다/);
    expect(d).toMatch(/CSV는 엑셀/);
  });

  it("CSV 클릭 시 export API 호출 + 파일 경로 표시", async () => {
    const api = _api();
    render(<DecisionEpisodeExportButton apiClient={api} />);
    fireEvent.click(screen.getByTestId("export-csv-btn"));
    await waitFor(() => expect(screen.getByTestId("export-result")).toBeTruthy());
    expect(api.agentExportDecisionEpisodes).toHaveBeenCalledWith({ format: "csv" });
    expect(screen.getByTestId("export-file-path").textContent).toMatch(/decision_episodes_/);
    expect(screen.getByTestId("export-result").textContent).toMatch(/42행/);
  });

  it("JSONL 클릭 시 export API 호출", async () => {
    const api = _api({ format: "jsonl", row_count: 10,
                       file_path: "exports/decision_episodes/x.jsonl",
                       contains_secret: false });
    render(<DecisionEpisodeExportButton apiClient={api} />);
    fireEvent.click(screen.getByTestId("export-jsonl-btn"));
    await waitFor(() => expect(api.agentExportDecisionEpisodes)
      .toHaveBeenCalledWith({ format: "jsonl" }));
  });

  it("실패 시 오류 표시", async () => {
    const api = { agentExportDecisionEpisodes: vi.fn(async () => { throw new Error("boom"); }) };
    render(<DecisionEpisodeExportButton apiClient={api} />);
    fireEvent.click(screen.getByTestId("export-csv-btn"));
    await waitFor(() => expect(screen.getByTestId("export-error")).toBeTruthy());
    expect(screen.getByTestId("export-error").textContent).toMatch(/boom/);
  });

  it("실전/매수/매도/LIVE 버튼 없음 + secret/account 표시 없음", () => {
    const { container } = render(<DecisionEpisodeExportButton apiClient={_api()} />);
    // export 버튼 2개만 존재 (CSV/JSONL).
    expect(container.querySelectorAll("button").length).toBe(2);
    expect(container.querySelectorAll("input").length).toBe(0);
    for (const b of ["실전 전환", "LIVE 활성화", "지금 매수", "지금 매도",
                     "Place Order", "app_secret", "account_no", "api_key"]) {
      expect(container.textContent).not.toContain(b);
    }
  });
});
