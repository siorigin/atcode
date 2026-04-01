# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import pickle
from pathlib import Path

from loguru import logger
from tree_sitter import Node


class ASTCache:
    """Persistent AST cache stored in .atcode/ast_cache/.

    Caches are keyed by file MD5 hash, so unchanged files can be
    detected and their ASTs reused without re-parsing.

    Example:
        cache = ASTCache(state_dir=Path(".atcode"), project_name="myproject")
        ast = cache.get(file_path)
        if ast is None:
            root_node, language = parse_file(file_path)
            cache.put(file_path, root_node, language)
    """

    def __init__(self, state_dir: Path, project_name: str) -> None:
        """Initialize the AST cache.

        Args:
            state_dir: Directory for cache storage (e.g., .atcode)
            project_name: Project name for namespacing
        """
        self.cache_dir = state_dir / "ast_cache"
        self._writable = True
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            logger.debug(
                f"Cannot create AST cache directory (permission denied): {self.cache_dir}"
            )
            self._writable = False
        except Exception as e:
            logger.debug(f"Cannot create AST cache directory: {e}")
            self._writable = False
        self._project_name = project_name

    def _get_cache_path(self, file_md5: str) -> Path:
        """Get the cache file path for a given file MD5."""
        return self.cache_dir / f"{file_md5}.pkl"

    def _compute_file_md5(self, file_path: Path) -> str:
        """Compute MD5 hash of file content."""
        try:
            content = file_path.read_bytes()
            return hashlib.md5(content).hexdigest()
        except Exception as e:
            logger.warning(f"Failed to compute MD5 for {file_path}: {e}")
            return ""

    def get(self, file_path: Path) -> tuple[Node, str] | None:
        """Get cached AST if file hasn't changed.

        Args:
            file_path: Path to the source file

        Returns:
            (root_node, language) tuple if cache hit, None otherwise
        """
        file_md5 = self._compute_file_md5(file_path)
        if not file_md5:
            return None

        cache_path = self._get_cache_path(file_md5)

        if cache_path.exists():
            try:
                with open(cache_path, "rb") as f:
                    result = pickle.load(f)
                    logger.debug(f"AST cache hit: {file_path}")
                    return result
            except Exception as e:
                # Cache corrupted, delete it
                logger.warning(f"Cache corrupted for {file_path}, deleting: {e}")
                cache_path.unlink(missing_ok=True)

        logger.debug(f"AST cache miss: {file_path}")
        return None

    def put(self, file_path: Path, root_node: Node, language: str) -> None:
        """Store AST to cache.

        Args:
            file_path: Path to the source file
            root_node: Parsed AST root node
            language: Language name
        """
        if not self._writable:
            return

        file_md5 = self._compute_file_md5(file_path)
        if not file_md5:
            return

        cache_path = self._get_cache_path(file_md5)

        try:
            with open(cache_path, "wb") as f:
                pickle.dump((root_node, language), f, protocol=pickle.HIGHEST_PROTOCOL)
            logger.debug(f"Cached AST: {file_path} -> {file_md5}.pkl")
        except PermissionError:
            logger.debug(f"Cannot cache AST (permission denied): {cache_path}")
        except Exception as e:
            logger.warning(f"Failed to cache AST for {file_path}: {e}")

    def delete(self, file_md5: str) -> bool:
        """Delete cached AST by file MD5.

        Args:
            file_md5: MD5 hash of the file content

        Returns:
            True if cache was deleted, False if it didn't exist
        """
        cache_path = self._get_cache_path(file_md5)
        if cache_path.exists():
            cache_path.unlink()
            logger.debug(f"Deleted AST cache: {file_md5}.pkl")
            return True
        return False

    def cleanup(self, valid_md5s: set[str]) -> int:
        """Clean up cache files that are no longer referenced.

        Args:
            valid_md5s: Set of MD5 hashes that are still in use

        Returns:
            Number of cache files deleted
        """
        count = 0
        for cache_file in self.cache_dir.glob("*.pkl"):
            if cache_file.stem not in valid_md5s:
                cache_file.unlink()
                count += 1
                logger.debug(f"Cleaned up orphaned cache: {cache_file.name}")

        if count > 0:
            logger.info(f"Cleaned up {count} orphaned AST cache files")

        return count

    def clear_all(self) -> int:
        """Clear all cached AST files.

        Returns:
            Number of cache files deleted
        """
        count = 0
        for cache_file in self.cache_dir.glob("*.pkl"):
            cache_file.unlink()
            count += 1

        logger.info(f"Cleared all AST cache files: {count} deleted")
        return count

    def get_stats(self) -> dict[str, int]:
        """Get cache statistics.

        Returns:
            Dict with 'count' and 'total_size_bytes'
        """
        count = 0
        total_size = 0
        for cache_file in self.cache_dir.glob("*.pkl"):
            count += 1
            total_size += cache_file.stat().st_size

        return {"count": count, "total_size_bytes": total_size}
