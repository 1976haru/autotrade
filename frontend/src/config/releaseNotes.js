// Release notes — VersionBadge / ReleaseNotesModal에서 사용.
//
// 새 버전 release 시 본 배열의 *맨 앞*에 신규 entry 추가. `appInfo.version`이
// 본 배열의 첫 entry version과 일치해야 한다 (테스트로 lock).
//
// safetyNotes는 *반드시* 포함 — "수익 보장 X / 실거래 미허가 / Paper/Shadow
// 검증 필수" 같은 운영 정책을 매 release마다 명시.

export const RELEASE_NOTES = [
  {
    version: "1.0.0",
    label:   "Agent Trader v1",
    date:    "2026-05-08",
    title:   "에이전트 트레이더 v1 첫 공개",
    highlights: [
      "AI 에이전트 중심 대시보드 — 시장 / 전략 / 리스크를 6개 역할로 분리",
      "Mock / Paper / Virtual / Shadow 기반 자동매매 검증 환경",
      "RiskManager + Approval Queue + Audit Log 기반 다층 안전 구조",
      "GitHub Pages Demo Mode 지원 — backend 없이도 UI 데모 가능",
      "Daily Report Agent + Strategy Researcher + Risk Auditor + Execution " +
        "Recommender + Agent Memory 통합",
      "실거래와 AI 자동실행은 *기본 비활성화* — 운영자 별도 옵트인 절차 필요",
      "배포 / 접속 / 보안 정책 문서화 (Local / LAN / Tailscale / Pages 4 mode)",
      "베타 배포 + 자동 업데이트 단계 계획 — 1단계 수동 다운로드, 2단계 알림, " +
        "3단계 자동 (후속 PR)",
    ],
    safetyNotes: [
      "이 버전은 *실거래 자동매매 허가 버전이 아닙니다*.",
      "실제 주문 전 Paper / Shadow / Manual Approval 단계의 검증이 필요합니다.",
      "AI 에이전트의 판단은 *참고자료*이며, 최종 책임은 사용자에게 있습니다.",
      "API key / Secret / 계좌번호 / 개인정보는 절대 화면이나 git에 입력하지 마세요.",
    ],
  },
];


// 본 모듈에서 *최신* (top entry)을 빠르게 가져오는 helper.
export function latestReleaseNote() {
  return RELEASE_NOTES[0] || null;
}
