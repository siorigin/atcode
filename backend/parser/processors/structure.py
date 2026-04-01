# Copyright 2025 Vitali Avagyan.
# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: MIT AND Apache-2.0
#
# This file is derived from code-graph-rag (MIT License).
# Modifications by SiOrigin Co. Ltd. are licensed under Apache-2.0.
# See the LICENSE file in the root directory for details.

import os
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Any

from core.config import IGNORE_PATTERNS
from core.fs_utils import ALLOWED_HIDDEN_DIRS, _git_ls_files
from graph.service import MemgraphIngestor
from loguru import logger


class StructureProcessor:
    """Handles identification and processing of project structure."""

    def __init__(
        self,
        ingestor: MemgraphIngestor,
        repo_path: Path,
        project_name: str,
        queries: dict[str, Any],
        subdirs: AbstractSet[str] | None = None,
        gitignore_parser: object | None = None,
    ):
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.queries = queries
        self.subdirs = subdirs  # Optional: only process these subdirectories
        self.structural_elements: dict[
            Path, str
        ] = {}  # Map path -> qualified_name (all folders now have qn)
        self.ignore_dirs = IGNORE_PATTERNS
        self.gitignore_parser = gitignore_parser

    # Directories that are allowed despite starting with '.'
    _ALLOWED_HIDDEN_DIRS = {".github", ".gitlab"}

    def identify_structure(self) -> None:
        """First pass: Efficiently finds all packages and folders.

        Uses git ls-files fast path when available (extracts directories from
        tracked file paths — no stat calls at all). Falls back to os.walk with
        in-place dirnames pruning for non-git repos.
        """
        directories = {self.repo_path}  # Start with root

        # Fast path: extract directories from git ls-files
        raw_files = _git_ls_files(self.repo_path)
        if raw_files is not None:
            directories |= self._dirs_from_git_files(raw_files)
        else:
            # Fallback: os.walk
            directories |= self._dirs_from_os_walk()

        logger.info(f"identify_structure: found {len(directories)} directories")

        # Pre-collect all filenames per directory for fast package indicator check.
        # When using git ls-files, we already have all file paths in memory —
        # build a set of (dir, filename) pairs to avoid per-directory stat calls.
        self._known_files: set[tuple[Path, str]] | None = None
        if raw_files is not None:
            self._known_files = set()
            for rel in raw_files:
                parts = rel.rsplit("/", 1)
                if len(parts) == 2:
                    dir_path = self.repo_path / parts[0]
                    self._known_files.add((dir_path, parts[1]))
                else:
                    # File in root
                    self._known_files.add((self.repo_path, parts[0]))

        # Process directories in a deterministic order
        for root in sorted(directories):
            try:
                relative_root = root.relative_to(self.repo_path)
            except ValueError:
                # Skip paths that are not relative to repo_path
                continue

            # Normalize relative_root: remove duplicate project_name prefix
            # E.g., if project_name is "simo" and relative_root is "simo/simo/csrc",
            # we want "simo/csrc" not "simo/simo/csrc"
            rel_parts = list(relative_root.parts)
            if (
                len(rel_parts) >= 2
                and rel_parts[0] == self.project_name
                and rel_parts[1] == self.project_name
            ):
                # Remove the duplicate project_name at the beginning
                relative_root = Path(*rel_parts[1:])
                logger.debug(
                    f"Normalized folder path with duplicate prefix: {rel_parts} -> {list(relative_root.parts)}"
                )

            parent_rel_path = relative_root.parent

            # Calculate parent's qualified_name (all folders now have qualified_name)
            if parent_rel_path == Path("."):
                parent_container_qn = None  # Root case: parent is Project
            else:
                # Normalize: skip first part if it matches project_name
                parent_parts = list(parent_rel_path.parts)
                if parent_parts and parent_parts[0] == self.project_name:
                    parent_parts = parent_parts[1:]
                parent_container_qn = ".".join(
                    [self.project_name] + parent_parts
                )

            # Check if this directory is a package for any supported language
            is_package = False
            package_indicators = set()

            # Collect package indicators from all language configs
            for lang_name, lang_queries in self.queries.items():
                lang_config = lang_queries["config"]
                package_indicators.update(lang_config.package_indicators)

            # Check if any package indicator exists
            # Use pre-collected file set when available (no stat calls)
            for indicator in package_indicators:
                if self._known_files is not None:
                    if (root, indicator) in self._known_files:
                        is_package = True
                        break
                else:
                    if (root / indicator).exists():
                        is_package = True
                        break

            if root != self.repo_path:
                # Use Folder node for both packages and regular directories
                # is_package attribute distinguishes them
                # All folders now get a qualified_name for proper project isolation
                # Normalize: skip first part if it matches project_name
                # to stay consistent with module QN (definition.py:272-273)
                folder_parts = list(relative_root.parts)
                if folder_parts and folder_parts[0] == self.project_name:
                    folder_parts = folder_parts[1:]
                folder_qn = ".".join([self.project_name] + folder_parts)
                self.structural_elements[relative_root] = (
                    folder_qn  # Store for other processors
                )

                if is_package:
                    logger.debug(f"  Identified Folder (package): {folder_qn}")
                else:
                    logger.debug(
                        f"  Identified Folder: '{relative_root}' -> {folder_qn}"
                    )

                self.ingestor.ensure_node_batch(
                    "Folder",
                    {
                        "path": str(relative_root),
                        "name": root.name,
                        "is_package": is_package,
                        "qualified_name": folder_qn,  # All folders now have qualified_name for project isolation
                    },
                )

                parent_label, parent_key, parent_val = (
                    ("Project", "name", self.project_name)
                    if parent_rel_path == Path(".")
                    else ("Folder", "qualified_name", parent_container_qn)
                )
                # Use qualified_name to match the target Folder (for project isolation)
                self.ingestor.ensure_relationship_batch(
                    (parent_label, parent_key, parent_val),
                    "CONTAINS_FOLDER",
                    ("Folder", "qualified_name", folder_qn),
                )

    def _dirs_from_git_files(self, raw_files: list[str]) -> set[Path]:
        """Extract unique directory paths from git ls-files output.

        Pure string operations — no stat calls. Filters by ignore_dirs and subdirs.
        """
        dirs: set[Path] = set()
        for rel in raw_files:
            parts = rel.split("/")
            if len(parts) <= 1:
                continue  # file in root, no directory to add
            # Check subdirs filter
            if self.subdirs and parts[0] not in self.subdirs:
                continue
            # Build directory paths incrementally, checking each component
            skip = False
            for i, p in enumerate(parts[:-1]):  # exclude filename
                if p in self.ignore_dirs:
                    skip = True
                    break
                if p.startswith(".") and p not in ALLOWED_HIDDEN_DIRS:
                    skip = True
                    break
            if skip:
                continue
            # Add all ancestor directories
            for i in range(1, len(parts)):
                dirs.add(self.repo_path / "/".join(parts[:i]))
        return dirs

    def _dirs_from_os_walk(self) -> set[Path]:
        """Fallback: collect directories via os.walk with pruning."""
        dirs: set[Path] = set()
        roots_to_walk: list[Path] = []
        if self.subdirs:
            for subdir_name in self.subdirs:
                subdir_path = self.repo_path / subdir_name
                if subdir_path.is_dir():
                    roots_to_walk.append(subdir_path)
        else:
            roots_to_walk.append(self.repo_path)

        for walk_root in roots_to_walk:
            for dirpath, dirnames, _ in os.walk(walk_root, topdown=True):
                kept_dirs = []
                for d in dirnames:
                    if d in self.ignore_dirs:
                        continue
                    if d.startswith(".") and d not in self._ALLOWED_HIDDEN_DIRS:
                        continue
                    if self.gitignore_parser is not None:
                        try:
                            dir_path = Path(dirpath) / d
                            if self.gitignore_parser.should_ignore(dir_path, is_dir=True):
                                continue
                        except Exception:
                            pass
                    kept_dirs.append(d)
                dirnames[:] = kept_dirs
                for d in dirnames:
                    dirs.add(Path(dirpath) / d)
        return dirs

    def process_generic_file(self, file_path: Path, file_name: str) -> None:
        """Process a generic (non-parseable) file and create appropriate nodes/relationships."""
        try:
            relative_filepath = file_path.relative_to(self.repo_path)
            relative_root = file_path.parent.relative_to(self.repo_path)
        except ValueError:
            logger.warning(f"File not relative to repo_path, skipping: {file_path}")
            return

        # Normalize paths: remove duplicate project_name prefix
        # E.g., if project_name is "simo" and relative_path is "simo/simo/csrc/xxx.py",
        # we want "simo/csrc/xxx.py" not "simo/simo/csrc/xxx.py"
        filepath_parts = list(relative_filepath.parts)
        root_parts = list(relative_root.parts)

        if (
            len(filepath_parts) >= 2
            and filepath_parts[0] == self.project_name
            and filepath_parts[1] == self.project_name
        ):
            # Remove the duplicate project_name at the beginning
            relative_filepath = Path(*filepath_parts[1:])
            relative_root = (
                Path(*root_parts[1:])
                if len(root_parts) >= 2 and root_parts[0] == self.project_name
                else relative_root
            )
            logger.debug(
                f"Normalized generic file path with duplicate prefix: {filepath_parts} -> {list(relative_filepath.parts)}"
            )

        relative_filepath_str = str(relative_filepath)

        # Determine the parent container (all folders now have qualified_name)
        if relative_root == Path("."):
            parent_label, parent_key, parent_val = (
                "Project",
                "name",
                self.project_name,
            )
        else:
            # Normalize: skip first part if it matches project_name
            parent_parts = list(relative_root.parts)
            if parent_parts and parent_parts[0] == self.project_name:
                parent_parts = parent_parts[1:]
            parent_container_qn = ".".join(
                [self.project_name] + parent_parts
            )
            parent_label, parent_key, parent_val = (
                "Folder",
                "qualified_name",
                parent_container_qn,
            )

        # Generate qualified_name for the file for proper project isolation
        # Format: project_name.path.to.file (without extension)
        # This ensures files from different projects don't collide even if they have the same path
        # Normalize: skip first part if it matches project_name (consistent with module QN)
        file_qn_parts = list(relative_root.parts) + [file_path.stem]
        if file_qn_parts and file_qn_parts[0] == self.project_name:
            file_qn_parts = file_qn_parts[1:]
        file_qualified_name = ".".join([self.project_name] + file_qn_parts)

        # Create File node
        self.ingestor.ensure_node_batch(
            "File",
            {
                "path": relative_filepath_str,
                "name": file_name,
                "extension": file_path.suffix,
                "qualified_name": file_qualified_name,  # All files now have qualified_name for project isolation
            },
        )

        # Create relationship to parent container
        # Use qualified_name to match the target File (for project isolation)
        self.ingestor.ensure_relationship_batch(
            (parent_label, parent_key, parent_val),
            "CONTAINS_FILE",
            ("File", "qualified_name", file_qualified_name),
        )
