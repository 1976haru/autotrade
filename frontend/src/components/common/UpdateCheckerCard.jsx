import { Card, SectionLabel } from "../common";
import { APP_INFO } from "../../config/appInfo";
import {
  UPDATE_CHANNEL,
  UPDATE_STAGE,
  useUpdateChecker,
} from "../../store/useUpdateChecker";

// #86 UpdateCheckerCard — 데스크톱 업데이트 확인 / 다운로드 / 재시작 UI.
//
// **본 PR 시점: mock 상태 표시.** 실제 Tauri updater 연결은 후속 PR.
// 본 카드는 *주문 흐름에 영향을 주지 않는다* — broker 호출 0건, .env 변경 0건,
// 안전 flag 변경 0건. 사용자가 "재시작하여 적용" 을 *명시적*으로 누르기 전에
// 어떤 자동 적용도 일어나지 않는다.


const _STAGE_LABEL = {
  [UPDATE_STAGE.IDLE]:          "확인 전",
  [UPDATE_STAGE.CHECKING]:      "확인 중…",
  [UPDATE_STAGE.UP_TO_DATE]:    "최신 상태",
  [UPDATE_STAGE.UPDATE_FOUND]:  "새 버전 있음",
  [UPDATE_STAGE.DOWNLOADING]:   "다운로드 중…",
  [UPDATE_STAGE.READY_RESTART]: "재시작하여 적용 대기",
  [UPDATE_STAGE.ERROR]:         "확인 실패",
};

const _STAGE_COLOR = {
  [UPDATE_STAGE.IDLE]:          "#94a3b8",
  [UPDATE_STAGE.CHECKING]:      "#7dd3fc",
  [UPDATE_STAGE.UP_TO_DATE]:    "#22c55e",
  [UPDATE_STAGE.UPDATE_FOUND]:  "#fbbf24",
  [UPDATE_STAGE.DOWNLOADING]:   "#7dd3fc",
  [UPDATE_STAGE.READY_RESTART]: "#22c55e",
  [UPDATE_STAGE.ERROR]:         "#ef4444",
};


function _formatLastChecked(iso) {
  if (!iso) return "(아직 확인 안 함)";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString("ko-KR", { hour12: false });
  } catch {
    return iso;
  }
}


function _Field({ label, value, testid }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between",
                   alignItems: "baseline", padding: "5px 0",
                   borderBottom: "1px solid var(--c-border)",
                   fontSize: "var(--fs-sm)" }}>
      <span style={{ color: "var(--c-text-3)" }}>{label}</span>
      <span data-testid={testid}
            style={{ color: "var(--c-text)", fontWeight: 600,
                      fontFamily: "monospace" }}>
        {value}
      </span>
    </div>
  );
}


/**
 * UpdateCheckerCard.
 *
 * Props (모두 optional — caller 가 직접 hook 결과를 prop drilling 할 수도 있고,
 * 본 카드가 자체적으로 hook 을 사용하게 둘 수도 있음):
 *
 *   - `provider`: async ({ channel }) => latestInfo — 테스트용 mock 주입.
 *   - `data-testid`: root 의 testid override.
 */
export function UpdateCheckerCard({ provider, "data-testid": testId }) {
  const {
    stage, channel, current, latest, lastChecked, error, progress,
    check, setChannel, simulateDownload, simulateApply,
  } = useUpdateChecker({ provider });

  return (
    <Card>
     <div data-testid={testId || "update-checker-card"}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>🔄 업데이트 확인</SectionLabel>
        <span data-testid="update-checker-mock-badge" style={{
          fontSize: "var(--fs-xs)", fontWeight: 700, color: "#94a3b8",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #94a3b855", background: "#94a3b815",
        }}>
          베타 mock · 자동 적용 안 함
        </span>
      </div>

      <div data-testid="update-checker-notice"
           style={{ marginBottom: 8, padding: "6px 8px",
                     background: "var(--c-surface-2, #f1f5f9)",
                     borderRadius: 4,
                     fontSize: "var(--fs-xs)",
                     color: "var(--c-text-3)", lineHeight: 1.6 }}>
        본 카드는 데스크톱 앱 업데이트 *확인* 만 합니다. *적용* 은 사용자가
        "재시작하여 적용" 버튼을 명시적으로 눌러야만 진행됩니다. 서명되지 않은
        업데이트 파일은 자동으로 거부됩니다.
      </div>

      <_Field
        label="현재 버전"
        value={
          <span data-testid="update-checker-current">
            v{current} ({APP_INFO.releaseLabel})
          </span>
        }
      />
      <_Field
        label="최신 버전"
        value={
          <span data-testid="update-checker-latest">
            {latest?.version ? `v${latest.version}` : "—"}
          </span>
        }
      />
      <_Field
        label="채널"
        value={
          <span data-testid="update-checker-channel-current">
            {channel}
          </span>
        }
      />
      <_Field
        label="마지막 확인"
        value={
          <span data-testid="update-checker-last-checked">
            {_formatLastChecked(lastChecked)}
          </span>
        }
      />
      <_Field
        label="상태"
        value={
          <span data-testid="update-checker-stage"
                style={{ color: _STAGE_COLOR[stage] || "var(--c-text)" }}>
            {_STAGE_LABEL[stage] || stage}
          </span>
        }
      />

      {/* 채널 전환 */}
      <div style={{ marginTop: 10, display: "flex", gap: 6,
                     alignItems: "center" }}>
        <span style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          업데이트 채널:
        </span>
        <button
          data-testid="update-checker-channel-beta"
          onClick={() => setChannel(UPDATE_CHANNEL.BETA)}
          style={{
            padding: "3px 10px", fontSize: "var(--fs-xs)",
            borderRadius: 3,
            border: `1px solid ${channel === UPDATE_CHANNEL.BETA ? "#7dd3fc" : "var(--c-border)"}`,
            background: channel === UPDATE_CHANNEL.BETA ? "#7dd3fc22" : "transparent",
            color: channel === UPDATE_CHANNEL.BETA ? "#7dd3fc" : "var(--c-text-2)",
            cursor: "pointer", fontWeight: 600, fontFamily: "inherit",
          }}>
          beta
        </button>
        <button
          data-testid="update-checker-channel-stable"
          onClick={() => setChannel(UPDATE_CHANNEL.STABLE)}
          style={{
            padding: "3px 10px", fontSize: "var(--fs-xs)",
            borderRadius: 3,
            border: `1px solid ${channel === UPDATE_CHANNEL.STABLE ? "#7dd3fc" : "var(--c-border)"}`,
            background: channel === UPDATE_CHANNEL.STABLE ? "#7dd3fc22" : "transparent",
            color: channel === UPDATE_CHANNEL.STABLE ? "#7dd3fc" : "var(--c-text-2)",
            cursor: "pointer", fontWeight: 600, fontFamily: "inherit",
          }}>
          stable
        </button>
      </div>

      {/* 단계별 액션 버튼 */}
      <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
        <button
          data-testid="update-checker-check-button"
          onClick={check}
          disabled={stage === UPDATE_STAGE.CHECKING || stage === UPDATE_STAGE.DOWNLOADING}
          style={{
            padding: "5px 12px", fontSize: "var(--fs-sm)",
            background: "var(--c-info)", color: "#fff",
            border: "1px solid var(--c-info)", borderRadius: 4,
            cursor: stage === UPDATE_STAGE.CHECKING ? "not-allowed" : "pointer",
            fontWeight: 700, fontFamily: "inherit",
            opacity: (stage === UPDATE_STAGE.CHECKING ||
                     stage === UPDATE_STAGE.DOWNLOADING) ? 0.5 : 1,
          }}>
          ↻ 업데이트 확인
        </button>

        {stage === UPDATE_STAGE.UPDATE_FOUND && (
          <button
            data-testid="update-checker-download-button"
            onClick={simulateDownload}
            style={{
              padding: "5px 12px", fontSize: "var(--fs-sm)",
              background: "#fbbf24", color: "#000",
              border: "1px solid #fbbf24", borderRadius: 4,
              cursor: "pointer", fontWeight: 700, fontFamily: "inherit",
            }}>
            ⬇ 새 버전 다운로드
          </button>
        )}

        {stage === UPDATE_STAGE.READY_RESTART && (
          <button
            data-testid="update-checker-apply-button"
            onClick={simulateApply}
            style={{
              padding: "5px 12px", fontSize: "var(--fs-sm)",
              background: "#22c55e", color: "#000",
              border: "1px solid #22c55e", borderRadius: 4,
              cursor: "pointer", fontWeight: 700, fontFamily: "inherit",
            }}>
            ⏻ 재시작하여 적용
          </button>
        )}
      </div>

      {/* 진행률 (다운로드 단계) */}
      {stage === UPDATE_STAGE.DOWNLOADING && (
        <div data-testid="update-checker-progress"
             style={{ marginTop: 10, fontSize: "var(--fs-xs)",
                       color: "var(--c-text-3)" }}>
          다운로드 진행: {progress}%
          <div style={{
            marginTop: 4, height: 4, background: "var(--c-surface-2, #f1f5f9)",
            borderRadius: 2, overflow: "hidden",
          }}>
            <div data-testid="update-checker-progress-bar"
                 style={{
                   width:  `${progress}%`,
                   height: "100%",
                   background: "#7dd3fc",
                   transition: "width 80ms linear",
                 }} />
          </div>
        </div>
      )}

      {/* 새 버전 상세 */}
      {stage === UPDATE_STAGE.UPDATE_FOUND && latest?.notes && (
        <div data-testid="update-checker-notes"
             style={{ marginTop: 10, padding: "6px 8px",
                       background: "#fef9c3",
                       border: "1px solid #fbbf24",
                       borderRadius: 4,
                       fontSize: "var(--fs-xs)", color: "#78350f",
                       lineHeight: 1.6 }}>
          <div style={{ fontWeight: 700, marginBottom: 3 }}>
            v{latest.version} 변경사항
          </div>
          {latest.notes}
        </div>
      )}

      {/* 재시작 안내 */}
      {stage === UPDATE_STAGE.READY_RESTART && (
        <div data-testid="update-checker-restart-notice"
             style={{ marginTop: 10, padding: "6px 8px",
                       background: "#dcfce7",
                       border: "1px solid #22c55e",
                       borderRadius: 4,
                       fontSize: "var(--fs-xs)", color: "#166534",
                       lineHeight: 1.6 }}>
          다운로드가 끝났습니다. "재시작하여 적용" 버튼을 누르면 앱이 종료된 후
          새 버전으로 자동 재시작됩니다 — 입력한 키 / 설정은 유지됩니다.
        </div>
      )}

      {/* 오류 */}
      {stage === UPDATE_STAGE.ERROR && (
        <div data-testid="update-checker-error"
             style={{ marginTop: 10, padding: "6px 8px",
                       background: "#fee2e2",
                       border: "1px solid #ef4444",
                       borderRadius: 4,
                       fontSize: "var(--fs-xs)", color: "#991b1b",
                       lineHeight: 1.6 }}>
          <div style={{ fontWeight: 700, marginBottom: 3 }}>
            ❌ 업데이트 확인 실패
          </div>
          {error || "원인을 알 수 없습니다."}
          <div style={{ marginTop: 4 }}>
            네트워크 / Wi-Fi 연결을 확인하거나, GitHub Releases 페이지에서 최신
            installer 를 직접 받아 설치하세요 (기존 데이터는 유지됨).
          </div>
        </div>
      )}
     </div>
    </Card>
  );
}
