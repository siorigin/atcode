# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from functools import lru_cache
from pathlib import Path

from loguru import logger

# Import language config to get authoritative extension mappings
from .language_config import LANGUAGE_CONFIGS


@lru_cache(maxsize=1)
def _build_extension_to_language_map() -> dict[str, str]:
    """Build a mapping from file extensions to language names.

    This function creates a comprehensive mapping from the language_config,
    ensuring all supported extensions are properly mapped to their languages.

    Returns:
        Dictionary mapping file extensions (with dots) to language names.
    """
    extension_map = {}

    for language_name, lang_config in LANGUAGE_CONFIGS.items():
        if lang_config.file_extensions:
            for ext in lang_config.file_extensions:
                # Normalize extension to include dot if not present
                normalized_ext = ext if ext.startswith(".") else f".{ext}"
                extension_map[normalized_ext] = language_name

    return extension_map


def detect_language_from_path(file_path: str) -> str:
    """Detect programming language from file extension.

    This function provides robust language detection by:
    1. Using the authoritative language_config as source of truth
    2. Supporting virtually any file extension
    3. Providing intelligent fallbacks for unknown extensions
    4. Caching results for performance

    Args:
        file_path: Path to the file (can be relative or absolute)

    Returns:
        Language identifier string (e.g., 'python', 'cpp', 'javascript')
        Defaults to 'python' for unknown extensions.
    """
    if not file_path:
        logger.warning("Empty file path provided to detect_language_from_path")
        return "python"

    # Get the file extension
    path_obj = Path(file_path)
    extension = path_obj.suffix.lower()  # e.g., '.py', '.cu', '.ts'

    if not extension:
        logger.debug(f"No file extension found for {file_path}, defaulting to python")
        return "python"

    # Look up in the extension map
    extension_map = _build_extension_to_language_map()

    if extension in extension_map:
        language = extension_map[extension]
        logger.debug(f"Detected language '{language}' for file {file_path}")
        return language

    # Intelligent fallback based on file name patterns
    file_name = path_obj.name.lower()

    # Check for common patterns
    if file_name in ("makefile", "dockerfile"):
        return "makefile"
    if file_name == "cmakelists.txt":
        return "cmake"
    if file_name.endswith(".lock") or file_name.endswith(".config"):
        return "text"

    # Check for double extensions (e.g., .test.ts, .spec.js)
    if "." in file_name:
        parts = file_name.split(".")
        if len(parts) > 2:
            # Try the last two parts as extension
            double_ext = f".{parts[-2]}.{parts[-1]}".lower()
            if double_ext in extension_map:
                language = extension_map[double_ext]
                logger.debug(
                    f"Detected language '{language}' for file {file_path} using double extension"
                )
                return language

    # Log unknown extension and return default
    logger.debug(
        f"Unknown file extension '{extension}' for {file_path}, defaulting to python"
    )
    return "python"


def get_supported_languages() -> list[str]:
    """Get list of all supported programming languages.

    Returns:
        List of language identifiers supported by the system.
    """
    return list(LANGUAGE_CONFIGS.keys())


def get_extensions_for_language(language: str) -> list[str]:
    """Get all file extensions supported by a specific language.

    Args:
        language: Language identifier (e.g., 'python', 'cpp')

    Returns:
        List of file extensions for the language, or empty list if not found.
    """
    if language not in LANGUAGE_CONFIGS:
        logger.warning(f"Language '{language}' not found in LANGUAGE_CONFIGS")
        return []

    lang_config = LANGUAGE_CONFIGS[language]
    return list(lang_config.file_extensions) if lang_config.file_extensions else []


def is_supported_language(language: str) -> bool:
    """Check if a language is supported by the system.

    Args:
        language: Language identifier to check

    Returns:
        True if the language is supported, False otherwise.
    """
    return language in LANGUAGE_CONFIGS


def is_supported_file(file_path: str) -> bool:
    """Check if a file extension is supported by the system.

    Args:
        file_path: Path to the file

    Returns:
        True if the file extension is supported, False otherwise.
    """
    extension = Path(file_path).suffix.lower()
    extension_map = _build_extension_to_language_map()
    return extension in extension_map
