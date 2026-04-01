# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from loguru import logger


@dataclass
class CPUConfig:
    """Configuration for CPU limiting."""

    max_cpu_percent: int = 90
    max_workers: int | None = None
    nice_level: int = 5
    yield_interval_ms: int = 20
    batch_yield_threshold: int = 100
    # Kept for backward compatibility (no longer functional)
    use_cpu_affinity: bool = False
    min_idle_threshold: float = 80.0


class CPULimiter:
    """Manages CPU usage during intensive operations.

    Strategies:
    1. Process priority adjustment (nice level)
    2. Controlled parallelism (limited worker count)
    3. Cooperative yielding between batches
    """

    def __init__(self, config: CPUConfig | None = None):
        self.config = config or CPUConfig()
        self._max_workers = self._calculate_max_workers()
        self._apply_nice_level()

    def _calculate_max_workers(self) -> int:
        """Calculate optimal worker count based on configuration."""
        cpu_count = os.cpu_count() or 4

        if self.config.max_workers is not None:
            return min(self.config.max_workers, cpu_count)

        target_cores = max(1, int(cpu_count * self.config.max_cpu_percent / 100))
        return max(1, target_cores)

    def _apply_nice_level(self) -> None:
        """Apply nice level to reduce process priority."""
        try:
            if hasattr(os, "nice"):
                current_nice = os.nice(0)
                if current_nice < self.config.nice_level:
                    os.nice(self.config.nice_level - current_nice)
                    logger.info(f"Set process nice level to {self.config.nice_level}")
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not set nice level: {e}")

    def yield_cpu(self) -> None:
        """Yield CPU to other processes.

        No-op when yield_interval_ms is 0 for max throughput.
        """
        if self.config.yield_interval_ms > 0:
            time.sleep(self.config.yield_interval_ms / 1000.0)

    def create_thread_pool(self) -> ThreadPoolExecutor:
        """Create a thread pool with limited workers."""
        return ThreadPoolExecutor(max_workers=self._max_workers)

    def get_max_workers(self) -> int:
        """Get the calculated maximum worker count."""
        return self._max_workers
