from __future__ import annotations

import os


def pytest_configure() -> None:
    if os.environ.get("PYTEST_SAFE_WINDOWS_ACL") != "1":
        return

    try:
        import _pytest.pathlib as pathlib_mod
        import _pytest.tmpdir as tmpdir_mod
    except Exception:
        return

    def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    pathlib_mod.cleanup_dead_symlinks = _noop
    tmpdir_mod.cleanup_dead_symlinks = _noop
