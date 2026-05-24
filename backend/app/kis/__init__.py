"""KIS endpoint separation (#71 / 9-02) — Paper/Live host·TR·account 분리 정책.

본 패키지는 *endpoint 선택 정책* 만 담는다 — 실제 네트워크 호출이나 주문은 하지
않는다. KIS_IS_PAPER=false 라도 explicit live gate 없이는 BLOCKED.
"""
