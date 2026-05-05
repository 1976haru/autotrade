import { useState } from "react";
import { DEFAULT_BROKER, BROKERS } from "../config/brokers";
import { backendApi } from "../services/backend/client";

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
  };
}
