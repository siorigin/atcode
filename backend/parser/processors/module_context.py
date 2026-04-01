# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field

from parser.utils import safe_decode_text
from tree_sitter import Node


@dataclass
class ModuleContextResult:
    """Structured result from module context extraction."""

    raw_context: str  # Full text for storage
    imports: list[str] = field(default_factory=list)
    constants: list[str] = field(default_factory=list)
    type_aliases: list[str] = field(default_factory=list)
    conditional_blocks: list[str] = field(default_factory=list)
    docstring: str | None = None

    # Metadata
    total_lines: int = 0
    truncated: bool = False


# Node types to include in module context
CONTEXT_NODE_TYPES = {
    "import_statement",
    "import_from_statement",
    "future_import_statement",
    "expression_statement",  # Includes assignments, docstrings
    "if_statement",  # For TYPE_CHECKING blocks
    "try_statement",  # For conditional imports
    "type_alias_statement",  # Python 3.12+ type aliases
    "assert_statement",  # Top-level assertions (rare but valid)
}

# Node types that are NOT part of module context
DEFINITION_NODE_TYPES = {
    "function_definition",
    "class_definition",
    "decorated_definition",
}

# Maximum context size to prevent bloat
MAX_CONTEXT_BYTES = 10 * 1024  # 10KB


def extract_module_context(
    root_node: Node,
    max_size: int = MAX_CONTEXT_BYTES,
) -> ModuleContextResult:
    """Extract module-level context from a Python AST.

    This function extracts all top-level statements that provide context
    for understanding the code within a module, including:
    - Import statements (regardless of position)
    - Top-level assignments (constants, configurations)
    - Conditional blocks (TYPE_CHECKING, try/except imports)
    - Module docstring

    Args:
        root_node: The root AST node of the module
        max_size: Maximum size of context in bytes (default 10KB)

    Returns:
        ModuleContextResult with extracted context and metadata
    """
    result = ModuleContextResult(raw_context="")
    context_parts: list[str] = []
    current_size = 0

    for i, child in enumerate(root_node.children):
        if child.type in DEFINITION_NODE_TYPES:
            # Skip function/class definitions
            continue

        if child.type not in CONTEXT_NODE_TYPES:
            # Skip other node types (comments handled separately if needed)
            continue

        node_text = safe_decode_text(child)
        if not node_text:
            continue

        node_text = node_text.strip()
        node_size = len(node_text.encode("utf-8"))

        # Check size limit
        if current_size + node_size > max_size:
            result.truncated = True
            break

        current_size += node_size

        # Categorize the node
        if child.type in (
            "import_statement",
            "import_from_statement",
            "future_import_statement",
        ):
            result.imports.append(node_text)
            context_parts.append(node_text)

        elif child.type == "expression_statement":
            first_child = child.children[0] if child.children else None

            # Check if it's a module docstring (first statement, is a string)
            if i == 0 and first_child and first_child.type == "string":
                result.docstring = node_text
                context_parts.append(node_text)
            # Check if it's an assignment (constant)
            elif first_child and first_child.type == "assignment":
                result.constants.append(node_text)
                context_parts.append(node_text)
            # Also handle augmented assignments (+=, etc.)
            elif first_child and first_child.type == "augmented_assignment":
                result.constants.append(node_text)
                context_parts.append(node_text)

        elif child.type == "type_alias_statement":
            result.type_aliases.append(node_text)
            context_parts.append(node_text)

        elif child.type in ("if_statement", "try_statement"):
            result.conditional_blocks.append(node_text)
            context_parts.append(node_text)

        elif child.type == "assert_statement":
            # Include top-level assertions
            result.constants.append(node_text)
            context_parts.append(node_text)

    # Build raw context with preserved ordering
    result.raw_context = "\n".join(context_parts)
    result.total_lines = result.raw_context.count("\n") + 1 if result.raw_context else 0

    return result


def extract_module_context_simple(root_node: Node) -> str:
    """Simplified extraction returning just the raw context string.

    Use this when you only need the text for storage and don't need
    the categorized structure.

    Args:
        root_node: The root AST node of the module

    Returns:
        String containing all module context code
    """
    return extract_module_context(root_node).raw_context


def extract_module_context_for_language(
    root_node: Node,
    language: str,
) -> str:
    """Extract module context based on the programming language.

    Args:
        root_node: The root AST node of the module
        language: The programming language identifier

    Returns:
        String containing all module context code for the given language
    """
    if language == "python":
        return extract_module_context_simple(root_node)
    elif language in ("javascript", "typescript"):
        return _extract_js_ts_module_context(root_node)
    elif language == "rust":
        return _extract_rust_module_context(root_node)
    elif language == "go":
        return _extract_go_module_context(root_node)
    elif language == "java":
        return _extract_java_module_context(root_node)
    elif language == "cpp":
        return _extract_cpp_module_context(root_node)
    else:
        # Default to Python-style extraction for unknown languages
        return extract_module_context_simple(root_node)


def _extract_js_ts_module_context(root_node: Node) -> str:
    """Extract module context for JavaScript/TypeScript files.

    Includes:
    - import statements
    - export statements (re-exports)
    - top-level const/let/var declarations
    """
    _js_ts_definition_node_types = {
        "function_declaration",
        "class_declaration",
        "function",
        "arrow_function",
    }

    context_parts: list[str] = []

    for child in root_node.children:
        if child.type in _js_ts_definition_node_types:
            # Stop at function/class definitions
            continue

        if child.type == "import_statement":
            node_text = safe_decode_text(child)
            if node_text:
                context_parts.append(node_text.strip())
        elif child.type == "export_statement":
            # Include re-exports: export { x } from './module'
            node_text = safe_decode_text(child)
            if node_text:
                # Check if it's a re-export (has 'from' keyword)
                if "from" in node_text:
                    context_parts.append(node_text.strip())
        elif child.type in ("lexical_declaration", "variable_declaration"):
            # Only top-level constants, not inside functions
            node_text = safe_decode_text(child)
            if node_text:
                context_parts.append(node_text.strip())

    return "\n".join(context_parts)


def _extract_rust_module_context(root_node: Node) -> str:
    """Extract module context for Rust files.

    Includes:
    - use declarations
    - mod declarations
    - const/static items
    - type aliases
    - attribute items
    """
    _rust_context_node_types = {
        "use_declaration",
        "mod_item",  # mod foo; declarations
        "const_item",
        "static_item",
        "type_alias",
        "type_item",
        "attribute_item",  # #[derive(...)] etc.
    }

    _rust_definition_node_types = {
        "function_item",
        "impl_item",
        "struct_item",
        "enum_item",
        "trait_item",
    }

    context_parts: list[str] = []

    for child in root_node.children:
        if child.type in _rust_definition_node_types:
            # Skip major definitions
            continue

        if child.type in _rust_context_node_types:
            node_text = safe_decode_text(child)
            if node_text:
                context_parts.append(node_text.strip())

    return "\n".join(context_parts)


def _extract_go_module_context(root_node: Node) -> str:
    """Extract module context for Go files.

    Includes:
    - package declaration
    - import declarations
    - const declarations
    - var declarations
    - type declarations
    """
    _go_context_node_types = {
        "package_clause",
        "import_declaration",
        "const_declaration",
        "var_declaration",
        "type_declaration",
    }

    _go_definition_node_types = {
        "function_declaration",
        "method_declaration",
    }

    context_parts: list[str] = []

    for child in root_node.children:
        if child.type in _go_definition_node_types:
            # Skip function/method definitions
            continue

        if child.type in _go_context_node_types:
            node_text = safe_decode_text(child)
            if node_text:
                context_parts.append(node_text.strip())

    return "\n".join(context_parts)


def _extract_java_module_context(root_node: Node) -> str:
    """Extract module context for Java files.

    Includes:
    - package declaration
    - import declarations
    """
    context_parts: list[str] = []

    # Java has a program node, look inside it
    for child in root_node.children:
        if child.type == "package_declaration":
            node_text = safe_decode_text(child)
            if node_text:
                context_parts.append(node_text.strip())
        elif child.type == "import_declaration":
            node_text = safe_decode_text(child)
            if node_text:
                context_parts.append(node_text.strip())
        elif child.type == "class_declaration":
            # Stop at class definitions
            break

    return "\n".join(context_parts)


def _extract_cpp_module_context(root_node: Node) -> str:
    """Extract module context for C/C++ header and source files.

    For C++, module context is more limited than Python because most
    "context" is inside namespaces. We only extract:
    - #pragma directives
    - #include directives
    - #define macros (short ones only)
    - #if/#ifdef/#ifndef guards
    - using declarations at file scope (outside namespaces)

    We explicitly DO NOT include:
    - namespace blocks (contain actual code)
    - function/class definitions
    - template definitions
    """
    context_parts: list[str] = []
    total_size = 0
    MAX_SIZE = 4096  # Limit C++ context to 4KB

    for child in root_node.children:
        # Include preprocessor directives
        if child.type == "preproc_include":
            node_text = safe_decode_text(child)
            if node_text:
                text = node_text.strip()
                if total_size + len(text) < MAX_SIZE:
                    context_parts.append(text)
                    total_size += len(text)

        elif child.type == "preproc_def":
            # Include short #define macros (not large multi-line macros)
            node_text = safe_decode_text(child)
            if node_text:
                text = node_text.strip()
                # Only include short defines (single line, < 200 chars)
                if "\n" not in text and len(text) < 200:
                    if total_size + len(text) < MAX_SIZE:
                        context_parts.append(text)
                        total_size += len(text)

        elif child.type in ("preproc_ifdef", "preproc_ifndef", "preproc_if"):
            # Include header guards and conditional compilation
            # But only the directive line, not the entire block
            node_text = safe_decode_text(child)
            if node_text:
                # Extract just the first line (the directive itself)
                first_line = node_text.split("\n")[0].strip()
                if first_line and total_size + len(first_line) < MAX_SIZE:
                    context_parts.append(first_line)
                    total_size += len(first_line)

        elif child.type == "preproc_call":
            # #pragma and other preprocessor calls
            node_text = safe_decode_text(child)
            if node_text:
                text = node_text.strip()
                if len(text) < 200 and total_size + len(text) < MAX_SIZE:
                    context_parts.append(text)
                    total_size += len(text)

        elif child.type == "using_declaration":
            # using declarations at file scope (outside namespaces)
            node_text = safe_decode_text(child)
            if node_text:
                text = node_text.strip()
                if total_size + len(text) < MAX_SIZE:
                    context_parts.append(text)
                    total_size += len(text)

        elif child.type == "alias_declaration":
            # using type = ... at file scope
            node_text = safe_decode_text(child)
            if node_text:
                text = node_text.strip()
                if len(text) < 300 and total_size + len(text) < MAX_SIZE:
                    context_parts.append(text)
                    total_size += len(text)

        # Skip namespace_definition, function_definition, class_specifier, etc.
        # These contain actual code, not "context"

    return "\n".join(context_parts)
