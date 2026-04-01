# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import re

from loguru import logger
from parser.utils import safe_decode_text
from tree_sitter import Node


class DynamicImportTracker:
    """Tracks dynamic imports and attribute extraction patterns.

    Detects patterns like:
        _dg = importlib.import_module("deep_gemm")
        _grouped_impl = getattr(_dg, "m_grouped_fp8_gemm_nt_contiguous", None)

    And builds a mapping:
        {"_grouped_impl": "deep_gemm.m_grouped_fp8_gemm_nt_contiguous"}
    """

    def __init__(self) -> None:
        # Maps variable name -> module reference
        # e.g., {"_dg": "deep_gemm"}
        self.module_references: dict[str, str] = {}

        # Maps variable name -> full qualified name
        # e.g., {"_grouped_impl": "deep_gemm.m_grouped_fp8_gemm_nt_contiguous"}
        self.dynamic_import_mapping: dict[str, str] = {}

        # Set of discovered external module names
        # e.g., {"deep_gemm", "triton"}
        self.discovered_externals: set[str] = set()

    def process_module(
        self,
        root_node: Node,
        module_qn: str,
        language: str,
    ) -> dict[str, str]:
        """Process a module AST to extract dynamic import patterns.

        Args:
            root_node: The root AST node of the module
            module_qn: The module's qualified name
            language: The programming language

        Returns:
            Dictionary mapping local variable names to external qualified names
        """
        if language != "python":
            # Currently only supporting Python
            return {}

        # Fast-path: skip expensive AST traversal if the source text doesn't
        # contain the trigger string.  This turns two O(N-nodes) recursive
        # traversals into a single O(source-length) byte search for >99% of
        # files, saving significant per-file processing time.
        source_text = root_node.text
        if source_text is None or b"import_module" not in source_text:
            return {}

        # Reset per-module state
        self.module_references.clear()
        self.dynamic_import_mapping.clear()

        # Pass 1: Find importlib.import_module() calls
        self._find_importlib_calls(root_node)

        # Pass 2: Only search for getattr() calls if we actually found
        # importlib references — otherwise there's nothing to alias.
        if self.module_references:
            self._find_getattr_calls(root_node)

        if self.dynamic_import_mapping:
            logger.debug(
                f"Found {len(self.dynamic_import_mapping)} dynamic imports in {module_qn}: "
                f"{list(self.dynamic_import_mapping.keys())}"
            )

        return self.dynamic_import_mapping.copy()

    def _find_importlib_calls(self, root_node: Node) -> None:
        """Find all importlib.import_module() calls and track variable assignments.

        Patterns detected:
            _dg = importlib.import_module("deep_gemm")
            mod = importlib.import_module("package.submodule")

        Uses iterative stack-based traversal to avoid Python recursion overhead.
        """
        stack = [root_node]
        while stack:
            node = stack.pop()
            # Check for assignment: var = importlib.import_module("module")
            if node.type == "assignment":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")

                if (
                    left
                    and right
                    and left.type == "identifier"
                    and right.type == "call"
                ):
                    var_name = safe_decode_text(left)
                    module_name = self._extract_importlib_module_name(right)

                    if var_name and module_name:
                        self.module_references[var_name] = module_name
                        self.discovered_externals.add(module_name.split(".")[0])
                        logger.debug(
                            f"Tracked dynamic import: {var_name} -> {module_name}"
                        )

            # Add children to stack
            stack.extend(node.children)

    def _extract_importlib_module_name(self, call_node: Node) -> str | None:
        """Extract module name from importlib.import_module() call."""
        func_node = call_node.child_by_field_name("function")
        args_node = call_node.child_by_field_name("arguments")

        if not func_node or not args_node:
            return None

        # Check if function is importlib.import_module
        if func_node.type == "attribute":
            obj_node = func_node.child_by_field_name("object")
            attr_node = func_node.child_by_field_name("attribute")

            if obj_node and attr_node:
                obj_name = safe_decode_text(obj_node)
                attr_name = safe_decode_text(attr_node)

                if obj_name == "importlib" and attr_name == "import_module":
                    # Extract the first string argument
                    for child in args_node.children:
                        if child.type in ("string", "concatenated_string"):
                            module_str = safe_decode_text(child)
                            if module_str:
                                # Strip string prefixes (f, b, r, u, etc.) and quotes
                                # e.g., f"verl" → verl, b"mod" → mod, "mod" → mod
                                cleaned = re.sub(
                                    r'^[fFbBrRuU]*["\']|["\']$', "", module_str
                                )
                                if cleaned and not (
                                    "{" in cleaned or "}" in cleaned
                                ):
                                    # Only accept static strings (no f-string interpolation)
                                    return cleaned

        return None

    def _find_getattr_calls(self, root_node: Node) -> None:
        """Find all getattr() calls that extract from dynamic modules.

        Patterns detected:
            _func = getattr(_dg, "function_name", None)
            _func = getattr(_dg, "function_name")

        Uses iterative stack-based traversal to avoid Python recursion overhead.
        """
        module_refs = self.module_references  # local ref for faster lookup
        stack = [root_node]
        while stack:
            node = stack.pop()
            if node.type == "assignment":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")

                if (
                    left
                    and right
                    and left.type == "identifier"
                    and right.type == "call"
                ):
                    var_name = safe_decode_text(left)
                    result = self._extract_getattr_info(right)

                    if var_name and result:
                        module_var, attr_name = result

                        # Check if module_var refers to a dynamically imported module
                        if module_var in module_refs:
                            module_name = module_refs[module_var]
                            full_qn = f"{module_name}.{attr_name}"
                            self.dynamic_import_mapping[var_name] = full_qn
                            logger.debug(
                                f"Tracked getattr alias: {var_name} -> {full_qn}"
                            )

            stack.extend(node.children)

    def _extract_getattr_info(self, call_node: Node) -> tuple[str, str] | None:
        """Extract (module_var, attr_name) from getattr(module_var, "attr_name", ...) call."""
        func_node = call_node.child_by_field_name("function")
        args_node = call_node.child_by_field_name("arguments")

        if not func_node or not args_node:
            return None

        # Check if function is getattr
        if func_node.type == "identifier" and safe_decode_text(func_node) == "getattr":
            args = [c for c in args_node.children if c.type not in ("(", ")", ",")]

            if len(args) >= 2:
                module_var_node = args[0]
                attr_name_node = args[1]

                if (
                    module_var_node.type == "identifier"
                    and attr_name_node.type == "string"
                ):
                    module_var = safe_decode_text(module_var_node)
                    attr_name = safe_decode_text(attr_name_node)

                    if module_var and attr_name:
                        return (module_var, attr_name.strip("'\""))

        return None

    def get_discovered_externals(self) -> set[str]:
        """Return set of external module names discovered via dynamic imports."""
        return self.discovered_externals.copy()

    def reset(self) -> None:
        """Reset all tracking state."""
        self.module_references.clear()
        self.dynamic_import_mapping.clear()
        self.discovered_externals.clear()
