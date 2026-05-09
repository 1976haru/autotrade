import { Btn, Card, SectionLabel } from "../common";
import { FuturesOrderAuditCard } from "./FuturesOrderAuditCard";
import { FuturesMarginRiskCard } from "./FuturesMarginRiskCard";


function GateRow({ label, value, ok }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "5px 0",
                  borderBottom: "1px solid #05121f", fontSize: 11 }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span style={{ color: ok ? "#22c55e" : "#ef4444", fontWeight: 700 }}>{value}</span>
    </div>
  );
}


export function Futures() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* Status banner */}
      <Card accentColor="#ef444455">
        <div style={{ fontSize: 12, fontWeight: 700, color: "#ef4444", marginBottom: 6 }}>
          🪙 선물 거래 비활성화
        </div>
        <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.7 }}>
          선물 모듈은 의도적으로 stub 상태입니다. CLAUDE.md 절대 원칙에 따라 주식 MVP가
          안정화된 후 별도 PR에서 단계적으로 활성화됩니다. 현재 어떤 주문 경로도 broker로
          도달하지 않습니다.
        </div>
      </Card>

      {/* Multi-layer guards */}
      <Card>
        <SectionLabel>다층 안전 가드 (모두 적용 중)</SectionLabel>
        <GateRow label="ENABLE_FUTURES_LIVE_TRADING"   value="false (default)" ok={false} />
        <GateRow label="FuturesRiskManager"            value="모든 주문 REJECTED" ok={false} />
        <GateRow label="MockFuturesBroker.place_order" value="NotImplementedError" ok={false} />
        <GateRow label="외부 모듈 임포트"               value="0 (DI 비연결)"     ok={false} />
        <div style={{ fontSize: 10, color: "#64748b", marginTop: 8, lineHeight: 1.6 }}>
          * 한 층이 풀려도 다른 층이 선물 주문을 막습니다. 활성화는 4단계 모두 명시적
          전환이 필요합니다.
        </div>
      </Card>

      {/* Mock data preview */}
      <Card>
        <SectionLabel>미리보기 (실제 데이터 없음)</SectionLabel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
          <div>
            <div style={{ fontSize: 9, color: "#475569", marginBottom: 3 }}>증거금 사용</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: "#475569" }}>—</div>
          </div>
          <div>
            <div style={{ fontSize: 9, color: "#475569", marginBottom: 3 }}>증거금 가능</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: "#475569" }}>—</div>
          </div>
          <div>
            <div style={{ fontSize: 9, color: "#475569", marginBottom: 3 }}>총 평가</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: "#475569" }}>—</div>
          </div>
          <div>
            <div style={{ fontSize: 9, color: "#475569", marginBottom: 3 }}>예수금</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: "#475569" }}>—</div>
          </div>
        </div>
        <div style={{ color: "#1e3a5c", fontSize: 11, textAlign: "center", padding: 12 }}>
          선물 broker가 연결되면 KOSPI200 / 미니 선물 잔고가 여기 표시됩니다.
        </div>
      </Card>

      <Card>
        <SectionLabel>오픈 포지션</SectionLabel>
        <div style={{ color: "#1e3a5c", fontSize: 12, textAlign: "center", padding: 16 }}>
          포지션 데이터 없음
        </div>
      </Card>

      {/* 194: FuturesOrderAuditLog (169) read-only surface — MockFuturesBroker가
          만든 행만 누적되며 LIVE 호출은 여전히 비활성화되어 있다. */}
      <FuturesOrderAuditCard />

      {/* 48: 마진/레버리지/강제청산 사전 평가 — read-only POST. broker 호출 0건. */}
      <FuturesMarginRiskCard />

      {/* Disabled order form */}
      <Card accentColor="#33415555">
        <SectionLabel>주문 (전체 비활성화)</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569", marginBottom: 10, lineHeight: 1.7 }}>
          선물 주문은 별도 PR에서 추가됩니다. 활성화 전까지 이 화면의 모든 동작은 비활성화
          상태로 유지됩니다.
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <Btn color="#22c55e" disabled small>매수 (개시)</Btn>
          <Btn color="#ef4444" disabled small>매도 (개시)</Btn>
          <Btn color="#7dd3fc" disabled small>청산</Btn>
        </div>
      </Card>

      {/* Activation roadmap */}
      <Card>
        <SectionLabel>활성화 로드맵</SectionLabel>
        <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.9 }}>
          1️⃣ 주식 MVP의 PAPER → LIVE_MANUAL_APPROVAL 단계 안정화<br />
          2️⃣ <code>FuturesRiskManager.evaluate_order</code> 실제 평가 로직 구현 (증거금/등락률/만기)<br />
          3️⃣ KIS 선물 API SHADOW 어댑터 추가 (read-only)<br />
          4️⃣ <code>FUTURES_LIVE</code> 운영자 옵트인 → PAPER 단계 진입<br />
        </div>
      </Card>

      <div style={{ fontSize: 10, color: "#1e3a5c", lineHeight: 1.6, padding: "0 4px" }}>
        ⚠ 선물은 레버리지·강제청산·만기 등 추가 위험이 있어 주식보다 엄격한 한도와
        검증을 적용합니다. 실거래 자금 유입은 운영자가 명시적으로 승인한 시점부터만
        가능합니다.
      </div>
    </div>
  );
}
