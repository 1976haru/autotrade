import { CONFLUENCE_WEIGHTS, CONFLUENCE_THRESHOLD } from "../../config/constants";
import { backendFetch } from "../backend/client";

/**
 * AI 응답에서 합류점수 JSON 파싱
 */
export const parseConfluenceScore = (text) => {
  try {
    const match = text.match(/\{[\s\S]*?"total"[\s\S]*?\}/);
    if (!match) return null;
    const data = JSON.parse(match[0]);
    // 가중 합계 재계산 (AI 계산 오류 방지)
    data.total = Math.round(
      (data.tech  || 0) * CONFLUENCE_WEIGHTS.tech  +
      (data.trend || 0) * CONFLUENCE_WEIGHTS.trend +
      (data.news  || 0) * CONFLUENCE_WEIGHTS.news  +
      (data.flow  || 0) * CONFLUENCE_WEIGHTS.flow
    );
    return data;
  } catch {
    return null;
  }
};

/**
 * 합류점수 기반 진입 가능 여부
 */
export const shouldEnter = (score) =>
  score?.total >= CONFLUENCE_THRESHOLD.enter;

/**
 * Claude AI 에이전트 분석 요청
 * 실제 AI API Key는 backend에만 둔다. 현재 backend AI route는 다음 단계에서 구현한다.
 */
export async function runAgentAnalysis({ ticker, extra, activeStrats, risk, onChunk, onScore }) {
  const payload = { ticker, extra, activeStrats, risk };
  try {
    const data = await backendFetch("/api/ai/analyze", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const text = data.text || "AI 분석 라우트가 아직 연결되지 않았습니다.";
    onChunk?.(text);
    const score = parseConfluenceScore(text);
    if (score) onScore?.(score);
    return text;
  } catch (error) {
    const fallback = `AI 분석은 현재 백엔드 라우트 연결 전입니다. 주문 판단에는 사용하지 마세요. 오류: ${error.message}`;
    onChunk?.(fallback);
    return fallback;
  }
}
