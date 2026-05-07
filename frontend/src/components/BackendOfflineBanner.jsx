import { useBackendStatus } from "../store/useBackendStatus";

// 214: GitHub Pages 배포 시엔 실제 백엔드가 없다 (FastAPI는 Pages에서 실행되지
// 않음). VITE_DEMO_MODE=true 빌드 플래그가 켜진 상태에서 backend가 unreachable
// 이면 "백엔드 켜라"는 운영자 메시지가 아니라 "🧪 Demo Mode" 배너를 띄워
// 데모 사용자가 mock 데이터임을 즉시 인지하도록 한다.
//
// 213: 로컬 dev에서는 VITE_DEMO_MODE 가 비어 있어 기존 메시지(uvicorn 실행
// 안내)가 그대로 노출된다. 두 모드는 export 한 헬퍼 isDemoBuild로만 분기 —
// 단위 테스트가 import.meta.env를 직접 모킹하기 까다로워 별도 함수로 추출.
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
    return (
      <div
        data-testid="demo-mode-banner"
        style={{
          padding: "10px 14px", margin: "10px 12px",
          background: "#0c2035", border: "1px solid #7dd3fc66",
          borderRadius: 6, color: "#7dd3fc", fontSize: 12, lineHeight: 1.5,
        }}
      >
        <div style={{ fontWeight: 700, marginBottom: 4 }}>
          🧪 Demo Mode (GitHub Pages)
        </div>
        <div style={{ color: "#94a3b8" }}>
          이 화면은 <b>UI 데모</b>입니다. 실제 백엔드/브로커가 없어 모든 데이터는 mock·virtual 입니다.
          실거래·실주문은 발생하지 않으며, 일부 카드는 빈 상태로 표시됩니다.
        </div>
        <div style={{ fontSize: 10, color: "#475569", marginTop: 6 }}>
          전체 기능을 보려면 로컬에서 backend(uvicorn) + frontend(npm run dev)를 함께 실행하세요.
        </div>
      </div>
    );
  }

  return (
    <div
      data-testid="backend-offline-banner"
      style={{
        padding: "10px 14px", margin: "10px 12px",
        background: "#1a0e0e", border: "1px solid #ef444466",
        borderRadius: 6, color: "#fca5a5", fontSize: 12, lineHeight: 1.5,
      }}
    >
      <div style={{ fontWeight: 700, marginBottom: 4 }}>
        ⚠ 백엔드 연결 실패
      </div>
      <div style={{ color: "#94a3b8", marginBottom: 6 }}>
        FastAPI 서버에 연결할 수 없습니다. 데이터가 표시되지 않을 수 있습니다.
      </div>
      <pre style={{
        background: "#0c2035", padding: 6, borderRadius: 4,
        fontSize: 10, color: "#7dd3fc", margin: 0,
        whiteSpace: "pre-wrap", wordBreak: "break-word",
      }}>
{`cd backend
uvicorn app.main:app --reload`}
      </pre>
      <div style={{ fontSize: 10, color: "#475569", marginTop: 6 }}>
        실행 후 페이지를 새로고침하세요. (마지막 오류: {error})
      </div>
    </div>
  );
}
