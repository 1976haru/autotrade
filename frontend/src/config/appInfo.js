// Agent Trader v1 — 프로그램 정체성 단일 진실.
//
// 본 모듈이 *유일한* 브랜딩 / 버전 정보 출처 — header / footer / VersionBadge /
// release notes / feedback modal / about 문구 모두 본 객체를 import해서 표시.
//
// 버전 정책:
//   - 화면 표시: "에이전트 트레이더 v1" / "Agent Trader v1"
//   - 내부 버전: SEMVER (1.0.0)
//   - v1.0.x: 버그 수정
//   - v1.x.0: 기능 추가
//   - v2.0.0: 구조가 크게 바뀌는 대규모 변경
//
// 버전 단일 진실 (fix/update-banner-stale-release-notes): build-time 에
// `vite.config.js` 가 `package.json::version` 을 `VITE_APP_VERSION` 으로 주입.
// 본 모듈은 그 값을 읽으며, 주입 실패 시 fallback 은 의도적으로 부자연스러운
// "0.0.0-unknown" — 화면에서 스테일 정보가 정상 버전처럼 표시되지 않도록.

function _readBuildTimeVersion() {
  try {
    if (typeof import.meta !== "undefined" && import.meta.env) {
      const v = import.meta.env.VITE_APP_VERSION;
      if (typeof v === "string" && v.trim()) return v.trim();
    }
  } catch {
    // import.meta 미사용 환경 — fallback 으로 진행.
  }
  return "0.0.0-unknown";
}

export const APP_INFO = Object.freeze({
  // 한글 표시명
  nameKo:       "에이전트 트레이더",
  // 영문 표시명
  nameEn:       "Agent Trader",
  // 화면 헤더 / 탭 타이틀에 쓰는 full display name
  displayName:  "에이전트 트레이더 v1",
  displayEn:    "Agent Trader v1",
  // 내부 SEMVER — build-time inject from package.json (단일 진실).
  version:      _readBuildTimeVersion(),
  // 마이너 라벨 — 모달 / 배지에 짧게 노출
  releaseLabel: "v1",
  // 한 줄 소개
  tagline:
    "AI 에이전트가 시장을 분석하고, 사용자는 핵심 판단과 위험만 확인하는 자동매매 관제 시스템",
  // 운영 모드 안내 — Demo / 로컬 / 운영 어디서나 동일하게 노출.
  modeNote:
    "현재는 가상 / 모의 / 관제 중심이며 실거래는 별도 승인 전까지 비활성화됩니다.",
});


// helper — 화면 보조 텍스트용. e.g. "Agent Trader v1.0.0"
export function appVersionLine() {
  return `${APP_INFO.displayEn} v${APP_INFO.version}`;
}


// Help / Feedback 창의 mailto 대상. *공개 가능* 운영 메일 주소만 사용.
// .env.example의 VITE_FEEDBACK_EMAIL 참조 — Secret 아님.
// 미설정 시 빈 문자열 → UI는 mailto 대신 클립보드 복사만 노출.
export function feedbackEmail() {
  if (typeof import.meta === "undefined") return "";
  const v = import.meta.env?.VITE_FEEDBACK_EMAIL;
  return typeof v === "string" ? v.trim() : "";
}
