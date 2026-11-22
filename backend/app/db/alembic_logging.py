"""alembic in-process 실행 시 launcher 의 logging handler 를 보존하기 위한 가드.

배경 (2026-06-04 확인):
  `alembic/env.py` 가 `fileConfig(alembic.ini)` 를 무조건 호출하면 root logger 의
  handler 가 *교체* 되어, launcher 가 붙여 둔 FileHandler 가 제거된다. 그 결과
  migration 이후의 앱 로그(예: `fill_poller started` 마커, 런타임 ERROR/WARN)가
  `%APPDATA%\\Autotrade\\logs\\backend-*.log` 에 더 이상 기록되지 않는다 — 운영자가
  fill_poller 기동/런타임 오류를 로그로 확인할 수 없게 된다.

해결:
  alembic 이 *standalone CLI* 로 실행될 때(root logger 에 handler 가 아직 없음)만
  fileConfig 를 적용하고, launcher / FastAPI in-process 로 실행될 때(이미 handler
  존재)는 건너뛴다 → launcher 의 FileHandler 가 migration 이후에도 살아남는다.
  fileConfig 는 *로깅 설정* 만 바꾸며 migration 로직과 무관하므로, 건너뛰어도
  migration 동작에는 영향이 없다.
"""

from __future__ import annotations


def should_apply_alembic_file_config(
    config_file_name: str | None,
    root_logger_has_handlers: bool,
) -> bool:
    """alembic 의 fileConfig(alembic.ini) 를 적용할지 여부.

    - config 파일이 없으면 적용 불가 → False.
    - root logger 에 이미 handler 가 있으면(launcher/in-process) 건너뜀 → False
      (기존 handler/FileHandler 보존).
    - config 파일이 있고 handler 가 없으면(standalone alembic CLI) → True.
    """
    return bool(config_file_name) and not root_logger_has_handlers


__all__ = ["should_apply_alembic_file_config"]
