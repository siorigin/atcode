# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

__version__ = "0.2.0"

# Re-export commonly used items for convenience
from .core.config import settings

__all__ = [
    "settings",
    "__version__",
]
