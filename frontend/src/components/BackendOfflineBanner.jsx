import { useBackendStatus } from "../store/useBackendStatus";
import { isDesktopApp } from "../desktop/backendLauncher";

// 214: GitHub Pages 배포 시엔 실제 백엔드가 없다 (FastAPI는 Pages에서 실행되지
// 않음). VITE_DEMO_MODE=true 빌드 플래그가 켜진 상태에서 backend가 unreachable
// 이면 "백엔드 켜라"는 운영자 메시지가 아니라 "🧪 Demo Mode" 배너를 띄워
// 데모 사용자가 mock 데이터임을 즉시 인지하도록 한다.
//
// 213: 로컬 dev에서는 VITE_DEMO_MODE 가 비어 있어 기존 메시지(uvicorn 실행
// 안내)가 그대로 노출된다. 두 모드는 export 한 헬퍼 isDemoBuild로만 분기 —
// 단위 테스트가 import.meta.env를 직접 모킹하기 까다로워 별도 함수로 추출.
//
// EXE (Tauri) 모드 — sidecar 가 자동 실행되므로 "uvicorn 실행하세요" 안내는
// 사용자가 개발자가 아닌 베타테스터일 때 *오히려 혼란*. EXE 모드에서는 친절한
// "백엔드 자동 실행 중" + 재시도 안내만 노출.
export function isDemoBuild() {
  if (typeof import.meta === "undefined") return false;
  const v = import.meta.env?.VITE_DEMO_MODE;
  return v === "true" || v === true;
}

export function BackendOfflineBanner() {
  const { error, loading } = useBackendStatus();
  if (loading) return null;
  if (!error) return null;

  if (isDemoBuild()) {
    // 234 (UI-006): 새 .ui-demo-banner 토큰으로 시각 통일. wrapper margin은
    // 그대로 유지해 기존 레이아웃과 호환.
    return (
      <div data-testid="demo-mode-banner" style={{ margin: "10px 12px" }}>
        <div className="ui-demo-banner">
          <div className="ui-demo-banner__title">🧪 Demo Mode (GitHub Pages)</div>
          <div className="ui-demo-banner__body">
            이 화면은 <b>UI 데모</b>입니다. 실제 백엔드/브로커가 없어 모든 데이터는 mock·virtual 입니다.
            실거래·실주문은 발생하지 않으며, 일부 카드는 빈 상태로 표시됩니다.
          </div>
          <div className="ui-demo-banner__hint">
            전체 기능을 보려면 로컬에서 backend(uvicorn) + frontend(npm run dev)를 함께 실행하세요.
          </div>
          {/* 220 build tag — auto-update 회로 검증용. 작은 회색 글씨로 유지. */}
          <div data-testid="demo-build-tag"
               style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-5)" }}>
            build · auto-update-220
          </div>
        </div>
      </div>
    );
  }

  // EXE/데스크톱 모드 — sidecar 가 자동 실행되므로 uvicorn 안내 *비노출*.
  // 사용자에게는 "백엔드 자동 실행 중" + 복구 / 로그 안내만 표시.
  if (isDesktopApp()) {
    return (
      <div data-testid="desktop-backend-launching-banner"
           style={{
             padding: "12px 16px", margin: "10px 12px",
             background: "#eff6ff",
             border: "1px solid #bfdbfe",
             borderRadius: "var(--r-lg)",
             color: "#1e3a8a",
             fontSize: "var(--fs-sm)",
             lineHeight: "var(--lh-loose)",
             boxShadow: "var(--sh-1)",
           }}>
        <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4,
                       fontSize: "var(--fs-md)" }}>
          🔄 백엔드 자동 실행 중입니다
        </div>
        <div style={{ color: "var(--c-text-2)", marginBottom: 8 }}>
          앱이 백엔드 sidecar 를 자동으로 시작하고 있습니다. 잠시만 기다려주세요.
        </div>
        <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
                       marginTop: 6 }}>
          30 초가 지나도 연결되지 않으면 앱을 한 번 재시작해주세요.
          그래도 안 되면 시작 → 모든 프로그램 → Agent Trader v1 → 로그 보기.
        </div>
        <div data-testid="desktop-mode-badge"
             style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-4)",
                       marginTop: 6 }}>
          데스크톱 모드 · KIS 모의 · 실거래 OFF
        </div>
      </div>
    );
  }

  // 240 (Light-003): 로컬 dev 환경 — light red surface + 개발자용 안내.
  // 베타테스터/사용자에게는 EXE 모드에서 위 desktop banner 가 대신 노출됨.
  return (
    <div data-testid="backend-offline-banner"
         style={{
           padding: "12px 16px", margin: "10px 12px",
           background: "#fef2f2",
           border: "1px solid #fecaca",
           borderRadius: "var(--r-lg)",
           color: "#7f1d1d",
           fontSize: "var(--fs-sm)",
           lineHeight: "var(--lh-loose)",
           boxShadow: "var(--sh-1)",
         }}>
      <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4,
                     fontSize: "var(--fs-md)" }}>
        ⚠ 백엔드 연결 대기 중입니다
      </div>
      <div style={{ color: "var(--c-text-2)", marginBottom: 8 }}>
        실데이터를 보려면 backend와 frontend를 함께 실행하세요.
      </div>
      <pre style={{
        background: "var(--c-surface-2)",
        border: "1px solid var(--c-border)",
        padding: "8px 10px", borderRadius: "var(--r-md)",
        fontSize: "var(--fs-xs)", color: "var(--c-text)", margin: 0,
        whiteSpace: "pre-wrap", wordBreak: "break-word",
      }}>
{`cd backend
uvicorn app.main:app --reload`}
      </pre>
      <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
                     marginTop: 6 }}>
        실행 후 페이지를 새로고침하세요.
      </div>
    </div>
  );
}
