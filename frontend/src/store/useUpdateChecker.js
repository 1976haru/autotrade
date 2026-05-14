/**
 * #86 useUpdateChecker — desktop update 확인 hook.
 *
 * 본 PR 시점: **mock 상태만 제공.** Tauri updater 실 API 연결은 후속 PR.
 * 호출 흐름은 미래에 그대로 사용할 수 있게 *현재 / 최신 / 채널 / 마지막 확인
 * 시간 / 단계* 형식으로 노출한다.
 *
 * 안전:
 *   - 본 hook 은 backend / broker 호출 0건.
 *   - localStorage 에 secret 저장 0건 (channel 선택 + 마지막 확인 시간만 저장).
 *   - 자동 *적용* 0건 — 새 버전 발견 후에도 사용자가 "재시작하여 적용" 클릭
 *     해야 installer 실행 (mock 단계에서도 같은 패턴).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { APP_INFO } from "../config/appInfo";


export const UPDATE_STAGE = Object.freeze({
  IDLE:           "idle",            // 초기 상태
  CHECKING:       "checking",        // 서버에 최신 버전 조회 중
  UP_TO_DATE:     "up_to_date",      // 이미 최신
  UPDATE_FOUND:   "update_found",    // 새 버전 발견 — 사용자 다운로드 동의 대기
  DOWNLOADING:    "downloading",     // 다운로드 중 (진행률 표시)
  READY_RESTART:  "ready_restart",   // 다운로드 완료, "재시작하여 적용" 대기
  ERROR:          "error",           // 실패 (네트워크 / 서명 검증 / disk 등)
});

export const UPDATE_CHANNEL = Object.freeze({
  BETA:   "beta",
  STABLE: "stable",
});

const _LAST_CHECKED_KEY = "agent-trader-update-last-checked";
const _CHANNEL_KEY      = "agent-trader-update-channel";


function _readLastChecked() {
  if (typeof window === "undefined") return null;
  try { return window.localStorage.getItem(_LAST_CHECKED_KEY); } catch { return null; }
}

function _writeLastChecked(iso) {
  if (typeof window === "undefined") return;
  try { window.localStorage.setItem(_LAST_CHECKED_KEY, iso); } catch { /* ignore */ }
}

function _readChannel() {
  if (typeof window === "undefined") return UPDATE_CHANNEL.BETA;
  try {
    const v = window.localStorage.getItem(_CHANNEL_KEY);
    return v === UPDATE_CHANNEL.STABLE ? UPDATE_CHANNEL.STABLE : UPDATE_CHANNEL.BETA;
  } catch {
    return UPDATE_CHANNEL.BETA;
  }
}

function _writeChannel(ch) {
  if (typeof window === "undefined") return;
  try { window.localStorage.setItem(_CHANNEL_KEY, ch); } catch { /* ignore */ }
}


/**
 * mock latest version provider — 후속 PR 에서 Tauri updater 의 latest.json
 * fetch 로 대체. 본 PR 의 default 는 *현재 버전과 동일* → "최신 상태" 응답.
 *
 * caller(예: UpdateCheckerCard) 가 prop 으로 다른 mock 응답을 주입할 수 있어
 * 테스트 시나리오를 쉽게 만들 수 있다.
 */
async function _defaultProvider() {
  return {
    version:   APP_INFO.version,
    channel:   UPDATE_CHANNEL.BETA,
    notes:     "mock — 현재 버전과 동일 (실 Tauri updater 연결은 후속 PR)",
    download_url: null,    // 후속 PR 에서 release URL carry
    signature:    null,    // ed25519 서명 — 검증 후 다운로드만 진행
  };
}


/**
 * useUpdateChecker — 데스크톱 업데이트 확인 상태 관리.
 *
 * @param {Object} [opts]
 * @param {Function} [opts.provider] — async () => latestInfo. 미지정 시 mock.
 * @returns {Object} { stage, channel, current, latest, lastChecked, error,
 *                     check, setChannel, simulateDownload, simulateApply }
 */
export function useUpdateChecker(opts) {
  const provider = opts?.provider || _defaultProvider;
  const [stage, setStage]       = useState(UPDATE_STAGE.IDLE);
  const [latest, setLatest]     = useState(null);
  const [error, setError]       = useState("");
  const [channel, setChannelState] = useState(_readChannel());
  const [lastChecked, setLastChecked] = useState(_readLastChecked());
  // 다운로드 진행률 (mock).
  const [progress, setProgress] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  const check = useCallback(async () => {
    setStage(UPDATE_STAGE.CHECKING);
    setError("");
    try {
      const info = await provider({ channel });
      if (!mountedRef.current) return;
      setLatest(info);
      const nowIso = new Date().toISOString();
      _writeLastChecked(nowIso);
      setLastChecked(nowIso);
      // semver 단순 비교 — 같지 않으면 update 발견. 후속 PR 에서 정식 semver
      // 비교 라이브러리로 교체.
      if (info?.version && info.version !== APP_INFO.version) {
        setStage(UPDATE_STAGE.UPDATE_FOUND);
      } else {
        setStage(UPDATE_STAGE.UP_TO_DATE);
      }
    } catch (e) {
      if (!mountedRef.current) return;
      setError(e?.message || "업데이트 확인 실패");
      setStage(UPDATE_STAGE.ERROR);
    }
  }, [provider, channel]);

  const setChannel = useCallback((ch) => {
    const safe = ch === UPDATE_CHANNEL.STABLE
      ? UPDATE_CHANNEL.STABLE
      : UPDATE_CHANNEL.BETA;
    _writeChannel(safe);
    setChannelState(safe);
    // 채널이 바뀌면 stale 상태 reset.
    setStage(UPDATE_STAGE.IDLE);
    setLatest(null);
  }, []);

  // 다운로드 / 적용 시뮬레이션 — 후속 PR 에서 Tauri updater 의 실 동작으로 교체.
  const simulateDownload = useCallback(async () => {
    if (stage !== UPDATE_STAGE.UPDATE_FOUND) return;
    setStage(UPDATE_STAGE.DOWNLOADING);
    setProgress(0);
    // 0 -> 100 까지 200ms 간격으로 mock progress.
    for (let p = 10; p <= 100; p += 10) {
      await new Promise((r) => setTimeout(r, 60));
      if (!mountedRef.current) return;
      setProgress(p);
    }
    if (!mountedRef.current) return;
    setStage(UPDATE_STAGE.READY_RESTART);
  }, [stage]);

  const simulateApply = useCallback(() => {
    if (stage !== UPDATE_STAGE.READY_RESTART) return;
    // 실 동작에서는 Tauri 의 `process.relaunch()` / updater installer launch.
    // mock 단계: stage 만 reset (사용자에게 "정상적으로 재시작될 예정" 메시지).
    setStage(UPDATE_STAGE.IDLE);
    setLatest(null);
    setProgress(0);
  }, [stage]);

  return {
    stage,
    channel,
    current:     APP_INFO.version,
    latest,
    lastChecked,
    error,
    progress,
    check,
    setChannel,
    simulateDownload,
    simulateApply,
  };
}


// ---- 테스트 helper — 운영 코드에서는 호출하지 않는다.
export function _resetUpdateCheckerStorageForTests() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(_LAST_CHECKED_KEY);
    window.localStorage.removeItem(_CHANNEL_KEY);
  } catch { /* ignore */ }
}
