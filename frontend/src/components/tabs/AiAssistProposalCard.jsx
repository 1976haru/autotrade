import { useEffect, useState } from "react";
import { Card, SectionLabel, Inp } from "../common";
import { backendApi } from "../../services/backend/client";

// 44: AI Assisted Trading.
//
// AI는 매수/매도 *후보*만 만들고, 운영자가 승인해야 broker로 진행되는
// flow를 한 카드로 노출한다. 본 카드는:
//  - AI candidate 입력 (운영자가 AI 분석 결과를 그대로 수기로 옮길 수도, AI
//    분석 후 자동 채울 수도 있도록 frontend는 입력 필드만 제공).
//  - "승인 대기 등록" 버튼 → POST /api/ai/assist/submit
//  - 응답: NEEDS_APPROVAL이면 "결재 큐에 등록되었습니다" + approval_id.
//          REJECTED면 RiskManager 거부 사유를 그대로 표시.
//          403이면 AI Permission Gate 차단 사유.
//
// 절대 invariant: 본 카드는 broker 호출에 직결되지 않는다 — backendApi.aiAssistSubmit
// 만 호출. 그 endpoint는 RiskManager 사전검사 + PendingApproval 큐만 한다.

const _disabledNotice = (
  "이 카드는 AI 제안만 등록합니다. 실제 주문은 결재 탭에서 사람이 승인한 " +
  "뒤에만 broker로 전송됩니다."
);


function _DecisionBanner({ decision, reasons }) {
  if (!decision) return null;
  const palette = {
    NEEDS_APPROVAL: { bg: "#0c2035", border: "#22c55e66", color: "#22c55e",
                      label: "결재 큐 등록됨 — 운영자 승인 대기" },
    REJECTED:       { bg: "#3b1f25", border: "#ef444466", color: "#fca5a5",
                      label: "RiskManager 거부 — 큐에 등록되지 않음" },
    APPROVED:       { bg: "#0c2035", border: "#22c55e66", color: "#22c55e",
                      label: "RiskManager 승인 (이 모드에선 발생하지 않아야 함)" },
    BLOCKED:        { bg: "#3b1f25", border: "#ef444466", color: "#fca5a5",
                      label: "차단됨" },
  }[decision] || { bg: "#0c2035", border: "#94a3b855", color: "#94a3b8",
                   label: decision };
  return (
    <div data-testid="ai-assist-decision-banner"
         style={{ marginTop: 8, padding: "8px 10px", background: palette.bg,
                  border: `1px solid ${palette.border}`, borderRadius: 4,
                  fontSize: 11 }}>
      <div style={{ color: palette.color, fontWeight: 700, marginBottom: 4 }}>
        {palette.label}
      </div>
      {reasons && reasons.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 18, color: "#94a3b8" }}>
          {reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
    </div>
  );
}


export function AiAssistProposalCard({ defaultSymbol = "" }) {
  const [symbol,             setSymbol]            = useState(defaultSymbol);
  const [side,               setSide]              = useState("BUY");
  const [quantity,           setQuantity]          = useState("1");
  const [confidence,         setConfidence]        = useState("60");
  const [supportingReasons,  setSupportingReasons] = useState("");
  const [opposingReasons,    setOpposingReasons]   = useState("");
  const [riskNote,           setRiskNote]          = useState("");
  const [targetPrice,        setTargetPrice]       = useState("");
  const [stopPrice,          setStopPrice]         = useState("");

  const [busy,    setBusy]    = useState(false);
  const [error,   setError]   = useState("");
  const [last,    setLast]    = useState(null);

  const submit = async () => {
    if (!symbol.trim()) return;
    setBusy(true); setError(""); setLast(null);
    try {
      const body = {
        symbol: symbol.trim(),
        side,
        quantity: Number(quantity) || 1,
        confidence: Math.max(0, Math.min(100, Number(confidence) || 0)),
        supporting_reasons: supportingReasons.split("\n").map((s) => s.trim()).filter(Boolean),
        opposing_reasons:   opposingReasons.split("\n").map((s) => s.trim()).filter(Boolean),
        risk_note:    riskNote.trim() || null,
        target_price: Number(targetPrice) || null,
        stop_price:   Number(stopPrice)   || null,
      };
      const res = await backendApi.aiAssistSubmit(body);
      setLast(res);
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  };

  return (
    <Card data-testid="ai-assist-proposal-card">
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>🤖 AI 제안 → 사람 승인</SectionLabel>
        <span data-testid="ai-assist-not-real-badge" style={{
          fontSize: 9, fontWeight: 700, color: "#94a3b8",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #94a3b855", background: "#94a3b815",
        }}>
          실제 주문 아님
        </span>
      </div>

      <div style={{ fontSize: 9, color: "#334155", marginBottom: 8, lineHeight: 1.5 }}>
        {_disabledNotice}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6,
                    marginBottom: 6 }}>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>종목</div>
          <Inp value={symbol} onChange={setSymbol} placeholder="005930" />
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>수량</div>
          <Inp value={quantity} onChange={setQuantity} type="number" />
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6,
                    marginBottom: 6 }}>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>방향</div>
          <select
            data-testid="ai-assist-side-select"
            value={side}
            onChange={(e) => setSide(e.target.value)}
            style={{ width: "100%", padding: "6px 8px",
                      background: "#0c2035", color: "#7dd3fc",
                      border: "1px solid #1e3a5c", borderRadius: 4,
                      fontFamily: "inherit", fontSize: 12 }}
          >
            <option value="BUY">매수</option>
            <option value="SELL">매도</option>
          </select>
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>
            confidence (0-100)
          </div>
          <Inp value={confidence} onChange={setConfidence} type="number" />
        </div>
      </div>

      <div style={{ marginBottom: 6 }}>
        <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>
          주요 근거 (한 줄에 하나)
        </div>
        <textarea
          data-testid="ai-assist-supporting-reasons"
          value={supportingReasons}
          onChange={(e) => setSupportingReasons(e.target.value)}
          rows={3}
          placeholder="SMA 골든크로스&#10;5일 이평선 상승 추세"
          style={{ width: "100%", padding: "6px 8px",
                    background: "#0c2035", color: "#e2e8f0",
                    border: "1px solid #1e3a5c", borderRadius: 4,
                    fontFamily: "inherit", fontSize: 11, resize: "vertical" }}
        />
      </div>

      <div style={{ marginBottom: 6 }}>
        <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>
          반대 근거 (한 줄에 하나)
        </div>
        <textarea
          data-testid="ai-assist-opposing-reasons"
          value={opposingReasons}
          onChange={(e) => setOpposingReasons(e.target.value)}
          rows={2}
          placeholder="RSI 78 과열 우려"
          style={{ width: "100%", padding: "6px 8px",
                    background: "#0c2035", color: "#e2e8f0",
                    border: "1px solid #1e3a5c", borderRadius: 4,
                    fontFamily: "inherit", fontSize: 11, resize: "vertical" }}
        />
      </div>

      <div style={{ marginBottom: 6 }}>
        <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>리스크 노트</div>
        <Inp value={riskNote} onChange={setRiskNote}
             placeholder="단기 과열 — 손절 -1.5% 권장" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6,
                    marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>
            권장 청산가 (선택)
          </div>
          <Inp value={targetPrice} onChange={setTargetPrice} type="number" />
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>
            권장 손절가 (선택)
          </div>
          <Inp value={stopPrice} onChange={setStopPrice} type="number" />
        </div>
      </div>

      <button
        type="button"
        data-testid="ai-assist-submit-btn"
        onClick={submit}
        disabled={busy || !symbol.trim()}
        style={{
          width: "100%", padding: "10px 20px", borderRadius: "var(--r-md)",
          border: "none", cursor: (busy || !symbol.trim()) ? "not-allowed" : "pointer",
          background: (busy || !symbol.trim()) ? "var(--c-surface-3)" : "#a78bfa",
          color: (busy || !symbol.trim()) ? "var(--c-text-4)" : "#fff",
          fontWeight: "var(--fw-bold)", fontSize: "var(--fs-base)",
          fontFamily: "inherit", letterSpacing: "0.02em",
        }}
      >
        {busy ? "⟳ 제출 중..." : "📤 승인 대기 등록"}
      </button>

      {error && (
        <div data-testid="ai-assist-error"
             style={{ marginTop: 8, padding: "6px 8px", background: "#3b1f25",
                       border: "1px solid #ef444466", borderRadius: 4,
                       color: "#fca5a5", fontSize: 11 }}>
          {error}
        </div>
      )}

      {last && (
        <_DecisionBanner decision={last.decision} reasons={last.reasons} />
      )}

      {last && last.approval_id && (
        <div data-testid="ai-assist-approval-id"
             style={{ marginTop: 6, padding: "5px 8px",
                       background: "#0c2035", borderRadius: 3,
                       fontSize: 11, color: "#94a3b8" }}>
          결재 ID: <span style={{ color: "#7dd3fc", fontFamily: "monospace" }}>
            #{last.approval_id}
          </span> — 결재 탭에서 승인하세요.
        </div>
      )}
    </Card>
  );
}


// 44: AI Assist 24h 요약 hook + tile (Dashboard 카드용).
export function useAiAssistSummary() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");

  const refresh = async () => {
    setLoading(true); setError("");
    try {
      const data = await backendApi.aiAssistSummary();
      setSummary(data);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setError("");
      try {
        const data = await backendApi.aiAssistSummary();
        if (!cancelled) setSummary(data);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, []);

  return { summary, loading, error, refresh };
}


export function AiAssistSummaryTile({ summary, loading, error, onJumpTab }) {
  if (loading && !summary) {
    return (
      <Card>
        <SectionLabel>AI Assist 제안</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>AI Assist 제안</SectionLabel>
        <div data-testid="ai-assist-summary-error"
             style={{ fontSize: 11, color: "#f87171" }}>{error}</div>
      </Card>
    );
  }
  if (!summary) return null;
  const { pending_count = 0, approved_count_24h = 0,
          rejected_count_24h = 0, total_24h = 0, notice = "" } = summary;
  return (
    <Card data-testid="ai-assist-summary-tile">
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>AI Assist 제안 (24h)</SectionLabel>
        <span style={{ fontSize: 9, fontWeight: 700, color: "#a78bfa",
                       padding: "1px 6px", borderRadius: 3,
                       border: "1px solid #a78bfa55", background: "#a78bfa15" }}>
          AI 제안만 / 사람 승인 후 주문
        </span>
      </div>
      <div style={{ fontSize: 9, color: "#334155", marginBottom: 8, lineHeight: 1.5 }}>
        {notice}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)",
                    gap: 6 }}>
        <button
          type="button"
          data-testid="ai-assist-tile-pending"
          onClick={() => onJumpTab && onJumpTab("approve")}
          style={{ background: "#0c2035", border: "1px solid #1e3a5c",
                    borderRadius: 4, padding: 6, cursor: onJumpTab ? "pointer" : "default",
                    fontFamily: "inherit", color: "inherit" }}
        >
          <div style={{ fontSize: 9, color: "#475569" }}>결재 대기</div>
          <div style={{ fontSize: 13, fontWeight: 700,
                          color: pending_count > 0 ? "#fbbf24" : "#94a3b8" }}>
            {pending_count}
          </div>
        </button>
        <div data-testid="ai-assist-tile-approved-24h"
             style={{ textAlign: "center", padding: 6,
                       background: "#0c2035", borderRadius: 4 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>승인 (24h)</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#22c55e" }}>
            {approved_count_24h}
          </div>
        </div>
        <div data-testid="ai-assist-tile-rejected-24h"
             style={{ textAlign: "center", padding: 6,
                       background: "#0c2035", borderRadius: 4 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>거부 (24h)</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#ef4444" }}>
            {rejected_count_24h}
          </div>
        </div>
        <div data-testid="ai-assist-tile-total-24h"
             style={{ textAlign: "center", padding: 6,
                       background: "#0c2035", borderRadius: 4 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>총 제안</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#7dd3fc" }}>
            {total_24h}
          </div>
        </div>
      </div>
    </Card>
  );
}
