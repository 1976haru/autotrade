/**
 * #57 / 7-05 — 앱 버전 / 빌드 commit 표시 카드 (Settings 탭, read-only).
 *
 * 사용자가 실행 중인 EXE 가 어느 main commit / 빌드 시각인지 확인할 수 있도록
 * 프론트엔드 build metadata(Vite 주입)를 표시하고, backend sidecar 의
 * build-info(`/api/system/build-info`)도 함께 조회해 frontend ↔ backend commit
 * 일치 여부를 안내한다.
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수 / 매도 / 실거래 시작 / Place Order / ENABLE_* 버튼 0개.
 *  - 입력 form(input/textarea/select) 0개.
 *  - API key / Secret / 계좌번호 원문 표시 0건 (build metadata 만 표시).
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";
import {
  getBuildInfo,
  normalizeBuildInfo,
  getCommitShort,
  formatBuildTime,
  isUnknownBuildInfo,
  commitsMatch,
} from "../../utils/buildInfo";

const POLL_INTERVAL_MS = 0; // build info 는 정적 — 폴링 불필요 (mount 1회).

function _Row({ label, value, testid, tone = "neutral" }) {
  const color = tone === "warn" ? "#92400e" : "var(--c-text-1)";
  return (
    <div
      data-testid={testid}
      style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        padding: "5px 8px", borderRadius: 4, background: "var(--c-surface-2)",
        marginBottom: 4, fontSize: "var(--fs-xs)",
      }}
    >
      <span style={{ color: "var(--c-text-2)" }}>{label}</span>
      <span style={{
        fontWeight: "var(--fw-bold)", color,
        fontFamily: "var(--font-mono, monospace)",
      }}>
        {value}
      </span>
    </div>
  );
}

export function AppVersionCard({
  apiClient = backendApi,
  testId = "app-version-card",
  pollIntervalMs = POLL_INTERVAL_MS,
  // 테스트 주입용 — 미지정 시 Vite 주입값(getBuildInfo) 사용.
  frontendInfo = null,
} = {}) {
  const fe = frontendInfo ? normalizeBuildInfo(frontendInfo) : getBuildInfo();
  const [backend, setBackend] = useState(null);
  const [backendError, setBackendError] = useState(null);

  const refresh = useCallback(async () => {
    if (typeof apiClient.buildInfo !== "function") return;
    try {
      const raw = await apiClient.buildInfo();
      setBackend(normalizeBuildInfo(raw));
      setBackendError(null);
    } catch (err) {
      setBackend(null);
      setBackendError(err?.message || String(err));
    }
  }, [apiClient]);

  useEffect(() => {
    refresh();
    if (!pollIntervalMs || pollIntervalMs <= 0) return undefined;
    const t = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(t);
  }, [refresh, pollIntervalMs]);

  const feUnknown = isUnknownBuildInfo(fe);
  const match = backend ? commitsMatch(fe, backend) : null;

  return (
    <div data-testid={testId}>
    <Card>
      <SectionLabel>🏷️ 앱 버전 / 빌드 정보</SectionLabel>

      <div
        data-testid="app-version-intro"
        style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8 }}
      >
        현재 실행 중인 빌드 정보입니다. 자격정보(API key / Secret / 계좌번호)는
        포함되지 않습니다.
      </div>

      <_Row label="앱 버전" value={fe.version} testid="app-version-version" />
      <_Row label="채널" value={fe.channel} testid="app-version-channel" />
      <_Row label="빌드 commit"
            value={getCommitShort(fe.commit)} testid="app-version-commit" />
      <_Row label="브랜치 / source"
            value={`${fe.branch} · ${fe.source}`} testid="app-version-branch" />
      <_Row label="빌드 시각"
            value={formatBuildTime(fe.build_time)} testid="app-version-build-time" />
      <_Row label="로컬 변경 포함(dirty)"
            value={fe.is_dirty ? "예 (dirty build)" : "아니오"}
            tone={fe.is_dirty ? "warn" : "neutral"}
            testid="app-version-dirty" />

      {fe.is_dirty && (
        <div
          data-testid="app-version-dirty-warning"
          style={{
            padding: "5px 8px", borderRadius: 4, background: "#fef3c7",
            color: "#92400e", fontSize: "var(--fs-xs)", marginBottom: 4,
          }}
        >
          이 빌드는 커밋되지 않은 로컬 변경을 포함할 수 있습니다(dirty build).
        </div>
      )}

      {/* backend sidecar build info — frontend ↔ backend commit 일치 확인. */}
      {backend && (
        <_Row label="Backend sidecar commit"
              value={getCommitShort(backend.commit)}
              testid="app-version-backend-commit" />
      )}
      {backend && match === false && (
        <div
          data-testid="app-version-mismatch-warning"
          style={{
            padding: "5px 8px", borderRadius: 4, background: "#fef2f2",
            color: "#b91c1c", fontSize: "var(--fs-xs)", marginBottom: 4,
          }}
        >
          프론트엔드와 backend sidecar 의 commit 이 다릅니다. 일부만 업데이트된
          빌드일 수 있습니다.
        </div>
      )}
      {backendError && (
        <div
          data-testid="app-version-backend-error"
          style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 4 }}
        >
          Backend 빌드 정보를 확인할 수 없습니다.
        </div>
      )}

      {feUnknown && (
        <div
          data-testid="app-version-unknown-warning"
          style={{
            padding: "5px 8px", borderRadius: 4, background: "#fef3c7",
            color: "#92400e", fontSize: "var(--fs-xs)", marginBottom: 4,
          }}
        >
          빌드 정보를 확인할 수 없습니다(unknown). 정식 빌드가 아닐 수 있습니다.
        </div>
      )}

      <div
        data-testid="app-version-footer"
        style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          lineHeight: 1.6,
        }}
      >
        현재 EXE 가 최신 main 기준인지 확인하려면 위 빌드 commit
        (<code>{getCommitShort(fe.commit)}</code>) 을 GitHub main 의 최신 commit
        과 비교하세요. commit 이 다르면 구버전일 수 있습니다. 이 정보에는
        자격정보가 포함되지 않습니다.
      </div>
    </Card>
    </div>
  );
}

export default AppVersionCard;
