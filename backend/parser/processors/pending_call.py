# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Data structures for deferred call resolution and embedding generation.

This module provides lightweight data structures that replace AST caching,
reducing memory usage by ~99% while preserving all information needed for
call resolution and embedding generation.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class RelationshipType(StrEnum):
    """All relationship types supported in the knowledge graph.

    These are organized by which Pass creates them:

    Pass 1 (Discovery + Collection):
        - Structure: CONTAINS_FOLDER, CONTAINS_FILE, CONTAINS_MODULE
        - Definition: DEFINES, DEFINES_METHOD, DEFINES_VARIABLE
        - Reference: IMPORTS, INHERITS

    Pass 2 (Resolution + Embedding):
        - Call: CALLS, BINDS_TO
        - Variable: USES, ASSIGNS

    Pass 3 (Post-processing):
        - Inheritance: OVERRIDES
        - Dependency: DEPENDS_ON_EXTERNAL
    """

    # Structure relationships (Pass 1)
    # Note: Packages are now represented as Folder nodes with is_package=true
    CONTAINS_FOLDER = "CONTAINS_FOLDER"
    CONTAINS_FILE = "CONTAINS_FILE"
    CONTAINS_MODULE = "CONTAINS_MODULE"

    # Definition relationships (Pass 1)
    DEFINES = "DEFINES"
    DEFINES_METHOD = "DEFINES_METHOD"
    DEFINES_VARIABLE = "DEFINES_VARIABLE"  # File/Class → Variable

    # Reference relationships (Pass 1)
    IMPORTS = "IMPORTS"
    INHERITS = "INHERITS"

    # Call relationships (Pass 2 - via PendingCall)
    CALLS = "CALLS"
    BINDS_TO = "BINDS_TO"  # Python-C++ binding (pybind11)

    # Variable reference relationships (Pass 2)
    USES = "USES"  # Function/Method → Variable (read)
    ASSIGNS = "ASSIGNS"  # Function/Method → Variable (write)

    # Post-processing relationships (Pass 3)
    OVERRIDES = "OVERRIDES"
    DEPENDS_ON_EXTERNAL = "DEPENDS_ON_EXTERNAL"


@dataclass
class PendingCall:
    """Stores unresolved call information for deferred resolution.

    This lightweight structure replaces AST caching, reducing memory
    usage by ~99% while preserving all information needed for resolution.

    Attributes:
        caller_qn: Caller's qualified name (e.g., "module.MyClass.method")
        caller_type: Type of caller node - "Function" | "Method" | "File"
        callee_name: Called name (may be simple or qualified)
        call_type: Type of call - "function" | "method" | "constructor" | "binding"
        module_qn: Module qualified name for import resolution
        file_path: Source file path for error reporting
        line_number: Line number for error reporting
        relationship_type: Graph relationship type - "CALLS" | "BINDS_TO"
        class_context: Enclosing class qualified name (for super() resolution)
        local_var_types: Pre-computed type map for local variables in scope
        receiver_expr: For method calls, the receiver expression (e.g., "self", "obj")
        receiver_type: Inferred type of the receiver if available
        is_chained: Whether this is part of a method chain
        is_imported: Whether the callee is from an import
        import_source: Original import module if applicable
    """

    caller_qn: str
    caller_type: str  # "Function" | "Method" | "File"
    callee_name: str
    call_type: str  # "function" | "method" | "constructor" | "binding"
    module_qn: str
    file_path: str
    line_number: int

    # Relationship type for graph (default CALLS, can be BINDS_TO for pybind11)
    relationship_type: str = "CALLS"

    # Class context for super() resolution
    class_context: str | None = None

    # Pre-computed type info for local variables (replaces AST-based type inference)
    local_var_types: dict[str, str] = field(default_factory=dict)

    # Method call specifics
    receiver_expr: str | None = None
    receiver_type: str | None = None
    is_chained: bool = False

    # Import context
    is_imported: bool = False
    import_source: str | None = None


@dataclass
class SourceCode:
    """Stores extracted source code for embedding generation.

    This structure holds the source code of functions, methods, and classes
    that need embeddings generated. It captures the code content along with
    metadata for later batch processing.

    Attributes:
        qualified_name: Entity qualified name (e.g., "module.MyClass.method")
        source: Extracted source code text
        node_type: Node label - "Function" | "Method" | "Class"
        file_path: Source file path
        start_line: Start line number in the source file
        end_line: End line number in the source file
    """

    qualified_name: str
    source: str
    node_type: str  # "Function" | "Method" | "Class"
    file_path: str
    start_line: int
    end_line: int


@dataclass
class ResolvedCall:
    """Represents a successfully resolved call relationship.

    This structure is created after a PendingCall has been resolved
    to its target qualified name during Pass 2.

    Attributes:
        caller_qn: Caller qualified name
        callee_qn: Resolved callee qualified name
        call_type: Type of call - "function" | "method" | "constructor"
        is_external: True if the callee is an external library
    """

    caller_qn: str
    callee_qn: str
    call_type: str  # "function" | "method" | "constructor"
    is_external: bool = False


@dataclass
class EmbeddingResult:
    """Stores embedding generation result.

    This structure holds the embedding vector for a code entity,
    ready to be stored in the Memgraph database.

    Attributes:
        qualified_name: Entity qualified name
        embedding: Embedding vector (list of floats)
        node_type: Node label - "Function" | "Method" | "Class"
    """

    qualified_name: str
    embedding: list[float]
    node_type: str  # "Function" | "Method" | "Class"
