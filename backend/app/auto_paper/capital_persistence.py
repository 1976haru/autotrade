"""PART1-3: CapitalState 영속화 — 재시작해도 Paper 현금/원금/실현손익 복원.

배경 (2026-06-01 첫 실전 모의):
  `CapitalState` 는 프로세스 메모리 싱글톤이라, 백엔드를 재시작하면
  현금/invested/realized_pnl/buy·sell count 가 전부 리셋됐다. 며칠치 손익을
  누적하려면 이 상태가 *영속* 되어야 한다.

설계 (보수적 · backward-compatible):
  - `persistent_settings.get_config_dir()` 와 *동일한* config dir 에 단일 JSON
    파일(`paper_capital_state.json`)로 저장.
  - 싱글톤 최초 생성 시 파일이 있으면 *복원*, 없거나 손상되면 *기존처럼
    initial_cash 로 시작* (★0원 fallback 절대 금지 — 손상 시 0 이 아니라
    initial_cash 로).
  - `commit_buy` / `commit_sell` / `reset` 후 best-effort 저장. 저장 실패는
    *주문 흐름을 막지 않는다* (advisory — 로그만 남기고 진행).

CLAUDE.md 절대 원칙 (capital_state 와 동일하게 유지):
  - broker / OrderExecutor / route_order / KIS / AI SDK / 외부 HTTP import 0건
  - app.core.config.get_settings import 0건
  - 저장 payload 에 secret / 계좌번호 0건 — 순수 숫자 잔고 필드만
  - 본 모듈은 *상태 저장/복원* 만, 어떤 주문도 발행하지 않음
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

_log = logging.getLogger("autotrade.paper_capital_persistence")

# 영속 파일명 — persistent_settings (paper_capital_settings.json) 와 *별개* 파일.
STATE_FILENAME = "paper_capital_state.json"

# 저장 파일에 담기는 *허용된 키* — 전부 정수 잔고 필드. secret 0건.
_PERSISTED_KEYS = (
    "initial_cash_krw",
    "available_cash_krw",
    "invested_krw",
    "realized_pnl_krw",
    "buy_count",
    "sell_count",
    "last_event_at",
)

_io_lock = threading.Lock()


def get_state_path() -> Path:
    """영속 파일 경로 — persistent_settings 와 동일한 config dir 재사용.

    config dir 을 가져오지 못하면(예: import 실패) repo-local fallback 사용.
    """
    try:
        from app.settings.persistent_settings import get_config_dir
        return get_config_dir() / STATE_FILENAME
    except Exception:  # noqa: BLE001 — config dir 미가용 시 repo-local fallback.
        d = Path(__file__).resolve().parents[2] / ".runtime" / "config"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return d / STATE_FILENAME


def load_state() -> dict[str, Any] | None:
    """저장된 capital state 복원. 파일 없음/손상/이상값 → None (caller 가
    initial_cash 로 시작 — ★0원 fallback 아님).

    검증:
      - 필수 정수 키 존재 + int 변환 가능 + 음수 아님
      - available_cash / invested / initial_cash >= 0
    하나라도 실패하면 None (손상으로 간주, 조용히 initial 로).
    """
    path = get_state_path()
    try:
        with _io_lock:
            if not path.exists():
                return None
            raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — 손상 파일은 None.
        _log.warning("[paper-capital] load_state failed (will use initial): %s", exc)
        return None

    if not isinstance(raw, dict):
        _log.warning("[paper-capital] load_state: not a dict, ignoring")
        return None

    out: dict[str, Any] = {}
    try:
        for key in ("initial_cash_krw", "available_cash_krw", "invested_krw",
                    "realized_pnl_krw", "buy_count", "sell_count"):
            if key not in raw:
                _log.warning("[paper-capital] load_state: missing key %s", key)
                return None
            out[key] = int(raw[key])
        out["last_event_at"] = raw.get("last_event_at")
    except (TypeError, ValueError) as exc:
        _log.warning("[paper-capital] load_state: bad value (%s), using initial", exc)
        return None

    # ★0원 fallback 금지 가드: 음수/비정상 잔고면 복원 거부 → initial 로.
    if out["initial_cash_krw"] < 0 or out["available_cash_krw"] < 0 or out["invested_krw"] < 0:
        _log.warning("[paper-capital] load_state: negative balance, refusing restore")
        return None

    _log.info(
        "[paper-capital] restored state: cash=%d invested=%d realized=%d "
        "buy=%d sell=%d",
        out["available_cash_krw"], out["invested_krw"], out["realized_pnl_krw"],
        out["buy_count"], out["sell_count"],
    )
    return out


def save_state(snapshot_dict: dict[str, Any]) -> bool:
    """capital state 영속 저장 (best-effort, atomic write).

    저장 실패는 주문 흐름을 막지 않는다 — False 반환 + 로그만. secret/계좌번호
    필드는 _PERSISTED_KEYS 화이트리스트로 *구조적으로* 배제.
    """
    payload = {k: snapshot_dict.get(k) for k in _PERSISTED_KEYS}
    path = get_state_path()
    try:
        with _io_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            # atomic: temp 파일에 쓰고 os.replace 로 교체 (부분 쓰기 방지).
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        return True
    except Exception as exc:  # noqa: BLE001 — 저장 실패는 흐름을 막지 않음.
        _log.warning("[paper-capital] save_state failed (non-fatal): %s", exc)
        return False


def clear_state_for_tests() -> None:
    """테스트용 — 영속 파일 삭제."""
    path = get_state_path()
    try:
        with _io_lock:
            if path.exists():
                path.unlink()
    except OSError:
        pass


__all__ = [
    "STATE_FILENAME",
    "get_state_path",
    "load_state",
    "save_state",
    "clear_state_for_tests",
]
