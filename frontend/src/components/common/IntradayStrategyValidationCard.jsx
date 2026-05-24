/**
 * INTRADAY-DATA-01 — 분봉 데이터 단타 전략 검증 카드 (AISignal 탭, read-only).
 *
 * ORB/VWAP/Momentum/Gap 은 장중 단타 전략이라 *분봉* 데이터로 검증해야 한다(일봉은 0 trade).
 * intraday_data_used / bar_size / PASS·BLOCKED 종목 / total_trades / win_rate / PF /
 * expectancy / WF / Agent 무진입·효과 요약 / verdict 를 표시한다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수 / 매도 / 실전 전환 / 자동 적용 / 승인 버튼 0개 (새로고침/복사만).
 *  - secret/account 원문 표시 0건. input/textarea 0개.
 *  - "분봉 데이터 기준" / "자동 적용 아님" / "실전 승인 아님" / "수익 보장 아님" 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";
import { MarketClosedNotice } from "./MarketClosedNotice";
import { currentMarketPhase } from "../../utils/marketHours";

const _V_COLOR = {
  STRONG_CANDIDATE: "#166534", CAUTIOUS_CANDIDATE: "#a16207",
  RESEARCH_ONLY: "#0369a1", NOT_READY: "#9a3412", BLOCKED: "#b91c1c",
};
const _V_BG = {
  STRONG_CANDIDATE: "#dcfce7", CAUTIOUS_CANDIDATE: "#fef9c3",
  RESEARCH_ONLY: "#e0f2fe", NOT_READY: "#ffedd5", BLOCKED: "#fee2e2",
};

function _fmt(x) {
  return x === null || x === undefined ? "평가불가" : Number(x).toFixed(2);
}

export function IntradayStrategyValidationCard({
  apiClient = backendApi,
  testId = "intraday-strategy-validation-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [dataSource, setDataSource] = useState(null);
  const [finalResult, setFinalResult] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.intradayStrategyValidationLatest !== "function") return;
    try {
      setReport((await apiClient.intradayStrategyValidationLatest()) || null);
      setError("");
    } catch (e) {
      setError(e?.message || String(e));
    }
    if (typeof apiClient.intradayDataSourceStatus === "function") {
      try {
        setDataSource((await apiClient.intradayDataSourceStatus()) || null);
      } catch { /* data-source 상태 실패는 치명 아님 */ }
    }
    if (typeof apiClient.realIntradayFinalResult === "function") {
      try {
        setFinalResult((await apiClient.realIntradayFinalResult()) || null);
      } catch { /* 최종 판정 실패는 치명 아님 */ }
    }
  }, [apiClient]);

  useEffect(() => { refresh(); }, [refresh]);

  const copyReport = useCallback(() => {
    try {
      const text = JSON.stringify(report || {}, null, 2);
      if (navigator?.clipboard?.writeText) {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }
    } catch { /* clipboard 미가용 무시 */ }
  }, [report]);

  const verdict = report?.overall_verdict || null;
  const passSymbols = Array.isArray(report?.pass_symbols) ? report.pass_symbols : [];
  const blockedSymbols = Array.isArray(report?.blocked_symbols) ? report.blocked_symbols : [];
  const noTrade = Array.isArray(report?.agent_no_trade_symbols) ? report.agent_no_trade_symbols : [];
  const nextSteps = Array.isArray(report?.next_steps) ? report.next_steps : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#6366f133">
        <SectionLabel>⏱️ 분봉 단타 전략 검증 (INTRADAY-DATA-01)</SectionLabel>

        <div data-testid="intraday-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          ORB/VWAP/Momentum/Gap 은 장중 단타 전략이라 <b>분봉 데이터 기준</b>으로 검증합니다
          (일봉으로는 진입이 거의 발생하지 않음). <b> 자동 적용 아님 · 실전 승인 아님 ·
          수익 보장 아님.</b> KIS 분봉 시세 API 는 미구현이라 분봉 CSV 입력을 사용합니다.
        </div>

        {/* INSTALL-UX-FIX-01: 장 닫힘 안내 (설치본 혼란 방지). */}
        <MarketClosedNotice phase={currentMarketPhase()} />

        {finalResult && finalResult.user_final_judgement === "BLOCKED_BY_DATA" ? (
          <div data-testid="intraday-no-data-notice" style={{
            padding: "6px 10px", borderRadius: 6, marginBottom: 8,
            background: "#eff6ff", border: "1px solid #bfdbfe", color: "#1e3a8a",
            fontSize: "var(--fs-xs)", lineHeight: 1.5,
          }}>
            ℹ️ <b>실제 분봉 데이터 없음 — 전략 검증 미실행.</b> 설치 오류가 아닙니다.
            분봉 CSV 를 <code>data/market/intraday_ohlcv</code> 에 넣거나 yfinance 수집 후
            다시 실행하면 전략 검증이 수행됩니다.
          </div>
        ) : null}

        {finalResult && finalResult.user_final_judgement ? (
          <div data-testid="intraday-final-judgement" style={{
            padding: "8px 10px", borderRadius: 6, marginBottom: 8,
            background: "var(--c-bg-2)", border: "1px solid var(--c-border)",
          }}>
            <div style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)" }}>
              내 전략 최종 판단:{" "}
              <span data-testid="intraday-user-judgement">{finalResult.user_final_judgement}</span>
            </div>
            <div data-testid="intraday-one-liner" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 2,
            }}>
              현재 실제 분봉 데이터 기준으로, 내 매매기법 + Agent 전략은{" "}
              <b>{finalResult.one_line_conclusion}</b>
            </div>
            <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
              실제 데이터:{" "}
              <span data-testid="intraday-actual-data">{finalResult.actual_data_used ? "예" : "아니오"}</span>
              {" · "}total_trades {finalResult.total_trades ?? 0}
              {" · "}Paper 리허설 권고:{" "}
              <span data-testid="intraday-paper-rec">
                {finalResult.paper_rehearsal_recommended ? "예" : "아니오(아직)"}
              </span>
              {" · 실전 승인 아님 · 수익 보장 아님"}
            </div>
          </div>
        ) : null}

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="intraday-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="intraday-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {dataSource ? (
          <div data-testid="intraday-data-source" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 6,
            padding: "4px 8px", borderRadius: 4, background: "var(--c-bg-2)",
          }}>
            데이터 경로: <code>{dataSource.standard_input_dir}</code> · 입력 모드:{" "}
            <span data-testid="intraday-input-mode">{dataSource.input_mode}</span>
            {" · "}CSV {dataSource.csv_file_count ?? 0}개 · KIS collector:{" "}
            <span data-testid="intraday-kis-status">
              {dataSource.kis_collector?.status || "?"}
            </span>
            {dataSource.kis_collector?.status === "NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION" ? (
              <div data-testid="intraday-kis-note" style={{ color: "#a16207", marginTop: 2 }}>
                ⚠️ KIS 분봉 collector 는 공식 endpoint 확인 전까지 실제 호출하지 않습니다.
                실제 분봉 CSV 를 <code>data/market/intraday_ohlcv</code> 에 넣어 검증하세요.
              </div>
            ) : null}
          </div>
        ) : null}

        {error ? (
          <div data-testid="intraday-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>분봉 검증 결과를 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {report ? (
          <>
            <div data-testid="intraday-verdict" style={{
              padding: "6px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[verdict] || "#f1f5f9", fontWeight: "var(--fw-bold)",
              fontSize: "var(--fs-xs)", color: _V_COLOR[verdict] || "var(--c-text)",
            }}>
              종합 판정: {verdict} · 분봉 trade {report.total_trades ?? 0}건
            </div>

            <div data-testid="intraday-data" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              intraday_data_used:{" "}
              <span data-testid="intraday-used">{report.intraday_data_used ? "예" : "아니오"}</span>
              {" · "}bar_size {report.bar_size_minutes ?? "?"}분 · 종목 {report.symbols_count ?? 0}
              {" · "}PASS {passSymbols.length} · BLOCKED {blockedSymbols.length}
            </div>

            <div data-testid="intraday-metrics" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              win_rate {_fmt(report.win_rate)} · PF {_fmt(report.profit_factor)} ·
              expectancy {_fmt(report.expectancy)} · MDD {_fmt(report.max_drawdown)} ·
              WF {_fmt(report.walk_forward_score)}
            </div>

            <div data-testid="intraday-agent" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
            }}>
              Agent 효과: {report.agent_value_summary} · 무진입 종목{" "}
              <span data-testid="intraday-no-trade">{noTrade.length ? noTrade.join(", ") : "없음"}</span>
            </div>

            {nextSteps.length > 0 ? (
              <div data-testid="intraday-next-steps" style={{
                fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4,
              }}>추천 다음 단계: {nextSteps.join(" · ")}</div>
            ) : null}
          </>
        ) : (
          <div data-testid="intraday-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          }}>표시할 분봉 검증 결과가 없습니다.</div>
        )}

        <div data-testid="intraday-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          본 검증은 분봉 단타 전략 가능성 평가 전용입니다. <b>threshold 추천은 자동 적용되지
          않으며</b>, 실전 전환 승인과 무관하고, 수익을 보장하지 않습니다. 자격정보
          (계좌번호/secret)는 표시되지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default IntradayStrategyValidationCard;
