"""2026-06-04: alembic in-process logging-handler 보존 가드 테스트.

`fileConfig(alembic.ini)` 가 launcher 의 FileHandler 를 교체해 migration 이후 로그가
파일에 안 남던 문제의 가드. 순수 함수 — 부작용 0.
"""

from app.db.alembic_logging import should_apply_alembic_file_config


def test_standalone_cli_applies_fileconfig():
    # config 파일 있음 + root handler 없음 (standalone alembic CLI) → 적용.
    assert should_apply_alembic_file_config("alembic.ini", root_logger_has_handlers=False) is True


def test_inprocess_with_existing_handlers_skips_fileconfig():
    # config 파일 있음 + root handler 이미 존재 (launcher / FastAPI in-process)
    # → 건너뜀 (launcher FileHandler 보존).
    assert should_apply_alembic_file_config("alembic.ini", root_logger_has_handlers=True) is False


def test_no_config_file_never_applies():
    assert should_apply_alembic_file_config(None, root_logger_has_handlers=False) is False
    assert should_apply_alembic_file_config(None, root_logger_has_handlers=True) is False


def test_empty_config_file_name_does_not_apply():
    assert should_apply_alembic_file_config("", root_logger_has_handlers=False) is False
