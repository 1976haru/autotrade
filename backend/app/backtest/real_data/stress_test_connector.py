"""Stress test 연결부 (3-05) — 본 PR 시점에는 *6 시나리오 구조 stub*.

후속 PR (3-05) 에서 실제 시나리오 데이터 변형 / 결과 측정 로직 추가.
본 PR 에서는 *시나리오 카탈로그 + 결과 dataclass + 운영자 helper* 만 제공.

6 시나리오:
1. CRASH         — 급락 시뮬 (close 5~10% 갭다운).
2. SURGE         — 급등 시뮬 (close 5~10% 갭업).
3. SIDEWAYS      — 횡보 시뮬 (변동성 ↓, mean reversion 강화).
4. SLIPPAGE      — 슬리피지 + 호가 갭 확대.
5. DATA_GAP      — 데이터 누락 (bar 결측 / NaN).
6. EXECUTION_REJECT — 체결 거부 시뮬 (broker reject 비율 ↑).

각 시나리오는 *데이터 변형 / 비용 가중* 만 — broker 호출 / 자동 적용 0건.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건.
- 본 모듈은 *결정론적 시뮬레이션* helper — 자동 주문 / 자동 적용 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StressScenario(StrEnum):
    CRASH            = "CRASH"
    SURGE            = "SURGE"
    SIDEWAYS         = "SIDEWAYS"
    SLIPPAGE         = "SLIPPAGE"
    DATA_GAP         = "DATA_GAP"
    EXECUTION_REJECT = "EXECUTION_REJECT"


class StressVerdict(StrEnum):
    """단일 시나리오 결과 verdict."""
    PASS         = "PASS"          # 시나리오에서도 위험 한도 내 유지.
    WARN         = "WARN"          # 일부 지표 악화 — 운영자 검토 필요.
    FAIL         = "FAIL"          # 한도 breach — 후보 자격 박탈.


@dataclass(frozen=True)
class StressScenarioSpec:
    """시나리오 정의 — 본 PR 시점 spec dataclass 만, 적용 로직은 후속 PR."""

    name:         StressScenario
    description:  str
    notes:        str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":        self.name.value,
            "description": self.description,
            "notes":       self.notes,
        }


@dataclass(frozen=True)
class StressTestResult:
    """단일 (scenario, strategy, symbol) 결과 — 본 PR 에서는 prepared 구조만."""

    scenario:    StressScenario
    verdict:     StressVerdict
    metrics:     dict[str, Any]        # 변형 후 metrics (compute_extended_metrics)
    reasons:     list[str]             = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.value,
            "verdict":  self.verdict.value,
            "metrics":  dict(self.metrics),
            "reasons":  list(self.reasons),
        }


# 6 시나리오 standard catalog — 운영자 / 후속 PR 가 직접 호출.
STRESS_SCENARIO_CATALOG: tuple[StressScenarioSpec, ...] = (
    StressScenarioSpec(
        StressScenario.CRASH,
        "급락 시뮬 (close 5~10% 갭다운).",
        "stop-loss / max_drawdown 임계값 검증.",
    ),
    StressScenarioSpec(
        StressScenario.SURGE,
        "급등 시뮬 (close 5~10% 갭업).",
        "탑승 누락 / 익절 타이밍 검증.",
    ),
    StressScenarioSpec(
        StressScenario.SIDEWAYS,
        "횡보 시뮬 (변동성 ↓, mean reversion 강화).",
        "수수료 누적 / whipsaw 위험 검증.",
    ),
    StressScenarioSpec(
        StressScenario.SLIPPAGE,
        "슬리피지 + 호가 갭 확대 (commission_bps + slippage_bps 가중).",
        "fee_adjusted_return / slippage_adjusted_return 변화 측정.",
    ),
    StressScenarioSpec(
        StressScenario.DATA_GAP,
        "데이터 누락 (bar 결측 / NaN / 거래정지).",
        "전략이 누락 데이터에서도 안전한지 검증 (silent error 차단).",
    ),
    StressScenarioSpec(
        StressScenario.EXECUTION_REJECT,
        "체결 거부 시뮬 (broker reject 비율 ↑).",
        "재시도 로직 / 미체결 위험 검증.",
    ),
)


def list_stress_scenarios() -> list[dict[str, Any]]:
    """카탈로그를 dict 리스트로 — 리포트 / API 응답용."""
    return [spec.to_dict() for spec in STRESS_SCENARIO_CATALOG]
