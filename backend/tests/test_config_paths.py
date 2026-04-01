from pathlib import Path

from core.config import get_project_root, resolve_project_path


def test_resolve_project_path_keeps_absolute_paths() -> None:
    absolute = Path("/tmp/atcode-log-dir")
    assert resolve_project_path(absolute) == absolute


def test_resolve_project_path_uses_project_root_for_relative_paths() -> None:
    assert resolve_project_path("./data/logs") == get_project_root() / "data" / "logs"
