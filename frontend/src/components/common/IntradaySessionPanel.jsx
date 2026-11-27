import { useCallback, useEffect, useState } from "react";

import { backendApi } from "../../services/backend/client";
import "./IntradaySessionPanel.css";

// INTRADAY-FLAT-BY-CLOSE-V1: 시작 버튼 옆 — 오늘 단타 일정(14:30 매수마감 · 15:15 강제청산 ·
//   15:30 마감) + EOD 청산 상태/진행률 + 운용모드/시세·브로커 출처. 모든 판단은 *서버 시계*
//   (GET /api/kis-paper/eod/status) — 프론트 시각으로 매수 가능 여부를 계산하지 않는다.
//   주문/설정 변경 버튼 0개 (운영자 확인 acknowledge 만 — 주문 0건).
export const EOD_POLL_MS = 15_000;

const STATE_LABEL = {
  IDLE: "대기 (매수 가능 시간)",
  BUY_CUTOFF_ACTIVE: "신규매수 마감 — 보유 청산만",
  FLATTENING: "장마감 강제청산 진행 중 (15:15부터 잔여 봇 보유분)",
  WAITING_FOR_FILLS: "청산 주문 체결/최종 대조 대기",
  FLAT_CONFIRMED: "당일 청산 완료 확인 (보유 0)",
  FLATTEN_INCOMPLETE: "청산 미완료 — 운영자 확인 필요",
  BLOCKED: "청산 차단 — 수동 대응 필요",
};

const LIFECYCLE_LABEL = {
  SCHEDULED: "예약됨",
  RECONCILING: "잔고·미체결 대조 중",
  LIQUIDATING: "봇 보유분 청산 중",
  WAITING_FILL: "청산 주문 체결 확인 중",
  COMPLETED: "KIS 잔고 대조로 청산 완료 확인",
  FAILED: "청산 실패 — 운영자 확인 필요",
  UNRESOLVED: "대조 불명 — 운영자 확인 필요",
  BLOCKED: "안전 조건 차단",
};

const CANCEL_LABEL = {
  CANCEL_REQUESTED_AWAITING_CONFIRM: "취소 요청됨·확인 대기",
  CONFIRMED_NO_REMAINING: "잔량 없음 확인",
  CANCEL_FAILED: "취소 실패 — 확인 필요",
  CANCEL_ATTEMPT_FAILED: "취소 시도 실패",
  AWAITING_KIS_VISIBILITY: "증권사 조회 대기",
  DRY_RUN_CANCEL_PLANNED: "점검 모드 — 취소 계획만",
};

const MODE_LABEL = {
  KIS_REALTIME_PAPER_AUTO: "KIS 모의 자동주문 (실시간 시세)",
  KIS_REALTIME_DRYRUN: "KIS 실시간 시세 · 주문 전송 안 함 (점검 모드)",
  KIS_REALTIME_SMOKE_TEST: "KIS 모의 smoke (1종목·1주·1건)",
  VIRTUAL_ONLY: "가상 전용 (KIS 주문 0건)",
};

const stateTone = (s) =>
  s === "FLAT_CONFIRMED" ? "ok"
    : s === "FLATTEN_INCOMPLETE" || s === "BLOCKED" ? "bad"
      : s === "FLATTENING" || s === "WAITING_FOR_FILLS" || s === "BUY_CUTOFF_ACTIVE" ? "warn"
        : "idle";

const hm = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString("ko-KR", {
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Seoul",
    });
  } catch {
    return "—";
  }
};

export function summarizeEodProgress(summary) {
  const t = summary?.totals || {};
  const syms = Object.values(summary?.symbols || {});
  return {
    symbols: syms.filter((s) => (s.managed_qty || 0) > 0).length,
    heldQty: t.managed_qty ?? 0,
    openQty: (t.open_sell_qty ?? 0) + (t.unconfirmed_eod_qty ?? 0) + (t.program_open_buy_qty ?? 0),
    submitted: t.eod_orders_submitted ?? 0,
    filledQty: t.eod_filled_qty ?? 0,
    unmanaged: t.unmanaged_symbols ?? 0,
  };
}

export function IntradaySessionPanel({ api = backendApi, pollMs = EOD_POLL_MS }) {
  const [st, setSt] = useState(null);
  const [failed, setFailed] = useState(false);
  const [ackBusy, setAckBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.kisPaperEodStatus?.();
      if (!d || typeof d.phase !== "string" || !d.schedule) {
        setFailed(true);
        return;
      }
      setSt(d);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [api]);

  useEffect(() => {
    load();
    const id = setInterval(load, pollMs);
    return () => clearInterval(id);
  }, [load, pollMs]);

  const acknowledge = async () => {
    const td = st?.previous_incident_trading_date;
    if (!td) return;
    const who = window.prompt(
      `${td} 장마감 청산 미완료를 확인했습니까?\nKIS 모의계좌 잔고를 직접 확인한 뒤 이름을 입력하세요.`, "");
    if (!who || !who.trim()) return;
    setAckBusy(true);
    try {
      await api.kisPaperEodAcknowledge({ tradingDate: td, decidedBy: who.trim() });
      await load();
    } finally {
      setAckBusy(false);
    }
  };

  if (failed && !st) {
    return (
      <div className="isp isp-bad" data-testid="intraday-session-panel">
        <div className="isp-title">오늘 단타 일정</div>
        <div className="isp-line" data-testid="isp-failed">
          서버 상태를 불러오지 못했어요 — 매수마감/청산 상태를 확인할 수 없습니다.
        </div>
      </div>
    );
  }
  if (!st) {
    return <div className="isp" data-testid="intraday-session-panel">일정 확인 중…</div>;
  }

  const sch = st.schedule || {};
  const mode = st.paper_auto_mode?.mode || "VIRTUAL_ONLY";
  const sum = st.summary || {};
  const activeFailureCode = st.eod_state === "FLAT_CONFIRMED" && st.last_reconcile_ok
    ? null
    : sum.last_failure_code;
  const prog = summarizeEodProgress(sum);
  const reconciled = !failed && st.last_reconcile_ok && sum.totals?.managed_qty != null;
  const tone = stateTone(st.eod_state);
  const isKisPaper = st.broker_label === "KIS_PAPER" && st.kis_is_paper && !st.enable_live_trading;
  const nonTrading = sch.calendar && sch.calendar !== "TRADING_DAY";
  const cancelRows = Object.entries(sum.buy_cancels || {});

  return (
    <div className={`isp isp-${tone}`} data-testid="intraday-session-panel">
      <div className="isp-head">
        <span className="isp-title">오늘 단타 일정 (KST · 서버 기준)</span>
        <span className={`isp-badge ${isKisPaper ? "isp-badge-paper" : "isp-badge-virtual"}`}
          data-testid="isp-account-badge">
          {isKisPaper ? "모의투자 계좌 (실제 돈 0원)" : "가상 · KIS 주문 없음"}
        </span>
      </div>

      {nonTrading ? (
        <div className="isp-line" data-testid="isp-non-trading">
          {sch.calendar === "UNKNOWN"
            ? "휴장일 달력에 없는 날짜 — 신규 매수 차단 (청산만)"
            : "오늘은 휴장일/주말이에요"}
        </div>
      ) : (
        <div className="isp-times" data-testid="isp-times">
          <span>신규매수 마감 <b data-testid="isp-buy-cutoff">{sch.buy_cutoff || "—"}</b></span>
          <span>청산 사전점검 <b data-testid="isp-precheck">{sch.precheck_start || "—"}</b></span>
          <span>강제청산 시작 <b data-testid="isp-flatten-start">{sch.flatten_start || "—"}</b></span>
          <span>정규장 종료 <b data-testid="isp-close">{sch.session_close || "—"}</b></span>
        </div>
      )}

      <div className="isp-line" data-testid="isp-buy-status">
        {st.buy_allowed ? "🟢 지금은 신규 매수 가능" : `⛔ 신규 매수 불가 — ${st.buy_block_reason_message || st.buy_block_reason_code}`}
      </div>

      <div className="isp-line" data-testid="isp-eod-state">
        청산 상태: <b>{STATE_LABEL[st.eod_state] || st.eod_state}</b>
        <span className="isp-code"> ({st.eod_state}{st.eod_reason_code ? ` · ${st.eod_reason_code}` : ""})</span>
      </div>

      {sum.verdict_basis === "DRY_RUN_NO_KIS_ORDERS" && st.eod_state !== "IDLE" && (
        <div className="isp-code" data-testid="isp-dryrun-basis">
          ※ 점검 모드 판정 — KIS 주문을 보내지 않았으므로 실제 청산 결과가 아닙니다.
        </div>
      )}

      {(st.eod_state !== "IDLE" || prog.heldQty > 0) && (
        <div className="isp-grid" data-testid="isp-progress">
          <span>청산 대상 {prog.symbols}종목</span>
          <span>남은 보유 {reconciled ? `${prog.heldQty}주` : "확인 불가"}</span>
          <span>미체결 {reconciled ? `${prog.openQty}주` : "확인 불가"}</span>
          <span>주문 접수 {prog.submitted}건</span>
          <span>체결 {prog.filledQty}주</span>
          <span>제외(수동) {prog.unmanaged}종목</span>
        </div>
      )}

      <div className="isp-meta" data-testid="isp-start-status">
        <span data-testid="isp-kis-conn">
          KIS 모의계좌 설정: {st.kis_credentials_present ? "✅ 연결 정보 있음" : "⚠ 키/계좌 미설정 (%APPDATA%\\Autotrade\\.env)"}
        </span>
        <span data-testid="isp-rehearsal">
          리허설 모드: {st.rehearsal_mode
            ? `켜짐 — 신규 매수는 ${st.rehearsal_symbol} 최대 ${st.rehearsal_max_buy_qty}주, 자동 스캔 매수 0`
            : "꺼짐"}
        </span>
        <span data-testid="isp-loop">
          자동매매: {st.auto_loop_state === "RUNNING" ? "▶ 실행 중"
            : st.auto_loop_state === "EMERGENCY_STOP" ? "🛑 긴급정지"
              : "⏸ 정지 (시작 버튼을 눌러야 신규 매수)"}
        </span>
        <span data-testid="isp-holdings">
          원장상 프로그램 보유 {(st.program_ledger_holdings || []).length}종목 · 미체결 {reconciled ? `${prog.openQty}주` : "확인 불가"}
        </span>
        <span>오류가 있으면 설정 탭의 &apos;로그 보기&apos; 또는 %APPDATA%\Autotrade\logs 를 확인하세요.</span>
      </div>

      <div className="isp-meta" data-testid="isp-meta">
        <span data-testid="isp-eod-lifecycle">장마감 청산: {LIFECYCLE_LABEL[st.lifecycle_state] || STATE_LABEL[st.eod_state] || st.eod_state || "예약됨"}</span>
        <span>운용 모드: <b data-testid="isp-mode">{MODE_LABEL[mode] || mode}</b></span>
        <span>시세: {st.paper_auto_mode?.price_source || "—"} · 브로커: {st.broker_label}</span>
        <span>점검 모드(주문 전송 0): {st.paper_auto_mode?.dry_run ? "예" : "아니오"} · 1주 시험(smoke): {st.paper_auto_mode?.smoke_mode ? "예" : "아니오"}</span>
        <span>마지막 주문번호: {sum.last_broker_order_no || "—"} · 마지막 체결 감지: {hm(sum.last_fill_detected_at)}</span>
        <span>실패 코드: {activeFailureCode || "—"} · 잔고 대조: {hm(st.last_reconcile_at)}{st.last_reconcile_at && !st.last_reconcile_ok ? " (실패)" : ""}</span>
        <span>Guardian 최근 완료: {hm(st.eod_guardian_last_finished_at)}{st.eod_guardian_stalled ? " · 응답 지연" : ""}</span>
        <span>최근 청산 시도: {hm(sum.last_sell_attempt_at)} · 접수 {sum.totals?.eod_orders_submitted ?? "—"}건 · 체결 {sum.totals?.eod_filled_qty ?? "—"}주</span>
        {!st.background_driver_running && !st.eod_guardian_running && (
          <span className="isp-warn" data-testid="isp-driver-off">
            ⚠ 백엔드 실행기가 모두 꺼져 있어 자동 청산이 실행되지 않습니다.
          </span>
        )}
      </div>

      {st.manual_liquidation_required && (
        <div className="isp-incident" data-testid="isp-manual-liquidation">
          🛑 긴급정지 중이라 자동 청산·취소를 하지 않아요. 프로그램 보유
          {(st.program_ledger_holdings || []).length > 0
            ? ` ${(st.program_ledger_holdings || []).length}종목` : ""}이 남아 있어
          KIS 모의투자 앱에서 직접 정리해야 해요.
        </div>
      )}

      {cancelRows.length > 0 && (
        <div className="isp-meta" data-testid="isp-buy-cancels">
          <span>미체결 매수 취소: {cancelRows.map(([, c]) =>
            `${c.symbol || ""}(${CANCEL_LABEL[c.status] || c.status}${c.remaining ? `, 잔량 ${c.remaining}주` : ""})`).join(" · ")}</span>
        </div>
      )}

      <div className="isp-code" data-testid="isp-stop-vs-emergency">
        ※ ⏸ 자동매매 정지 = 신규 매수만 중단(장마감 자동 청산은 계속) · 🛑 긴급정지 LEVEL 1 = 신규 매수 차단(청산·미체결 매수 취소는 계속) · LEVEL 2/3·이전 정책 긴급정지 = 청산 매도·미체결 취소까지 전부 중단(MTS/HTS 직접 조치)
      </div>

      {st.kill_switch && st.kill_switch.active && (
        <div className="isp-meta" data-testid="isp-kill-switch">
          <span>긴급정지 {st.kill_switch.level}{st.kill_switch.legacy_strict ? " (이전 정책)" : ""}: {st.kill_switch.operator_action_ko}</span>
        </div>
      )}

      <div className="isp-code" data-testid="isp-not-guaranteed">
        ※ 창의 X 버튼은 트레이로 숨김(청산 계속) · PC 절전·전원 종료·강제 종료·네트워크 끊김이면 자동 청산이 실행되지 않을 수 있어요 (자동 보장 아님).
      </div>

      {st.previous_incident_trading_date && (
        <div className="isp-incident" data-testid="isp-incident">
          🚨 {st.previous_incident_trading_date} 장마감 청산이 확인되지 않아 신규 매수가 막혀 있어요.
          KIS 모의계좌 잔고를 직접 확인한 뒤 확인 버튼을 누르세요.
          <button type="button" onClick={acknowledge} disabled={ackBusy} data-testid="isp-ack">
            {ackBusy ? "처리 중…" : "확인했음 (매수 차단 해제)"}
          </button>
        </div>
      )}
      {failed && <div className="isp-code">⚠ 최근 갱신 실패 — 마지막 값 표시 중</div>}
    </div>
  );
}

export default IntradaySessionPanel;
