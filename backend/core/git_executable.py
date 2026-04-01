# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Cross-platform git executable resolution.

On Linux/macOS, ``git`` is assumed to be on PATH.
On Windows, the API process may inherit an incomplete PATH (e.g. when
started from an IDE or service).  We resolve the full path via
``shutil.which`` first, then fall back to common Git for Windows
install locations.

Usage::

    from core.git_executable import GIT

    subprocess.run([GIT, "clone", url, path])
"""

import os
import shutil
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def get_git_executable() -> str:
    """Return the path to the git executable."""
    if sys.platform != "win32":
        return "git"

    # Windows: try PATH first
    git_path = shutil.which("git")
    if git_path:
        return git_path

    # Try common Git for Windows install locations
    common_paths = [
        os.path.expandvars(r"%ProgramFiles%\Git\cmd\git.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Git\cmd\git.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe"),
    ]
    for path in common_paths:
        if os.path.isfile(path):
            return path

    # Fall back to bare name (will fail with clear FileNotFoundError)
    return "git"


GIT = get_git_executable()
