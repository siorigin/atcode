# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Shared tool descriptions registry — single source of truth for both MCP and native paths.

Consumer-specific hints (MCP's "Call set_project() first", native's "[[qualified_name]] links")
belong in their respective system prompts, NOT here.
"""

TOOL_DESCRIPTIONS: dict[str, str] = {
    # =========================================================================
    # Project management
    # =========================================================================
    "set_project": (
        "Set active project context. Required before using search/analysis tools.\n\n"
        "Args:\n"
        "    project_name: Exact name from list_repos() (e.g., \"vllm_claude\")\n"
        "    project_root: (Optional) Project root path. Auto-detected from DB if omitted."
    ),
    "list_repos": (
        "List all available repositories in the knowledge graph. Call FIRST to discover project names.\n\n"
        "Returns: {repositories: [{name, path}, ...]}\n"
        "Cross-repo: use the name from this list as repo_name in other tools."
    ),
    # =========================================================================
    # Code search & discovery
    # =========================================================================
    "find_nodes": (
        "Search for code elements (functions, classes, methods). PRIMARY discovery tool.\n\n"
        "Combine terms with | for OR: \"attention|attn|flash|paged\".\n"
        "Supports batch: [\"a\",\"b\",\"c\"]. Syntax: \"keyword\" | \"a|b\" (OR) | \"a*b\" (glob) | \".*regex.*\"\n"
        "node_type: \"Code\" (default) or \"All\" (includes files).\n"
        "Cross-repo: find_nodes(\"func\", repo_name=\"RepoName\")\n"
        "Returns up to 50 qualified_names."
    ),
    "find_calls": (
        "Find call relationships (callers or callees) for a function/method.\n\n"
        "Prefer explore_code — it returns source + callers + callees in one call.\n"
        "Only use find_calls if you EXCLUSIVELY need relationship data without source code.\n\n"
        "Args:\n"
        "    qualified_name: Full qualified_name from find_nodes()\n"
        "    direction: \"outgoing\" (what it calls) or \"incoming\" (what calls it)\n"
        "    depth: Traversal depth 1-5 (default: 1)"
    ),
    "get_children": (
        "List methods of a class, or contents of a file/directory. Use \".\" for project root.\n\n"
        "find_nodes is usually faster than browsing directories.\n\n"
        "Args:\n"
        "    identifier: Qualified_name, file path, or \".\" for project root.\n"
        "    identifier_type: \"auto\" (recommended), \"project\", \"folder\", \"file\", or \"class\"\n"
        "    depth: 1-5 (default: 1). child_types: filter e.g. \"Class,Function\""
    ),
    # =========================================================================
    # Code retrieval
    # =========================================================================
    "get_code_snippet": (
        "Lightweight source-code retrieval. For full context (source + callers + callees), use explore_code instead.\n\n"
        "Args:\n"
        "    qualified_name: Full qualified_name from find_nodes()\n"
        "    repo_name: Repository name for cross-repo queries (optional)\n\n"
        "Returns: {found, qualified_name, file_path, line_start, line_end, source_code, docstring}"
    ),
    "explore_code": (
        "Explore a code element in depth. Returns source code + callers + callees + dependency tree in ONE call.\n\n"
        "PREFERRED TOOL for deep analysis. Replaces: get_code_snippet + find_calls(in) + find_calls(out).\n"
        "Supports fuzzy match — if exact name not found, will auto-search by basename.\n\n"
        "Args:\n"
        "    identifier: qualified_name from find_nodes()\n"
        "    repo_name: Repository name for cross-repo queries (optional)\n"
        "    max_dependency_depth: Callee traversal depth 1-10 (default: 5)\n"
        "    include_dependency_source_code: Include source code (default: true)"
    ),
    "read": (
        "Read file/folder content with optional pattern search.\n\n"
        "For code exploration, prefer find_nodes → explore_code → get_code_snippet.\n"
        "Supports: single file read, folder listing, regex/literal search in files/folders.\n\n"
        "Args:\n"
        "    identifier: File path, folder path, or qualified_name\n"
        "    pattern: Optional search pattern (required for folder search)\n"
        "    match_mode: \"full\" (read entire file), \"regex\", or \"literal\""
    ),
    # =========================================================================
    # Analysis
    # =========================================================================
    "find_class_hierarchy": (
        "Find parent and child classes (inheritance hierarchy) of a class.\n\n"
        "Args:\n"
        "    class_qualified_name: Full qualified_name from find_nodes()\n\n"
        "Returns: {class_name, parents: [...], children: [...]}"
    ),
    "trace_dependencies": (
        "Trace dependency path between two code elements, or find all reachable elements.\n\n"
        "Two modes:\n"
        "- With end_qualified_name: Find shortest path start → end\n"
        "- Without: Find all reachable elements from start\n\n"
        "Args:\n"
        "    start_qualified_name: Starting function/class\n"
        "    end_qualified_name: Optional target\n"
        "    max_depth: 1-10 (default: 5)\n"
        "    relationship_type: \"CALLS\" (default), \"IMPORTS\", or \"INHERITS\""
    ),
    "find_module_imports": (
        "Find all files imported by a specific file.\n"
        "Returns imported files (up to 100 results)."
    ),
    "find_external_dependencies": (
        "Find all external package dependencies of the project.\n"
        "Returns external packages and their versions."
    ),
    # =========================================================================
    # Graph management (compound)
    # =========================================================================
    "manage_graph": (
        "Knowledge graph build and management.\n\n"
        "Args:\n"
        '    action: "build" | "refresh" | "job_status"\n'
        "    project_path: Absolute path to project root (for build)\n"
        '    project_name: Project name (for build/refresh). "_claude" suffix added automatically for build.\n'
        "    fast_mode: Skip embeddings for faster build (default: true, for build/refresh)\n"
        "    job_id: Job ID from build/refresh (for job_status)\n\n"
        "Returns: {success, project_name, job_id} for build/refresh, or {status, progress} for job_status."
    ),
    "check_health": (
        "Check database connection and current project context. Use when tools return errors.\n\n"
        "Returns: {database: {status}, project_context: {status, project, root}}"
    ),
    # =========================================================================
    # Sync (compound)
    # =========================================================================
    "sync": (
        "Sync operations: real-time file monitoring and incremental graph updates.\n"
        "Embeddings are skipped by default for speed. Other advanced options\n"
        "(track_variables, auto_watch, initial_sync, use_polling) use sensible defaults.\n\n"
        "Args:\n"
        '    project_name: Repository name\n'
        '    action: "start" | "stop" | "now" | "status"\n'
        '    repo_path: Repository path (required for "start")\n'
        '    subdirs: Comma-separated subdirectories to watch (for "start")'
    ),
    # =========================================================================
    # Repository management (compound)
    # =========================================================================
    "manage_repo": (
        "Repository lifecycle: add, remove, or clean graph data.\n"
        "Embeddings are skipped by default for speed.\n\n"
        "Args:\n"
        '    action: "add" | "remove" | "clean"\n'
        "    repo_url: Git remote URL (for add)\n"
        "    local_path: Local filesystem path (for add)\n"
        "    project_name: Custom project name (for add)\n"
        '    repo_name: Repo name from list_repos() (for remove/clean)'
    ),
    # =========================================================================
    # Git (compound)
    # =========================================================================
    "git": (
        "Git operations for a repository.\n\n"
        "Args:\n"
        "    project_name: Repository name\n"
        '    action: "checkout" | "fetch" | "list_refs" | "pull"\n'
        "    ref: Branch, tag, or commit hash (for checkout)\n"
        '    remote: Remote name (default: "origin", for fetch/pull)\n'
        "    branch: Branch to pull (default: current branch, for pull)"
    ),
    # =========================================================================
    # Document editing tools
    # =========================================================================
    "read_doc_trace": (
        "Read the generation trace of a documentation section.\n"
        "Returns the scope (topic, description, key components) and a summary of the "
        "AI agent's exploration process used to generate this section.\n"
        "Use this to understand the context before editing a document.\n\n"
        "Args:\n"
        "    repo_name: Repository name\n"
        "    section_id: Section filename stem (e.g., '001_核心架构与_hybridflow_设计')\n"
        "    version: Version ID or 'latest' (default)"
    ),
    "read_doc_file": (
        "Read a documentation file with line numbers.\n"
        "Returns the file content in 'line_number | content' format for precise editing.\n"
        "Supports both .md files and legacy .json docs (reads the embedded markdown field).\n\n"
        "Args:\n"
        "    file_path: Absolute path to the .md or .json file"
    ),
    "edit_doc_file": (
        "Edit a documentation file by replacing a range of lines.\n"
        "Use start_line=1 and end_line=-1 to replace the entire file.\n"
        "Supports both .md files and legacy .json docs (edits the embedded markdown field).\n\n"
        "Args:\n"
        "    file_path: Absolute path to the .md or .json file\n"
        "    start_line: Start line number (1-based, inclusive)\n"
        "    end_line: End line number (1-based, inclusive). -1 = end of file\n"
        "    new_content: New content to replace the specified line range"
    ),
    # --- Paper Reading Tools ---
    "read_paper": (
        "Start the complete paper reading pipeline: search → download PDF → parse with MinerU → extract GitHub repos → build code graph → generate interactive document.\n\n"
        "Args:\n"
        "    query: Search query to find the paper (optional if paper_url or arxiv_id provided)\n"
        "    paper_url: Direct URL to the paper (optional)\n"
        "    arxiv_id: arXiv paper ID, e.g. '2504.20073' (optional)\n"
        "    auto_build_repos: Whether to build code graph for discovered repos (default: true)\n"
        "    max_papers: Maximum number of papers to process (default: 1)\n\n"
        "Returns: {task_id} for tracking progress via get_task_status()."
    ),
    "get_paper_doc": (
        "Get the generated interactive reading document for a paper.\n\n"
        "Two modes:\n"
        "- **Skeleton (default)**: Returns paper metadata, a tree of section/subsection titles with ~200 char "
        "previews and char counts, figure/table captions, code analysis summary, and reference count. "
        "Large sections include a `subsections` array showing the internal chapter structure. "
        "Use this FIRST to see the paper's full outline.\n"
        "- **Sections**: Pass `sections` with comma-separated indices or titles (e.g. '0,2,Method,Related Work') "
        "to fetch full content. Supports both top-level section titles AND subsection titles within large sections.\n\n"
        "Workflow: call once with no sections → review the skeleton tree → request specific sections/subsections by title.\n\n"
        "Args:\n"
        "    paper_id: Paper ID (arxiv ID or sanitized identifier)\n"
        "    sections: (Optional) Comma-separated section indices or titles to fetch full content\n\n"
        "Returns: Skeleton with section tree (default) or requested sections' full content."
    ),
    "search_papers": (
        "Search for papers by keyword across crawled daily papers and processed library.\n"
        "PREFERRED over browse_papers when the user asks about a specific topic.\n\n"
        "Supports multiple keywords separated by | (OR logic) in a single call, e.g.\n"
        '    query: "flash attention|sparse MoE|mixture of experts"\n\n'
        "Searches locally cached data only (no external API calls). "
        "Use browse_papers to crawl new dates first if needed.\n\n"
        "Args:\n"
        "    query: Search keywords. Use | to search multiple terms at once.\n"
        "    start_date: (Optional) Only include papers from this date onward, YYYY-MM-DD\n"
        "    end_date: (Optional) Only include papers up to this date, YYYY-MM-DD\n"
        "    max_results: Max papers to return (default: 20, max: 50)\n\n"
        "Returns: List of matching papers with title, summary snippet, upvotes, github_repo, ai_keywords."
    ),
    "list_papers": (
        "List all processed papers in the local library.\n\n"
        "Returns: List of papers with paper_id, title, authors, source, and processing status."
    ),
    "browse_papers": (
        "Browse HuggingFace daily papers.\n\n"
        "Args:\n"
        '    mode: "daily" (default) | "range" | "crawl"\n'
        "    date: YYYY-MM-DD (for daily/crawl, default: today)\n"
        "    start_date: Start date YYYY-MM-DD (for range)\n"
        "    end_date: End date YYYY-MM-DD (for range)\n"
        "    min_upvotes: Min upvotes filter (for range, default: 0)\n\n"
        "Returns: List of papers with title, abstract, upvotes, GitHub info."
    ),
}
