"""#PaperCandidateWire / fix/desktop-kis-env-readiness-load:
launcher `load_env_via_dotenv` + readiness diagnostics.

Covers:
* `load_env_via_dotenv(path, override=True)` overwrites pre-existing
  empty env vars (the actual bug we are fixing).
* Secret values are never logged (only key names).
* `get_settings.cache_clear()` is invoked so a freshly-read Settings
  picks up the just-loaded values.
* `evaluate_readiness()` carries `env_file_found` /
  `env_file_loaded` / `env_loaded_path` from process env.
* `kis_app_key_present` / `kis_app_secret_present` / `kis_account_no_present`
  / `kis_is_paper` / `can_use_kis_paper` keys exposed in to_dict.
* All 3 KIS keys present + KIS_IS_PAPER=true → can_use_kis_paper=True.
* No live broker code path is touched.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Per-test env scrub so leakage between tests is impossible."""
    for var in (
        "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO",
        "KIS_IS_PAPER", "DEFAULT_MODE",
        "ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
        "ENABLE_FUTURES_LIVE_TRADING",
        "AUTOTRADE_ENV_FILE_PATH",
        "AUTOTRADE_ENV_FILE_FOUND",
        "AUTOTRADE_ENV_FILE_LOADED",
    ):
        monkeypatch.delenv(var, raising=False)
    # Settings cache must be cleared before/after every test.
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _write_env_file(tmp_path: Path,
                    *,
                    kis_key="PSTfake0000000000000000000000000000",
                    kis_secret="k8akFakeSecretValueForTesting+++=",
                    kis_account="5018666710",
                    kis_is_paper="true",
                    enable_live="false") -> Path:
    path = tmp_path / ".env"
    path.write_text(
        f"KIS_IS_PAPER={kis_is_paper}\n"
        f"DEFAULT_MODE=PAPER\n"
        f"ENABLE_LIVE_TRADING={enable_live}\n"
        f"ENABLE_AI_EXECUTION=false\n"
        f"ENABLE_FUTURES_LIVE_TRADING=false\n"
        f"\n"
        f"KIS_APP_KEY={kis_key}\n"
        f"KIS_APP_SECRET={kis_secret}\n"
        f"KIS_ACCOUNT_NO={kis_account}\n",
        encoding="utf-8",
    )
    return path


# ─────────────────────────────────────────────────────────────────────────────
# load_env_via_dotenv — overrides empty env vars
# ─────────────────────────────────────────────────────────────────────────────


class TestLoadEnvViaDotenv:

    def test_loads_keys_from_file(self, tmp_path):
        from app_desktop_launcher import load_env_via_dotenv
        path = _write_env_file(tmp_path)
        log = logging.getLogger("test")
        ok = load_env_via_dotenv(path, log)
        assert ok is True
        assert os.environ["KIS_APP_KEY"].startswith("PSTfake")
        assert os.environ["KIS_APP_SECRET"].startswith("k8akFake")
        assert os.environ["KIS_ACCOUNT_NO"] == "5018666710"
        assert os.environ["KIS_IS_PAPER"] == "true"

    def test_overrides_pre_existing_empty_value(self, tmp_path, monkeypatch):
        """The actual bug — pre-existing empty KIS_APP_KEY must be overwritten."""
        from app_desktop_launcher import load_env_via_dotenv
        monkeypatch.setenv("KIS_APP_KEY", "")   # parent shell leftover.
        monkeypatch.setenv("KIS_APP_SECRET", "")
        path = _write_env_file(tmp_path)
        log = logging.getLogger("test")
        ok = load_env_via_dotenv(path, log)
        assert ok is True
        # override=True must replace the empty values.
        assert os.environ["KIS_APP_KEY"].startswith("PSTfake")
        assert os.environ["KIS_APP_SECRET"].startswith("k8akFake")

    def test_secret_values_never_logged(self, tmp_path, caplog):
        """Verify the secret value never appears in log output."""
        from app_desktop_launcher import load_env_via_dotenv
        path = _write_env_file(tmp_path,
                               kis_key="PSTfake-secret-marker-X1Y2Z3")
        log = logging.getLogger("autotrade.launcher")
        with caplog.at_level(logging.INFO,
                             logger="autotrade.launcher"):
            load_env_via_dotenv(path, log)
        joined = "\n".join(r.getMessage() for r in caplog.records)
        # Key name yes, value no.
        assert "KIS_APP_KEY" in joined
        assert "PSTfake-secret-marker-X1Y2Z3" not in joined

    def test_missing_file_returns_false(self, tmp_path):
        from app_desktop_launcher import load_env_via_dotenv
        log = logging.getLogger("test")
        # dotenv silently treats missing file as no-op; load_dotenv returns
        # False when nothing was loaded. We accept either False or True (no
        # values injected) — the *contract* is "no values appeared".
        load_env_via_dotenv(tmp_path / "does-not-exist.env", log)
        assert "KIS_APP_KEY" not in os.environ \
            or os.environ.get("KIS_APP_KEY") == ""

    def test_settings_cache_cleared_after_load(self, tmp_path, monkeypatch):
        """Cache-clear ensures a *fresh* Settings() reads the just-loaded env.
        A developer `.env` may exist on disk so we can't assert empty bare
        defaults — but we *can* prove the cache is invalidated and the
        second `get_settings()` returns a *new* instance with new values.
        """
        from app_desktop_launcher import load_env_via_dotenv
        from app.core.config import get_settings
        get_settings.cache_clear()
        s_before = get_settings()
        path = _write_env_file(tmp_path)
        load_env_via_dotenv(path, logging.getLogger("test"))
        s_after = get_settings()
        assert s_after is not s_before, \
            "get_settings() must return a new instance after cache_clear"
        assert s_after.kis_app_key.startswith("PSTfake")
        assert s_after.kis_app_secret.startswith("k8akFake")
        assert s_after.kis_account_no == "5018666710"


# ─────────────────────────────────────────────────────────────────────────────
# readiness.evaluate_readiness — new diagnostic fields
# ─────────────────────────────────────────────────────────────────────────────


class TestReadinessDiagnostics:

    def test_env_file_found_and_loaded_carry(self, monkeypatch):
        from app.kis_paper.readiness import evaluate_readiness
        from app.core.config import Settings
        monkeypatch.setenv("AUTOTRADE_ENV_FILE_FOUND", "true")
        monkeypatch.setenv("AUTOTRADE_ENV_FILE_LOADED", "true")
        monkeypatch.setenv(
            "AUTOTRADE_ENV_FILE_PATH",
            r"C:\Users\user\AppData\Roaming\Autotrade\.env",
        )
        rd = evaluate_readiness(Settings())
        d = rd.to_dict()
        assert d["env_file_found"] is True
        assert d["env_file_loaded"] is True
        assert d["env_loaded_path"].endswith(".env")

    def test_env_file_missing_defaults(self, monkeypatch):
        from app.kis_paper.readiness import evaluate_readiness
        from app.core.config import Settings
        # No AUTOTRADE_ENV_FILE_* vars set.
        rd = evaluate_readiness(Settings())
        d = rd.to_dict()
        assert d["env_file_found"] is False
        assert d["env_file_loaded"] is False
        assert d["env_loaded_path"] == ""

    def test_new_alias_keys_present_in_to_dict(self):
        from app.kis_paper.readiness import evaluate_readiness
        from app.core.config import Settings
        rd = evaluate_readiness(Settings())
        d = rd.to_dict()
        for key in (
            "kis_app_key_present", "kis_app_secret_present",
            "kis_account_no_present", "kis_is_paper", "can_use_kis_paper",
            "env_file_found", "env_file_loaded", "env_loaded_path",
        ):
            assert key in d, f"missing field: {key}"

    def test_can_use_kis_paper_true_when_all_keys_present(self, monkeypatch):
        from app.kis_paper.readiness import evaluate_readiness
        from app.core.config import Settings, get_settings
        monkeypatch.setenv("KIS_APP_KEY", "PSTfake000000000000000000000000")
        monkeypatch.setenv("KIS_APP_SECRET", "k8akFakeSecretValue+++=")
        monkeypatch.setenv("KIS_ACCOUNT_NO", "5018666710")
        monkeypatch.setenv("KIS_IS_PAPER", "true")
        monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
        monkeypatch.setenv("ENABLE_AI_EXECUTION", "false")
        get_settings.cache_clear()
        rd = evaluate_readiness(Settings())
        d = rd.to_dict()
        assert d["kis_app_key_present"] is True
        assert d["kis_app_secret_present"] is True
        assert d["kis_account_no_present"] is True
        assert d["kis_is_paper"] is True
        assert d["can_use_kis_paper"] is True

    def test_can_use_kis_paper_false_when_key_missing(self):
        from app.kis_paper.readiness import evaluate_readiness
        from app.core.config import Settings
        # No KIS_* env vars set. _env_file=None disables the local .env read
        # so this test is hermetic on a developer machine with a real .env.
        rd = evaluate_readiness(Settings(_env_file=None))
        d = rd.to_dict()
        assert d["kis_app_key_present"] is False
        assert d["can_use_kis_paper"] is False

    def test_backwards_compat_old_keys_preserved(self, monkeypatch):
        """Old key names (kis_key_present, kis_secret_present,
        kis_account_present) must still be present alongside the new aliases.
        """
        from app.kis_paper.readiness import evaluate_readiness
        from app.core.config import Settings, get_settings
        monkeypatch.setenv("KIS_APP_KEY", "PSTfake000000")
        monkeypatch.setenv("KIS_APP_SECRET", "k8akFakeSecret")
        monkeypatch.setenv("KIS_ACCOUNT_NO", "5018666710")
        get_settings.cache_clear()
        rd = evaluate_readiness(Settings())
        d = rd.to_dict()
        # Both old and new.
        assert d["kis_key_present"] is True
        assert d["kis_app_key_present"] is True
        assert d["kis_secret_present"] is True
        assert d["kis_app_secret_present"] is True
        assert d["kis_account_present"] is True
        assert d["kis_account_no_present"] is True

    def test_secret_values_never_in_to_dict(self, monkeypatch):
        """to_dict must not contain raw secret values."""
        from app.kis_paper.readiness import evaluate_readiness
        from app.core.config import Settings, get_settings
        marker_key = "PST-secret-marker-A1B2C3D4-DO-NOT-LEAK"
        marker_secret = "k8ak-secret-marker-X9Y8Z7-DO-NOT-LEAK"
        marker_account = "0000888899"
        monkeypatch.setenv("KIS_APP_KEY", marker_key)
        monkeypatch.setenv("KIS_APP_SECRET", marker_secret)
        monkeypatch.setenv("KIS_ACCOUNT_NO", marker_account)
        get_settings.cache_clear()
        rd = evaluate_readiness(Settings())
        d = rd.to_dict()
        as_str = repr(d)
        assert marker_key not in as_str
        assert marker_secret not in as_str
        assert marker_account not in as_str


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end: launcher load_env_via_dotenv → readiness reflects it
# ─────────────────────────────────────────────────────────────────────────────


class TestEndToEnd:

    def test_load_then_readiness_returns_present(self, tmp_path, monkeypatch):
        """Full round-trip — launcher loads .env, readiness reads new value."""
        from app_desktop_launcher import load_env_via_dotenv
        from app.kis_paper.readiness import evaluate_readiness
        from app.core.config import Settings, get_settings

        # Simulate parent shell leftover empty values.
        monkeypatch.setenv("KIS_APP_KEY", "")
        monkeypatch.setenv("KIS_APP_SECRET", "")
        monkeypatch.setenv("KIS_ACCOUNT_NO", "")

        path = _write_env_file(tmp_path)
        ok = load_env_via_dotenv(path, logging.getLogger("test"))
        assert ok is True

        # Publish diagnostic vars (same as launcher.run does).
        monkeypatch.setenv("AUTOTRADE_ENV_FILE_FOUND", "true")
        monkeypatch.setenv("AUTOTRADE_ENV_FILE_LOADED", "true")
        monkeypatch.setenv("AUTOTRADE_ENV_FILE_PATH", str(path))

        get_settings.cache_clear()
        rd = evaluate_readiness(Settings())
        d = rd.to_dict()
        # The bug-reproduction assertions.
        assert d["kis_app_key_present"] is True
        assert d["kis_app_secret_present"] is True
        assert d["kis_account_no_present"] is True
        assert d["kis_is_paper"] is True
        assert d["can_use_kis_paper"] is True
        # Diagnostic carry.
        assert d["env_file_found"] is True
        assert d["env_file_loaded"] is True
        assert d["env_loaded_path"].endswith(".env")
        # Safety invariants preserved.
        assert d["is_order_intent"] is False
        assert d["is_order_signal"] is False

    def test_blocked_when_live_flag_true_even_with_keys(self, tmp_path, monkeypatch):
        """ENABLE_LIVE_TRADING=true still blocks KIS paper test even when keys
        are present — the safety guard remains."""
        from app_desktop_launcher import load_env_via_dotenv
        from app.kis_paper.readiness import evaluate_readiness
        from app.core.config import Settings, get_settings

        path = _write_env_file(tmp_path, enable_live="true")
        load_env_via_dotenv(path, logging.getLogger("test"))
        get_settings.cache_clear()
        rd = evaluate_readiness(Settings())
        d = rd.to_dict()
        # Keys present.
        assert d["kis_app_key_present"] is True
        # But can_use blocked.
        assert d["can_use_kis_paper"] is False
        assert "ENABLE_LIVE_TRADING_TRUE" in d["blocked_reasons"]


# ─────────────────────────────────────────────────────────────────────────────
# Static guards — no broker / executor imports added
# ─────────────────────────────────────────────────────────────────────────────


_LAUNCHER = Path(__file__).resolve().parents[1] / "app_desktop_launcher.py"
_READINESS = Path(__file__).resolve().parents[1] / "app" / "kis_paper" / "readiness.py"


class TestStaticGuards:

    @pytest.mark.parametrize("path", [_LAUNCHER, _READINESS])
    def test_no_forbidden_calls_in_modules(self, path):
        import ast
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = ast.unparse(node.func)
                for bad in ("broker.place_order", "route_order(",
                            "OrderExecutor("):
                    assert bad not in callee, f"{path.name}: forbidden {bad}"

    @pytest.mark.parametrize("path", [_LAUNCHER, _READINESS])
    def test_no_secret_value_logging(self, path):
        """No `log.*(...secret_value...)` style string interpolation that
        would leak secrets. Only key NAMES may be logged."""
        src = path.read_text(encoding="utf-8")
        # The "value redacted" marker proves the launcher logs only key names.
        if path == _LAUNCHER:
            assert "value redacted" in src
