# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Simplified incremental updater using Memgraph as the single source of truth.

Design principles:
- Memgraph is the ONLY source of truth. No local caches for definitions,
  callers, or function registries.
- File-level change detection via hash comparison.
- Definition-level (AST) diff for fine-grained incremental updates.
- Variable node support (module-level and class-level variables).

This replaces the complex caching approach (FunctionRegistry, CallersIndex,
DefinitionsStore) with direct Memgraph queries.
"""

import hashlib
import time
from pathlib import Path
from typing import Any

from core.language_config import get_language_config
from graph.service import MemgraphIngestor
from loguru import logger
from tree_sitter import Node, Parser

from .models import FileChange, UpdateResult


def compute_signature_hash(source_code: str) -> str:
    """Compute a hash of source code for change detection.

    Args:
        source_code: Source code string to hash.

    Returns:
        SHA-256 hex digest of the source code.
    """
    return hashlib.sha256(source_code.encode("utf-8", errors="replace")).hexdigest()


class SimpleUpdater:
    """Simplified incremental updater that queries Memgraph directly.

    Instead of maintaining local caches (FunctionRegistry, CallersIndex,
    DefinitionsStore), this updater queries Memgraph for old definitions
    and computes diffs against newly parsed AST.

    Usage:
        updater = SimpleUpdater(
            ingestor=ingestor,
            repo_path=Path("/path/to/repo"),
            project_name="myproject",
            parsers={"python": parser, ...},
            queries={"python": {...}, ...},
        )
        updater.handle_file_change(file_path, "modify")
    """

    def __init__(
        self,
        ingestor: MemgraphIngestor,
        repo_path: Path,
        project_name: str,
        parsers: dict[str, Parser],
        queries: dict[str, Any],
        track_variables: bool = True,
    ):
        """Initialize the simple updater.

        Args:
            ingestor: MemgraphIngestor instance for database operations.
            repo_path: Repository root path.
            project_name: Project name for qualified names.
            parsers: Tree-sitter parser dictionary.
            queries: Language query configuration dictionary.
            track_variables: Whether to track module/class-level variables.
        """
        self.ingestor = ingestor
        self.repo_path = Path(repo_path)
        self.project_name = project_name
        self.parsers = parsers
        self.queries = queries
        self.track_variables = track_variables

    # =========================================================================
    # Qualified Name Helpers
    # =========================================================================

    def _get_module_qn(self, file_path: Path) -> str:
        """Get the module qualified name for a file.

        Args:
            file_path: Absolute path to the file.

        Returns:
            Module qualified name (e.g., "project.module.submodule").
        """
        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            return ""

        if file_path.name == "__init__.py":
            parts = list(relative_path.parent.parts)
        else:
            parts = list(relative_path.with_suffix("").parts)

        return ".".join([self.project_name] + parts)

    def _get_file_qn(self, file_path: Path) -> str:
        """Get the file qualified name.

        Args:
            file_path: Absolute path to the file.

        Returns:
            File qualified name.
        """
        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            return ""

        parent_dir = relative_path.parent
        # Normalize: skip first part if it matches project_name
        file_parts = list(parent_dir.parts) + [file_path.stem]
        if file_parts and file_parts[0] == self.project_name:
            file_parts = file_parts[1:]
        return ".".join([self.project_name] + file_parts)

    def _get_folder_qn(self, folder_path: Path) -> str:
        """Get the folder qualified name.

        Args:
            folder_path: Relative folder path from repo root.

        Returns:
            Folder qualified name.
        """
        # Normalize: skip first part if it matches project_name
        folder_parts = list(folder_path.parts)
        if folder_parts and folder_parts[0] == self.project_name:
            folder_parts = folder_parts[1:]
        return ".".join([self.project_name] + folder_parts)

    # =========================================================================
    # Folder Operations
    # =========================================================================

    def _create_folder_node(self, relative_path: Path, folder_qn: str) -> None:
        """Create a Folder node with CONTAINS_FOLDER relationship."""
        is_package = (self.repo_path / relative_path / "__init__.py").exists()

        self.ingestor.ensure_node_batch(
            "Folder",
            {
                "path": str(relative_path),
                "name": relative_path.name,
                "is_package": is_package,
                "qualified_name": folder_qn,
            },
        )

        parent_dir = relative_path.parent
        if parent_dir == Path("."):
            self.ingestor.ensure_relationship_batch(
                ("Project", "name", self.project_name),
                "CONTAINS_FOLDER",
                ("Folder", "qualified_name", folder_qn),
            )
        else:
            parent_qn = self._get_folder_qn(parent_dir)
            self.ingestor.ensure_relationship_batch(
                ("Folder", "qualified_name", parent_qn),
                "CONTAINS_FOLDER",
                ("Folder", "qualified_name", folder_qn),
            )

        self.ingestor.flush_all()

    def _delete_folder_cascade(self, folder_qn: str) -> None:
        """Delete a Folder and all its children (cascade).

        Uses DETACH DELETE to remove the folder and all contained nodes.
        """
        # Delete all nodes whose qualified_name starts with folder_qn
        self.ingestor.execute_query(
            """
            MATCH (n)
            WHERE n.qualified_name STARTS WITH $prefix_dot
               OR n.qualified_name = $qn
            DETACH DELETE n
            """,
            {"prefix_dot": folder_qn + ".", "qn": folder_qn},
        )

    # =========================================================================
    # File Operations
    # =========================================================================

    def handle_file_change(self, file_path: Path, action: str) -> UpdateResult:
        """Handle file add/delete/modify.

        Args:
            file_path: Absolute path to the file.
            action: One of "add", "delete", "modify".

        Returns:
            UpdateResult with statistics.
        """
        result = UpdateResult()
        file_qn = self._get_file_qn(file_path)
        module_qn = self._get_module_qn(file_path)

        if not file_qn or not module_qn:
            return result

        if action == "add":
            self._add_file(file_path, file_qn, module_qn)
            result.added = 1
        elif action == "delete":
            self._delete_file(file_path, module_qn)
            result.deleted = 1
        elif action == "modify":
            self._update_file_definitions(file_path, file_qn, module_qn)
            result.modified = 1

        return result

    def _add_file(self, file_path: Path, file_qn: str, module_qn: str) -> None:
        """Add a new file: create File node, parse content, create definitions."""
        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            return

        # Ensure folder chain exists
        self._ensure_folder_chain(file_path)

        # Create File node
        self.ingestor.ensure_node_batch(
            "File",
            {
                "path": str(relative_path),
                "name": file_path.name,
                "extension": file_path.suffix,
                "qualified_name": file_qn,
            },
        )

        # Create CONTAINS_FILE relationship
        parent_dir = relative_path.parent
        if parent_dir == Path("."):
            self.ingestor.ensure_relationship_batch(
                ("Project", "name", self.project_name),
                "CONTAINS_FILE",
                ("File", "qualified_name", file_qn),
            )
        else:
            parent_qn = self._get_folder_qn(parent_dir)
            self.ingestor.ensure_relationship_batch(
                ("Folder", "qualified_name", parent_qn),
                "CONTAINS_FILE",
                ("File", "qualified_name", file_qn),
            )

        self.ingestor.flush_all()

        # Parse and create definitions
        new_defs = self._parse_definitions(file_path, module_qn)
        for def_info in new_defs:
            self._add_definition(file_path, file_qn, module_qn, def_info)

        self.ingestor.flush_all()

    def _delete_file(self, file_path: Path, module_qn: str) -> None:
        """Delete a file and all its definitions from the graph.

        DETACH DELETE removes all nodes and relationships.
        """
        # Delete all nodes with qualified_name matching this module
        self.ingestor.execute_query(
            """
            MATCH (n)
            WHERE n.qualified_name STARTS WITH $prefix_dot
               OR n.qualified_name = $prefix
            DETACH DELETE n
            """,
            {"prefix_dot": module_qn + ".", "prefix": module_qn},
        )

        # Also delete the File node by path
        try:
            relative_path = file_path.relative_to(self.repo_path)
            self.ingestor.execute_query(
                "MATCH (f:File {path: $path}) DETACH DELETE f",
                {"path": str(relative_path)},
            )
        except ValueError:
            pass

    def _update_file_definitions(
        self, file_path: Path, file_qn: str, module_qn: str
    ) -> None:
        """Detect and update definition changes within a modified file.

        This is the core of the incremental update: compare old definitions
        from Memgraph with new definitions from AST, then apply the diff.
        """
        # Step 1: Get old definitions from Memgraph
        old_defs = self._query_definitions_from_db(module_qn)
        old_def_map = {d["qn"]: d for d in old_defs}

        # Step 2: Parse new definitions from file
        new_defs = self._parse_definitions(file_path, module_qn)
        new_def_map = {d["qualified_name"]: d for d in new_defs}

        # Step 3: Compute diff
        old_qns = set(old_def_map.keys())
        new_qns = set(new_def_map.keys())

        added_qns = new_qns - old_qns
        removed_qns = old_qns - new_qns
        potentially_modified_qns = old_qns & new_qns

        # Step 4: Detect modifications via signature_hash
        modified_qns: set[str] = set()
        for qn in potentially_modified_qns:
            old_hash = old_def_map[qn].get("sig_hash", "")
            new_hash = new_def_map[qn].get("signature_hash", "")
            if old_hash != new_hash:
                modified_qns.add(qn)

        # Step 5: Apply changes
        for qn in removed_qns:
            self._delete_definition(qn)
            logger.debug(f"Deleted definition: {qn}")

        for qn in added_qns:
            self._add_definition(file_path, file_qn, module_qn, new_def_map[qn])
            logger.debug(f"Added definition: {qn}")

        for qn in modified_qns:
            self._update_definition(file_path, module_qn, new_def_map[qn])
            logger.debug(f"Updated definition: {qn}")

        if added_qns or removed_qns or modified_qns:
            self.ingestor.flush_all()
            logger.info(
                f"File {file_path.name}: "
                f"+{len(added_qns)} -{len(removed_qns)} ~{len(modified_qns)} definitions"
            )

    # =========================================================================
    # Definition Operations (Query / Add / Delete / Update)
    # =========================================================================

    def _query_definitions_from_db(self, module_qn: str) -> list[dict]:
        """Query all definitions under a module from Memgraph.

        Args:
            module_qn: Module qualified name prefix.

        Returns:
            List of definition dicts with qn, type, start_line, end_line, sig_hash.
        """
        results = self.ingestor.fetch_all(
            """
            MATCH (n)
            WHERE (n:Function OR n:Method OR n:Class OR n:Variable)
              AND (n.qualified_name STARTS WITH $prefix_dot
                   OR n.qualified_name = $prefix)
            RETURN n.qualified_name AS qn,
                   labels(n) AS labels,
                   n.start_line AS start_line,
                   n.end_line AS end_line,
                   n.signature_hash AS sig_hash
            """,
            {"prefix_dot": module_qn + ".", "prefix": module_qn},
        )

        defs = []
        for r in results:
            labels = r.get("labels", [])
            node_type = "Function"
            if "Variable" in labels:
                node_type = "Variable"
            elif "Method" in labels:
                node_type = "Method"
            elif "Class" in labels:
                node_type = "Class"

            defs.append(
                {
                    "qn": r["qn"],
                    "type": node_type,
                    "start_line": r.get("start_line"),
                    "end_line": r.get("end_line"),
                    "sig_hash": r.get("sig_hash", ""),
                }
            )

        return defs

    def _parse_definitions(self, file_path: Path, module_qn: str) -> list[dict]:
        """Parse a file and extract all definitions (functions, classes, methods, variables).

        Args:
            file_path: Absolute path to the file.
            module_qn: Module qualified name.

        Returns:
            List of definition dicts with qualified_name, name, type,
            start_line, end_line, signature_hash, source.
        """
        lang_config = get_language_config(file_path.suffix)
        if not lang_config or lang_config.name not in self.parsers:
            return []

        language = lang_config.name
        parser = self.parsers[language]

        try:
            source_bytes = file_path.read_bytes()
            tree = parser.parse(source_bytes)
            root_node = tree.root_node
        except Exception as e:
            logger.warning(f"Failed to parse {file_path}: {e}")
            return []

        source_text = source_bytes.decode("utf-8", errors="replace")

        definitions: list[dict] = []

        # Extract functions, classes, methods
        self._extract_definitions_from_node(
            root_node, language, module_qn, source_text, definitions
        )

        # Extract variables (module-level and class-level)
        if self.track_variables:
            self._extract_variables_from_node(
                root_node, language, module_qn, source_text, definitions
            )

        return definitions

    def _extract_definitions_from_node(
        self,
        root_node: Node,
        language: str,
        module_qn: str,
        source_text: str,
        definitions: list[dict],
    ) -> None:
        """Extract function/class/method definitions from AST.

        Walks the AST and collects definition info.
        """
        # Language-specific definition node types
        func_types = {
            "python": {"function_definition", "decorated_definition"},
            "javascript": {"function_declaration", "method_definition"},
            "typescript": {"function_declaration", "method_definition"},
            "rust": {"function_item"},
            "go": {"function_declaration", "method_declaration"},
            "java": {"method_declaration"},
            "cpp": {"function_definition"},
            "lua": {"function_declaration"},
        }.get(language, set())

        class_types = {
            "python": {"class_definition"},
            "javascript": {"class_declaration"},
            "typescript": {"class_declaration"},
            "rust": {"struct_item", "impl_item", "trait_item"},
            "go": {"type_declaration"},
            "java": {"class_declaration", "interface_declaration"},
            "cpp": {"class_specifier"},
        }.get(language, set())

        def walk(node: Node, parent_qn: str, parent_type: str = "module") -> None:
            node_type_str = node.type

            if node_type_str in func_types:
                name = self._extract_name(node, language)
                if name:
                    qn = f"{parent_qn}.{name}"
                    def_type = "Method" if parent_type == "class" else "Function"
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    source = source_text[node.start_byte : node.end_byte][:2000]

                    definitions.append(
                        {
                            "qualified_name": qn,
                            "name": name,
                            "type": def_type,
                            "start_line": start_line,
                            "end_line": end_line,
                            "signature_hash": compute_signature_hash(source),
                            "source": source,
                        }
                    )

                    # Don't recurse into functions for nested definitions
                    # (we only track top-level and class-level)
                    return

            if node_type_str in class_types:
                name = self._extract_name(node, language)
                if name:
                    qn = f"{parent_qn}.{name}"
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    source = source_text[node.start_byte : node.end_byte][:2000]

                    definitions.append(
                        {
                            "qualified_name": qn,
                            "name": name,
                            "type": "Class",
                            "start_line": start_line,
                            "end_line": end_line,
                            "signature_hash": compute_signature_hash(source),
                            "source": source,
                        }
                    )

                    # Recurse into class body for methods
                    body = node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            walk(child, qn, "class")
                    else:
                        for child in node.children:
                            walk(child, qn, "class")
                    return

            # Recurse into children
            for child in node.children:
                walk(child, parent_qn, parent_type)

        walk(root_node, module_qn)

    def _extract_variables_from_node(
        self,
        root_node: Node,
        language: str,
        module_qn: str,
        source_text: str,
        definitions: list[dict],
    ) -> None:
        """Extract module-level and class-level variable definitions.

        Only tracks:
        - Module-level constants/variables (e.g., CONFIG = {...})
        - Class variables defined in class body (e.g., count = 0)

        Does NOT track:
        - Local variables inside functions
        - Instance attributes (self.x)
        """
        if language == "python":
            self._extract_python_variables(
                root_node, module_qn, source_text, definitions
            )
        elif language in ("javascript", "typescript"):
            self._extract_js_variables(root_node, module_qn, source_text, definitions)

    def _extract_python_variables(
        self,
        root_node: Node,
        module_qn: str,
        source_text: str,
        definitions: list[dict],
    ) -> None:
        """Extract Python module-level and class-level variables."""
        # Collect existing definition QNs to avoid duplicates
        existing_qns = {d["qualified_name"] for d in definitions}

        def process_assignment(node: Node, parent_qn: str) -> None:
            """Process an assignment statement for variable extraction."""
            if node.type == "expression_statement":
                for child in node.children:
                    if child.type == "assignment":
                        process_assignment(child, parent_qn)
                return

            if node.type != "assignment":
                return

            # Get the left-hand side
            left = node.child_by_field_name("left")
            if not left:
                return

            # Only track simple name assignments (not self.x, dict[key], etc.)
            if left.type == "identifier":
                name = left.text.decode("utf-8") if left.text else ""
                if name and not name.startswith("_"):
                    qn = f"{parent_qn}.{name}"
                    if qn not in existing_qns:
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        source = source_text[node.start_byte : node.end_byte][:500]

                        definitions.append(
                            {
                                "qualified_name": qn,
                                "name": name,
                                "type": "Variable",
                                "start_line": start_line,
                                "end_line": end_line,
                                "signature_hash": compute_signature_hash(source),
                                "source": source,
                            }
                        )
                        existing_qns.add(qn)

        # Module-level variables
        for child in root_node.children:
            if child.type in ("expression_statement", "assignment"):
                process_assignment(child, module_qn)

        # Class-level variables (find class bodies)
        for def_info in list(definitions):
            if def_info["type"] == "Class":
                class_qn = def_info["qualified_name"]
                # Find the class node in AST by line range
                class_node = self._find_node_at_line(
                    root_node, def_info["start_line"], "class_definition"
                )
                if class_node:
                    body = class_node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            if child.type in ("expression_statement", "assignment"):
                                process_assignment(child, class_qn)

    def _extract_js_variables(
        self,
        root_node: Node,
        module_qn: str,
        source_text: str,
        definitions: list[dict],
    ) -> None:
        """Extract JavaScript/TypeScript module-level variables."""
        existing_qns = {d["qualified_name"] for d in definitions}

        for child in root_node.children:
            if child.type in ("variable_declaration", "lexical_declaration"):
                for declarator in child.children:
                    if declarator.type == "variable_declarator":
                        name_node = declarator.child_by_field_name("name")
                        if name_node and name_node.type == "identifier":
                            name = (
                                name_node.text.decode("utf-8") if name_node.text else ""
                            )
                            if name:
                                qn = f"{module_qn}.{name}"
                                if qn not in existing_qns:
                                    start_line = child.start_point[0] + 1
                                    end_line = child.end_point[0] + 1
                                    source = source_text[
                                        child.start_byte : child.end_byte
                                    ][:500]

                                    definitions.append(
                                        {
                                            "qualified_name": qn,
                                            "name": name,
                                            "type": "Variable",
                                            "start_line": start_line,
                                            "end_line": end_line,
                                            "signature_hash": compute_signature_hash(
                                                source
                                            ),
                                            "source": source,
                                        }
                                    )
                                    existing_qns.add(qn)

    def _extract_name(self, node: Node, language: str) -> str:
        """Extract the name from a definition node.

        Args:
            node: AST node for a function/class/method.
            language: Language name.

        Returns:
            Name string, or empty string if not found.
        """
        # Handle Python decorated definitions
        if node.type == "decorated_definition":
            for child in node.children:
                if child.type in ("function_definition", "class_definition"):
                    return self._extract_name(child, language)
            return ""

        name_node = node.child_by_field_name("name")
        if name_node and name_node.text:
            return name_node.text.decode("utf-8")
        return ""

    def _find_node_at_line(
        self, root_node: Node, line: int, node_type: str
    ) -> Node | None:
        """Find an AST node of a specific type at a given line.

        Args:
            root_node: Root AST node to search.
            line: 1-indexed line number.
            node_type: AST node type to match.

        Returns:
            Matching node or None.
        """
        target_line = line - 1  # Convert to 0-indexed

        def search(node: Node) -> Node | None:
            if node.type == node_type and node.start_point[0] == target_line:
                return node
            for child in node.children:
                result = search(child)
                if result:
                    return result
            return None

        return search(root_node)

    # =========================================================================
    # Definition CRUD
    # =========================================================================

    def _add_definition(
        self,
        file_path: Path,
        file_qn: str,
        module_qn: str,
        def_info: dict,
    ) -> None:
        """Add a new definition node (Function/Class/Method/Variable).

        Creates the node and appropriate DEFINES relationship.
        """
        node_type = def_info["type"]
        qn = def_info["qualified_name"]

        # Create node
        node_props: dict[str, Any] = {
            "qualified_name": qn,
            "name": def_info["name"],
            "start_line": def_info["start_line"],
            "end_line": def_info["end_line"],
            "signature_hash": def_info.get("signature_hash", ""),
        }

        if node_type in ("Function", "Method"):
            node_props["source"] = def_info.get("source", "")

        self.ingestor.ensure_node_batch(node_type, node_props)

        # Create DEFINES relationship
        if node_type == "Method":
            # Method belongs to Class via DEFINES_METHOD
            class_qn = ".".join(qn.rsplit(".", 1)[:-1])  # Remove method name
            # Only create if the parent is a class (check by looking at qn structure)
            self.ingestor.ensure_relationship_batch(
                ("Class", "qualified_name", class_qn),
                "DEFINES_METHOD",
                ("Method", "qualified_name", qn),
            )
        elif node_type == "Variable" and self._is_class_variable(qn, module_qn):
            # Class variable belongs to Class via DEFINES_VARIABLE
            class_qn = ".".join(qn.rsplit(".", 1)[:-1])
            self.ingestor.ensure_relationship_batch(
                ("Class", "qualified_name", class_qn),
                "DEFINES_VARIABLE",
                ("Variable", "qualified_name", qn),
            )
        else:
            # Function, Class, module-level Variable belong to File
            rel_type = "DEFINES"
            self.ingestor.ensure_relationship_batch(
                ("File", "qualified_name", file_qn),
                rel_type,
                (node_type, "qualified_name", qn),
            )

    def _delete_definition(self, qn: str) -> None:
        """Delete a definition node and all its relationships.

        DETACH DELETE automatically removes all relationships.
        """
        self.ingestor.execute_query(
            "MATCH (n {qualified_name: $qn}) DETACH DELETE n",
            {"qn": qn},
        )

    def _update_definition(
        self, file_path: Path, module_qn: str, def_info: dict
    ) -> None:
        """Update properties of an existing definition.

        Updates line numbers, signature_hash, and source code.
        Does NOT rebuild relationships (CALLS, USES, etc.) here;
        that is handled separately by the full updater.
        """
        qn = def_info["qualified_name"]
        node_type = def_info["type"]

        props: dict[str, Any] = {
            "start_line": def_info["start_line"],
            "end_line": def_info["end_line"],
            "signature_hash": def_info.get("signature_hash", ""),
        }
        if node_type in ("Function", "Method"):
            props["source"] = def_info.get("source", "")

        set_clause = ", ".join(f"n.{k} = ${k}" for k in props)
        self.ingestor.execute_query(
            f"MATCH (n {{qualified_name: $qn}}) SET {set_clause}",
            {"qn": qn, **props},
        )

    def _is_class_variable(self, qn: str, module_qn: str) -> bool:
        """Check if a variable QN represents a class-level variable.

        A class variable has a QN like: module.ClassName.var_name
        where ClassName is a Class node, not just module.var_name.
        """
        # The variable is class-level if its parent QN (removing last part)
        # is NOT the module_qn (which would make it module-level).
        parent_qn = ".".join(qn.rsplit(".", 1)[:-1])
        return parent_qn != module_qn

    # =========================================================================
    # Folder Chain
    # =========================================================================

    def _ensure_folder_chain(self, file_path: Path) -> None:
        """Ensure all parent folders exist in the graph.

        Creates Folder nodes and CONTAINS_FOLDER relationships.
        """
        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            return

        parent_dir = relative_path.parent
        if parent_dir == Path("."):
            return

        folders_to_create: list[Path] = []
        current = parent_dir
        while current != Path("."):
            folders_to_create.insert(0, current)
            current = current.parent

        for folder_path in folders_to_create:
            folder_qn = self._get_folder_qn(folder_path)
            is_package = (self.repo_path / folder_path / "__init__.py").exists()

            self.ingestor.ensure_node_batch(
                "Folder",
                {
                    "path": str(folder_path),
                    "name": folder_path.name,
                    "is_package": is_package,
                    "qualified_name": folder_qn,
                },
            )

            parent = folder_path.parent
            if parent == Path("."):
                self.ingestor.ensure_relationship_batch(
                    ("Project", "name", self.project_name),
                    "CONTAINS_FOLDER",
                    ("Folder", "qualified_name", folder_qn),
                )
            else:
                parent_qn = self._get_folder_qn(parent)
                self.ingestor.ensure_relationship_batch(
                    ("Folder", "qualified_name", parent_qn),
                    "CONTAINS_FOLDER",
                    ("Folder", "qualified_name", folder_qn),
                )

    # =========================================================================
    # Call Resolution (Memgraph-based)
    # =========================================================================

    def _select_best_match(self, candidates: list[str], caller_module: str) -> str:
        """Select the best matching QN from candidates using heuristics.

        Prefers candidates in the same package/module hierarchy.

        Args:
            candidates: List of candidate qualified names.
            caller_module: Module QN of the caller for proximity scoring.

        Returns:
            Best matching qualified name.
        """
        caller_parts = caller_module.split(".")

        best_qn = candidates[0]
        best_score = 0

        for qn in candidates:
            qn_parts = qn.split(".")
            # Score: number of common prefix parts
            score = sum(1 for a, b in zip(caller_parts, qn_parts) if a == b)
            if score > best_score:
                best_score = score
                best_qn = qn

        return best_qn

    # =========================================================================
    # Batch Apply Changes
    # =========================================================================

    def apply_changes(self, changes: list[FileChange]) -> UpdateResult:
        """Apply a batch of file changes.

        Processing order:
        1. Process deletions
        2. Process additions
        3. Process modifications
        4. Flush all

        Args:
            changes: List of FileChange objects.

        Returns:
            UpdateResult with statistics.
        """
        start_time = time.time()
        result = UpdateResult()

        deletions = [c for c in changes if c.action == "delete"]
        additions = [c for c in changes if c.action == "add"]
        modifications = [c for c in changes if c.action == "modify"]

        logger.info(
            f"SimpleUpdater: {len(deletions)} del, "
            f"{len(additions)} add, {len(modifications)} mod"
        )

        # Process deletions
        for change in deletions:
            try:
                sub_result = self.handle_file_change(change.path, "delete")
                result.deleted += sub_result.deleted
            except Exception as e:
                result.add_error(f"Delete {change.path}: {e}")

        # Process additions
        for change in additions:
            try:
                sub_result = self.handle_file_change(change.path, "add")
                result.added += sub_result.added
            except Exception as e:
                result.add_error(f"Add {change.path}: {e}")

        # Process modifications
        for change in modifications:
            try:
                sub_result = self.handle_file_change(change.path, "modify")
                result.modified += sub_result.modified
            except Exception as e:
                result.add_error(f"Modify {change.path}: {e}")

        self.ingestor.flush_all()

        result.duration_ms = (time.time() - start_time) * 1000
        logger.info(
            f"SimpleUpdater complete: +{result.added} ~{result.modified} "
            f"-{result.deleted} in {result.duration_ms:.0f}ms"
        )

        return result
