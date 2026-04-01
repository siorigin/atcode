---
name: atcode
description: Advanced code exploration using AtCode knowledge graph with qualified_name-based workflow. Use for cross-file analysis, call tracing, and semantic search.
version: 2.6.0
author: zijing team
---

# AtCode Code Explorer

AtCode provides **advanced code exploration** through a knowledge graph. It complements Claude Code's native file reading capabilities.

## Critical: Understanding qualified_name

**ALL AtCode tools use `qualified_name` to identify code elements.**

**Format:** `project_name.module.path.ClassName.method_name`

**Examples:**
- Function: `vllm.worker.worker.execute_model`
- Class method: `vllm.attention.PagedAttention.forward`
- Class: `vllm.modeling_models.llama_llm_LlamaForCausalLM`

**Key Rules:**
1. Always start with `find_nodes()` to get the qualified_name
2. Use that qualified_name with other tools
3. The project_name prefix is auto-added based on `set_project()`

## Getting Started

### Step 1: Check Health (if errors occur)
```python
check_health()
# Returns: database status, config, project context
# If database error → ensure Memgraph is running
```

### Step 2: List Available Projects
```python
list_repos()
# Returns: ["AtCode", "vllm", "EasyR1", "verl", ...]
# These are the EXACT names to use with set_project()
```

### Step 3: Set Project Context
```python
# Use the EXACT name from list_repos()
set_project(project_name="vllm")
```
**Important:** Use the exact name returned by `list_repos()`. Projects may or may not have suffixes depending on how they were built.

### Step 4: Find Code
```python
# Search by name (single keyword recommended)
find_nodes("attention")

# Returns list with qualified_name:
# [{"qualified_name": "vllm.attention.PagedAttention.forward", ...}, ...]
```

### Step 5: Get Details
```python
# Get source code
get_code_snippet(qualified_name="vllm.attention.PagedAttention.forward")

# Trace calls
find_calls(qualified_name="vllm.attention.PagedAttention.forward",
          direction="incoming")

# Complete analysis (source + callers + dependencies in ONE call)
explore_code(identifier="vllm.attention.PagedAttention.forward")
```

## Standard Workflow

```
┌─────────────────────────────────────────────────────────────┐
│  1. list_repos() → discover available projects               │
│     ↓                                                        │
│  2. set_project("exact_name") → set context                 │
│     ↓                                                        │
│  3. find_nodes("keyword") → get qualified_name              │
│     ↓                                                        │
│  4. Use qualified_name with:                                │
│     - get_code_snippet(qualified_name="...")                │
│     - find_calls(qualified_name="...", direction="...")     │
│     - explore_code(identifier="...")                        │
│     - find_class_hierarchy(class_qualified_name="...")      │
│     - trace_dependencies(start_qualified_name="...")        │
└─────────────────────────────────────────────────────────────┘
```

## When to Use AtCode vs Native Tools

| Use AtCode MCP for: | Use Claude Code Native for: |
|------------------------|----------------------------|
| Cross-file relationship analysis | Reading a single file |
| Call tracing ("who calls this") | Simple "show me this function" |
| Large-scale discovery (100+ files) | Small projects, simple questions |
| Class hierarchy analysis | Single class inspection |
| Dependency chain tracing | Direct imports only |

## Available Tools

### Discovery

#### `find_nodes` - Find Code by Name
**START HERE** - Search for functions, classes, methods.

**Syntax:**
- Single keyword: `"attention"` (RECOMMENDED)
- OR pattern: `"flash|attn"`
- Glob: `"get_*_config"`

**Returns:** List of nodes with `qualified_name` that you use with other tools.

```python
find_nodes("main")
# Returns: [{"qualified_name": "project.main", ...}, ...]

find_nodes("cache|config")
# Returns multiple results with qualified_names
```

### Analysis

#### `find_calls` - Trace Call Relationships
**AtCode superpower** - trace who calls what.

**Requires:** `qualified_name` from `find_nodes()`

```python
# What calls this function?
find_calls(
    qualified_name="vllm.worker.worker.execute_model",
    direction="incoming"
)

# What does this function call?
find_calls(
    qualified_name="vllm.worker.worker.execute_model",
    direction="outgoing",
    depth=2
)
```

**Response Structure:**
```json
{
    "success": true,
    "results": [
        {
            "qualified_name": "vllm.engine.run",
            "name": "run",
            "type": ["Function"],
            "path": "vllm/engine.py"
        }
    ],
    "count": 1,
    "summary": "Found 1 function(s) that call..."
}
```

#### `explore_code` - Complete Context
Get source + callers + dependencies in ONE call.

```python
explore_code(identifier="vllm.worker.worker.execute_model")
```

**Returns:**
- Source code
- All callers (incoming)
- All dependencies (outgoing)
- Dependency tree

#### `find_class_hierarchy` - Class Inheritance Analysis
Find parent classes and child classes of a class.

```python
find_class_hierarchy(class_qualified_name="vllm.attention.PagedAttention")
```

**Response Structure:**
```json
{
    "success": true,
    "results": [{
        "class_name": "vllm.attention.PagedAttention",
        "parents": ["vllm.attention.BaseAttention"],
        "children": ["vllm.attention.FlashAttention"]
    }],
    "summary": "Found inheritance hierarchy..."
}
```

**Use Cases:**
- Understanding class relationships before refactoring
- Checking impact of base class changes
- Analyzing inheritance patterns

#### `trace_dependencies` - Dependency Chain Analysis
Trace paths between code elements and detect circular dependencies.

```python
# Find path from A to B
trace_dependencies(
    start_qualified_name="vllm.main",
    end_qualified_name="vllm.database.connect",
    max_depth=5
)

# Find all reachable from A (without end target)
trace_dependencies(
    start_qualified_name="vllm.main",
    max_depth=3
)
```

**Response Structure:**
```json
{
    "chain": [
        {"from": "vllm.main", "to": "vllm.engine.run", "type": "CALLS"},
        {"from": "vllm.engine.run", "to": "vllm.database.connect", "type": "CALLS"}
    ],
    "depth": 2,
    "total_elements": 3,
    "has_circular": false,
    "circular_paths": [],
    "summary": "Found path from vllm.main to vllm.database.connect..."
}
```

**Use Cases:**
- Finding how function A eventually calls function B
- Detecting circular dependencies
- Impact analysis before refactoring

### Retrieval

#### `get_code_snippet` - Get Source Code
```python
get_code_snippet(qualified_name="vllm.attention.PagedAttention.forward")
```

**Response Structure:**
```json
{
    "found": true,
    "qualified_name": "vllm.attention.PagedAttention.forward",
    "file_path": "vllm/attention.py",
    "line_start": 100,
    "line_end": 150,
    "source_code": "def forward(self, ...):\n    ...",
    "docstring": "Forward pass documentation..."
}
```

#### `get_children` - Navigate Structure
Get children of any node type.

**Special identifier values:**
- `"."` - Current project from `set_project()`

**Identifier types:**
- `"auto"` - Auto-detect (recommended)
- `"project"` - By project name
- `"folder"` - By file path
- `"file"` - By file path or qualified_name
- `"class"` - By qualified_name

```python
# Project structure
get_children(identifier=".", identifier_type="auto", depth=2)

# Class methods
get_children(identifier="vllm.modeling_models.LlamaModel",
             identifier_type="class")

# File contents
get_children(identifier="src/models/llama.py", identifier_type="file")
```

### Graph Management

#### `build_graph` - Build Knowledge Graph
```python
build_graph(project_path="/path/to/project", project_name="myproject")
# Creates graph named: "myproject_claude" (auto-adds _claude suffix)
# Returns a job_id → use get_job_status(job_id="...") to track progress
```

#### `refresh_graph` - Update After Changes
```python
refresh_graph(project_name="myproject")
# Uses exact name, or tries with "_claude" suffix if not found
```

#### `clean_graph` - Remove Graph
```python
clean_graph(project_name="myproject")
# Uses exact name, or tries with "_claude" suffix if not found
```

## Workflows

### Workflow 1: Understanding a Function
```python
# 1. Find it
find_nodes("calculate_attention")

# 2. Get comprehensive analysis
explore_code(identifier="vllm.attention.calculate_attention")
```

### Workflow 2: Refactoring - Who Uses This?
```python
# 1. Find the function
find_nodes("deprecated_function")

# 2. Find all callers
find_calls(
    qualified_name="vllm.utils.deprecated_function",
    direction="incoming",
    depth=3
)

# 3. Check each caller before refactoring
get_code_snippet(qualified_name="...")
```

### Workflow 3: Project Overview
```python
# 1. Check existing graphs
list_repos()

# 2. Build if needed
build_graph(project_path=".", project_name="myproject")
# Creates: "myproject_claude"

# 3. Set context (use exact name from list_repos)
set_project(project_name="myproject_claude")

# 4. Explore structure
get_children(identifier=".", depth=2)

# 5. Find entry points
find_nodes("main|cli|start")
```

### Workflow 4: Troubleshooting
```python
# 1. Check system health
check_health()
# Shows: database status, config, project context

# 2. If database error → ensure Memgraph is running
# 3. If project not set → call set_project()
# 4. If no projects → build_graph() first
```

### Workflow 5: Class Hierarchy Analysis
```python
# 1. Find the class
find_nodes("BaseAttention")

# 2. Get inheritance hierarchy
find_class_hierarchy(class_qualified_name="vllm.attention.BaseAttention")

# 3. Explore specific child classes
get_code_snippet(qualified_name="vllm.attention.FlashAttention")
```

### Workflow 6: Circular Dependency Detection
```python
# 1. Find suspicious function
find_nodes("process_request")

# 2. Trace dependencies and detect cycles
trace_dependencies(
    start_qualified_name="vllm.engine.process_request",
    max_depth=10
)
# Check has_circular and circular_paths in response
```

## Error Handling

### Common Errors and Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| "No project context" | `set_project()` not called | Call `set_project(project_name="...")` first |
| "Cannot connect to Memgraph" | Database not running | Run `docker ps \| grep memgraph` to check |
| "Code not found" | Invalid qualified_name | Use `find_nodes()` first to get valid names |
| "Project not found" | Wrong project name | Use `list_repos()` to see available names |

### Error Recovery Workflow
```python
# 1. Always start with health check if errors occur
result = check_health()

# 2. Check database status
if result["database"]["status"] == "error":
    # Start Memgraph: docker compose -f docker/compose.yaml up -d memgraph
    pass

# 3. Check project context
if result["project_context"]["status"] == "not_set":
    # List available projects and set context
    list_repos()
    set_project(project_name="...")
```

## Best Practices

1. **Always start with `find_nodes()`** - Get qualified_name first
2. **Use single keywords** - `"flops"` not `"flops calculation"`
3. **Use `|` for variants** - `"torch|cuda"` not `"torch cuda"`
4. **Use `explore_code` for deep dives** - One call vs many
5. **Check `list_repos()` first** - Avoid rebuilding existing graphs
6. **Run `check_health()` on errors** - Diagnose connection issues
7. **Use `find_class_hierarchy` for OOP analysis** - Understand inheritance
8. **Use `trace_dependencies` for impact analysis** - Before refactoring

## Performance Tips

- **find_calls depth**: Keep depth ≤ 3 for interactive use; depth 5 can be slow
- **trace_dependencies**: Use smaller max_depth (3-5) for faster results
- **find_nodes**: Single keywords are faster than complex patterns
- **sync tools**: Use `skip_embeddings=True` for faster updates if semantic search not needed

## Filtering Search Results

### Excluding Test Files
Search results often include test files. Use path patterns to focus on main code:

```python
# Instead of searching broadly
find_nodes("generate")  # Returns 50+ results including tests

# Use more specific keywords with module context
find_nodes("entrypoints.*generate")  # Focuses on entrypoints module
find_nodes("LLM.generate")  # Searches for class method pattern
```

### Path-based Filtering Tips
- Prefix with module path: `"project.engine.generate"` vs `"generate"`
- Use glob patterns: `"worker*execute"` matches worker-related execute functions
- Combine with type: `node_type="Code"` excludes File/Folder nodes

## Limitations & Known Issues

### Call Relationships May Be Empty
`find_calls()` and `trace_dependencies()` may return empty results for some functions:

**Causes:**
- **C++/CUDA code**: Call relationships are only tracked for Python code
- **Dynamic dispatch**: Calls through `getattr()`, decorators, or metaclasses may not be captured
- **External libraries**: Calls to third-party packages aren't tracked
- **Incomplete graph build**: The graph may not have analyzed all call sites

**Workaround:**
```python
# If find_calls returns empty, try explore_code for more context
explore_code(identifier="project.module.function")

# Or use get_code_snippet and manually trace
get_code_snippet(qualified_name="project.module.function")
```

### explore_code Source Code May Be Null
The `code_snippet` field in `explore_code()` results may be null if:
- The file path cannot be resolved
- The project_root is not correctly set
- The file exists but reading failed

**Workaround:**
```python
# Use get_code_snippet directly for reliable source retrieval
result = explore_code(identifier="project.module.function")
if result.get("code_snippet") is None:
    # Fallback to get_code_snippet
    get_code_snippet(qualified_name="project.module.function")
```

### get_code_snippet Returns Empty source_code
The `source_code` field may be empty even when metadata (path, line numbers) is correct:
- File may have been moved or deleted since graph build
- project_root may not match actual file locations

**Workaround:**
```python
# Verify file path and read manually if needed
result = get_code_snippet(qualified_name="...")
if not result.get("source_code"):
    # Use the returned file_path and line numbers to read directly
    print(f"File: {result['file_path']}, lines {result['line_start']}-{result['line_end']}")
```

### File Children May Return Empty
`get_children(identifier="path/to/file.py", identifier_type="file")` may return empty:
- Not all files have their internal structure indexed
- Some files may only be indexed at the file level

**Workaround:**
```python
# Use find_nodes to search for classes/functions in specific files
find_nodes("filename.ClassName")
```

### Duplicate qualified_name Prefixes
Some graphs may have duplicate entries like:
- `project.module.function`
- `project.project.module.function`

**Best Practice:** Always copy the exact `qualified_name` from `find_nodes()` results.

## Tool Reference

| Tool | Input | Returns | Use When |
|------|-------|---------|----------|
| `check_health` | - | System status | Diagnosing errors |
| `list_repos` | - | Project names | Discover available graphs |
| `set_project` | project_name | Success | Set context for queries |
| `find_nodes` | query | qualified_name list | Find code by name |
| `find_calls` | qualified_name | Callers/Callees | Trace relationships |
| `explore_code` | qualified_name | Full context | Deep analysis |
| `get_code_snippet` | qualified_name | Source code | Get implementation |
| `get_children` | identifier | Child nodes | Navigate structure |
| `find_class_hierarchy` | class_qualified_name | Parents/Children | Class inheritance |
| `trace_dependencies` | start_qualified_name | Dependency chain | Path finding, cycles |
| `build_graph` | project_path | Job ID | Create new graph |
| `refresh_graph` | project_name | Job ID | Update graph |
| `clean_graph` | project_name | Success | Remove graph |
| `start_sync` | project_name, repo_path | Status | Start real-time monitoring |
| `stop_sync` | project_name | Status | Stop monitoring |
| `sync_now` | project_name | Statistics | One-shot update |
| `get_sync_status` | project_name | Status | Check sync state |

## Real-time Sync Tools

### `start_sync` - Enable File Watching
Start continuous monitoring for code changes.

```python
start_sync(
    project_name="myproject",
    repo_path="/path/to/project",
    skip_embeddings=False,
    subdirs="backend,frontend"  # Optional: monitor only these directories
)
```

**Response Structure:**
```json
{
    "success": true,
    "project_name": "myproject",
    "status": "started",
    "message": "Sync started for 'myproject'"
}
```

### `sync_now` - One-time Update
Update the graph without continuous monitoring.

```python
sync_now(project_name="myproject")
```

**Response Structure:**
```json
{
    "success": true,
    "project_name": "myproject",
    "added": 5,
    "modified": 3,
    "deleted": 1,
    "duration_ms": 1500
}
```

### `stop_sync` - Disable Monitoring
```python
stop_sync(project_name="myproject")
```

### `get_sync_status` - Check Status
```python
get_sync_status(project_name="myproject")
```

**Response Structure:**
```json
{
    "success": true,
    "project_name": "myproject",
    "is_watching": true,
    "is_processing": false,
    "pending_changes": 0,
    "latest_result": {
        "added": 2,
        "modified": 1,
        "deleted": 0
    }
}
```

### Sync Workflow
```python
# 1. Start monitoring during development
start_sync(project_name="myproject", repo_path="/path/to/project")

# 2. Check status periodically
get_sync_status(project_name="myproject")

# 3. Stop when done
stop_sync(project_name="myproject")
```

### One-shot Sync (Alternative)
```python
# For quick updates without continuous monitoring
sync_now(project_name="myproject")
```
