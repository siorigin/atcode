# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from core.fs_utils import walk_files


def _relative_set(base_path: Path, files: list[Path]) -> set[str]:
    return {str(path.relative_to(base_path)) for path in files}


def test_walk_files_prunes_metadata_suffix_dirs(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    (tmp_path / "demo.egg-info").mkdir()
    (tmp_path / "demo.egg-info" / "PKG-INFO").write_text("name: demo\n", encoding="utf-8")

    (tmp_path / "demo.dist-info").mkdir()
    (tmp_path / "demo.dist-info" / "METADATA").write_text("name: demo\n", encoding="utf-8")

    files = walk_files(tmp_path, {".egg-info", ".dist-info"})
    rel_paths = _relative_set(tmp_path, files)

    assert "src/main.py" in rel_paths
    assert "demo.egg-info/PKG-INFO" not in rel_paths
    assert "demo.dist-info/METADATA" not in rel_paths


def test_walk_files_prunes_glob_ignored_dirs(tmp_path: Path):
    (tmp_path / "pkg.egg-info").mkdir()
    (tmp_path / "pkg.egg-info" / "PKG-INFO").write_text("name: pkg\n", encoding="utf-8")

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

    files = walk_files(tmp_path, {"*.egg-info"})
    rel_paths = _relative_set(tmp_path, files)

    assert "src/app.py" in rel_paths
    assert "pkg.egg-info/PKG-INFO" not in rel_paths
