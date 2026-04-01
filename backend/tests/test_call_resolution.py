# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for call resolution improvements in CallProcessor.

Tests cover:
1. self.method() resolution using class_context + MRO
2. Decorator call resolution (non-parenthesized)
3. Class.method() for same-module classmethod/staticmethod
4. __init__.py re-export chain resolution
"""

import threading
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Lightweight stub for FunctionRegistryTrie so we don't need Memgraph
# ---------------------------------------------------------------------------


class FakeRegistry(dict):
    """A dict-based stand-in for FunctionRegistryTrie."""

    def find_ending_with(self, suffix: str) -> list[str]:
        return [k for k in self if k.endswith(f".{suffix}") or k == suffix]


# ---------------------------------------------------------------------------
# Lightweight stub for MemgraphIngestor
# ---------------------------------------------------------------------------


class FakeIngestor:
    def __init__(self):
        self.relationships: list[tuple] = []
        self.nodes: list[tuple] = []

    def ensure_relationship_batch(self, src, rel_type, dst):
        self.relationships.append((src, rel_type, dst))

    def ensure_node_batch(self, label, props):
        self.nodes.append((label, props))

    def flush_relationships(self):
        pass


# ---------------------------------------------------------------------------
# Factory helper to build a minimal CallProcessor
# ---------------------------------------------------------------------------


def _build_call_processor(
    function_registry: dict | None = None,
    import_mapping: dict | None = None,
    class_inheritance: dict | None = None,
    project_name: str = "proj",
):
    """Create a CallProcessor with fake dependencies."""
    from parser.processors.call import CallProcessor

    registry = FakeRegistry(function_registry or {})
    simple_name_lookup: dict[str, set[str]] = defaultdict(set)
    for qn in registry:
        simple_name = qn.rsplit(".", 1)[-1]
        simple_name_lookup[simple_name].add(qn)

    ingestor = FakeIngestor()

    # Minimal ImportProcessor mock
    import_processor = MagicMock()
    import_processor.import_mapping = import_mapping or {}

    # Minimal TypeInferenceEngine mock
    type_inference = MagicMock()
    type_inference.build_local_variable_type_map.return_value = {}

    # Minimal LocalModuleFilter: always process
    with patch.object(CallProcessor, "__init__", lambda self, *a, **kw: None):
        cp = CallProcessor.__new__(CallProcessor)

    cp.ingestor = ingestor
    cp.repo_path = Path("/fake")
    cp.project_name = project_name
    cp.function_registry = registry
    cp.simple_name_lookup = simple_name_lookup
    cp.import_processor = import_processor
    cp.type_inference = type_inference
    cp.class_inheritance = class_inheritance or {}
    cp.callers_index = None
    cp._current_file_path = None
    cp.external_call_counts = {}
    cp._external_counts_lock = threading.Lock()
    cp._registry_lock = threading.Lock()
    cp._callers_lock = threading.Lock()
    cp._inheritance_cache = {}
    cp._inheritance_cache_lock = threading.Lock()

    # LocalModuleFilter that always says "process"
    local_filter = MagicMock()
    local_filter.should_process_call.return_value = True
    local_filter.is_tracked_external.return_value = False
    cp.local_module_filter = local_filter

    return cp, ingestor


# ===================================================================
# Test 1: self.method() resolution using class_context + MRO
# ===================================================================


class TestSelfMethodResolution:
    """self.method() should resolve to class_context.method or inherited."""

    def test_self_method_same_class(self):
        """self.some_method() resolves to the method on the same class."""
        cp, _ = _build_call_processor(
            function_registry={
                "proj.mod.MyClass": "Class",
                "proj.mod.MyClass.some_method": "Method",
                "proj.mod.MyClass.caller": "Method",
            },
        )
        result = cp._resolve_function_call(
            "self.some_method",
            "proj.mod",
            local_var_types=None,
            class_context="proj.mod.MyClass",
        )
        assert result is not None
        assert result[1] == "proj.mod.MyClass.some_method"

    def test_self_method_inherited(self):
        """self.parent_method() resolves via MRO to parent class."""
        cp, _ = _build_call_processor(
            function_registry={
                "proj.mod.Base": "Class",
                "proj.mod.Base.parent_method": "Method",
                "proj.mod.Child": "Class",
                "proj.mod.Child.caller": "Method",
            },
            class_inheritance={
                "proj.mod.Child": ["proj.mod.Base"],
            },
        )
        result = cp._resolve_function_call(
            "self.parent_method",
            "proj.mod",
            local_var_types=None,
            class_context="proj.mod.Child",
        )
        assert result is not None
        assert result[1] == "proj.mod.Base.parent_method"

    def test_cls_method_resolution(self):
        """cls.method() in @classmethod also resolves via class_context."""
        cp, _ = _build_call_processor(
            function_registry={
                "proj.mod.MyClass": "Class",
                "proj.mod.MyClass.create": "Method",
                "proj.mod.MyClass.from_dict": "Method",
            },
        )
        result = cp._resolve_function_call(
            "cls.create",
            "proj.mod",
            local_var_types=None,
            class_context="proj.mod.MyClass",
        )
        assert result is not None
        assert result[1] == "proj.mod.MyClass.create"


# ===================================================================
# Test 2: Same-module classmethod/staticmethod resolution
# ===================================================================


class TestClassMethodResolution:
    """Class.method() where Class is in the same module (not imported)."""

    def test_same_module_class_method(self):
        """Class.static_method() should resolve when Class is in same module."""
        cp, _ = _build_call_processor(
            function_registry={
                "proj.mod.MyClass": "Class",
                "proj.mod.MyClass.from_config": "Method",
                "proj.mod.helper": "Function",
            },
        )
        result = cp._resolve_function_call(
            "MyClass.from_config",
            "proj.mod",
            local_var_types=None,
            class_context=None,
        )
        assert result is not None
        assert result[1] == "proj.mod.MyClass.from_config"

    def test_same_module_inherited_class_method(self):
        """Class.inherited_method() resolves via MRO for same-module class."""
        cp, _ = _build_call_processor(
            function_registry={
                "proj.mod.Base": "Class",
                "proj.mod.Base.class_method": "Method",
                "proj.mod.Child": "Class",
            },
            class_inheritance={
                "proj.mod.Child": ["proj.mod.Base"],
            },
        )
        result = cp._resolve_function_call(
            "Child.class_method",
            "proj.mod",
            local_var_types=None,
            class_context=None,
        )
        assert result is not None
        assert result[1] == "proj.mod.Base.class_method"


# ===================================================================
# Test 3: __init__.py re-export chain resolution
# ===================================================================


class TestInitReexportResolution:
    """from package import Symbol where Symbol is re-exported via __init__.py."""

    def test_single_hop_reexport(self):
        """Symbol re-exported once through __init__.py."""
        cp, _ = _build_call_processor(
            function_registry={
                "proj.package.submodule.Symbol": "Class",
            },
            import_mapping={
                # The __init__.py of proj.package re-exports Symbol
                "proj.package": {"Symbol": "proj.package.submodule.Symbol"},
                # The calling module imports from package
                "proj.caller": {"Symbol": "proj.package.Symbol"},
            },
        )
        result = cp._resolve_init_reexport("proj.package.Symbol")
        assert result is not None
        assert result[1] == "proj.package.submodule.Symbol"

    def test_double_hop_reexport(self):
        """Symbol re-exported through two __init__.py levels."""
        cp, _ = _build_call_processor(
            function_registry={
                "proj.a.b.c.Symbol": "Function",
            },
            import_mapping={
                "proj.a": {"Symbol": "proj.a.b.Symbol"},
                "proj.a.b": {"Symbol": "proj.a.b.c.Symbol"},
            },
        )
        result = cp._resolve_init_reexport("proj.a.Symbol")
        assert result is not None
        assert result[1] == "proj.a.b.c.Symbol"

    def test_reexport_not_found(self):
        """Returns None if the symbol doesn't exist anywhere."""
        cp, _ = _build_call_processor(
            function_registry={},
            import_mapping={
                "proj.package": {},
            },
        )
        result = cp._resolve_init_reexport("proj.package.NonExistent")
        assert result is None

    def test_reexport_integrated_with_resolve_function_call(self):
        """End-to-end: _resolve_function_call follows re-export chain."""
        cp, _ = _build_call_processor(
            function_registry={
                "proj.package.submodule.MyFunc": "Function",
            },
            import_mapping={
                "proj.package": {"MyFunc": "proj.package.submodule.MyFunc"},
                "proj.caller": {"MyFunc": "proj.package.MyFunc"},
            },
        )
        result = cp._resolve_function_call(
            "MyFunc",
            "proj.caller",
            local_var_types=None,
            class_context=None,
        )
        assert result is not None
        assert result[1] == "proj.package.submodule.MyFunc"


# ===================================================================
# Test 4: Inherited method resolution caching
# ===================================================================


class TestInheritedMethodResolution:
    """Verify the BFS MRO traversal works for multi-level inheritance."""

    def test_grandparent_method(self):
        """Method defined on grandparent should be found."""
        cp, _ = _build_call_processor(
            function_registry={
                "proj.mod.A": "Class",
                "proj.mod.A.method": "Method",
                "proj.mod.B": "Class",
                "proj.mod.C": "Class",
            },
            class_inheritance={
                "proj.mod.C": ["proj.mod.B"],
                "proj.mod.B": ["proj.mod.A"],
            },
        )
        result = cp._resolve_inherited_method("proj.mod.C", "method")
        assert result is not None
        assert result[1] == "proj.mod.A.method"

    def test_diamond_inheritance(self):
        """Diamond inheritance should find the first match in BFS order."""
        cp, _ = _build_call_processor(
            function_registry={
                "proj.mod.A": "Class",
                "proj.mod.A.method": "Method",
                "proj.mod.B": "Class",
                "proj.mod.C": "Class",
                "proj.mod.D": "Class",
            },
            class_inheritance={
                "proj.mod.D": ["proj.mod.B", "proj.mod.C"],
                "proj.mod.B": ["proj.mod.A"],
                "proj.mod.C": ["proj.mod.A"],
            },
        )
        result = cp._resolve_inherited_method("proj.mod.D", "method")
        assert result is not None
        assert result[1] == "proj.mod.A.method"

    def test_caching_works(self):
        """Second lookup should hit the cache."""
        cp, _ = _build_call_processor(
            function_registry={
                "proj.mod.Base": "Class",
                "proj.mod.Base.method": "Method",
                "proj.mod.Child": "Class",
            },
            class_inheritance={
                "proj.mod.Child": ["proj.mod.Base"],
            },
        )
        # First call populates cache
        result1 = cp._resolve_inherited_method("proj.mod.Child", "method")
        # Second call should use cache
        result2 = cp._resolve_inherited_method("proj.mod.Child", "method")
        assert result1 == result2
        assert ("proj.mod.Child", "method") in cp._inheritance_cache
