/**
 * 체크리스트 #63: PWA 설치 안내 배너.
 *
 * 사용자에게 "홈화면에 추가해 빠르게 관제할 수 있습니다" 안내.
 * 'beforeinstallprompt' 이벤트가 발생하면 promptable=true가 되고, 사용자가
 * 버튼을 누르면 prompt.prompt()로 native install dialog를 띄운다.
 *
 * iOS Safari는 'beforeinstallprompt'를 지원하지 않으므로, iOS 사용자에게는
 * 별도 안내 문구를 보여준다 ("공유 → 홈화면에 추가").
 *
 * 절대 원칙:
 *   - 본 컴포넌트는 display + browser install API만 호출. 주문 / approve /
 *     Kill Switch 어떤 액션도 만들지 않는다.
 *   - sessionStorage / localStorage 키로 "닫음" 상태 저장 — 같은 세션 동안
 *     반복 노출하지 않음. Secret 같은 민감 데이터는 저장하지 않는다.
 *   - 푸시 알림 안내 X — 본 컴포넌트는 "설치" 안내만. 푸시는 별도 보안 검토.
 *
 * standalone 모드(이미 설치됨)에서는 노출하지 않는다.
 */

import { useCallback, useEffect, useState } from "react";


const DISMISS_KEY = "autotrade.pwaInstallHintDismissed";


function _isStandalone() {
  if (typeof window === "undefined") return false;
  // iOS Safari
  if (window.navigator && window.navigator.standalone) return true;
  // Chrome / others
  if (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) {
    return true;
  }
  return false;
}


function _isIos() {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent || "";
  // iPad on iPadOS 13+ shows as Mac with touch — include that.
  const ipadOs = /Mac/.test(ua) && typeof navigator.maxTouchPoints === "number"
                 && navigator.maxTouchPoints > 1;
  return /iPad|iPhone|iPod/.test(ua) || ipadOs;
}


function _wasDismissed() {
  if (typeof sessionStorage === "undefined") return false;
  try {
    return sessionStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}


function _markDismissed() {
  if (typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.setItem(DISMISS_KEY, "1");
  } catch {
    /* noop — private mode storage refused */
  }
}


export function PwaInstallHint({ testId = "pwa-install-hint" }) {
  const [installEvent, setInstallEvent] = useState(null);
  const [dismissed,    setDismissed]    = useState(() => _wasDismissed());
  const [standalone,   setStandalone]   = useState(() => _isStandalone());

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const handler = (e) => {
      // chromium은 자동 안내를 막기 위해 e.preventDefault()를 권장.
      try { e.preventDefault(); } catch { /* noop */ }
      setInstallEvent(e);
    };
    const installed = () => setStandalone(true);
    window.addEventListener("beforeinstallprompt", handler);
    window.addEventListener("appinstalled", installed);
    return () => {
      window.removeEventListener("beforeinstallprompt", handler);
      window.removeEventListener("appinstalled", installed);
    };
  }, []);

  const install = useCallback(async () => {
    if (!installEvent) return;
    try {
      await installEvent.prompt();
      // 결과 무시 — 사용자가 dismiss하든 accept하든 본 세션은 종료.
      setInstallEvent(null);
      _markDismissed();
      setDismissed(true);
    } catch {
      // promptable 이벤트가 한 번 소비되면 다시 쓸 수 없는 게 정상.
      setInstallEvent(null);
    }
  }, [installEvent]);

  const close = useCallback(() => {
    _markDismissed();
    setDismissed(true);
  }, []);

  // 이미 설치된 standalone, 또는 사용자가 닫음 → 노출 안 함.
  if (standalone) return null;
  if (dismissed) return null;

  const ios = _isIos();
  const canPrompt = !!installEvent;

  // 안내할 사유가 없는 환경(Chrome desktop without beforeinstallprompt 이벤트,
  // and not iOS)에서는 안내 자체를 생략 — 잡음 줄이기.
  if (!canPrompt && !ios) return null;

  return (
    <div data-testid={testId}
         role="region"
         aria-label="홈화면 설치 안내"
         style={{
           margin: "10px 12px",
           padding: "12px 14px",
           background: "#ecfeff",
           border: "1px solid #67e8f9",
           borderRadius: "var(--r-lg, 12px)",
           color: "#0e7490",
           fontSize: "var(--fs-sm, 13px)",
           lineHeight: 1.55,
           boxShadow: "var(--sh-1, 0 1px 3px rgba(0,0,0,0.04))",
           display: "flex", flexDirection: "column", gap: 6,
         }}>
      <div data-testid={`${testId}-title`}
           style={{ fontWeight: 700, fontSize: "var(--fs-md, 14px)" }}>
        📱 홈화면에 추가하기
      </div>
      <div data-testid={`${testId}-body`}>
        스마트폰 홈화면에 추가해 빠르게 관제할 수 있습니다. 본 PWA는
        *관제 대시보드*이며, 실제 매매 / 주문 기능은 백엔드 연결이 필요합니다.
      </div>

      {canPrompt && (
        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <button
            data-testid={`${testId}-install-btn`}
            type="button"
            onClick={install}
            style={{
              flex: 1, padding: "8px 12px", fontSize: 13, fontWeight: 700,
              borderRadius: 8, border: "none", cursor: "pointer",
              background: "#0ea5e9", color: "#fff",
              fontFamily: "inherit",
            }}>
            ➕ 홈화면에 추가
          </button>
          <button
            data-testid={`${testId}-dismiss-btn`}
            type="button"
            onClick={close}
            style={{
              padding: "8px 12px", fontSize: 12, fontWeight: 700,
              borderRadius: 8, border: "1px solid #67e8f9", cursor: "pointer",
              background: "#ffffff", color: "#0e7490",
              fontFamily: "inherit",
            }}>
            나중에
          </button>
        </div>
      )}

      {!canPrompt && ios && (
        <>
          <div data-testid={`${testId}-ios-hint`}
               style={{ fontSize: 12 }}>
            iOS Safari에서는 하단 <b>공유</b> 버튼 → <b>홈 화면에 추가</b>로
            설치할 수 있습니다.
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
            <button
              data-testid={`${testId}-dismiss-btn`}
              type="button"
              onClick={close}
              style={{
                padding: "8px 12px", fontSize: 12, fontWeight: 700,
                borderRadius: 8, border: "1px solid #67e8f9", cursor: "pointer",
                background: "#ffffff", color: "#0e7490",
                fontFamily: "inherit",
              }}>
              닫기
            </button>
          </div>
        </>
      )}

      <div data-testid={`${testId}-push-hint`}
           style={{ fontSize: 11, color: "#0e7490", opacity: 0.85, marginTop: 2 }}>
        ※ 푸시 알림은 보안 검토 후 별도 제공 예정입니다.
      </div>
    </div>
  );
}


export { _isStandalone, _isIos, _wasDismissed, DISMISS_KEY };
