import { useState } from "react";
import { Card, SectionLabel } from ".";
import { APP_INFO } from "../../config/appInfo";

// PHASE 3: A4 1-page beginner user guide.
//
// 본 컴포넌트는 docs/user_guide_a4.md의 *요약*을 modal로 노출. 화면이 변경
// 되어도 문서는 별도 파일이므로 진실의 원천은 docs/user_guide_a4.md가 유지된다.
// modal은 운영자가 빠르게 핵심 7항목을 훑어볼 수 있게 함 — 상세는 docs link.

const _MODES = [
  ["SIMULATION",           "가짜 데이터 + Mock Broker. 학습/체험용"],
  ["PAPER",                "실 시세 + 모의투자 (가상 자금)"],
  ["LIVE_SHADOW",          "실 계좌 read-only. 주문 X, 추정 기록만"],
  ["LIVE_MANUAL_APPROVAL", "사람 승인 필요. 모든 주문이 큐를 거침"],
  ["LIVE_AI_ASSIST",       "AI 후보 + 사람 승인 (v1 핵심 흐름)"],
  ["LIVE_AI_EXECUTION",    "최종 단계. *기본 비활성* — 8개 옵트인 조건"],
];

const _CORE_TABS = [
  ["홈 (대시보드)",  "운용 모드 / Agent 판단 / 손익 / 긴급중단 / 승인 대기"],
  ["에이전트",        "AI 결정 hero / 전략 / 시장 regime"],
  ["승인",            "LIVE_AI_ASSIST 큐 — 결재 / 거부 / 취소"],
  ["리스크",          "RiskManager 정책 / Kill Switch / shadow trade"],
  ["로그",            "OrderAuditLog / 결재 history / Agent decisions"],
  ["설정",            "모드 / 운영자 / 안전 flag / 버전 / 도움말"],
];

const _USE_FLOW = [
  "대시보드에서 현재 상태 확인 (운용 모드 / 백엔드 연결 / 긴급중단)",
  "Agent 판단 확인 — Hero 카드 + 전략 chip",
  "리스크 상태 확인 — Risk Auditor / 긴급정지 이력",
  "승인 대기 항목 확인 (사유 / 사전검사 결과)",
  "필요 시 시작 / 일시정지 / 긴급중단 사용",
  "장 종료 후 Daily Report 확인 (운영·검증·개선 자료)",
];

const _CAUTIONS = [
  "본 프로그램은 *수익 보장 도구가 아닙니다*.",
  "실거래 전 Paper / Shadow / Manual Approval 검증이 필수.",
  "AI 판단은 *참고자료*이며, 최종 책임은 사용자에게 있습니다.",
  "긴급중단 버튼 위치를 반드시 숙지하세요.",
  "API key / Secret / 계좌번호 / 비밀번호를 화면이나 git에 입력하지 마세요.",
];


export function UserGuideModal({ open, onClose, docsLink = "docs/user_guide_a4.md" }) {
  if (!open) return null;
  return (
    <>
      <div
        data-testid="user-guide-backdrop"
        onClick={onClose}
        style={{
          position: "fixed", inset: 0, zIndex: 200,
          background: "rgba(15, 23, 42, 0.55)",
        }}
      />
      <div
        data-testid="user-guide-modal"
        role="dialog"
        aria-labelledby="user-guide-title"
        style={{
          position: "fixed",
          top: "50%", left: "50%",
          transform: "translate(-50%, -50%)",
          width: "min(640px, 94vw)",
          maxHeight: "88vh",
          overflowY: "auto",
          background: "var(--c-surface, #ffffff)",
          border: "1px solid var(--c-border)",
          borderRadius: "var(--r-lg)",
          boxShadow: "0 16px 48px rgba(15, 23, 42, 0.25)",
          zIndex: 201,
          padding: "20px 22px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between",
                       marginBottom: 4, alignItems: "baseline",
                       gap: 8, flexWrap: "wrap" }}>
          <h2 id="user-guide-title"
              style={{ margin: 0, fontSize: "var(--fs-xl, 18px)",
                        color: "var(--c-text)", fontWeight: 800 }}>
            📘 {APP_INFO.displayName} 사용자 가이드
          </h2>
          <span style={{ fontSize: "var(--fs-xs)",
                          color: "var(--c-text-3)" }}>
            A4 1장 / 초보자용
          </span>
        </div>
        <div style={{ marginBottom: 12, fontSize: "var(--fs-xs)",
                       color: "var(--c-text-3)" }}>
          본 가이드는 *시스템 사용 설명*이며 <strong>투자 조언이 아닙니다</strong>.
        </div>

        <_Section title="1. 이 프로그램은 무엇인가?" testId="ug-section-1">
          AI 에이전트가 시장 / 전략 / 리스크를 *분석*하고, 사용자는 결과와 위험을
          *확인*해 시작 / 일시정지 / 긴급중단 / 승인을 결정하는 자동매매 관제
          시스템입니다. <strong>수익을 보장하지 않습니다</strong>.
        </_Section>

        <_Section title="2. 지금 버전에서 가능한 것" testId="ug-section-2">
          <ul style={_listStyle}>
            <li>GitHub Pages Demo 화면 (백엔드 없이도 UI 체험)</li>
            <li>Mock / Virtual / Paper 모드 구조 검증</li>
            <li>Agent 판단 요약 / 리스크 / 승인 / Audit Log 확인</li>
            <li>실거래는 <strong>기본 비활성화</strong></li>
          </ul>
        </_Section>

        <_Section title="3. 사용자가 봐야 할 핵심 화면" testId="ug-section-3">
          <table style={_tableStyle}>
            <tbody>
              {_CORE_TABS.map(([tab, desc]) => (
                <tr key={tab}>
                  <td style={_tdLabel}>{tab}</td>
                  <td style={_tdValue}>{desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </_Section>

        <_Section title="4. 기본 사용 순서" testId="ug-section-4">
          <ol style={{ ..._listStyle, paddingLeft: 22 }}>
            {_USE_FLOW.map((step, i) => <li key={i}>{step}</li>)}
          </ol>
        </_Section>

        <_Section title="5. 가장 중요한 주의사항" testId="ug-section-5"
                  warnBox>
          <ul style={_listStyle}>
            {_CAUTIONS.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </_Section>

        <_Section title="6. 운용 모드 설명" testId="ug-section-6">
          <table style={_tableStyle}>
            <tbody>
              {_MODES.map(([mode, desc]) => (
                <tr key={mode}>
                  <td style={{ ..._tdLabel, fontFamily: "monospace" }}>{mode}</td>
                  <td style={_tdValue}>{desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </_Section>

        <_Section title="7. 문제 발생 시" testId="ug-section-7">
          <table style={_tableStyle}>
            <tbody>
              <tr><td style={_tdLabel}>"백엔드 연결 대기"</td>
                <td style={_tdValue}>로컬: uvicorn 실행. Pages: 자동 demo.</td></tr>
              <tr><td style={_tdLabel}>데이터 지연</td>
                <td style={_tdValue}>collector 재시작. 거래 일시정지.</td></tr>
              <tr><td style={_tdLabel}>승인 실패</td>
                <td style={_tdValue}>RiskManager 재검증 사유 확인 후 재시도.</td></tr>
              <tr><td style={_tdLabel}>긴급중단</td>
                <td style={_tdValue}>OperatorPanel / 리스크 탭 Kill Switch.</td></tr>
              <tr><td style={_tdLabel}>문의 / 개선</td>
                <td style={_tdValue}>설정 탭의 도움말 / 문의 버튼.</td></tr>
            </tbody>
          </table>
        </_Section>

        <div style={{ fontSize: "var(--fs-xs)",
                       color: "var(--c-text-3)",
                       marginTop: 12, paddingTop: 10,
                       borderTop: "1px solid var(--c-border)" }}>
          전체 원본: <code>{docsLink}</code> · {APP_INFO.displayEn} v{APP_INFO.version}
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end",
                       marginTop: 12 }}>
          <button data-testid="user-guide-close"
                  onClick={onClose}
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
          }}>닫기</button>
        </div>
      </div>
    </>
  );
}


function _Section({ title, children, testId, warnBox = false }) {
  return (
    <div data-testid={testId}
         style={{
           marginBottom: 12,
           padding: warnBox ? "10px 12px" : "0",
           background: warnBox ? "#fef9c3" : "transparent",
           border: warnBox ? "1px solid #fbbf24" : "none",
           borderRadius: warnBox ? "var(--r-md)" : 0,
         }}>
      <div style={{
        fontSize: "var(--fs-md, 14px)",
        color: warnBox ? "#92400e" : "var(--c-text)",
        fontWeight: 700, marginBottom: 6,
      }}>{title}</div>
      <div style={{
        fontSize: "var(--fs-sm)",
        color: warnBox ? "#78350f" : "var(--c-text-2)",
        lineHeight: 1.7,
      }}>{children}</div>
    </div>
  );
}


const _listStyle = {
  margin: 0,
  paddingLeft: 20,
};

const _tableStyle = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: "var(--fs-sm)",
};

const _tdLabel = {
  padding: "4px 8px 4px 0",
  color: "var(--c-text)",
  fontWeight: 600,
  whiteSpace: "nowrap",
  verticalAlign: "top",
  width: "30%",
};

const _tdValue = {
  padding: "4px 0",
  color: "var(--c-text-2)",
  verticalAlign: "top",
};


// 카드 형태 — Settings 탭에 마운트해서 "사용자 가이드 보기" 버튼 제공.
export function UserGuideCard() {
  const [open, setOpen] = useState(false);
  return (
    <Card data-testid="user-guide-card">
      <SectionLabel>📘 사용자 가이드</SectionLabel>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: "var(--fs-sm)",
                        color: "var(--c-text-2)" }}>
            처음 사용자도 3분 안에 핵심 사용법 이해 — A4 1장.
          </div>
          <div style={{ fontSize: "var(--fs-xs)",
                        color: "var(--c-text-3)", marginTop: 3 }}>
            본 가이드는 *시스템 사용 설명*이며 투자 조언이 아닙니다.
          </div>
        </div>
        <button data-testid="user-guide-open"
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
        }}>가이드 보기</button>
      </div>
      <UserGuideModal open={open} onClose={() => setOpen(false)} />
    </Card>
  );
}
