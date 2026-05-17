// Local notes — *초기 안내 (welcome)* 와 *릴리스 노트 (release update)* 의
// 두 개념을 별개로 표현.
//
// fix/update-banner-stale-release-notes: 기존 단일 RELEASE_NOTES 배열은
// "첫 공개" 안내 1건만 들고 있었는데, 이게 GitHub Release fetch 실패 시
// *마치 최신 업데이트인 것처럼* 보이는 혼선이 있었다. 본 PR 에서:
//
//   - `WELCOME_NOTES` 배열 — *초기 안내* 만. `kind: "welcome"` 명시.
//     ReleaseNotesModal 이 이를 노출할 때 "초기 안내" 배지 + ack 시
//     localStorage 에 영구 저장 → 같은 안내 재팝업 0건 (welcome 전용
//     storage key 사용으로 ack 누락 가능성도 최소화).
//   - `RELEASE_NOTES` 배열 — *실 릴리스 변경 내역*. 본 PR 시점 배열 비어 있음
//     (배포된 GitHub Release 없음). 새 릴리스가 publish 되면 운영자가
//     docs/release_notes.md 와 함께 본 배열에도 entry 추가.
//
// GitHub Release fetch 실패 시 UpdateBanner 는 *RELEASE_NOTES 와 무관하게*
// "최신 버전 확인 불가" 만 노출 — 본 모듈의 WELCOME 안내가 *최신 업데이트
// 처럼 둔갑하지 않도록* UpdateBanner 는 본 모듈을 import 하지 않는다.

// 초기 안내 — 앱 첫 실행 / 새 버전 첫 접속 시 1회 노출용.
export const WELCOME_NOTES = [
  {
    kind:    "welcome",
    version: "1.0.0",
    label:   "Agent Trader v1",
    date:    "2026-05-08",
    title:   "에이전트 트레이더 v1 · 초기 안내",
    // 본 안내는 *최신 릴리스 변경 내역이 아니라* 첫 사용자 / 베타테스터에게
    // 보여주는 *프로그램 안내* 다. ReleaseNotesModal 이 "초기 안내" 배지 표시.
    isInitialAnnouncement: true,
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

// 실 릴리스 변경 내역 — GitHub Release 와 동기화. 본 PR 시점 배열은
// *의도적으로 비어 있음* (배포된 release 없음, 향후 추가).
export const RELEASE_NOTES = [];


// 최신 *릴리스 노트* — RELEASE_NOTES 만 본다. WELCOME 은 별개.
// 본 PR 시점에는 항상 null (RELEASE_NOTES 비어 있음).
export function latestReleaseNote() {
  return RELEASE_NOTES[0] || null;
}

// 최신 *초기 안내* — WELCOME_NOTES 최상단. ReleaseNotesModal 이 사용.
export function latestWelcomeNote() {
  return WELCOME_NOTES[0] || null;
}
