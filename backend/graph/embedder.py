# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""API-based embedding client supporting OpenAI-compatible providers.

This module provides embedding generation using the OpenAI API (or compatible APIs).
It replaces the previous GPU-based UniXcoder implementation with a simpler,
API-based approach that:
- Requires no GPU resources
- Uses text-embedding-3-small model (1536 dimensions)
- Supports batch processing with automatic chunking
- Provides both sync and async interfaces
"""

import asyncio
from typing import Any

from core.provider_utils import normalize_embedding_provider
from loguru import logger

# Embedding provider configurations
EMBEDDING_PROVIDERS = {
    "openai-compatible": {
        "model": "text-embedding-3-small",
        "dimension": 1536,
        "max_batch_size": 2048,
        "max_tokens_per_request": 300000,
        "max_input_tokens": 8191,  # OpenAI's limit for text-embedding-3-small
    },
    "voyage": {
        "model": "voyage-code-3",
        "dimension": 1024,
        "max_batch_size": 128,
        "max_tokens_per_request": 120000,
        "max_input_tokens": 16000,  # Voyage's limit
    },
    "vllm": {
        "model": "embed",  # Default vLLM embedding model used by the local helper script
        "dimension": 1024,  # Will be auto-detected from API response
        "max_batch_size": 2048,
        "max_tokens_per_request": 300000,
        "max_input_tokens": 4096,  # Default vLLM max_model_len used by the local helper script
    },
}


class EmbeddingClient:
    """Unified embedding client supporting API-based embedding providers.

    This client provides:
    - Configurable provider (OpenAI-compatible, Voyage, or vLLM)
    - Automatic batching for large input sets
    - Async support with concurrency limiting
    - Custom base URL support for alternative endpoints
    """

    def __init__(
        self,
        provider: str = "openai-compatible",
        api_key: str | None = None,
        base_url: str | None = None,
        max_concurrent: int = 10,
    ):
        """Initialize the embedding client.

        Args:
            provider: Provider name ("openai-compatible", "voyage", or "vllm")
            api_key: API key for authentication
            base_url: Custom base URL for OpenAI-compatible APIs
            max_concurrent: Maximum concurrent API requests
        """
        self.provider = normalize_embedding_provider(provider) or "openai-compatible"
        self.config = EMBEDDING_PROVIDERS.get(
            self.provider, EMBEDDING_PROVIDERS["openai-compatible"]
        )
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._client = None
        self._async_client = None
        self._api_key = api_key
        self._base_url = base_url

    def _get_sync_client(self) -> Any:
        """Get or create the synchronous OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError(
                    "OpenAI package is required for embeddings. "
                    "Install with: pip install openai"
                )

            kwargs: dict[str, Any] = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            if self._base_url:
                kwargs["base_url"] = self._base_url

            self._client = OpenAI(**kwargs)
        return self._client

    def _get_async_client(self) -> Any:
        """Get or create the async OpenAI client."""
        if self._async_client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise RuntimeError(
                    "OpenAI package is required for embeddings. "
                    "Install with: pip install openai"
                )

            kwargs: dict[str, Any] = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            if self._base_url:
                kwargs["base_url"] = self._base_url

            self._async_client = AsyncOpenAI(**kwargs)
        return self._async_client

    def _truncate_text(self, text: str) -> str:
        """Truncate text to fit within the model's max token limit.

        Uses a conservative estimate for code (2.5 chars per token) with
        a 10% safety margin. Code is more token-dense than English text.

        Args:
            text: Text to potentially truncate

        Returns:
            Truncated text if necessary
        """
        max_tokens = self.config.get("max_input_tokens", 4096)

        # Code is more token-dense than plain text (~2.5 chars/token vs ~4 for English)
        # Also add a 10% safety margin to avoid hitting the limit
        chars_per_token = 2.5
        safety_margin = 0.9  # Use 90% of max to be safe
        max_chars = int(max_tokens * chars_per_token * safety_margin)

        if len(text) > max_chars:
            # Truncate and add indicator
            truncated = text[: max_chars - 20] + "\n... [truncated]"
            logger.info(
                f"Truncated text from {len(text)} to {len(truncated)} chars (max_tokens={max_tokens})"
            )
            return truncated
        return text

    @property
    def dimension(self) -> int:
        """Get the embedding dimension for the current provider."""
        return self.config["dimension"]

    def embed_one(self, text: str) -> list[float]:
        """Embed a single text string synchronously.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        client = self._get_sync_client()
        truncated_text = self._truncate_text(text)
        response = client.embeddings.create(
            input=truncated_text, model=self.config["model"]
        )
        return response.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts synchronously with automatic batching.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        client = self._get_sync_client()
        max_batch = self.config["max_batch_size"]
        all_embeddings: list[list[float]] = []

        # Truncate all texts before processing
        truncated_texts = [self._truncate_text(t) for t in texts]

        # Process in batches
        for i in range(0, len(truncated_texts), max_batch):
            batch = truncated_texts[i : i + max_batch]
            response = client.embeddings.create(input=batch, model=self.config["model"])
            # Sort by index to maintain order
            sorted_data = sorted(response.data, key=lambda x: x.index)
            batch_embeddings = [d.embedding for d in sorted_data]
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    async def embed_one_async(self, text: str) -> list[float]:
        """Embed a single text string asynchronously.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        async with self._semaphore:
            client = self._get_async_client()
            truncated_text = self._truncate_text(text)
            response = await client.embeddings.create(
                input=truncated_text, model=self.config["model"]
            )
            return response.data[0].embedding

    async def embed_batch_async(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts asynchronously with automatic batching.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        # Truncate all texts before processing
        truncated_texts = [self._truncate_text(t) for t in texts]

        max_batch = self.config["max_batch_size"]
        batches = [
            truncated_texts[i : i + max_batch]
            for i in range(0, len(truncated_texts), max_batch)
        ]

        async def process_batch(batch: list[str]) -> list[list[float]]:
            async with self._semaphore:
                client = self._get_async_client()
                response = await client.embeddings.create(
                    input=batch, model=self.config["model"]
                )
                sorted_data = sorted(response.data, key=lambda x: x.index)
                return [d.embedding for d in sorted_data]

        results = await asyncio.gather(*[process_batch(b) for b in batches])
        return [emb for batch_result in results for emb in batch_result]


# Global client instance (lazy initialization)
_client: EmbeddingClient | None = None
_embedding_available: bool | None = None  # Cache for availability check


def is_embedding_available() -> bool:
    """Check if embedding service is properly configured and available.

    This function checks:
    1. For OpenAI/Voyage providers: requires API key
    2. For vLLM provider: requires base URL (local deployment, no API key needed)
    3. Tests connection with a simple embedding request

    Returns:
        True if embedding service is available and working, False otherwise
    """
    global _embedding_available

    # Return cached result if already checked
    if _embedding_available is not None:
        return _embedding_available

    from core.config import settings

    provider = normalize_embedding_provider(
        getattr(settings, "EMBEDDING_PROVIDER", "openai-compatible")
    ) or "openai-compatible"
    api_key = (
        settings.EMBEDDING_API_KEY
        or settings.LLM_API_KEY
        or settings.ORCHESTRATOR_API_KEY
    )
    base_url = (
        settings.EMBEDDING_BASE_URL or settings.LLM_BASE_URL or settings.OPENAI_BASE_URL
    )

    # Check basic configuration
    if provider in ("openai-compatible", "voyage"):
        # Cloud providers require API key
        if not api_key:
            logger.info(
                f"Embedding not available: No API key configured for provider '{provider}'"
            )
            _embedding_available = False
            return False
    elif provider == "vllm":
        # vLLM (local deployment) requires base URL
        if not base_url:
            logger.info(
                "Embedding not available: No base URL configured for vLLM provider"
            )
            _embedding_available = False
            return False

    # Try a simple test embedding to verify connectivity
    try:
        client = get_embedding_client()
        # Test with minimal input
        test_result = client.embed_one("test")
        if test_result and len(test_result) > 0:
            logger.info(
                f"Embedding service available: provider={provider}, dimension={len(test_result)}"
            )
            _embedding_available = True
            return True
    except Exception as e:
        logger.warning(f"Embedding service not available: {e}")
        _embedding_available = False
        return False

    _embedding_available = False
    return False


def reset_embedding_availability_cache():
    """Reset the embedding availability cache (useful for testing or reconfiguration)."""
    global _embedding_available, _client
    _embedding_available = None
    _client = None


def get_embedding_client() -> EmbeddingClient:
    """Get or create the global embedding client.

    The client is configured using environment variables:
    - EMBEDDING_PROVIDER: Provider name (openai-compatible, voyage, vllm)
    - EMBEDDING_API_KEY: API key for embedding service (falls back to LLM_API_KEY, then ORCHESTRATOR_API_KEY)
    - EMBEDDING_BASE_URL: Base URL for embedding service (falls back to LLM_BASE_URL, then OPENAI_BASE_URL)
    - EMBEDDING_MAX_CONCURRENT: Max concurrent API requests
    - EMBEDDING_MODEL: Override default model name
    - EMBEDDING_DIMENSION: Override default embedding dimension

    Returns:
        Configured EmbeddingClient instance
    """
    global _client
    if _client is None:
        from core.config import settings

        # Provider selection: support vllm for local deployment
        provider = normalize_embedding_provider(
            getattr(settings, "EMBEDDING_PROVIDER", "openai-compatible")
        ) or "openai-compatible"
        if provider not in EMBEDDING_PROVIDERS:
            logger.warning(
                "Unknown embedding provider "
                f"'{provider}', falling back to 'openai-compatible'"
            )
            provider = "openai-compatible"

        # API key priority: EMBEDDING_API_KEY > LLM_API_KEY > ORCHESTRATOR_API_KEY
        api_key = (
            settings.EMBEDDING_API_KEY
            or settings.LLM_API_KEY
            or settings.ORCHESTRATOR_API_KEY
        )

        # Base URL priority: EMBEDDING_BASE_URL > LLM_BASE_URL > OPENAI_BASE_URL
        base_url = (
            settings.EMBEDDING_BASE_URL
            or settings.LLM_BASE_URL
            or settings.OPENAI_BASE_URL
        )

        # Allow model override via environment variable
        model_override = getattr(settings, "EMBEDDING_MODEL", None)
        if model_override:
            # Update the provider config with custom model
            EMBEDDING_PROVIDERS[provider]["model"] = model_override

        # Use configured max concurrent from settings (default 10, can be increased for vllm)
        max_concurrent = settings.EMBEDDING_MAX_CONCURRENT
        if provider == "vllm":
            # vLLM can handle higher concurrency for local deployment
            max_concurrent = max(max_concurrent, 20)

        _client = EmbeddingClient(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            max_concurrent=max_concurrent,
        )
        logger.info(
            f"Initialized embedding client: provider={provider}, "
            f"model={_client.config['model']}, dimension={_client.dimension}, "
            f"max_concurrent={max_concurrent}, base_url={base_url or 'default'}"
        )
    return _client


def get_embedding_dimension() -> int:
    """Get embedding dimension from current provider.

    Returns:
        Embedding dimension (1536 for text-embedding-3-small)
    """
    return get_embedding_client().dimension


# ============================================================================
# Public API (backward compatible function names)
# ============================================================================


def embed_code(code: str, max_length: int = 512) -> list[float]:
    """Generate embedding for a single code snippet.

    This is the main function for embedding code. It uses the OpenAI
    text-embedding-3-small model which produces 1536-dimensional vectors.

    Args:
        code: Source code to embed
        max_length: Maximum token length (not used in API-based approach,
                   kept for backward compatibility)

    Returns:
        1536-dimensional embedding as list of floats
    """
    _ = max_length  # API handles truncation automatically
    return get_embedding_client().embed_one(code)


def embed_code_batch(
    codes: list[str],
    max_length: int = 512,
    batch_size: int = 32,
    gpu_id: int | None = None,
) -> list[list[float]]:
    """Generate embeddings for multiple code snippets.

    This function provides efficient batch embedding by making a single
    API call with multiple inputs (up to 2048 per request).

    Args:
        codes: List of source code strings to embed
        max_length: Maximum token length (not used, kept for compatibility)
        batch_size: Batch size hint (not used, API handles batching)
        gpu_id: GPU ID (not used in API-based approach)

    Returns:
        List of 1536-dimensional embeddings as lists of floats
    """
    _ = max_length, batch_size, gpu_id  # Not used in API-based approach
    if not codes:
        return []
    return get_embedding_client().embed_batch(codes)


def embed_code_batch_multi_gpu(
    codes: list[str],
    max_length: int = 512,
    batch_size: int = 512,
    num_gpus: int | None = None,
) -> list[list[float]]:
    """Generate embeddings using the API (replaces multi-GPU approach).

    This function is kept for backward compatibility. In the API-based
    approach, there's no GPU involved - the API handles parallelism.

    Args:
        codes: List of source code strings to embed
        max_length: Maximum token length (not used)
        batch_size: Batch size hint (not used)
        num_gpus: Number of GPUs (not used)

    Returns:
        List of 1536-dimensional embeddings as lists of floats
    """
    _ = max_length, batch_size, num_gpus
    return embed_code_batch(codes)


def embed_code_batch_for_repo(
    codes: list[str],
    repo_name: str,
    max_length: int = 512,
    batch_size: int = 512,
    parallel: bool = True,
    max_concurrent: int = 5,
) -> list[list[float]]:
    """Generate embeddings for a specific repository.

    Supports parallel API calls for improved performance with large codebases.

    Args:
        codes: List of source code strings to embed
        repo_name: Repository identifier (logged for debugging)
        max_length: Maximum token length (not used)
        batch_size: Batch size hint (not used)
        parallel: If True, use parallel API calls for faster processing
        max_concurrent: Maximum concurrent API requests when parallel=True

    Returns:
        List of 1536-dimensional embeddings as lists of floats
    """
    _ = max_length, batch_size
    if not codes:
        return []
    logger.debug(
        f"Generating embeddings for repo '{repo_name}': {len(codes)} items (parallel={parallel})"
    )

    if parallel and len(codes) > 2048:
        # Use parallel processing for large batches
        return embed_code_batch_parallel(codes, max_concurrent=max_concurrent)
    return get_embedding_client().embed_batch(codes)


def embed_code_batch_parallel(
    codes: list[str],
    max_concurrent: int = 5,
) -> list[list[float]]:
    """Generate embeddings using parallel API calls.

    This function splits the input into chunks and processes them
    concurrently using asyncio, significantly reducing total time
    for large batches.

    Args:
        codes: List of source code strings to embed
        max_concurrent: Maximum concurrent API requests

    Returns:
        List of embeddings in the same order as input codes
    """
    if not codes:
        return []

    client = get_embedding_client()
    max_batch = client.config["max_batch_size"]

    # Truncate all codes before processing
    truncated_codes = [client._truncate_text(code) for code in codes]

    # Split into batches
    batches = [
        truncated_codes[i : i + max_batch]
        for i in range(0, len(truncated_codes), max_batch)
    ]

    if len(batches) <= 1:
        # Single batch, no need for parallel processing
        return client.embed_batch(codes)

    logger.info(
        f"Processing {len(codes)} embeddings in {len(batches)} parallel batches (max_concurrent={max_concurrent})"
    )

    async def process_all_batches():
        semaphore = asyncio.Semaphore(max_concurrent)
        async_client = client._get_async_client()

        async def process_batch(
            batch_idx: int, batch: list[str]
        ) -> tuple[int, list[list[float]]]:
            async with semaphore:
                try:
                    response = await async_client.embeddings.create(
                        input=batch, model=client.config["model"]
                    )
                    sorted_data = sorted(response.data, key=lambda x: x.index)
                    return (batch_idx, [d.embedding for d in sorted_data])
                except Exception as e:
                    logger.error(f"Batch {batch_idx} failed: {e}")
                    raise

        tasks = [process_batch(i, batch) for i, batch in enumerate(batches)]
        results = await asyncio.gather(*tasks)

        # Sort by batch index and flatten
        results.sort(key=lambda x: x[0])
        return [emb for _, batch_embs in results for emb in batch_embs]

    # Run async code in sync context
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If already in async context, create new loop in thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, process_all_batches())
                return future.result()
        else:
            return loop.run_until_complete(process_all_batches())
    except RuntimeError:
        # No event loop, create one
        return asyncio.run(process_all_batches())


def release_repo_gpu(repo_name: str) -> None:
    """Release GPU allocation (no-op in API-based approach).

    This function is kept for backward compatibility.

    Args:
        repo_name: Repository identifier
    """
    logger.debug(f"release_repo_gpu called for '{repo_name}' (no-op in API mode)")


def get_available_gpu_count() -> int:
    """Get available GPU count (returns 0 in API-based approach).

    Returns:
        0 (no GPU needed for API-based embeddings)
    """
    return 0


def get_device_info() -> dict[str, Any]:
    """Get information about the embedding device/API.

    Returns:
        Dictionary with device/API information
    """
    client = get_embedding_client()
    return {
        "cuda_available": False,
        "device": "api",
        "device_name": f"OpenAI API ({client.config['model']})",
        "embedding_dimension": client.dimension,
        "provider": client.provider,
        "base_url": client._base_url or "https://api.openai.com/v1",
    }


# ============================================================================
# Async API
# ============================================================================


async def embed_code_async(code: str) -> list[float]:
    """Async: Generate embedding for a single code snippet.

    Args:
        code: Source code to embed

    Returns:
        1536-dimensional embedding as list of floats
    """
    return await get_embedding_client().embed_one_async(code)


async def embed_code_batch_async(codes: list[str]) -> list[list[float]]:
    """Async: Generate embeddings for multiple code snippets.

    Args:
        codes: List of source code strings to embed

    Returns:
        List of 1536-dimensional embeddings
    """
    if not codes:
        return []
    return await get_embedding_client().embed_batch_async(codes)
