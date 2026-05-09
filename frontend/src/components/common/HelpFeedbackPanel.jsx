import { useMemo, useState } from "react";
import { Card, SectionLabel } from ".";
import { APP_INFO, appVersionLine, feedbackEmail } from "../../config/appInfo";

// PHASE 4: Help / FAQ / Feedback 창.
//
// 사용자가 사용법 / 오류 / 개선사항을 운영자에게 보낼 수 있다. 본 PR은 SMTP /
// 외부 mail API를 사용하지 *않는다* — mailto 링크 또는 클립보드 복사로
// frontend-only draft를 만든다 (CLAUDE.md 절대 원칙 4: frontend에 Secret 저장
// 금지).
//
// 자동 포함 정보: app name / version / current mode / browser user agent /
// current URL / timestamp. **API key / Secret / 계좌번호는 절대 자동 포함하지
// 않으며**, 사용자 입력에서도 입력 금지 안내.

const _CATEGORIES = [
  ["사용법 질문",            "usage_question"],
  ["오류 신고",              "bug_report"],
  ["개선 제안",              "feature_request"],
  ["AI 판단 관련 문의",       "ai_judgment"],
  ["리스크 / 승인 관련 문의",  "risk_approval"],
  ["기타",                   "other"],
];

const _SEVERITIES = [
  ["낮음",   "low"],
  ["보통",   "normal"],
  ["높음",   "high"],
  ["심각 / 운영 중단", "critical"],
];

const _FAQ = [
  {
    q: "이 프로그램은 실제 돈이 나가나요?",
    a: "기본 비활성입니다. SIMULATION / PAPER / VIRTUAL / LIVE_SHADOW에서는 실제 돈이 나가지 않습니다. LIVE_MANUAL_APPROVAL 이상에서 *운영자 명시 옵트인 + 사람 승인*을 거쳐야 실 주문이 발생합니다.",
  },
  {
    q: "SIMULATION / PAPER / SHADOW 차이는?",
    a: "SIMULATION은 가짜 데이터 + Mock Broker. PAPER는 실 시세 + 모의투자(가상 자금). LIVE_SHADOW는 실 계좌 read-only — 주문 X, 추정 기록만 합니다.",
  },
  {
    q: "AI가 직접 주문하나요?",
    a: "아닙니다. AI 에이전트는 *제안*만 만듭니다. 모든 실 주문은 RiskManager → PermissionGate → OrderExecutor 흐름을 거치며, AI는 broker.place_order를 직접 호출하지 않습니다 (CLAUDE.md 절대 원칙 1).",
  },
  {
    q: "긴급중단(Kill Switch)은 무엇인가요?",
    a: "장중 모든 신규 주문을 즉시 차단하는 운영자 토글입니다. 모바일은 OperatorPanel, PC는 리스크 탭에서 접근. LEVEL_1~3 단계가 있으며 자동 청산 / 자동 취소는 *없습니다* (read-only candidate list만 표시 — 운영자 수동 승인).",
  },
  {
    q: "백엔드 연결 대기란?",
    a: "FastAPI backend가 응답하지 않을 때 표시됩니다. 로컬에서는 'cd backend && uvicorn app.main:app --reload'로 실행. GitHub Pages에서는 backend가 없으므로 자동 Demo Mode로 전환됩니다.",
  },
  {
    q: "GitHub Pages 데모와 로컬 실행 차이는?",
    a: "GitHub Pages는 frontend UI만 제공 (backend 없음 → mock / virtual 데이터). 로컬 실행은 FastAPI backend + DB까지 실제로 동작 (Mock Broker 기본). 실거래는 KIS 모의투자 / 실거래 자격증명 + 운영자 옵트인이 필요합니다.",
  },
  {
    q: "모의투자와 실거래 차이는?",
    a: "모의투자(PAPER)는 실 시세를 받지만 가상 자금으로 거래. 실거래(LIVE_*)는 실 계좌 / 실 자금으로 broker.place_order가 발생. v1은 LIVE를 *기본 비활성*으로 두며, 검증 단계(Backtest → Shadow → Paper → Manual → AI Assist)를 모두 통과해야 LIVE_AI_EXECUTION이 가능합니다.",
  },
  {
    q: "실거래는 언제 가능한가요?",
    a: "운영자가 ENABLE_LIVE_TRADING=true + 8개 옵트인 조건(promotion_policy.md)을 모두 통과한 *별도 PR*에서 활성화. v1 시점에는 활성화하지 않습니다.",
  },
];


// 자동 수집 메타 — Secret 아님 (URL / UA / mode / version은 공개 가능).
function _autoCollectMeta(currentMode = "unknown") {
  const safe = (k, v) => `- ${k}: ${v}`;
  return [
    safe("App",     APP_INFO.displayName),
    safe("Version", appVersionLine()),
    safe("Mode",    currentMode),
    safe("URL",     typeof window !== "undefined" ? window.location.href : "(SSR)"),
    safe("UA",      typeof navigator !== "undefined" ? navigator.userAgent : "(SSR)"),
    safe("Time",    new Date().toISOString()),
  ].join("\n");
}


// 본 함수는 사용자 입력에서 *명시적*으로 secret-like 토큰을 *발견*하면 경고
// 메시지 반환. fail-closed (입력 차단)이 아니라 advisory — 사용자가 의도적으로
// secret을 보내려고 시도하면 차단하지 않지만, 적어도 시각적 경고를 표시한다.
// 백엔드 agent_memory의 sanitize와는 정책이 다르다 (여기는 클라이언트 advisory).
const _SECRET_HINTS = [
  /sk-[A-Za-z0-9_-]{20,}/i,
  /sk-ant-[A-Za-z0-9_-]{20,}/i,
  /\b\d{2,4}-\d{4,8}-\d{2,3}\b/,        // 한국 계좌번호
  /\b\d{6}-?[1-4]\d{6}\b/,                // 주민번호
  /(api[_ ]?key|app[_ ]?key|app[_ ]?secret|access[_ ]?token|password)\s*[:=]\s*\S{4,}/i,
];


function _hasSecretShape(text) {
  if (!text) return false;
  return _SECRET_HINTS.some((p) => p.test(text));
}


export function FeedbackModal({ open, onClose, currentMode = "unknown" }) {
  const [category,  setCategory]  = useState("usage_question");
  const [name,      setName]      = useState("");
  const [replyTo,   setReplyTo]   = useState("");
  const [subject,   setSubject]   = useState("");
  const [body,      setBody]      = useState("");
  const [severity,  setSeverity]  = useState("normal");
  const [reproduce, setReproduce] = useState("");
  const [proposal,  setProposal]  = useState("");
  const [copied,    setCopied]    = useState(false);

  const supportEmail = feedbackEmail();
  const meta = useMemo(() => _autoCollectMeta(currentMode),
                       [currentMode, open]); // re-collect on open

  const draft = useMemo(() => {
    const lines = [];
    lines.push(`[${APP_INFO.displayName}] ${subject || "(제목 없음)"}`);
    lines.push("");
    lines.push(`### 분류: ${_CATEGORIES.find(([, v]) => v === category)?.[0] || "기타"}`);
    lines.push(`### 심각도: ${_SEVERITIES.find(([, v]) => v === severity)?.[0] || "보통"}`);
    if (name) lines.push(`### 보낸 사람: ${name}`);
    if (replyTo) lines.push(`### 답장 받을 이메일: ${replyTo}`);
    lines.push("");
    lines.push("### 내용");
    lines.push(body || "(내용 미입력)");
    if (reproduce) {
      lines.push("");
      lines.push("### 재현 방법");
      lines.push(reproduce);
    }
    if (proposal) {
      lines.push("");
      lines.push("### 개선 제안");
      lines.push(proposal);
    }
    lines.push("");
    lines.push("### 자동 수집 정보");
    lines.push(meta);
    lines.push("");
    lines.push("---");
    lines.push("※ 본 메시지는 시스템 개선을 위한 자료로 활용됩니다.");
    lines.push("※ API key / Secret / 계좌번호는 포함하지 않았습니다.");
    return lines.join("\n");
  }, [category, name, replyTo, subject, body, severity, reproduce, proposal, meta]);

  const fieldsContainSecretShape =
    _hasSecretShape(body) || _hasSecretShape(reproduce) ||
    _hasSecretShape(proposal) || _hasSecretShape(subject);

  const mailtoHref = useMemo(() => {
    if (!supportEmail) return null;
    const params = new URLSearchParams({
      subject: `[${APP_INFO.displayName}] ${subject || "문의"}`,
      body: draft,
    });
    return `mailto:${supportEmail}?${params.toString()}`;
  }, [supportEmail, subject, draft]);

  const handleCopy = async () => {
    try {
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(draft);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }
    } catch { /* clipboard not available — silently skip */ }
  };

  if (!open) return null;
  return (
    <>
      <div data-testid="feedback-backdrop"
           onClick={onClose}
           style={{ position: "fixed", inset: 0, zIndex: 200,
                     background: "rgba(15, 23, 42, 0.55)" }} />
      <div data-testid="feedback-modal"
           role="dialog" aria-labelledby="feedback-title"
           style={{
             position: "fixed", top: "50%", left: "50%",
             transform: "translate(-50%, -50%)",
             width: "min(620px, 94vw)",
             maxHeight: "90vh", overflowY: "auto",
             background: "var(--c-surface, #ffffff)",
             border: "1px solid var(--c-border)",
             borderRadius: "var(--r-lg)",
             boxShadow: "0 16px 48px rgba(15, 23, 42, 0.25)",
             zIndex: 201, padding: "20px 22px",
           }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                       marginBottom: 4, alignItems: "baseline" }}>
          <h2 id="feedback-title"
              style={{ margin: 0, fontSize: "var(--fs-xl, 18px)",
                        color: "var(--c-text)", fontWeight: 800 }}>
            ✉ 도움말 / 문의 / 개선 제안
          </h2>
          <button data-testid="feedback-close" onClick={onClose} style={{
            background: "transparent", border: "none", cursor: "pointer",
            color: "var(--c-text-3)", fontSize: 20, lineHeight: 1,
          }}>×</button>
        </div>

        <div data-testid="feedback-secret-warn" style={{
          padding: "8px 10px", marginBottom: 12,
          background: "#fef9c3", border: "1px solid #fbbf24",
          borderRadius: "var(--r-md)",
          fontSize: "var(--fs-sm)", color: "#78350f", lineHeight: 1.6,
        }}>
          <strong>⚠ Secret / 개인정보 입력 금지</strong>
          <br />
          API key / Secret / 계좌번호 / 비밀번호 / 인증 토큰을 입력하지 *마세요*.
          본 양식은 시스템 개선을 위한 자료이며, 입력하시면 그대로 메일에 포함됩니다.
        </div>

        {fieldsContainSecretShape && (
          <div data-testid="feedback-secret-detected" style={{
            padding: "8px 10px", marginBottom: 12,
            background: "#fef2f2", border: "1px solid #ef4444",
            borderRadius: "var(--r-md)",
            fontSize: "var(--fs-sm)", color: "#7f1d1d",
          }}>
            ⚠ <strong>입력에서 Secret 같은 패턴이 감지되었습니다</strong>.
            전송 / 복사하기 전에 본문을 다시 확인하세요. 자동 차단은 하지 않으나
            그대로 보내면 운영자에게 그대로 전달됩니다.
          </div>
        )}

        <_Field label="분류">
          <select data-testid="feedback-category"
                   value={category} onChange={(e) => setCategory(e.target.value)}
                   style={_inputStyle}>
            {_CATEGORIES.map(([label, value]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </_Field>

        <_Field label="이름 / 별칭 (선택)">
          <input data-testid="feedback-name" type="text"
                  value={name} onChange={(e) => setName(e.target.value)}
                  style={_inputStyle} placeholder="예: 김운영 (선택)" />
        </_Field>

        <_Field label="답장 받을 이메일 (선택)">
          <input data-testid="feedback-replyto" type="email"
                  value={replyTo} onChange={(e) => setReplyTo(e.target.value)}
                  style={_inputStyle} placeholder="reply@example.com" />
        </_Field>

        <_Field label="제목 *">
          <input data-testid="feedback-subject" type="text"
                  value={subject} onChange={(e) => setSubject(e.target.value)}
                  style={_inputStyle} placeholder="짧고 명확한 한 줄" />
        </_Field>

        <_Field label="내용 *">
          <textarea data-testid="feedback-body"
                     value={body} onChange={(e) => setBody(e.target.value)}
                     style={{ ..._inputStyle, minHeight: 100,
                               resize: "vertical" }}
                     placeholder="현재 상황 / 발생 시점 / 무엇을 기대했는지" />
        </_Field>

        <_Field label="심각도">
          <select data-testid="feedback-severity"
                   value={severity} onChange={(e) => setSeverity(e.target.value)}
                   style={_inputStyle}>
            {_SEVERITIES.map(([label, value]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </_Field>

        <_Field label="재현 방법 (선택)">
          <textarea data-testid="feedback-reproduce"
                     value={reproduce} onChange={(e) => setReproduce(e.target.value)}
                     style={{ ..._inputStyle, minHeight: 60,
                               resize: "vertical" }}
                     placeholder="1) ... 2) ... 3) ..." />
        </_Field>

        <_Field label="개선 제안 (선택)">
          <textarea data-testid="feedback-proposal"
                     value={proposal} onChange={(e) => setProposal(e.target.value)}
                     style={{ ..._inputStyle, minHeight: 60,
                               resize: "vertical" }}
                     placeholder="이렇게 바뀌면 좋을 것 같습니다" />
        </_Field>

        <details data-testid="feedback-auto-meta-details" style={{ marginBottom: 12 }}>
          <summary style={{ cursor: "pointer", fontSize: "var(--fs-sm)",
                             color: "var(--c-text-2)" }}>
            자동 수집 정보 미리보기 (Secret 미포함)
          </summary>
          <pre style={{
            margin: 6, padding: "8px 10px",
            background: "var(--c-surface-2, #f1f5f9)",
            border: "1px solid var(--c-border)",
            borderRadius: "var(--r-md)",
            fontSize: "var(--fs-xs)",
            color: "var(--c-text-2)",
            whiteSpace: "pre-wrap",
          }}>{meta}</pre>
        </details>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end",
                       flexWrap: "wrap" }}>
          <button data-testid="feedback-copy"
                  onClick={handleCopy}
                  style={{
            padding: "6px 14px",
            background: copied ? "#22c55e" : "var(--c-surface-2, #f1f5f9)",
            border: "1px solid var(--c-border)",
            borderRadius: "var(--r-md)",
            cursor: "pointer",
            color: copied ? "#ffffff" : "var(--c-text)",
            fontSize: "var(--fs-sm)",
            fontFamily: "inherit",
          }}>
            {copied ? "✓ 복사됨" : "📋 클립보드 복사"}
          </button>
          {mailtoHref ? (
            <a data-testid="feedback-mailto"
               href={mailtoHref}
               style={{
              padding: "6px 14px",
              background: "var(--c-info)",
              border: "1px solid var(--c-info)",
              borderRadius: "var(--r-md)",
              cursor: "pointer",
              color: "#ffffff",
              fontSize: "var(--fs-sm)",
              fontWeight: 700,
              fontFamily: "inherit",
              textDecoration: "none",
            }}>✉ 메일 앱으로 보내기</a>
          ) : (
            <span data-testid="feedback-no-mail-target"
                  style={{
              padding: "6px 14px",
              background: "var(--c-surface-3, #e2e8f0)",
              border: "1px dashed var(--c-border)",
              borderRadius: "var(--r-md)",
              color: "var(--c-text-3)",
              fontSize: "var(--fs-xs)",
            }}>
              VITE_FEEDBACK_EMAIL 미설정 — 클립보드 복사 후 전달
            </span>
          )}
        </div>
      </div>
    </>
  );
}


function _Field({ label, children }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: "var(--fs-xs)",
                     color: "var(--c-text-2)",
                     fontWeight: 600, marginBottom: 3 }}>{label}</div>
      {children}
    </div>
  );
}

const _inputStyle = {
  width: "100%",
  padding: "6px 8px",
  fontSize: "var(--fs-sm)",
  background: "var(--c-surface, #ffffff)",
  border: "1px solid var(--c-border)",
  borderRadius: "var(--r-md)",
  color: "var(--c-text)",
  fontFamily: "inherit",
  boxSizing: "border-box",
};


// FAQ 카드 — 자주 묻는 질문 8건. modal과 별개로 항상 펼쳐서 노출 (Settings).
export function FaqCard() {
  return (
    <Card data-testid="faq-card">
      <SectionLabel>❓ 자주 묻는 질문 (FAQ)</SectionLabel>
      <div style={{ fontSize: "var(--fs-sm)", color: "var(--c-text-2)",
                     marginBottom: 8 }}>
        본 답변은 <strong>시스템 사용 설명</strong>이며 투자 조언이 아닙니다.
      </div>
      <div data-testid="faq-list">
        {_FAQ.map((entry, i) => (
          <details key={i} data-testid={`faq-entry-${i}`} style={{
            padding: "8px 10px", marginBottom: 6,
            background: "var(--c-surface-2, #f8fafc)",
            border: "1px solid var(--c-border)",
            borderRadius: "var(--r-md)",
          }}>
            <summary style={{ cursor: "pointer",
                                fontSize: "var(--fs-sm)",
                                color: "var(--c-text)",
                                fontWeight: 700 }}>
              {entry.q}
            </summary>
            <div style={{ marginTop: 4, fontSize: "var(--fs-sm)",
                           color: "var(--c-text-2)", lineHeight: 1.7 }}>
              {entry.a}
            </div>
          </details>
        ))}
      </div>
    </Card>
  );
}


// HelpFeedbackPanel — Settings에 마운트하는 단일 entry 카드. FaqCard +
// 도움말 / 문의 버튼을 함께 노출.
export function HelpFeedbackPanel({ currentMode = "unknown" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Card data-testid="help-feedback-panel">
        <SectionLabel>✉ 도움말 / 문의 / 개선 제안</SectionLabel>
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ fontSize: "var(--fs-sm)",
                           color: "var(--c-text-2)" }}>
              사용 중 궁금하거나 오류 / 개선사항이 있으면 운영자에게 직접
              메시지를 보낼 수 있습니다.
            </div>
            <div style={{ fontSize: "var(--fs-xs)",
                           color: "var(--c-text-3)", marginTop: 3 }}>
              ⚠ API key / Secret / 계좌번호 / 비밀번호는 입력하지 마세요.
            </div>
          </div>
          <button data-testid="help-feedback-open"
                  onClick={() => setOpen(true)}
                  style={{
            padding: "6px 14px",
            background: "var(--c-info)",
            border: "1px solid var(--c-info)",
            borderRadius: "var(--r-md)",
            cursor: "pointer",
            color: "#ffffff",
            fontSize: "var(--fs-sm)",
            fontWeight: 700,
            fontFamily: "inherit",
          }}>도움말 / 문의 열기</button>
        </div>
      </Card>
      <FeedbackModal open={open} onClose={() => setOpen(false)}
                      currentMode={currentMode} />
    </>
  );
}
