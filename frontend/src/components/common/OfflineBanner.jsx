/**
 * 체크리스트 #63: 브라우저 *네트워크 오프라인* 배너.
 *
 * BackendOfflineBanner (214/213)는 "backend가 켜져있지 않다" 또는 "Demo Mode"를
 * 알리고, 본 컴포넌트는 그보다 더 앞단의 *기기 네트워크 단절*(navigator.onLine
 * === false)을 알린다. 두 배너는 의미가 다르므로 공존한다.
 *
 * 절대 원칙:
 *   - 본 컴포넌트는 *display only*. 주문 / approve / cancel / Kill Switch 어떤
 *     액션도 호출하지 않는다.
 *   - 오프라인 시 주문 가능처럼 보이는 위험을 피하려고 *명시적*으로 "주문 /
 *     승인 / 봇 시작이 동작하지 않습니다" 안내를 노출.
 *   - 푸시 알림 부재 — "푸시 알림은 보안 검토 후 별도 제공 예정" 문구로 명시.
 *
 * 동작:
 *   - 마운트 시 navigator.onLine 값으로 초기 상태 결정.
 *   - 'online' / 'offline' window 이벤트로 toggle.
 *   - online 일 땐 아무것도 렌더하지 않음 (배너 자체가 없음).
 */

import { useEffect, useState } from "react";


function _isInitiallyOffline() {
  if (typeof navigator === "undefined") return false;
  // navigator.onLine 미지원 환경은 보수적으로 false (배너 안 보임).
  if (typeof navigator.onLine !== "boolean") return false;
  return navigator.onLine === false;
}


export function OfflineBanner({ testId = "offline-banner" }) {
  const [offline, setOffline] = useState(_isInitiallyOffline);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const handleOnline  = () => setOffline(false);
    const handleOffline = () => setOffline(true);
    window.addEventListener("online",  handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online",  handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  if (!offline) return null;

  return (
    <div data-testid={testId}
         role="status"
         aria-live="polite"
         style={{
           padding: "12px 16px", margin: "10px 12px",
           background: "#fef3c7",
           border: "1px solid #fbbf24",
           borderRadius: "var(--r-lg, 12px)",
           color: "#92400e",
           fontSize: "var(--fs-sm, 13px)",
           lineHeight: 1.55,
           boxShadow: "var(--sh-1, 0 1px 3px rgba(0,0,0,0.04))",
         }}>
      <div data-testid={`${testId}-title`}
           style={{ fontWeight: 700, marginBottom: 4, fontSize: "var(--fs-md, 14px)" }}>
        📡 오프라인 상태입니다
      </div>
      <div data-testid={`${testId}-body`}
           style={{ marginBottom: 6 }}>
        실시간 계좌 / 주문 데이터는 확인할 수 없습니다. 자동매매 제어는
        백엔드 연결 후 사용하세요.
      </div>
      <div data-testid={`${testId}-disabled-hint`}
           style={{
             fontSize: "var(--fs-xs, 11px)",
             color: "#78350f",
             padding: "6px 8px",
             background: "rgba(120, 53, 15, 0.06)",
             border: "1px dashed rgba(120, 53, 15, 0.3)",
             borderRadius: 6,
             marginBottom: 4,
           }}>
        ⚠ 오프라인에서는 <b>주문 / 승인 / 봇 시작 / Kill Switch 토글</b>이
        동작하지 않습니다. 화면에 보이는 카드는 마지막 캐시된 정적 자산만
        반영됩니다.
      </div>
      <div data-testid={`${testId}-push-hint`}
           style={{ fontSize: "var(--fs-xs, 11px)", color: "#78350f" }}>
        ※ 푸시 알림은 보안 검토 후 별도 제공 예정입니다.
      </div>
    </div>
  );
}


// 단위 테스트가 import 가능하도록 별도 export.
export { _isInitiallyOffline };
