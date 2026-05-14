/**
 * 증권사 API 설정 모듈
 * 새 증권사 추가 시 이 파일만 수정하면 됩니다
 */
export const BROKERS = {
  mirae: {
    id: "mirae",
    name: "미래에셋증권",
    short: "MIRAE",
    color: "#f59e0b",
    baseUrl: "https://openapi.miraeasset.com",
    docsUrl: "https://openapi.miraeasset.com",
    tokenEndpoint: "/oauth/token",
    orderEndpoint: "/order/stock",
    balanceEndpoint: "/account/balance",
    priceEndpoint: "/stock/price",
    fields: [
      { key: "appKey",    label: "App Key",   type: "text" },
      { key: "appSecret", label: "App Secret",type: "password" },
      { key: "accountNo", label: "계좌번호",  type: "text", placeholder: "12345678-01" },  // security-scan: ignore (UI placeholder, not a real account)
    ],
    note: "미래에셋 OpenAPI | REST | 모의투자 지원",
  },

  kis: {
    id: "kis",
    name: "한국투자증권",
    short: "KIS",
    color: "#3b82f6",
    baseUrl: "https://openapi.koreainvestment.com:9443",
    docsUrl: "https://apiportal.koreainvestment.com",
    tokenEndpoint: "/oauth2/tokenP",
    orderEndpoint: "/uapi/domestic-stock/v1/trading/order-cash",
    balanceEndpoint: "/uapi/domestic-stock/v1/trading/inquire-balance",
    priceEndpoint: "/uapi/domestic-stock/v1/quotations/inquire-price",
    fields: [
      { key: "appKey",    label: "App Key",   type: "text" },
      { key: "appSecret", label: "App Secret",type: "password" },
      { key: "accountNo", label: "계좌번호",  type: "text", placeholder: "50123456-01" },  // security-scan: ignore (UI placeholder, not a real account)
    ],
    note: "KIS Developers | REST API | 문서 최충실",
  },

  ebest: {
    id: "ebest",
    name: "이베스트투자증권",
    short: "EBEST",
    color: "#10b981",
    baseUrl: "https://openapi.ebestsec.co.kr",
    docsUrl: "https://openapi.ebestsec.co.kr/apiservice",
    tokenEndpoint: "/oauth/token",
    orderEndpoint: "/stock/order",
    balanceEndpoint: "/account/balance",
    priceEndpoint: "/stock/price",
    fields: [
      { key: "appKey",    label: "App Key",   type: "text" },
      { key: "appSecret", label: "App Secret",type: "password" },
      { key: "accountNo", label: "계좌번호",  type: "text" },
    ],
    note: "xing REST API | 고빈도 트레이딩 최적화",
  },

  nh: {
    id: "nh",
    name: "NH투자증권",
    short: "NH",
    color: "#8b5cf6",
    baseUrl: "https://apigw.nhqv.com",
    docsUrl: "https://apigw.nhqv.com/namuapi",
    tokenEndpoint: "/oauth/token",
    orderEndpoint: "/namuapi/order",
    balanceEndpoint: "/namuapi/balance",
    priceEndpoint: "/namuapi/price",
    fields: [
      { key: "appKey",    label: "App Key",   type: "text" },
      { key: "appSecret", label: "App Secret",type: "password" },
      { key: "accountNo", label: "계좌번호",  type: "text" },
    ],
    note: "나무 OpenAPI | WebSocket 실시간 지원",
  },

  kiwoom: {
    id: "kiwoom",
    name: "키움증권",
    short: "KIWOOM",
    color: "#6b7280",
    disabled: true,
    fields: [],
    note: "⚠ Windows COM(OCX) 방식 → 로컬 서버 필요",
  },
};

export const DEFAULT_BROKER = "mirae";
