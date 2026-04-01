# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from core.provider_utils import (
    is_openai_compatible_llm_provider,
    normalize_llm_provider,
)
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env from project root directory (parent of backend/)
# Note: config.py is now at backend/core/config.py, so parents[2]
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _PROJECT_ROOT / ".env"
load_dotenv(_ENV_FILE)


def get_project_root() -> Path:
    """Get the project root directory (atcode/)."""
    return _PROJECT_ROOT


def resolve_project_path(path: str | Path) -> Path:
    """Resolve a path relative to the project root.

    Relative paths from environment variables should behave consistently
    regardless of the current working directory, especially in containers
    where the backend runs from ``/app/backend``.
    """
    resolved = Path(path)
    if resolved.is_absolute():
        return resolved
    return (_PROJECT_ROOT / resolved).resolve()


def get_data_dir() -> Path:
    """Get the data directory (atcode/data/).

    All persistent data (wiki_doc, wiki_repos, wiki_chat) should be stored here.
    This uses a relative path from project root to ensure portability.
    """
    data_dir = _PROJECT_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_wiki_doc_dir(repo_name: str | None = None) -> Path:
    """Get the wiki_doc directory for storing generated documentation.

    Args:
        repo_name: Optional repository name for repo-specific subdirectory

    Returns:
        Path to wiki_doc directory (or repo subdirectory if repo_name provided)
    """
    wiki_doc = get_data_dir() / "wiki_doc"
    wiki_doc.mkdir(parents=True, exist_ok=True)
    if repo_name:
        repo_dir = wiki_doc / repo_name
        repo_dir.mkdir(parents=True, exist_ok=True)
        return repo_dir
    return wiki_doc


def get_wiki_repos_dir() -> Path:
    """Get the wiki_repos directory for storing cloned repositories."""
    wiki_repos = get_data_dir() / "wiki_repos"
    wiki_repos.mkdir(parents=True, exist_ok=True)
    return wiki_repos


def get_wiki_chat_dir() -> Path:
    """Get the wiki_chat directory for storing chat history."""
    wiki_chat = get_data_dir() / "wiki_chat"
    wiki_chat.mkdir(parents=True, exist_ok=True)
    return wiki_chat


def get_wiki_papers_dir() -> Path:
    """Get the wiki_papers directory for storing paper reading data."""
    wiki_papers = get_data_dir() / "wiki_papers"
    wiki_papers.mkdir(parents=True, exist_ok=True)
    return wiki_papers


def calculate_adaptive_cache_params(file_count: int) -> tuple[int, int]:
    """Calculate adaptive AST cache parameters based on file count.

    Args:
        file_count: Number of files to process

    Returns:
        Tuple of (max_entries, max_memory_mb)
    """
    from .config import settings

    # Use at least 1.2x file count to account for multiple parses
    target_entries = max(settings.AST_CACHE_MIN_ENTRIES, int(file_count * 1.2))
    target_entries = min(target_entries, settings.AST_CACHE_MAX_ENTRIES)

    # Scale memory proportionally (~10KB per entry)
    target_memory = max(settings.AST_CACHE_MIN_MEMORY_MB, int(target_entries * 0.05))
    target_memory = min(target_memory, settings.AST_CACHE_MAX_MEMORY_MB)

    return target_entries, target_memory


@dataclass
class ModelConfig:
    """Configuration for a specific model."""

    provider: str
    model_id: str
    api_key: str | None = None
    endpoint: str | None = None
    project_id: str | None = None
    region: str | None = None
    provider_type: str | None = None
    thinking_budget: int | None = None
    service_account_file: str | None = None


class AppConfig(BaseSettings):
    """
    Application Configuration using Pydantic for robust validation and type-safety.
    All settings are loaded from environment variables or a .env file.
    """

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra env vars (like LangSmith vars) that aren't in this config
    )

    # Redis settings - For task queue and caching
    # Used as the single source of truth for task state storage
    REDIS_URL: str = "redis://localhost:6379/0"

    # Memgraph settings - Local instance connection
    # Each machine connects to its local Memgraph (localhost)
    MEMGRAPH_HOST: str = "localhost"
    MEMGRAPH_PORT: int = 7687

    # Memgraph Mode: "standalone" or "replication"
    # - standalone: Single machine, all read/write operations on local instance
    # - replication: Multi-machine with MAIN/REPLICA setup via WAL
    MEMGRAPH_MODE: str = "standalone"

    # Replication settings (only used when MEMGRAPH_MODE=replication)
    # Role is AUTO-DETECTED based on local IP vs MAIN_HOST
    MEMGRAPH_MAIN_HOST: str = ""  # MAIN instance IP (handles writes)
    MEMGRAPH_MAIN_PORT: int = 7687

    # Explicit role override (optional, auto-detected if not set)
    # Set to "main" or "replica" to override auto-detection
    MEMGRAPH_ROLE: str = ""

    # Backward compatibility: legacy boolean flag (deprecated, use MEMGRAPH_MODE)
    MEMGRAPH_REPLICATION_ENABLED: bool = False

    # Legacy Build Instance mode (deprecated, use replication instead)
    MEMGRAPH_BUILD_HOST: str = ""
    MEMGRAPH_BUILD_PORT: int = 7687
    MEMGRAPH_BUILD_SYNC_MODE: str = "immediate"
    MEMGRAPH_BUILD_KEEP_AFTER_SYNC: bool = False

    API_PORT: int = (
        8000  # FastAPI backend port (used by MCP tools to call internal APIs)
    )
    MEMGRAPH_BATCH_SIZE: int = 10000  # Larger batches for faster processing
    MEMGRAPH_FULL_BUILD_BATCH_SIZE: int = (
        50000  # Even larger batches for full builds (use_create=True)
    )
    MEMGRAPH_WRITE_DELAY_MS: int = 0  # No delay for max throughput (was 30)

    # Incremental flush configuration (gradual flush during Pass 1/2)
    # Flush nodes and relationships periodically instead of all at Pass 4
    MEMGRAPH_INTERMEDIATE_FLUSH_INTERVAL: int = (
        10000  # Flush every N files/calls (reduced frequency for speed)
    )
    # ==========================================================================
    # LLM Configuration (Recommended - use these instead of ORCHESTRATOR_*)
    # ==========================================================================
    # Canonical user-facing values:
    # - openai-compatible
    # - gemini
    # - ollama
    # Legacy aliases still accepted: openai, google
    LLM_PROVIDER: str = ""
    LLM_MODEL: str = ""
    LLM_API_KEY: str | None = None
    LLM_ENDPOINT: str | None = None
    LLM_BASE_URL: str | None = None
    LLM_PROJECT_ID: str | None = None
    LLM_REGION: str = "us-central1"
    LLM_PROVIDER_TYPE: str | None = None
    LLM_THINKING_BUDGET: int | None = None
    LLM_SERVICE_ACCOUNT_FILE: str | None = None

    # ==========================================================================
    # Deprecated: ORCHESTRATOR_* variables (kept for backward compatibility)
    # Please migrate to LLM_* variables above
    # ==========================================================================
    ORCHESTRATOR_PROVIDER: str = ""
    ORCHESTRATOR_MODEL: str = ""
    ORCHESTRATOR_API_KEY: str | None = None
    ORCHESTRATOR_ENDPOINT: str | None = None
    ORCHESTRATOR_PROJECT_ID: str | None = None
    ORCHESTRATOR_REGION: str = "us-central1"
    ORCHESTRATOR_PROVIDER_TYPE: str | None = None
    ORCHESTRATOR_THINKING_BUDGET: int | None = None
    ORCHESTRATOR_SERVICE_ACCOUNT_FILE: str | None = None

    # Fallback endpoint for ollama
    LOCAL_MODEL_ENDPOINT: AnyHttpUrl = AnyHttpUrl("http://localhost:11434/v1")

    # General settings
    REPOS_BASE_PATH: str = (
        "../wiki_repos"  # Base path for repositories (relative to backend dir)
    )
    LANGGRAPH_RECURSION_LIMIT: int = (
        100  # Maximum iterations for LangGraph workflow (increased from 50)
    )

    # AST Cache settings (used during graph building)
    AST_CACHE_MAX_ENTRIES: int = 50000
    AST_CACHE_MAX_MEMORY_MB: int = 20000
    AST_CACHE_MIN_ENTRIES: int = 2000
    AST_CACHE_MIN_MEMORY_MB: int = 5000

    # OpenAI settings
    OPENAI_BASE_URL: str | None = None

    # Embedding settings (for semantic search)
    # Canonical user-facing values:
    # - openai-compatible
    # - voyage
    # - vllm
    # Legacy alias still accepted: openai
    # Uses text-embedding-3-small model via OpenAI-compatible API
    EMBEDDING_PROVIDER: str = "openai-compatible"
    EMBEDDING_MODEL: str | None = None  # Override default model
    EMBEDDING_DIMENSION: int = 1536  # text-embedding-3-small dimension
    EMBEDDING_MAX_CONCURRENT: int = (
        20  # Max concurrent API requests (increased for performance)
    )

    # Embedding granularity: "method" (all Functions + Methods) or "class" (only Class + top-level Functions)
    # - "method": Generate embeddings for all functions and methods (comprehensive, slower, more storage)
    # - "class": Generate embeddings only for classes and top-level functions (faster, less storage)
    EMBEDDING_GRANULARITY: str = "method"  # method, class

    # Embedding-specific API configuration (optional, falls back to LLM_* variables)
    EMBEDDING_API_KEY: str | None = None  # Independent API key for embedding service
    EMBEDDING_BASE_URL: str | None = None  # Independent base URL for embedding service

    # Incremental sync embedding behavior
    SYNC_SKIP_EMBEDDINGS: bool = (
        False  # Skip embeddings during incremental sync (for faster updates)
    )

    # Semantic Scholar API key (optional, for higher rate limits)
    S2_API_KEY: str | None = None

    # File size limits for parsing (to avoid slow parsing of huge files)
    MAX_FILE_SIZE_KB: int = 500  # Skip files larger than this (KB), 0 = no limit

    # Runtime overrides
    _active_orchestrator: ModelConfig | None = None

    def _get_default_config(self, role: str) -> ModelConfig:
        """Determine default configuration for a given role.

        Uses fallback logic: LLM_* variables take priority, then falls back
        to ORCHESTRATOR_* variables with deprecation warnings.
        """
        from loguru import logger

        role_upper = role.upper()

        # Try new LLM_* naming first, then fallback to old ORCHESTRATOR_* naming
        provider = normalize_llm_provider(self.LLM_PROVIDER)
        model = self.LLM_MODEL
        using_deprecated = False

        # Fallback to ORCHESTRATOR_* if LLM_* not set
        if not provider:
            provider = normalize_llm_provider(
                getattr(self, f"{role_upper}_PROVIDER", None) or ""
            )
            if provider:
                using_deprecated = True
                logger.warning(
                    f"Using deprecated {role_upper}_PROVIDER. "
                    f"Please migrate to LLM_PROVIDER."
                )

        if not model:
            model = getattr(self, f"{role_upper}_MODEL", None) or ""
            if model:
                using_deprecated = True
                logger.warning(
                    f"Using deprecated {role_upper}_MODEL. Please migrate to LLM_MODEL."
                )

        # Check for explicit provider configuration
        if provider and model:
            # Get API key with fallback
            api_key = self.LLM_API_KEY
            if not api_key:
                api_key = getattr(self, f"{role_upper}_API_KEY", None)
                if api_key and not using_deprecated:
                    logger.warning(
                        f"Using deprecated {role_upper}_API_KEY. "
                        f"Please migrate to LLM_API_KEY."
                    )

            # Get endpoint with fallback chain:
            # LLM_ENDPOINT -> ORCHESTRATOR_ENDPOINT -> LLM_BASE_URL -> OPENAI_BASE_URL
            endpoint = self.LLM_ENDPOINT
            if not endpoint:
                endpoint = getattr(self, f"{role_upper}_ENDPOINT", None)
                if endpoint and not using_deprecated:
                    logger.warning(
                        f"Using deprecated {role_upper}_ENDPOINT. "
                        f"Please migrate to LLM_ENDPOINT."
                    )
            if not endpoint and (
                is_openai_compatible_llm_provider(provider) or provider == "ollama"
            ):
                endpoint = self.LLM_BASE_URL
                if not endpoint:
                    endpoint = self.OPENAI_BASE_URL
                    if endpoint:
                        logger.warning(
                            "Using deprecated OPENAI_BASE_URL. "
                            "Please migrate to LLM_BASE_URL."
                        )

            # Get project_id with fallback
            project_id = self.LLM_PROJECT_ID
            if not project_id:
                project_id = getattr(self, f"{role_upper}_PROJECT_ID", None)
                if project_id and not using_deprecated:
                    logger.warning(
                        f"Using deprecated {role_upper}_PROJECT_ID. "
                        f"Please migrate to LLM_PROJECT_ID."
                    )

            # Get region with fallback
            region = self.LLM_REGION
            if region == "us-central1":  # Default value, check if old var is set
                old_region = getattr(self, f"{role_upper}_REGION", "us-central1")
                if old_region != "us-central1":
                    region = old_region
                    if not using_deprecated:
                        logger.warning(
                            f"Using deprecated {role_upper}_REGION. "
                            f"Please migrate to LLM_REGION."
                        )

            # Get provider_type with fallback
            provider_type = self.LLM_PROVIDER_TYPE
            if not provider_type:
                provider_type = getattr(self, f"{role_upper}_PROVIDER_TYPE", None)
                if provider_type and not using_deprecated:
                    logger.warning(
                        f"Using deprecated {role_upper}_PROVIDER_TYPE. "
                        f"Please migrate to LLM_PROVIDER_TYPE."
                    )

            # Get thinking_budget with fallback
            thinking_budget = self.LLM_THINKING_BUDGET
            if not thinking_budget:
                thinking_budget = getattr(self, f"{role_upper}_THINKING_BUDGET", None)
                if thinking_budget and not using_deprecated:
                    logger.warning(
                        f"Using deprecated {role_upper}_THINKING_BUDGET. "
                        f"Please migrate to LLM_THINKING_BUDGET."
                    )

            # Get service_account_file with fallback
            service_account_file = self.LLM_SERVICE_ACCOUNT_FILE
            if not service_account_file:
                service_account_file = getattr(
                    self, f"{role_upper}_SERVICE_ACCOUNT_FILE", None
                )
                if service_account_file and not using_deprecated:
                    logger.warning(
                        f"Using deprecated {role_upper}_SERVICE_ACCOUNT_FILE. "
                        f"Please migrate to LLM_SERVICE_ACCOUNT_FILE."
                    )

            return ModelConfig(
                provider=provider,
                model_id=model,
                api_key=api_key,
                endpoint=endpoint,
                project_id=project_id,
                region=region,
                provider_type=provider_type,
                thinking_budget=thinking_budget,
                service_account_file=service_account_file,
            )

        # Default to Ollama
        return ModelConfig(
            provider="ollama",
            model_id="llama3.2",
            endpoint=str(self.LOCAL_MODEL_ENDPOINT),
            api_key="ollama",
        )

    def _get_default_orchestrator_config(self) -> ModelConfig:
        """Determine default orchestrator configuration."""
        return self._get_default_config("orchestrator")

    @property
    def active_llm_config(self) -> ModelConfig:
        """Get the active LLM model configuration."""
        return self._active_orchestrator or self._get_default_orchestrator_config()

    @property
    def active_orchestrator_config(self) -> ModelConfig:
        """Get the active orchestrator model configuration.

        Deprecated: Use active_llm_config instead.
        """
        return self.active_llm_config

    def set_llm(self, provider: str, model: str, **kwargs: Any) -> None:
        """Set the active LLM configuration."""
        self._active_orchestrator = ModelConfig(
            provider=normalize_llm_provider(provider), model_id=model, **kwargs
        )

    def set_orchestrator(self, provider: str, model: str, **kwargs: Any) -> None:
        """Set the active orchestrator configuration.

        Deprecated: Use set_llm instead.
        """
        self.set_llm(provider, model, **kwargs)

    def resolve_batch_size(self, batch_size: int | None) -> int:
        """Return a validated batch size, falling back to config when needed."""
        resolved = self.MEMGRAPH_BATCH_SIZE if batch_size is None else batch_size
        if resolved < 1:
            raise ValueError("batch_size must be a positive integer")
        return resolved

    # =========================================================================
    # Memgraph Mode Detection and Connection Helpers
    # =========================================================================

    def _get_local_ip(self) -> str | None:
        """Get the local IP address of this machine."""
        import socket

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            pass
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return None

    @property
    def is_replication_mode(self) -> bool:
        """Check if running in replication mode.

        Supports both new MEMGRAPH_MODE and legacy MEMGRAPH_REPLICATION_ENABLED.
        """
        if self.MEMGRAPH_MODE.lower() == "replication":
            return True
        # Backward compatibility with legacy flag
        return self.MEMGRAPH_REPLICATION_ENABLED and bool(self.MEMGRAPH_MAIN_HOST)

    @property
    def detected_role(self) -> str:
        """Get the detected role for this machine (main/replica/standalone).

        Role detection priority:
        1. Explicit MEMGRAPH_ROLE setting
        2. Auto-detect based on local IP vs MAIN_HOST
        3. Default to "main" if can't detect
        """
        if not self.is_replication_mode:
            return "standalone"

        # Check explicit role setting
        if self.MEMGRAPH_ROLE.lower() in ("main", "replica"):
            return self.MEMGRAPH_ROLE.lower()

        # Auto-detect based on IP
        local_ip = self._get_local_ip()
        if local_ip and self.MEMGRAPH_MAIN_HOST:
            if local_ip == self.MEMGRAPH_MAIN_HOST:
                return "main"
            else:
                return "replica"

        # Default to main if can't detect
        return "main"

    @property
    def is_main_node(self) -> bool:
        """Check if this machine is the MAIN node (handles writes)."""
        return self.detected_role in ("main", "standalone")

    @property
    def is_replica_node(self) -> bool:
        """Check if this machine is a REPLICA node (reads only, writes proxy to MAIN)."""
        return self.detected_role == "replica"

    def get_write_connection(self) -> tuple[str, int]:
        """Get the Memgraph connection for write operations.

        Returns:
            (host, port) tuple for the write instance.
            - In standalone mode: returns local connection
            - In replication mode as MAIN: returns local connection
            - In replication mode as REPLICA: returns MAIN connection
        """
        if self.is_replication_mode and self.is_replica_node:
            # REPLICA: writes go to MAIN
            return (self.MEMGRAPH_MAIN_HOST, self.MEMGRAPH_MAIN_PORT)
        # Standalone or MAIN: write locally
        return (self.MEMGRAPH_HOST, self.MEMGRAPH_PORT)

    def get_read_connection(self) -> tuple[str, int]:
        """Get the Memgraph connection for read operations.

        Returns:
            (host, port) tuple for the read instance.
            Always returns local connection (reads are always local).
        """
        return (self.MEMGRAPH_HOST, self.MEMGRAPH_PORT)


settings = AppConfig()


# --- Global Ignore Patterns ---
# Directories and files to ignore during codebase scanning and real-time updates.
IGNORE_PATTERNS = {
    ".git",
    ".atcode",  # AtCode cache directory
    "venv",
    ".venv",
    "env",
    ".env",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    ".eggs",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".claude",
    ".idea",
    ".vscode",
    ".next",
    # Conda/pip package metadata directories
    ".dist-info",
    ".egg-info",
    ".egg",
    ".metadata",
    ".tox",
    ".hypothesis",
    "htmlcov",
    ".coverage",
    "__phello__",
    "__hello__",
    # Experiment tracking / logging directories
    "wandb",
    "mlruns",
    "lightning_logs",
    # Static assets directories (typically contain bundled/minified files)
    "static",
    "assets",
    "vendor",
    "third_party",
    "3rdparty",
}
IGNORE_SUFFIXES = {
    ".tmp",
    "~",
    # Bundled/minified JavaScript files
    ".min.js",
    ".bundle.js",
    "-bundle.js",
    ".min.css",
    ".bundle.css",
    # Source maps
    ".map",
    # Compiled assets
    ".wasm",
}

# Binary and non-text file extensions to skip during processing
# These files cannot be meaningfully parsed as source code
BINARY_FILE_EXTENSIONS = {
    # Images
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".webp",
    ".svg",
    ".tiff",
    ".tif",
    ".psd",
    ".ai",
    ".eps",
    ".raw",
    ".heic",
    ".heif",
    # Audio
    ".mp3",
    ".wav",
    ".ogg",
    ".flac",
    ".aac",
    ".wma",
    ".m4a",
    ".opus",
    # Video
    ".mp4",
    ".avi",
    ".mkv",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".mpeg",
    ".mpg",
    ".3gp",
    # Archives
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".tgz",
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    # Documents (binary formats)
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    # Fonts
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".eot",
    # Compiled/Binary
    ".pyc",
    ".pyo",
    ".pyd",
    ".class",
    ".jar",
    ".war",
    ".ear",
    ".o",
    ".a",
    ".so",
    ".dll",
    ".dylib",
    ".lib",
    ".exe",
    ".bin",
    ".rlib",
    ".bc",
    # Database files
    ".db",
    ".sqlite",
    ".sqlite3",
    ".mdb",
    # Other binary formats
    ".pkl",
    ".pickle",
    ".npy",
    ".npz",
    ".h5",
    ".hdf5",
    ".parquet",
    ".feather",
    ".arrow",
    # Model files
    ".pt",
    ".pth",
    ".onnx",
    ".pb",
    ".tflite",
    ".safetensors",
    ".ckpt",
    ".bin",
    # Cache and temp
    ".swp",
    ".swo",
    ".bak",
}
