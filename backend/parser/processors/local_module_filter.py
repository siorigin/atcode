# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import os
from pathlib import Path

from loguru import logger

from .stdlib_checker import StdlibChecker


class LocalModuleFilter:
    """Filter to determine if modules and calls belong to the local codebase.

    Also tracks specific external dependencies that should be processed
    (e.g., libraries discovered via dynamic imports like importlib.import_module).
    """

    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        tracked_externals: set[str] | None = None,
    ):
        """Initialize the local module filter.

        Args:
            repo_path: Path to the repository root
            project_name: Name of the project (used as module prefix)
            tracked_externals: Set of external module names to track
                              (e.g., {"deep_gemm", "triton"})
        """
        self.repo_path = repo_path
        self.project_name = project_name
        self.tracked_externals = tracked_externals or set()
        self._local_modules: set[str] | None = None
        self._local_packages: set[str] | None = None

    def _build_local_module_cache(self) -> None:
        """Build cache of local modules and packages."""
        if self._local_modules is not None:
            return

        self._local_modules = set()
        self._local_packages = set()

        def onerror(error: OSError) -> None:
            """Handle errors during directory walk (e.g., permission denied)."""
            logger.debug(f"Skipping inaccessible directory during module scan: {error}")

        # Scan the repository for Python modules and packages
        for root, dirs, files in os.walk(self.repo_path, onerror=onerror):
            # Skip hidden directories and common non-code directories
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and d
                not in {
                    "__pycache__",
                    "node_modules",
                    ".git",
                    "venv",
                    "env",
                    "build",
                    "dist",
                    "target",
                    "out",
                    "bin",
                    "obj",
                }
            ]

            for file in files:
                if file.endswith(".py"):
                    # Convert file path to module name
                    try:
                        rel_path = Path(root).relative_to(self.repo_path)
                    except ValueError:
                        continue
                    module_parts = list(rel_path.parts)

                    # Handle files in the root directory (rel_path is '.')
                    # In this case, module_parts will be empty
                    if not module_parts:
                        # File is in root, use just the filename (minus .py)
                        if file == "__init__.py":
                            continue  # Root __init__.py doesn't define a module
                        module_parts = [file[:-3]]  # Remove .py
                    elif module_parts[-1] == "__init__.py":
                        module_parts = module_parts[:-1]
                    elif file == "__init__.py":
                        # __init__.py in a subdirectory - module_parts already has the path
                        pass
                    else:
                        # Regular file - add filename (minus .py)
                        module_parts.append(file[:-3])

                    if module_parts:  # Skip empty module names
                        module_name = ".".join(module_parts)
                        self._local_modules.add(module_name)
                        self._local_packages.add(module_name)

                        # Also add parent packages
                        for i in range(1, len(module_parts)):
                            parent_package = ".".join(module_parts[:i])
                            self._local_packages.add(parent_package)

        logger.debug(
            f"Found {len(self._local_modules)} local modules and {len(self._local_packages)} local packages"
        )

        # Second pass: register nested Python package roots for monorepo support.
        # In repos like ARPO/verl_arpo_entropy/verl/..., the import "from verl.utils import X"
        # uses "verl" as the top-level package, but it's nested under ARPO/verl_arpo_entropy/.
        # We detect package roots: directories with __init__.py whose parent does NOT have one.
        # Then register the root name and all sub-packages under it.
        package_roots: set[Path] = set()  # absolute paths of nested package roots
        for root, dirs, files in os.walk(self.repo_path, onerror=onerror):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in {"__pycache__", "node_modules", ".git", "venv", "env",
                              "build", "dist", "target", "out", "bin", "obj"}
            ]
            if "__init__.py" in files:
                root_path = Path(root)
                parent = root_path.parent
                # A package root is a dir with __init__.py whose parent lacks __init__.py
                # (and is not the repo root itself, which is already handled)
                if parent != self.repo_path and not (parent / "__init__.py").exists():
                    try:
                        rel = root_path.relative_to(self.repo_path)
                    except ValueError:
                        continue
                    if len(rel.parts) >= 2:  # Must be nested (not top-level)
                        package_roots.add(root_path)

        # For each package root, register it and all sub-packages
        nested_packages: set[str] = set()
        for pkg_root in package_roots:
            pkg_name = pkg_root.name
            if StdlibChecker.is_stdlib(pkg_name, "python"):
                continue
            nested_packages.add(pkg_name)
            # Walk sub-packages under this root
            for sub_root, sub_dirs, sub_files in os.walk(pkg_root, onerror=onerror):
                sub_dirs[:] = [d for d in sub_dirs if not d.startswith(".") and d != "__pycache__"]
                if "__init__.py" in sub_files:
                    try:
                        rel = Path(sub_root).relative_to(pkg_root)
                    except ValueError:
                        continue
                    if rel.parts:
                        sub_pkg = pkg_name + "." + ".".join(rel.parts)
                        nested_packages.add(sub_pkg)

        if nested_packages:
            self._local_packages.update(nested_packages)
            logger.debug(
                f"Added {len(nested_packages)} nested local packages for monorepo support "
                f"(roots: {[p.name for p in package_roots]})"
            )

    def add_tracked_external(self, module_name: str) -> None:
        """Add an external module to the tracked set.

        Args:
            module_name: The root module name (e.g., "deep_gemm")
        """
        self.tracked_externals.add(module_name)
        logger.debug(f"Added tracked external dependency: {module_name}")

    def add_tracked_externals(self, module_names: set[str]) -> None:
        """Add multiple external modules to the tracked set.

        Args:
            module_names: Set of root module names
        """
        self.tracked_externals.update(module_names)
        if module_names:
            logger.debug(f"Added tracked external dependencies: {module_names}")

    def is_tracked_external(self, module_name: str) -> bool:
        """Check if a module is a tracked external dependency.

        Args:
            module_name: The module name to check

        Returns:
            True if this is a tracked external dependency
        """
        # Extract root package name
        root_module = module_name.split(".")[0]
        return root_module in self.tracked_externals

    def is_local_module(self, module_name: str, language: str = "python") -> bool:
        """Check if a module belongs to the local codebase.

        Note: Even if a local file has the same name as a stdlib module (e.g., os.py),
        we treat stdlib names as external to avoid confusion and incorrect relationships.

        Args:
            module_name: The module name to check (e.g., 'vllm.lora.request')
            language: The programming language (default: "python")

        Returns:
            True if the module is local, False otherwise
        """
        # First, explicitly exclude stdlib modules
        # This prevents local files named 'os.py' from being treated as the real 'os' module
        if StdlibChecker.is_stdlib(module_name, language):
            return False

        self._build_local_module_cache()

        # Check if it's already prefixed with project name
        if module_name.startswith(f"{self.project_name}."):
            return True

        # Check if the top-level module exists locally
        top_level = module_name.split(".")[0]
        return top_level in self._local_packages

    def is_local_call(self, call_name: str, module_qn: str) -> bool:
        """Check if a function call belongs to the local codebase.

        Args:
            call_name: The function call name (e.g., 'LoRARequest', 'cache')
            module_qn: The current module qualified name

        Returns:
            True if the call should be processed, False if it should be skipped
        """
        # Handle different call patterns
        if "." in call_name:
            # Qualified call like 'Class.method' or 'module.function'
            parts = call_name.split(".")
            if len(parts) >= 2:
                # Check if the module part is local
                module_part = ".".join(parts[:-1])
                return self.is_local_module(module_part)

        # For unqualified calls, check if they're imported from local modules
        # This requires checking the import mapping, which we'll do in the caller
        return True  # Let the caller decide based on import mapping

    def should_process_call(
        self,
        call_name: str,
        module_qn: str,
        import_mapping: dict,
        language: str = "python",
    ) -> bool:
        """Determine if a call should be processed based on local module filtering.

        Returns True for:
        - Local module calls
        - Tracked external dependency calls

        Returns False for:
        - Standard library calls (os, sys, json, etc.) - explicitly checked
        - Untracked external library calls (numpy, pandas, etc.)

        Args:
            call_name: The function call name
            module_qn: The current module qualified name
            import_mapping: The import mapping for the current module
            language: The programming language (default: "python")

        Returns:
            True if the call should be processed, False if it should be skipped
        """
        # Handle qualified calls (e.g., os.path.join, mymodule.func)
        if "." in call_name:
            parts = call_name.split(".")
            if len(parts) >= 2:
                module_part = ".".join(parts[:-1])

                # Explicitly skip stdlib calls first
                if StdlibChecker.is_stdlib(module_part, language):
                    return False

                # Check if it's local
                if self.is_local_module(module_part, language):
                    return True

                # Check if it's a tracked external
                if self.is_tracked_external(module_part):
                    return True

                return False

        # Handle unqualified calls - check if they're imported from local modules
        if call_name in import_mapping:
            imported_name = import_mapping[call_name]

            # Explicitly skip stdlib imports
            if StdlibChecker.is_stdlib(imported_name, language):
                return False

            # Check if it's local
            if self.is_local_module(imported_name, language):
                return True

            # Check if it's a tracked external
            if self.is_tracked_external(imported_name):
                return True

            return False

        # For calls not in import mapping, check if they might be local
        # This includes calls to functions/classes defined in the same module
        return True

    def get_local_module_prefix(self, module_name: str) -> str:
        """Get the proper module prefix for a local module.

        Args:
            module_name: The module name

        Returns:
            The module name with project prefix if it's local, otherwise unchanged
        """
        if self.is_local_module(module_name):
            if not module_name.startswith(f"{self.project_name}."):
                return f"{self.project_name}.{module_name}"
        return module_name


def create_local_module_filter(repo_path: Path, project_name: str) -> LocalModuleFilter:
    """Create a local module filter instance.

    Args:
        repo_path: Path to the repository root
        project_name: Name of the project

    Returns:
        LocalModuleFilter instance
    """
    return LocalModuleFilter(repo_path, project_name)
