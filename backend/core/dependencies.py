# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import importlib.util

# Cache dependency checks to avoid repeated module lookups
_dependency_cache: dict[str, bool] = {}


def _check_dependency(module_name: str) -> bool:
    """Check if a module is available, with caching."""
    if module_name not in _dependency_cache:
        _dependency_cache[module_name] = (
            importlib.util.find_spec(module_name) is not None
        )
    return _dependency_cache[module_name]


def has_openai() -> bool:
    """Check if OpenAI client is available."""
    return _check_dependency("openai")


def check_dependencies(required_modules: list[str]) -> bool:
    """Check if all required modules are available.

    Args:
        required_modules: List of module names to check

    Returns:
        True if all modules are available, False otherwise
    """
    return all(_check_dependency(module) for module in required_modules)


def get_missing_dependencies(required_modules: list[str]) -> list[str]:
    """Get list of missing dependencies.

    Args:
        required_modules: List of module names to check

    Returns:
        List of missing module names
    """
    return [module for module in required_modules if not _check_dependency(module)]


# Commonly used dependency combinations
EMBEDDING_DEPENDENCIES = ["openai"]
