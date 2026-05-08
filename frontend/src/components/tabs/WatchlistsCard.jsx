import { useState } from "react";

import { Btn, Card, Inp, SectionLabel } from "../common";
import { useWatchlists } from "../../store/useWatchlists";


/**
 * 관심종목 관리 카드 (#18)
 *
 * Settings 탭에 노출. Watchlist는 Strategy/Agent의 universe 후보군이며 주문
 * 신호가 아니다 — 카드 상단에 그 의미를 명확히 안내.
 */
export function WatchlistsCard() {
  const {
    watchlists, maxItems, recommendedItems, loading, error,
    create, patch, remove, addItem, removeItem, importCsv,
  } = useWatchlists();

  const [newName, setNewName] = useState("");
  const [createMsg, setCreateMsg] = useState("");

  // 종목 추가 입력은 watchlist별로 별도 상태.
  const [itemSymbol, setItemSymbol] = useState({});
  const [itemMsg,    setItemMsg]    = useState({});

  // CSV import textarea — watchlist별로 별도.
  const [csvText, setCsvText] = useState({});
  const [csvMsg,  setCsvMsg]  = useState({});

  const handleCreate = async () => {
    setCreateMsg("");
    try {
      await create({ name: newName });
      setNewName("");
    } catch (e) {
      setCreateMsg(e.message || "생성에 실패했어요.");
    }
  };

  const handleAddItem = async (id) => {
    setItemMsg((m) => ({ ...m, [id]: "" }));
    const sym = itemSymbol[id] || "";
    try {
      await addItem(id, { symbol: sym });
      setItemSymbol((s) => ({ ...s, [id]: "" }));
    } catch (e) {
      setItemMsg((m) => ({ ...m, [id]: e.message || "추가 실패" }));
    }
  };

  const handleRemoveItem = async (id, itemId) => {
    try {
      await removeItem(id, itemId);
    } catch (e) {
      setItemMsg((m) => ({ ...m, [id]: e.message || "삭제 실패" }));
    }
  };

  const handleActivate = async (id, isActive) => {
    try {
      await patch(id, { is_active: !isActive });
    } catch (e) {
      // active 충돌 같은 사용자 에러도 메시지로만 — 성공 시 별도 표시 없음
      setCreateMsg(e.message || "상태 변경 실패");
    }
  };

  const handleDelete = async (id) => {
    try {
      await remove(id);
    } catch (e) {
      setCreateMsg(e.message || "삭제 실패");
    }
  };

  const handleCsvImport = async (id) => {
    setCsvMsg((m) => ({ ...m, [id]: "" }));
    try {
      const r = await importCsv(id, csvText[id] || "");
      const msg = `완료 — 추가 ${r.added} / 중복 ${r.skipped} / 무효 ${r.invalid} / 총 ${r.total_after_import}`;
      setCsvMsg((m) => ({ ...m, [id]: msg }));
      setCsvText((c) => ({ ...c, [id]: "" }));
    } catch (e) {
      setCsvMsg((m) => ({ ...m, [id]: e.message || "CSV 불러오기 실패" }));
    }
  };

  return (
    <Card>
      <div data-testid="watchlists-card">
      <SectionLabel>📋 관심종목 (Universe)</SectionLabel>

      <div style={{
        fontSize: 11, color: "#94a3b8", lineHeight: 1.6, marginBottom: 10,
        padding: "8px 10px", background: "#0c2035", borderRadius: 4,
        border: "1px solid #1a3a5c",
      }}>
        <div style={{ color: "#7dd3fc", fontWeight: 700, marginBottom: 4 }}>
          ℹ 관심종목은 주문 신호가 아닙니다
        </div>
        Strategy / Agent가 매매 후보군으로 사용하는 universe 필터입니다.
        한 목록당 최대 <b>{maxItems}개</b> (권장 {recommendedItems}개) — 너무 넓으면
        후보군의 의미가 흐려집니다. 등록 자체는 RiskManager / PermissionGate 우회와
        무관합니다.
      </div>

      {error && (
        <div style={{
          fontSize: 11, color: "#fca5a5", marginBottom: 10,
          padding: "6px 8px", background: "#7f1d1d22", borderRadius: 3,
        }}>{error}</div>
      )}

      {/* 새 watchlist 생성 */}
      <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
        <Inp value={newName} onChange={setNewName} placeholder="새 목록 이름 (예: 단타-반도체)" />
        <Btn small onClick={handleCreate} disabled={!newName.trim() || loading}>생성</Btn>
      </div>
      {createMsg && (
        <div data-testid="watchlist-create-msg"
             style={{ fontSize: 10, color: "#fca5a5", marginBottom: 8 }}>
          {createMsg}
        </div>
      )}

      {/* 빈 상태 */}
      {!loading && watchlists.length === 0 && (
        <div data-testid="watchlists-empty"
             style={{
               textAlign: "center", padding: "24px 12px",
               fontSize: 12, color: "#475569", background: "#0c2035",
               borderRadius: 4, border: "1px dashed #1a3a5c",
             }}>
          아직 관심종목 목록이 없습니다.<br />
          위에서 첫 목록을 만들고 종목을 등록하거나 CSV로 가져오세요.
        </div>
      )}

      {/* 목록별 카드 */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {watchlists.map((w) => (
          <div key={w.id}
               data-testid={`watchlist-row-${w.id}`}
               style={{
                 padding: 10, background: "#0c2035",
                 borderRadius: 4, border: `1px solid ${w.is_active ? "#22c55e66" : "#1a3a5c"}`,
               }}>
            <div style={{ display: "flex", alignItems: "center",
                          justifyContent: "space-between", marginBottom: 8 }}>
              <div>
                <span style={{ fontSize: 13, fontWeight: 700, color: "#cbd5e1" }}>
                  {w.name}
                </span>
                <span style={{ fontSize: 10, color: "#475569", marginLeft: 8 }}>
                  {w.item_count} / {maxItems}
                </span>
                {w.is_active && (
                  <span style={{
                    fontSize: 9, marginLeft: 6, padding: "2px 6px",
                    borderRadius: 3, background: "#14532d33", color: "#22c55e",
                    border: "1px solid #22c55e66", fontWeight: 700,
                  }}>활성</span>
                )}
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button onClick={() => handleActivate(w.id, w.is_active)}
                        style={{
                          fontSize: 10, padding: "4px 8px", borderRadius: 3,
                          background: "transparent",
                          border: `1px solid ${w.is_active ? "#22c55e" : "#1a3a5c"}`,
                          color:  w.is_active ? "#22c55e" : "#94a3b8",
                          cursor: "pointer", fontFamily: "inherit",
                        }}>
                  {w.is_active ? "활성 해제" : "활성화"}
                </button>
                <button onClick={() => handleDelete(w.id)}
                        data-testid={`watchlist-delete-${w.id}`}
                        style={{
                          fontSize: 10, padding: "4px 8px", borderRadius: 3,
                          background: "transparent", border: "1px solid #ef444466",
                          color: "#ef4444", cursor: "pointer", fontFamily: "inherit",
                        }}>
                  삭제
                </button>
              </div>
            </div>

            {/* 종목 추가 */}
            <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
              <Inp value={itemSymbol[w.id] || ""}
                   onChange={(v) => setItemSymbol((s) => ({ ...s, [w.id]: v }))}
                   placeholder="종목코드 (예: 005930)" />
              <Btn small onClick={() => handleAddItem(w.id)}
                   disabled={!(itemSymbol[w.id] || "").trim() ||
                             w.item_count >= maxItems}>
                종목 추가
              </Btn>
            </div>
            {itemMsg[w.id] && (
              <div data-testid={`watchlist-item-msg-${w.id}`}
                   style={{ fontSize: 10, color: "#fca5a5", marginBottom: 6 }}>
                {itemMsg[w.id]}
              </div>
            )}

            {/* 종목 list — 컴팩트 */}
            <WatchlistItemList
              items={w.items}
              onRemove={(itemId) => handleRemoveItem(w.id, itemId)}
            />

            {/* CSV import textarea */}
            <details style={{ marginTop: 8 }}>
              <summary style={{ fontSize: 10, color: "#94a3b8", cursor: "pointer" }}>
                CSV로 일괄 추가 ({maxItems - w.item_count}개 추가 가능)
              </summary>
              <div style={{ marginTop: 6 }}>
                <textarea
                  value={csvText[w.id] || ""}
                  onChange={(e) => setCsvText((c) => ({ ...c, [w.id]: e.target.value }))}
                  placeholder={"symbol,name,market,sector,note\n005930,삼성전자,KOSPI,반도체,코어\n"}
                  style={{
                    width: "100%", minHeight: 80,
                    background: "#02101e", color: "#cbd5e1",
                    border: "1px solid #1a3a5c", borderRadius: 3,
                    padding: 8, fontSize: 11, fontFamily: "monospace",
                  }} />
                <div style={{ display: "flex", justifyContent: "space-between",
                              alignItems: "center", marginTop: 6 }}>
                  <div style={{ fontSize: 10, color: "#475569" }}>
                    필수: symbol · 옵션: name / market / sector / note
                  </div>
                  <Btn small onClick={() => handleCsvImport(w.id)}
                       disabled={!(csvText[w.id] || "").trim()}>
                    가져오기
                  </Btn>
                </div>
                {csvMsg[w.id] && (
                  <div data-testid={`watchlist-csv-msg-${w.id}`}
                       style={{
                         fontSize: 10, marginTop: 6,
                         color: csvMsg[w.id].startsWith("완료") ? "#22c55e" : "#fca5a5",
                       }}>
                    {csvMsg[w.id]}
                  </div>
                )}
              </div>
            </details>
          </div>
        ))}
      </div>
      </div>
    </Card>
  );
}


function WatchlistItemList({ items, onRemove }) {
  if (!items || items.length === 0) {
    return (
      <div style={{ fontSize: 11, color: "#475569", padding: "6px 4px" }}>
        아직 종목이 없습니다.
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
      {items.map((it) => (
        <div key={it.id}
             data-testid={`watchlist-item-${it.id}`}
             style={{
               display: "inline-flex", alignItems: "center", gap: 4,
               padding: "3px 6px", borderRadius: 3,
               background: "#02101e", border: "1px solid #1a3a5c",
               fontSize: 11, color: "#cbd5e1", fontFamily: "monospace",
             }}>
          <span>{it.symbol}</span>
          {it.name && <span style={{ color: "#475569", fontSize: 10 }}>· {it.name}</span>}
          <button onClick={() => onRemove(it.id)}
                  aria-label={`${it.symbol} 삭제`}
                  style={{
                    marginLeft: 2, background: "transparent", border: "none",
                    color: "#475569", cursor: "pointer", padding: 0,
                    fontSize: 12, lineHeight: 1, fontFamily: "inherit",
                  }}>×</button>
        </div>
      ))}
    </div>
  );
}
