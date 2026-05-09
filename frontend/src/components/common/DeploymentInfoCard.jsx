import { Card, SectionLabel } from ".";
import { isDemoBuild } from "../BackendOfflineBanner";
import { useBackendStatus } from "../../store/useBackendStatus";

// PHASE: 배포 / 접속 안내 카드.
//
// 운영자에게 "지금 어떤 경로로 접속해서 무엇을 보고 있는지" + 안전 정책을
// 한눈에 보여준다. 사용자가 모바일 / 외부 접속 / Pages demo 중 어느 환경
// 인지 자동 감지하고, 그에 맞는 안내 + 절대 금지 사항을 노출.
//
// 본 카드는 *어떤 자격증명도 노출하지 않는다* — URL hostname / Pages flag /
// backend 응답 여부만 기반으로 분기.


function _detectAccessMode({ isDemo, backendOk, hostname }) {
  if (isDemo) return "pages-demo";
  if (!backendOk) return "offline";
  if (typeof hostname === "string") {
    if (hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1") {
      return "local";
    }
    // Tailscale: 100.x.y.z (CGNAT range 100.64.0.0/10)
    if (/^100\.(6[4-9]|[7-9]\d|1\d\d|2[01]\d|22[0-7])\./.test(hostname)) {
      return "tailscale";
    }
    // RFC1918 LAN: 10/8, 172.16-31, 192.168/16
    if (/^10\./.test(hostname)
        || /^192\.168\./.test(hostname)
        || /^172\.(1[6-9]|2\d|3[01])\./.test(hostname)) {
      return "lan";
    }
  }
  return "external";
}


const _MODE_INFO = {
  "local": {
    color: "#22c55e",
    label: "로컬 (이 PC)",
    desc:  "frontend / backend가 같은 PC에서 동작 중입니다. 가장 안전한 운영 방식입니다.",
    safe:  true,
  },
  "lan": {
    color: "#22c55e",
    label: "LAN (같은 Wi-Fi)",
    desc:  "같은 Wi-Fi의 PC backend로 연결되어 있습니다. 신뢰하는 네트워크에서만 사용하세요.",
    safe:  true,
  },
  "tailscale": {
    color: "#7c3aed",
    label: "Tailscale (사설 메시)",
    desc:  "Tailscale peer-to-peer 암호 터널로 외부에서 접속 중입니다. 권장 외부 접속 방식.",
    safe:  true,
  },
  "external": {
    color: "#ef4444",
    label: "외부 인터넷 (위험)",
    desc:  "외부 IP로 접속 중입니다. 공유기 포트포워딩 / 외부 공개 호스팅은 *금지* — Tailscale로 전환하세요.",
    safe:  false,
  },
  "pages-demo": {
    color: "#fbbf24",
    label: "GitHub Pages Demo",
    desc:  "정적 UI Demo입니다. 실제 backend / 실거래 / 실 자격증명 *없음*. 화면 구조 체험만.",
    safe:  true,
  },
  "offline": {
    color: "#94a3b8",
    label: "백엔드 연결 대기",
    desc:  "backend가 응답하지 않습니다. 로컬: uvicorn 실행. Pages demo는 자동 demo 모드.",
    safe:  true,
  },
};


export function DeploymentInfoCard() {
  const status = useBackendStatus();
  const isDemo = isDemoBuild();
  const hostname = typeof window !== "undefined"
    ? window.location.hostname
    : "";
  const mode = _detectAccessMode({
    isDemo,
    backendOk: !!status.status && !status.error,
    hostname,
  });
  const info = _MODE_INFO[mode];

  return (
    <Card data-testid="deployment-info-card">
      <SectionLabel>🌐 배포 / 접속 안내</SectionLabel>

      <div data-testid="deployment-mode-badge"
           style={{
             display: "inline-block",
             padding: "3px 10px",
             borderRadius: "var(--r-md)",
             background: `${info.color}15`,
             border: `1px solid ${info.color}55`,
             color: info.color,
             fontSize: "var(--fs-sm)",
             fontWeight: 700,
             marginBottom: 8,
           }}>
        {info.label}
      </div>
      <div style={{ fontSize: "var(--fs-sm)", color: "var(--c-text-2)",
                     lineHeight: 1.7, marginBottom: 10 }}>
        {info.desc}
      </div>

      <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
                     fontFamily: "monospace", marginBottom: 10 }}>
        host: {hostname || "(SSR)"} · demo: {String(isDemo)} ·
        backend: {status.status ? "ok" : (status.error ? "offline" : "loading")}
      </div>

      <div style={{ marginTop: 10, padding: "10px 12px",
                     background: "#fef9c3",
                     border: "1px solid #fbbf24",
                     borderRadius: "var(--r-md)" }}>
        <div style={{ fontSize: "var(--fs-sm)",
                       color: "#92400e", fontWeight: 700, marginBottom: 4 }}>
          ⚠ 배포 / 접속 절대 원칙
        </div>
        <ul data-testid="deployment-policy-list"
            style={{ margin: 0, paddingLeft: 18,
                      color: "#78350f",
                      fontSize: "var(--fs-sm)",
                      lineHeight: 1.7 }}>
          <li>공유기 포트포워딩 / 외부 공개 호스팅 *금지*</li>
          <li>외부 접속이 필요하면 Tailscale 등 사설 메시 VPN 사용</li>
          <li>API key / Secret / 계좌번호는 로컬 PC <code>.env</code>에만 저장</li>
          <li>베타테스터에게 운영자 <code>.env</code> 공유 금지 — 각자 자기 자격증명</li>
          <li>실거래 활성화 (LIVE_*)는 별도 옵트인 PR + promotion gate 통과 후</li>
        </ul>
      </div>

      <div style={{ marginTop: 10, fontSize: "var(--fs-xs)",
                     color: "var(--c-text-3)", lineHeight: 1.6 }}>
        <strong>접속 방식 4종</strong>:
        <span style={{ marginLeft: 4 }}>
          Local · LAN · Tailscale · GitHub Pages Demo · (Beta desktop app — 후속)
        </span>
        <br />
        자세한 정책: <code>docs/deployment_strategy.md</code>,{" "}
        <code>docs/mobile_access_guide.md</code>,{" "}
        <code>docs/local_security_policy.md</code>.
      </div>

      <div style={{ marginTop: 8, fontSize: "var(--fs-xs)",
                     color: "var(--c-text-3)", lineHeight: 1.6,
                     paddingTop: 8,
                     borderTop: "1px solid var(--c-border)" }}>
        <strong>업데이트 안내</strong>:
        새 버전이 있으면 *업데이트 안내*를 표시합니다. 베타테스터는 앱에서 알림을
        받을 수 있으며, *자동 업데이트는 후속 기능*입니다 (현재는 GitHub Releases
        수동 다운로드). 자세한 내용: <code>docs/auto_update_plan.md</code>.
      </div>
    </Card>
  );
}


// 단위 테스트용 export — 분기 함수 자체를 검증.
export const _detectAccessModeForTest = _detectAccessMode;
