import { useBackendStatus } from "../store/useBackendStatus";

// 213: 백엔드가 죽어 있어도 프론트 전체가 빈 화면이 되지 않도록.
// `/api/status` 한 번 fetch — 실패하면 "백엔드 연결 실패" 배너로 운영자에게
// uvicorn 명령을 안내. Loading 중에는 표시하지 않아 깜빡임을 줄이고, 성공
// 응답이 한 번이라도 오면 영구히 숨긴다.
export function BackendOfflineBanner() {
  const { error, loading } = useBackendStatus();
  if (loading) return null;
  if (!error) return null;
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
