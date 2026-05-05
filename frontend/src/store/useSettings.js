import { useCallback, useState } from "react";
import { DEFAULT_BROKER, BROKERS } from "../config/brokers";
import { backendApi } from "../services/backend/client";

const OPERATOR_NAME_KEY = "autotrade.operatorName";

function _readOperatorName() {
  try {
    return localStorage.getItem(OPERATOR_NAME_KEY) || "";
  } catch {
    // localStorage may be blocked in some embeds — fall back to "".
    return "";
  }
}

/**
 * API 설정 / 증권사 연결 훅
 */
export function useSettings() {
  const [brokerId,  setBrokerId]  = useState(DEFAULT_BROKER);
  const [tradeMode, setTradeMode] = useState("sim"); // "sim" | "live"
  const [apiKeys,   setApiKeys]   = useState({ appKey: "", appSecret: "", accountNo: "" });
  const [connected, setConnected] = useState(false);
  const [connecting,setConnecting]= useState(false);
  const [connMsg,   setConnMsg]   = useState("");
  const [token,     setToken]     = useState(null);
  // 운영자명: 감사 로그용 decided_by에 미리 채워질 값. 로컬 환경설정이라
  // localStorage에 영속화 — 백엔드는 토글마다 받은 값을 그대로 기록한다.
  const [operatorName, _setOperatorName] = useState(_readOperatorName);

  const setOperatorName = useCallback((value) => {
    _setOperatorName(value);
    try {
      localStorage.setItem(OPERATOR_NAME_KEY, value);
    } catch {
      // 영속화 실패는 사용자 경험에 직접 영향 없음 — 이 세션에서만 유효.
    }
  }, []);

  const broker = BROKERS[brokerId];

  /** API Key 단일 항목 업데이트 */
  const updateKey = (key, value) =>
    setApiKeys((prev) => ({ ...prev, [key]: value }));

  /** 증권사 전환 시 연결 초기화 */
  const switchBroker = (id) => {
    setBrokerId(id);
    setConnected(false);
    setToken(null);
    setConnMsg("");
  };

  /** 모드 전환 */
  const switchMode = (mode) => {
    setTradeMode(mode);
    setConnected(false);
    setToken(null);
    setConnMsg("");
  };

  /**
   * API 연결
   * 프론트는 증권사/AI Secret을 직접 다루지 않는다.
   * 연결 확인은 backend FastAPI 상태 API로만 수행한다.
   */
  const connect = async () => {
    setConnecting(true);
    setConnMsg("");
    try {
      const status = await backendApi.getStatus();
      setToken(null);
      setConnected(true);
      setConnMsg(`✓ 백엔드 연결 성공 · 기본모드 ${status.default_mode}`);
    } catch (error) {
      setConnected(false);
      setConnMsg(`백엔드 연결 실패: ${error.message}`);
    } finally {
      setConnecting(false);
    }
  };

  return {
    brokerId, broker, tradeMode, apiKeys, connected, connecting, connMsg, token,
    switchBroker, switchMode, updateKey, connect,
    operatorName, setOperatorName,
  };
}
