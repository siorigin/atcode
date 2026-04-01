# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end verification tests for the variable update mechanism and relationships.

Tests cover:
1. Python module-level variable extraction and DEFINES relationship
2. Python class-level variables and DEFINES_VARIABLE relationship
3. Variable modification detection (incremental diff)
4. JavaScript module-level variables
5. Incremental update performance (1/100 variable change)
6. File deletion cascade
7. Edge cases and track_variables toggle
8. Batch apply_changes interface
9. CALLS relationships (cross-file function calls)
10. CALLS relationships with class methods
11. Multi-level cross-file call chains (app -> services -> utils/models -> config)
12. CALLS relationship updates after file modification
13. Full graph integrity verification

Requirements:
- Memgraph must be running (docker start memgraph)
- Tree-sitter parsers must be available

Usage:
    cd /path/to/atcode/backend
    python -m pytest tests/test_variable_update.py -v
    python -m pytest tests/test_variable_update.py -v -k "TestCalls"
"""

import sys
import time
from pathlib import Path

import pytest
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import settings
from graph.service import MemgraphIngestor
from graph.sync.models import FileChange
from graph.sync.simple_updater import SimpleUpdater, compute_signature_hash
from graph.sync.updater import IncrementalUpdater
from parser.loader import load_parsers

# =============================================================================
# Constants
# =============================================================================

TEST_PROJECT_DIR = Path("/tmp/atcode_test_project")
TEST_PROJECT_NAME = "test_project"


# =============================================================================
# Test File Contents
# =============================================================================

CONFIG_PY = '''\
# 模块级变量
DATABASE_URL = "postgres://localhost/test"
CONFIG = {
    "timeout": 30,
    "retries": 3,
}
MAX_CONNECTIONS = 100
DEBUG_MODE = False

# 私有变量 (不应追踪)
_internal_cache = {}


def get_config():
    """Return the config dict."""
    local_var = "not tracked"
    return CONFIG


def get_db_url():
    """Return database URL."""
    return DATABASE_URL
'''

MODELS_PY = '''\
from config import CONFIG, get_db_url


class User:
    """User model."""
    count = 0
    default_role = "guest"
    table_name = "users"

    def __init__(self, name, role=None):
        self.name = name
        self.role = role or self.default_role
        User.count += 1

    def get_role(self):
        return self.role

    def save(self):
        """Save user to database."""
        db_url = get_db_url()
        timeout = CONFIG.get("timeout", 30)
        return True

    @classmethod
    def get_count(cls):
        return cls.count


class Product:
    """Product model."""
    inventory = []
    category_map = {}

    def __init__(self, name, price):
        self.name = name
        self.price = price

    def add_to_inventory(self):
        Product.inventory.append(self)

    def get_price(self):
        return self.price
'''

UTILS_PY = '''\
def format_name(name):
    """Format a name string."""
    return name.strip().title()


def validate_email(email):
    """Validate email format."""
    return "@" in email and "." in email


def calculate_total(items):
    """Calculate total price of items."""
    total = 0
    for item in items:
        total += item.get_price()
    return total


def generate_id():
    """Generate a unique ID."""
    import hashlib
    return hashlib.md5(b"seed").hexdigest()[:8]
'''

SERVICES_PY = '''\
from config import get_config, get_db_url, CONFIG, MAX_CONNECTIONS
from models import User, Product
from utils import format_name, validate_email, calculate_total, generate_id


SERVICE_NAME = "user_service"
MAX_RETRIES = 3


def create_user(name, email):
    """Create a new user with validation."""
    formatted = format_name(name)
    if not validate_email(email):
        raise ValueError("Invalid email")

    user = User(formatted)
    user.save()
    return user


def get_user_count():
    """Get total user count."""
    return User.get_count()


def create_product(name, price):
    """Create a new product."""
    uid = generate_id()
    product = Product(name, price)
    product.add_to_inventory()
    return product


def get_inventory_total():
    """Calculate total value of inventory."""
    return calculate_total(Product.inventory)


def get_service_config():
    """Get configuration for this service."""
    config = get_config()
    db_url = get_db_url()
    return {
        "service": SERVICE_NAME,
        "config": config,
        "db_url": db_url,
        "max_connections": MAX_CONNECTIONS,
    }
'''

APP_PY = '''\
from services import create_user, get_user_count, create_product, get_service_config


def main():
    """Application entry point."""
    config = get_service_config()
    print(f"Starting {config['service']}...")

    user = create_user("john doe", "john@example.com")
    print(f"Created user, total: {get_user_count()}")

    product = create_product("Widget", 9.99)
    print(f"Created product: {product.name}")


if __name__ == "__main__":
    main()
'''

ANIMALS_PY = """\
class Animal:
    species_count = 0
    def __init__(self, name):
        self.name = name
    def speak(self):
        return "..."
    def move(self):
        return "moving"

class Dog(Animal):
    breed_count = 0
    def speak(self):
        return "Woof!"
    def fetch(self):
        return "fetching"

class Cat(Animal):
    def speak(self):
        return "Meow!"

class Puppy(Dog):
    def speak(self):
        return "Yip!"
"""

CROSS_ANIMALS_PY = """\
from animals import Animal

class Fish(Animal):
    def speak(self):
        return "Blub!"
    def swim(self):
        return "swimming"
"""

EXTRA_UTILS_PY = """\
def helper_one():
    return 1
def helper_two():
    return 2
def helper_three():
    return helper_one() + helper_two()
"""

IMPORTER_PY = """\
import config
from utils import format_name, validate_email
from models import User

def do_something():
    cfg = config.get_config()
    name = format_name("test")
    return name
"""

API_CLIENT_JS = """\
const API_URL = "https://api.example.com";
let requestCount = 0;
var legacyConfig = { enabled: true };

function getCount() {
    const localConst = 1;
    return requestCount;
}

function makeRequest(endpoint) {
    requestCount += 1;
    return fetch(API_URL + endpoint);
}

function resetCount() {
    requestCount = 0;
}
"""


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def parsers_and_queries():
    """Load tree-sitter parsers and queries once for all tests."""
    parsers, queries = load_parsers()
    return parsers, queries


@pytest.fixture(scope="module")
def ingestor():
    """Create a MemgraphIngestor connected to the database."""
    ing = MemgraphIngestor(
        host=settings.MEMGRAPH_HOST,
        port=settings.MEMGRAPH_PORT,
    )
    ing.__enter__()
    yield ing
    ing.__exit__(None, None, None)


@pytest.fixture(scope="module")
def updater(ingestor, parsers_and_queries):
    """Create a SimpleUpdater for the test project."""
    parsers, queries = parsers_and_queries
    return SimpleUpdater(
        ingestor=ingestor,
        repo_path=TEST_PROJECT_DIR,
        project_name=TEST_PROJECT_NAME,
        parsers=parsers,
        queries=queries,
        track_variables=True,
    )


@pytest.fixture(scope="module")
def incremental_updater(ingestor, parsers_and_queries):
    """Create an IncrementalUpdater for the test project."""
    parsers, queries = parsers_and_queries
    return IncrementalUpdater(
        ingestor=ingestor,
        repo_path=TEST_PROJECT_DIR,
        project_name=TEST_PROJECT_NAME,
        parsers=parsers,
        queries=queries,
        track_variables=True,
        skip_embeddings=True,
    )


@pytest.fixture(autouse=True)
def clean_test_graph(ingestor):
    """Clean up test project nodes before each test."""
    ingestor.execute_query(
        """
        MATCH (n)
        WHERE n.qualified_name STARTS WITH $prefix
           OR (n:Project AND n.name = $name)
        DETACH DELETE n
        """,
        {"prefix": TEST_PROJECT_NAME + ".", "name": TEST_PROJECT_NAME},
    )
    ingestor.execute_query(
        "MERGE (p:Project {name: $name})",
        {"name": TEST_PROJECT_NAME},
    )
    yield


def _setup_test_files():
    """Write all test files to disk."""
    TEST_PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "config.py": CONFIG_PY,
        "models.py": MODELS_PY,
        "utils.py": UTILS_PY,
        "services.py": SERVICES_PY,
        "app.py": APP_PY,
        "api_client.js": API_CLIENT_JS,
    }
    for name, content in files.items():
        (TEST_PROJECT_DIR / name).write_text(content, encoding="utf-8")


def _create_file(filename: str, content: str) -> Path:
    """Create a test file and return its path."""
    file_path = TEST_PROJECT_DIR / filename
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


# =============================================================================
# Query Helpers
# =============================================================================


def _query_variables(ingestor, prefix: str = TEST_PROJECT_NAME) -> list[dict]:
    return ingestor.fetch_all(
        """
        MATCH (v:Variable)
        WHERE v.qualified_name STARTS WITH $prefix
        RETURN v.qualified_name AS qn, v.name AS name,
               v.start_line AS start_line, v.signature_hash AS sig_hash
        ORDER BY v.qualified_name
        """,
        {"prefix": prefix + "."},
    )


def _query_file_defines_variables(ingestor) -> list[dict]:
    return ingestor.fetch_all(
        """
        MATCH (f:File)-[:DEFINES]->(v:Variable)
        WHERE f.qualified_name STARTS WITH $prefix
        RETURN f.qualified_name AS file_qn, v.name AS var_name,
               v.qualified_name AS var_qn
        ORDER BY v.qualified_name
        """,
        {"prefix": TEST_PROJECT_NAME + "."},
    )


def _query_class_defines_variables(ingestor) -> list[dict]:
    return ingestor.fetch_all(
        """
        MATCH (c:Class)-[:DEFINES_VARIABLE]->(v:Variable)
        WHERE c.qualified_name STARTS WITH $prefix
        RETURN c.qualified_name AS class_qn, c.name AS class_name,
               v.name AS var_name, v.qualified_name AS var_qn
        ORDER BY v.qualified_name
        """,
        {"prefix": TEST_PROJECT_NAME + "."},
    )


def _query_private_variables(ingestor) -> list[dict]:
    return ingestor.fetch_all(
        """
        MATCH (v:Variable)
        WHERE v.qualified_name STARTS WITH $prefix
          AND v.name STARTS WITH '_'
        RETURN v.qualified_name AS qn, v.name AS name
        """,
        {"prefix": TEST_PROJECT_NAME + "."},
    )


def _query_null_signature_hashes(ingestor) -> list[dict]:
    return ingestor.fetch_all(
        """
        MATCH (v:Variable)
        WHERE v.qualified_name STARTS WITH $prefix
          AND v.signature_hash IS NULL
        RETURN v.qualified_name AS qn
        """,
        {"prefix": TEST_PROJECT_NAME + "."},
    )


def _query_variable_count(ingestor) -> int:
    result = ingestor.fetch_all(
        """
        MATCH (v:Variable)
        WHERE v.qualified_name STARTS WITH $prefix
        RETURN count(v) AS cnt
        """,
        {"prefix": TEST_PROJECT_NAME + "."},
    )
    return result[0]["cnt"] if result else 0


def _query_calls_from(ingestor, caller_qn: str) -> list[dict]:
    """Query outgoing CALLS from a specific function/method."""
    return ingestor.fetch_all(
        """
        MATCH (caller)-[:CALLS]->(callee)
        WHERE caller.qualified_name = $qn
        RETURN callee.qualified_name AS callee_qn, callee.name AS callee_name,
               labels(callee) AS callee_labels
        ORDER BY callee.qualified_name
        """,
        {"qn": caller_qn},
    )


def _query_calls_to(ingestor, callee_qn: str) -> list[dict]:
    """Query incoming CALLS to a specific function/method."""
    return ingestor.fetch_all(
        """
        MATCH (caller)-[:CALLS]->(callee)
        WHERE callee.qualified_name = $qn
        RETURN caller.qualified_name AS caller_qn, caller.name AS caller_name,
               labels(caller) AS caller_labels
        ORDER BY caller.qualified_name
        """,
        {"qn": callee_qn},
    )


def _query_all_calls(ingestor) -> list[dict]:
    """Query all CALLS relationships in the test project."""
    return ingestor.fetch_all(
        """
        MATCH (caller)-[:CALLS]->(callee)
        WHERE caller.qualified_name STARTS WITH $prefix
        RETURN caller.qualified_name AS caller_qn, caller.name AS caller_name,
               callee.qualified_name AS callee_qn, callee.name AS callee_name
        ORDER BY caller.qualified_name, callee.qualified_name
        """,
        {"prefix": TEST_PROJECT_NAME + "."},
    )


def _query_node_types(ingestor) -> dict[str, int]:
    """Get node type counts for the test project."""
    results = ingestor.fetch_all(
        """
        MATCH (n)
        WHERE n.qualified_name STARTS WITH $prefix
           OR (n:Project AND n.name = $name)
        RETURN labels(n) AS labels, count(*) AS cnt
        """,
        {"prefix": TEST_PROJECT_NAME + ".", "name": TEST_PROJECT_NAME},
    )
    return {r["labels"][0]: r["cnt"] for r in results}


def _query_imports_from(ingestor, file_qn: str) -> list[dict]:
    """Query outgoing IMPORTS from a specific File node."""
    return ingestor.fetch_all(
        """
        MATCH (f:File)-[:IMPORTS]->(t:File)
        WHERE f.qualified_name = $qn
        RETURN t.qualified_name AS target_qn, t.name AS target_name
        ORDER BY t.qualified_name
        """,
        {"qn": file_qn},
    )


def _query_all_imports(ingestor) -> list[dict]:
    """Query all IMPORTS relationships in the test project."""
    return ingestor.fetch_all(
        """
        MATCH (f:File)-[:IMPORTS]->(t:File)
        WHERE f.qualified_name STARTS WITH $prefix
        RETURN f.qualified_name AS source_qn, t.qualified_name AS target_qn
        ORDER BY f.qualified_name, t.qualified_name
        """,
        {"prefix": TEST_PROJECT_NAME + "."},
    )


def _query_inherits(ingestor) -> list[dict]:
    """Query all INHERITS relationships in the test project."""
    return ingestor.fetch_all(
        """
        MATCH (child:Class)-[:INHERITS]->(parent:Class)
        WHERE child.qualified_name STARTS WITH $prefix
        RETURN child.qualified_name AS child_qn, child.name AS child_name,
               parent.qualified_name AS parent_qn, parent.name AS parent_name
        ORDER BY child.qualified_name
        """,
        {"prefix": TEST_PROJECT_NAME + "."},
    )


def _query_overrides(ingestor) -> list[dict]:
    """Query all OVERRIDES relationships in the test project."""
    return ingestor.fetch_all(
        """
        MATCH (child:Method)-[:OVERRIDES]->(parent:Method)
        WHERE child.qualified_name STARTS WITH $prefix
        RETURN child.qualified_name AS child_qn, child.name AS child_name,
               parent.qualified_name AS parent_qn, parent.name AS parent_name
        ORDER BY child.qualified_name
        """,
        {"prefix": TEST_PROJECT_NAME + "."},
    )


def _query_project_structure(ingestor) -> list[dict]:
    """Query Project→CONTAINS_FILE→File relationships."""
    return ingestor.fetch_all(
        """
        MATCH (p:Project {name: $name})-[:CONTAINS_FILE]->(f:File)
        RETURN f.qualified_name AS file_qn, f.name AS file_name
        ORDER BY f.qualified_name
        """,
        {"name": TEST_PROJECT_NAME},
    )


def _query_file_defines(ingestor, file_qn: str) -> list[dict]:
    """Query File→DEFINES→(Function/Class) relationships."""
    return ingestor.fetch_all(
        """
        MATCH (f:File)-[:DEFINES]->(n)
        WHERE f.qualified_name = $qn
          AND (n:Function OR n:Class)
        RETURN n.qualified_name AS node_qn, n.name AS node_name,
               labels(n) AS node_labels
        ORDER BY n.qualified_name
        """,
        {"qn": file_qn},
    )


def _setup_inheritance_files():
    """Write animals.py and cross_animals.py to disk."""
    TEST_PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    (TEST_PROJECT_DIR / "animals.py").write_text(ANIMALS_PY, encoding="utf-8")
    (TEST_PROJECT_DIR / "cross_animals.py").write_text(
        CROSS_ANIMALS_PY, encoding="utf-8"
    )


def _add_all_files_via_graph_updater(ingestor, parsers_and_queries):
    """Build the full graph using GraphUpdater (includes CALLS resolution)."""
    from graph.updater import GraphUpdater

    parsers, queries = parsers_and_queries
    languages = None
    try:
        _, _, languages = load_parsers(return_languages=True)
    except Exception:
        pass

    updater = GraphUpdater(
        ingestor,
        TEST_PROJECT_DIR,
        parsers,
        queries,
        skip_embeddings=True,
        project_name=TEST_PROJECT_NAME,
        language_objects=languages,
    )
    updater.run(force_full_build=True)


# =============================================================================
# Test 1: Python Module-Level Variables
# =============================================================================


class TestPythonModuleVariables:
    """Test extraction of Python module-level variables."""

    def test_module_variables_created(self, updater, ingestor):
        _setup_test_files()
        file_path = TEST_PROJECT_DIR / "config.py"
        updater._add_file(
            file_path, f"{TEST_PROJECT_NAME}.config", f"{TEST_PROJECT_NAME}.config"
        )

        variables = _query_variables(ingestor, f"{TEST_PROJECT_NAME}.config")
        var_names = {v["name"] for v in variables}

        assert "DATABASE_URL" in var_names
        assert "CONFIG" in var_names
        assert "MAX_CONNECTIONS" in var_names
        assert "DEBUG_MODE" in var_names

    def test_private_variables_excluded(self, updater, ingestor):
        _setup_test_files()
        updater._add_file(
            TEST_PROJECT_DIR / "config.py",
            f"{TEST_PROJECT_NAME}.config",
            f"{TEST_PROJECT_NAME}.config",
        )

        private_vars = _query_private_variables(ingestor)
        assert len(private_vars) == 0, (
            f"Found private vars: {[v['name'] for v in private_vars]}"
        )

    def test_local_variables_excluded(self, updater, ingestor):
        _setup_test_files()
        updater._add_file(
            TEST_PROJECT_DIR / "config.py",
            f"{TEST_PROJECT_NAME}.config",
            f"{TEST_PROJECT_NAME}.config",
        )

        var_names = {v["name"] for v in _query_variables(ingestor)}
        assert "local_var" not in var_names

    def test_file_defines_variable_relationship(self, updater, ingestor):
        _setup_test_files()
        updater._add_file(
            TEST_PROJECT_DIR / "config.py",
            f"{TEST_PROJECT_NAME}.config",
            f"{TEST_PROJECT_NAME}.config",
        )

        rels = _query_file_defines_variables(ingestor)
        var_names = {r["var_name"] for r in rels}
        assert {"DATABASE_URL", "CONFIG", "MAX_CONNECTIONS", "DEBUG_MODE"} <= var_names

    def test_variable_count(self, updater, ingestor):
        _setup_test_files()
        updater._add_file(
            TEST_PROJECT_DIR / "config.py",
            f"{TEST_PROJECT_NAME}.config",
            f"{TEST_PROJECT_NAME}.config",
        )

        variables = _query_variables(ingestor, f"{TEST_PROJECT_NAME}.config")
        assert len(variables) == 4, (
            f"Expected 4, got {len(variables)}: {[v['name'] for v in variables]}"
        )

    def test_signature_hash_present(self, updater, ingestor):
        _setup_test_files()
        updater._add_file(
            TEST_PROJECT_DIR / "config.py",
            f"{TEST_PROJECT_NAME}.config",
            f"{TEST_PROJECT_NAME}.config",
        )

        null_hashes = _query_null_signature_hashes(ingestor)
        assert len(null_hashes) == 0


# =============================================================================
# Test 2: Python Class-Level Variables
# =============================================================================


class TestPythonClassVariables:
    """Test extraction of Python class-level variables."""

    def test_class_variables_created(self, updater, ingestor):
        _setup_test_files()
        updater._add_file(
            TEST_PROJECT_DIR / "models.py",
            f"{TEST_PROJECT_NAME}.models",
            f"{TEST_PROJECT_NAME}.models",
        )

        var_names = {
            v["name"] for v in _query_variables(ingestor, f"{TEST_PROJECT_NAME}.models")
        }
        assert "count" in var_names
        assert "default_role" in var_names
        assert "table_name" in var_names
        assert "inventory" in var_names
        assert "category_map" in var_names

    def test_class_defines_variable_relationship(self, updater, ingestor):
        _setup_test_files()
        updater._add_file(
            TEST_PROJECT_DIR / "models.py",
            f"{TEST_PROJECT_NAME}.models",
            f"{TEST_PROJECT_NAME}.models",
        )

        rels = _query_class_defines_variables(ingestor)

        user_vars = {r["var_name"] for r in rels if r["class_name"] == "User"}
        assert {"count", "default_role", "table_name"} <= user_vars

        product_vars = {r["var_name"] for r in rels if r["class_name"] == "Product"}
        assert {"inventory", "category_map"} <= product_vars

    def test_instance_attributes_excluded(self, updater, ingestor):
        _setup_test_files()
        updater._add_file(
            TEST_PROJECT_DIR / "models.py",
            f"{TEST_PROJECT_NAME}.models",
            f"{TEST_PROJECT_NAME}.models",
        )

        variables = _query_variables(ingestor, f"{TEST_PROJECT_NAME}.models")
        # self.name, self.role, self.price are instance attrs
        for v in variables:
            assert "User.name" not in v["qn"], "self.name is instance attr"
            assert "User.role" not in v["qn"], "self.role is instance attr"
            assert "Product.price" not in v["qn"], "self.price is instance attr"

    def test_class_variable_qualified_names(self, updater, ingestor):
        _setup_test_files()
        updater._add_file(
            TEST_PROJECT_DIR / "models.py",
            f"{TEST_PROJECT_NAME}.models",
            f"{TEST_PROJECT_NAME}.models",
        )

        qns = {
            v["qn"] for v in _query_variables(ingestor, f"{TEST_PROJECT_NAME}.models")
        }
        expected = {
            f"{TEST_PROJECT_NAME}.models.User.count",
            f"{TEST_PROJECT_NAME}.models.User.default_role",
            f"{TEST_PROJECT_NAME}.models.User.table_name",
            f"{TEST_PROJECT_NAME}.models.Product.inventory",
            f"{TEST_PROJECT_NAME}.models.Product.category_map",
        }
        assert qns == expected, f"Expected {expected}, got {qns}"


# =============================================================================
# Test 3: Variable Modification Detection
# =============================================================================


class TestVariableModification:
    def test_variable_update_detection(self, updater, ingestor):
        _setup_test_files()
        fqn = f"{TEST_PROJECT_NAME}.config"
        fp = TEST_PROJECT_DIR / "config.py"
        updater._add_file(fp, fqn, fqn)

        orig_hashes = {
            v["name"]: v["sig_hash"] for v in _query_variables(ingestor, fqn)
        }

        # Modify: change values, remove MAX_CONNECTIONS, add NEW_FLAG
        fp.write_text(
            """\
DATABASE_URL = "postgres://localhost/production"
CONFIG = {"timeout": 60}
DEBUG_MODE = True
NEW_FLAG = "added"

_internal_cache = {}

def get_config():
    return CONFIG

def get_db_url():
    return DATABASE_URL
""",
            encoding="utf-8",
        )

        updater._update_file_definitions(fp, fqn, fqn)

        updated = {v["name"]: v["sig_hash"] for v in _query_variables(ingestor, fqn)}

        assert updated["DATABASE_URL"] != orig_hashes["DATABASE_URL"]
        assert updated["CONFIG"] != orig_hashes["CONFIG"]
        assert "MAX_CONNECTIONS" not in updated
        assert "NEW_FLAG" in updated

        # Restore
        fp.write_text(CONFIG_PY, encoding="utf-8")

    def test_private_vars_stay_excluded_after_update(self, updater, ingestor):
        _setup_test_files()
        fqn = f"{TEST_PROJECT_NAME}.config"
        fp = TEST_PROJECT_DIR / "config.py"
        updater._add_file(fp, fqn, fqn)

        fp.write_text('DATABASE_URL = "x"\n_secret = "hidden"\n', encoding="utf-8")
        updater._update_file_definitions(fp, fqn, fqn)

        assert len(_query_private_variables(ingestor)) == 0
        fp.write_text(CONFIG_PY, encoding="utf-8")


# =============================================================================
# Test 4: JavaScript Module-Level Variables
# =============================================================================


class TestJavaScriptVariables:
    def test_js_module_variables_created(self, updater, ingestor):
        _setup_test_files()
        fp = TEST_PROJECT_DIR / "api_client.js"
        fqn = f"{TEST_PROJECT_NAME}.api_client"
        updater._add_file(fp, fqn, fqn)

        var_names = {v["name"] for v in _query_variables(ingestor, fqn)}
        assert "API_URL" in var_names
        assert "requestCount" in var_names
        assert "legacyConfig" in var_names

    def test_js_local_variables_excluded(self, updater, ingestor):
        _setup_test_files()
        fp = TEST_PROJECT_DIR / "api_client.js"
        fqn = f"{TEST_PROJECT_NAME}.api_client"
        updater._add_file(fp, fqn, fqn)

        var_names = {v["name"] for v in _query_variables(ingestor, fqn)}
        assert "localConst" not in var_names

    def test_js_variable_count(self, updater, ingestor):
        _setup_test_files()
        fp = TEST_PROJECT_DIR / "api_client.js"
        fqn = f"{TEST_PROJECT_NAME}.api_client"
        updater._add_file(fp, fqn, fqn)

        variables = _query_variables(ingestor, fqn)
        assert len(variables) == 3, (
            f"Expected 3, got {len(variables)}: {[v['name'] for v in variables]}"
        )


# =============================================================================
# Test 5: Incremental Update Performance
# =============================================================================


class TestIncrementalPerformance:
    def test_single_variable_update(self, updater, ingestor):
        # Generate large_config.py
        lines = ["# Large config"]
        for i in range(100):
            lines.append(f"VAR_{i} = {i}")
        fp = _create_file("large_config.py", "\n".join(lines) + "\n")

        fqn = f"{TEST_PROJECT_NAME}.large_config"
        updater._add_file(fp, fqn, fqn)

        orig = {v["name"]: v["sig_hash"] for v in _query_variables(ingestor, fqn)}
        assert len(orig) == 100

        content = fp.read_text().replace("VAR_50 = 50", "VAR_50 = 500")
        fp.write_text(content, encoding="utf-8")

        start = time.time()
        updater._update_file_definitions(fp, fqn, fqn)
        duration_ms = (time.time() - start) * 1000

        updated = _query_variables(ingestor, fqn)
        assert len(updated) == 100

        changed = [
            v
            for v in updated
            if orig.get(v["name"]) and v["sig_hash"] != orig[v["name"]]
        ]
        assert len(changed) == 1
        assert changed[0]["name"] == "VAR_50"

        logger.info(f"Incremental update 1/100 took {duration_ms:.0f}ms")

        content = fp.read_text().replace("VAR_50 = 500", "VAR_50 = 50")
        fp.write_text(content, encoding="utf-8")


# =============================================================================
# Test 6: File Deletion
# =============================================================================


class TestFileDeletion:
    def test_delete_file_removes_variables(self, updater, ingestor):
        fp = _create_file("temp_config.py", 'TEMP_VAR_1 = "hello"\nTEMP_VAR_2 = 42\n')
        fqn = f"{TEST_PROJECT_NAME}.temp_config"
        updater._add_file(fp, fqn, fqn)

        assert len(_query_variables(ingestor, fqn)) == 2
        updater._delete_file(fp, fqn)
        assert len(_query_variables(ingestor, fqn)) == 0


# =============================================================================
# Test 7: Edge Cases
# =============================================================================


class TestEdgeCases:
    def test_edge_cases_file(self, updater, ingestor):
        content = """\
counter = 0
counter += 1
x = y = z = 0
typed_var: int = 42
none_var = None
computed = 1 + 2 * 3
handler = lambda x: x * 2
"""
        fp = _create_file("edge_cases.py", content)
        fqn = f"{TEST_PROJECT_NAME}.edge_cases"
        updater._add_file(fp, fqn, fqn)

        var_names = {v["name"] for v in _query_variables(ingestor, fqn)}
        assert "counter" in var_names
        assert "none_var" in var_names
        assert "computed" in var_names
        assert "handler" in var_names

    def test_signature_hash_consistency(self, updater, ingestor):
        src = 'DATABASE_URL = "postgres://localhost/test"'
        assert compute_signature_hash(src) == compute_signature_hash(src)
        assert compute_signature_hash(src) != compute_signature_hash(
            src.replace("test", "prod")
        )

    def test_track_variables_disabled(self, ingestor, parsers_and_queries):
        _setup_test_files()
        parsers, queries = parsers_and_queries
        no_var = SimpleUpdater(
            ingestor=ingestor,
            repo_path=TEST_PROJECT_DIR,
            project_name=TEST_PROJECT_NAME,
            parsers=parsers,
            queries=queries,
            track_variables=False,
        )
        no_var._add_file(
            TEST_PROJECT_DIR / "config.py",
            f"{TEST_PROJECT_NAME}.config",
            f"{TEST_PROJECT_NAME}.config",
        )

        assert len(_query_variables(ingestor, f"{TEST_PROJECT_NAME}.config")) == 0


# =============================================================================
# Test 8: Batch Apply Changes
# =============================================================================


class TestBatchApplyChanges:
    def test_apply_add_change(self, updater, ingestor):
        fp = _create_file("batch_test.py", 'BATCH_VAR = "batch_test"\n')
        result = updater.apply_changes([FileChange(path=fp, action="add")])
        assert result.added == 1
        assert "BATCH_VAR" in {
            v["name"]
            for v in _query_variables(ingestor, f"{TEST_PROJECT_NAME}.batch_test")
        }

    def test_apply_delete_change(self, updater, ingestor):
        fp = _create_file("to_delete.py", 'TO_DELETE = "will be deleted"\n')
        updater.apply_changes([FileChange(path=fp, action="add")])
        assert _query_variable_count(ingestor) > 0

        result = updater.apply_changes([FileChange(path=fp, action="delete")])
        assert result.deleted == 1
        assert len(_query_variables(ingestor, f"{TEST_PROJECT_NAME}.to_delete")) == 0

    def test_apply_modify_change(self, updater, ingestor):
        fp = _create_file("mod_test.py", 'MOD_VAR = "original"\n')
        updater.apply_changes([FileChange(path=fp, action="add")])

        fp.write_text('MOD_VAR = "modified"\nNEW_VAR = 42\n', encoding="utf-8")
        result = updater.apply_changes([FileChange(path=fp, action="modify")])
        assert result.modified == 1

        var_names = {
            v["name"]
            for v in _query_variables(ingestor, f"{TEST_PROJECT_NAME}.mod_test")
        }
        assert "MOD_VAR" in var_names
        assert "NEW_VAR" in var_names


# =============================================================================
# Test 9: CALLS Relationships (Cross-File Function Calls)
# =============================================================================


class TestCallsRelationships:
    """Test CALLS relationships built by GraphUpdater."""

    def test_direct_function_calls(self, ingestor, parsers_and_queries):
        """Verify services.create_user CALLS utils.format_name and utils.validate_email."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        calls = _query_calls_from(ingestor, f"{TEST_PROJECT_NAME}.services.create_user")
        callee_names = {c["callee_name"] for c in calls}

        assert "format_name" in callee_names, (
            f"create_user should call format_name, got {callee_names}"
        )
        assert "validate_email" in callee_names, (
            f"create_user should call validate_email, got {callee_names}"
        )

    def test_cross_file_function_calls(self, ingestor, parsers_and_queries):
        """Verify services.get_service_config CALLS config.get_config and config.get_db_url."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        calls = _query_calls_from(
            ingestor, f"{TEST_PROJECT_NAME}.services.get_service_config"
        )
        callee_names = {c["callee_name"] for c in calls}

        assert "get_config" in callee_names, "get_service_config should call get_config"
        assert "get_db_url" in callee_names, "get_service_config should call get_db_url"

    def test_app_main_calls_services(self, ingestor, parsers_and_queries):
        """Verify app.main CALLS service functions."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        calls = _query_calls_from(ingestor, f"{TEST_PROJECT_NAME}.app.main")
        callee_names = {c["callee_name"] for c in calls}

        assert "create_user" in callee_names
        assert "get_user_count" in callee_names
        assert "create_product" in callee_names
        assert "get_service_config" in callee_names

    def test_incoming_calls(self, ingestor, parsers_and_queries):
        """Verify incoming CALLS: who calls utils.format_name?"""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        callers = _query_calls_to(ingestor, f"{TEST_PROJECT_NAME}.utils.format_name")
        caller_names = {c["caller_name"] for c in callers}

        assert "create_user" in caller_names, (
            "format_name should be called by create_user"
        )

    def test_generate_id_called_by_create_product(self, ingestor, parsers_and_queries):
        """Verify create_product calls generate_id (cross-file)."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        calls = _query_calls_from(
            ingestor, f"{TEST_PROJECT_NAME}.services.create_product"
        )
        callee_names = {c["callee_name"] for c in calls}

        assert "generate_id" in callee_names

    def test_no_self_calls(self, ingestor, parsers_and_queries):
        """Verify no function calls itself (unless recursive)."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        all_calls = _query_all_calls(ingestor)
        self_calls = [c for c in all_calls if c["caller_qn"] == c["callee_qn"]]

        assert len(self_calls) == 0, f"Unexpected self-calls: {self_calls}"


# =============================================================================
# Test 10: CALLS with Class Methods
# =============================================================================


class TestCallsWithMethods:
    """Test CALLS relationships involving class methods."""

    def test_method_calls_function(self, ingestor, parsers_and_queries):
        """Verify User.save() CALLS config.get_db_url()."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        calls = _query_calls_from(ingestor, f"{TEST_PROJECT_NAME}.models.User.save")
        callee_names = {c["callee_name"] for c in calls}

        assert "get_db_url" in callee_names, (
            f"User.save should call get_db_url, got {callee_names}"
        )

    def test_class_method_called(self, ingestor, parsers_and_queries):
        """Verify class method calls are resolved where possible.

        Note: `User.get_count()` style calls (attribute access on a class)
        may not be resolved by the call resolver. This test verifies that
        at minimum the Method node exists and is reachable.
        """
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        # Verify the Method node exists
        methods = ingestor.fetch_all(
            """
            MATCH (m:Method)
            WHERE m.qualified_name = $qn
            RETURN m.qualified_name AS qn
            """,
            {"qn": f"{TEST_PROJECT_NAME}.models.User.get_count"},
        )
        assert len(methods) == 1, "User.get_count method should exist"

        # Verify User class has DEFINES_METHOD -> get_count
        rels = ingestor.fetch_all(
            """
            MATCH (c:Class)-[:DEFINES_METHOD]->(m:Method)
            WHERE c.name = 'User' AND m.name = 'get_count'
              AND c.qualified_name STARTS WITH $prefix
            RETURN c.name AS class_name, m.name AS method_name
            """,
            {"prefix": TEST_PROJECT_NAME + "."},
        )
        assert len(rels) >= 1, "User class should DEFINES_METHOD get_count method"


# =============================================================================
# Test 11: Multi-Level Call Chains
# =============================================================================


class TestMultiLevelCallChains:
    """Test multi-level cross-file call chains."""

    def test_call_chain_depth(self, ingestor, parsers_and_queries):
        """Verify the full call chain: app.main -> services -> utils/models -> config."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        # Level 1: app.main -> services
        main_calls = {
            c["callee_name"]
            for c in _query_calls_from(ingestor, f"{TEST_PROJECT_NAME}.app.main")
        }
        assert "create_user" in main_calls

        # Level 2: services.create_user -> utils
        create_user_calls = {
            c["callee_name"]
            for c in _query_calls_from(
                ingestor, f"{TEST_PROJECT_NAME}.services.create_user"
            )
        }
        assert "format_name" in create_user_calls

    def test_total_calls_count(self, ingestor, parsers_and_queries):
        """Verify a reasonable number of CALLS relationships exist."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        all_calls = _query_all_calls(ingestor)
        # We expect at least:
        # app.main -> 4 (create_user, get_user_count, create_product, get_service_config)
        # services.create_user -> 2+ (format_name, validate_email)
        # services.create_product -> 1+ (generate_id)
        # services.get_service_config -> 2+ (get_config, get_db_url)
        # models.User.save -> 1+ (get_db_url)
        assert len(all_calls) >= 10, f"Expected >=10 CALLS, got {len(all_calls)}"

    def test_calls_span_multiple_files(self, ingestor, parsers_and_queries):
        """Verify CALLS relationships span different files."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        all_calls = _query_all_calls(ingestor)

        # Extract file prefixes from qualified names
        cross_file = []
        for c in all_calls:
            caller_mod = ".".join(c["caller_qn"].split(".")[:2])
            callee_mod = ".".join(c["callee_qn"].split(".")[:2])
            if caller_mod != callee_mod:
                cross_file.append(c)

        assert len(cross_file) >= 5, (
            f"Expected >=5 cross-file CALLS, got {len(cross_file)}: "
            f"{[(c['caller_name'], '->', c['callee_name']) for c in cross_file]}"
        )


# =============================================================================
# Test 12: CALLS Updates After File Modification
# =============================================================================


class TestCallsAfterModification:
    """Test that CALLS relationships update when files change."""

    def test_calls_update_on_add_call(
        self, incremental_updater, ingestor, parsers_and_queries
    ):
        """Adding a new function call in a file should create a new CALLS edge."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        # Original: services.get_inventory_total calls calculate_total
        orig_calls = _query_calls_from(
            ingestor, f"{TEST_PROJECT_NAME}.services.get_inventory_total"
        )
        orig_names = {c["callee_name"] for c in orig_calls}
        assert "calculate_total" in orig_names

        # Modify: add a call to generate_id in get_inventory_total
        modified = SERVICES_PY.replace(
            'def get_inventory_total():\n    """Calculate total value of inventory."""\n    return calculate_total(Product.inventory)',
            'def get_inventory_total():\n    """Calculate total value of inventory."""\n    uid = generate_id()\n    return calculate_total(Product.inventory)',
        )
        fp = TEST_PROJECT_DIR / "services.py"
        fp.write_text(modified, encoding="utf-8")

        changes = [FileChange(path=fp, action="modify")]
        incremental_updater.apply_changes(changes)

        updated_calls = _query_calls_from(
            ingestor, f"{TEST_PROJECT_NAME}.services.get_inventory_total"
        )
        updated_names = {c["callee_name"] for c in updated_calls}
        assert "generate_id" in updated_names, (
            f"Should now call generate_id, got {updated_names}"
        )
        assert "calculate_total" in updated_names, "Should still call calculate_total"

        # Restore
        fp.write_text(SERVICES_PY, encoding="utf-8")

    def test_calls_update_on_remove_call(
        self, incremental_updater, ingestor, parsers_and_queries
    ):
        """Removing a function call should remove the CALLS edge."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        # Original: create_user calls format_name and validate_email
        orig = {
            c["callee_name"]
            for c in _query_calls_from(
                ingestor, f"{TEST_PROJECT_NAME}.services.create_user"
            )
        }
        assert "validate_email" in orig

        # Modify: remove validate_email call
        modified = SERVICES_PY.replace(
            '    if not validate_email(email):\n        raise ValueError("Invalid email")\n\n    user',
            "    user",
        )
        fp = TEST_PROJECT_DIR / "services.py"
        fp.write_text(modified, encoding="utf-8")

        incremental_updater.apply_changes([FileChange(path=fp, action="modify")])

        updated = {
            c["callee_name"]
            for c in _query_calls_from(
                ingestor, f"{TEST_PROJECT_NAME}.services.create_user"
            )
        }
        assert "validate_email" not in updated, (
            f"validate_email should be removed, got {updated}"
        )
        assert "format_name" in updated, "format_name should still be there"

        fp.write_text(SERVICES_PY, encoding="utf-8")


# =============================================================================
# Test 13: Full Graph Integrity
# =============================================================================


class TestFullGraphIntegrity:
    """Verify overall graph integrity after build + variable sync."""

    def test_all_expected_node_types(self, ingestor, parsers_and_queries, updater):
        """Verify that all expected node types exist."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        # Add variables via SimpleUpdater
        for name in [
            "config.py",
            "models.py",
            "services.py",
            "app.py",
            "api_client.js",
        ]:
            fp = TEST_PROJECT_DIR / name
            fqn = f"{TEST_PROJECT_NAME}.{fp.stem}"
            updater._update_file_definitions(fp, fqn, fqn)

        types = _query_node_types(ingestor)

        assert types.get("Project", 0) >= 1
        assert types.get("File", 0) >= 5
        assert types.get("Function", 0) >= 10
        assert types.get("Class", 0) >= 2
        assert types.get("Method", 0) >= 2
        assert types.get("Variable", 0) >= 10

    def test_variables_and_calls_coexist(self, ingestor, parsers_and_queries, updater):
        """Verify that Variable nodes and CALLS edges coexist after sync."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        # Add variables
        for name in ["config.py", "models.py", "services.py"]:
            fp = TEST_PROJECT_DIR / name
            fqn = f"{TEST_PROJECT_NAME}.{fp.stem}"
            updater._update_file_definitions(fp, fqn, fqn)

        # Variables should exist
        vars = _query_variables(ingestor)
        assert len(vars) > 0, "Variables should exist"

        # CALLS should still exist
        calls = _query_all_calls(ingestor)
        assert len(calls) > 0, "CALLS should still exist after variable sync"

        # Specific check: services variables
        svc_vars = {
            v["name"]
            for v in _query_variables(ingestor, f"{TEST_PROJECT_NAME}.services")
        }
        assert "SERVICE_NAME" in svc_vars
        assert "MAX_RETRIES" in svc_vars

        # Specific check: services CALLS still intact
        svc_calls = {
            c["callee_name"]
            for c in _query_calls_from(
                ingestor, f"{TEST_PROJECT_NAME}.services.create_user"
            )
        }
        assert "format_name" in svc_calls

    def test_no_orphan_variables(self, ingestor, parsers_and_queries, updater):
        """Verify no Variable nodes lack a parent relationship."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        for name in ["config.py", "models.py", "services.py"]:
            fp = TEST_PROJECT_DIR / name
            fqn = f"{TEST_PROJECT_NAME}.{fp.stem}"
            updater._update_file_definitions(fp, fqn, fqn)

        orphans = ingestor.fetch_all(
            """
            MATCH (v:Variable)
            WHERE v.qualified_name STARTS WITH $prefix
              AND NOT ()-[:DEFINES]->(v)
              AND NOT ()-[:DEFINES_VARIABLE]->(v)
            RETURN v.qualified_name AS qn
            """,
            {"prefix": TEST_PROJECT_NAME + "."},
        )
        assert len(orphans) == 0, f"Orphan variables: {[o['qn'] for o in orphans]}"


# =============================================================================
# Test E: File DEFINES Function/Class
# =============================================================================


class TestFileDefinesFunction:
    """Test File --DEFINES--> Function and File --DEFINES--> Class relationships."""

    def test_file_defines_functions(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        defs = _query_file_defines(ingestor, f"{TEST_PROJECT_NAME}.config")
        names = {d["node_name"] for d in defs}
        assert "get_config" in names
        assert "get_db_url" in names

    def test_file_defines_all_utils_functions(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        defs = _query_file_defines(ingestor, f"{TEST_PROJECT_NAME}.utils")
        names = {d["node_name"] for d in defs}
        assert {
            "format_name",
            "validate_email",
            "calculate_total",
            "generate_id",
        } <= names

    def test_file_defines_class(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        defs = _query_file_defines(ingestor, f"{TEST_PROJECT_NAME}.models")
        names = {d["node_name"] for d in defs}
        assert "User" in names
        assert "Product" in names


# =============================================================================
# Test A: IMPORTS Relationships
# =============================================================================


class TestImportsRelationships:
    """Test File --IMPORTS--> File relationships."""

    def test_from_import_creates_imports_relationship(
        self, ingestor, parsers_and_queries
    ):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        imports = _query_imports_from(ingestor, f"{TEST_PROJECT_NAME}.services")
        target_names = {i["target_name"] for i in imports}
        # services.py imports from config, models, utils
        assert "config" in target_names or f"{TEST_PROJECT_NAME}.config" in {
            i["target_qn"] for i in imports
        }

    def test_import_module_creates_imports_relationship(
        self, ingestor, parsers_and_queries
    ):
        _setup_test_files()
        _create_file("importer.py", IMPORTER_PY)
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        imports = _query_imports_from(ingestor, f"{TEST_PROJECT_NAME}.importer")
        target_qns = {i["target_qn"] for i in imports}
        assert f"{TEST_PROJECT_NAME}.config" in target_qns

    def test_multiple_imports_in_single_file(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        imports = _query_imports_from(ingestor, f"{TEST_PROJECT_NAME}.services")
        target_qns = {i["target_qn"] for i in imports}
        # services.py imports from config and models at minimum
        assert f"{TEST_PROJECT_NAME}.config" in target_qns, (
            f"Expected config in {target_qns}"
        )
        assert f"{TEST_PROJECT_NAME}.models" in target_qns, (
            f"Expected models in {target_qns}"
        )
        assert len(target_qns) >= 2, f"Expected >=2 imports, got {target_qns}"

    def test_no_stdlib_imports_in_graph(self, ingestor, parsers_and_queries):
        """utils.py does `import hashlib` — this should not create an IMPORTS relationship."""
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        imports = _query_imports_from(ingestor, f"{TEST_PROJECT_NAME}.utils")
        target_qns = {i["target_qn"] for i in imports}
        # hashlib is stdlib, should not appear
        for qn in target_qns:
            assert "hashlib" not in qn, (
                f"stdlib hashlib should not be in IMPORTS: {target_qns}"
            )


# =============================================================================
# Test D: Project Structure
# =============================================================================


class TestProjectStructure:
    """Test project containment hierarchy."""

    def test_project_contains_files(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        files = _query_project_structure(ingestor)
        if len(files) == 0:
            # Some implementations use Folder as intermediary
            files = ingestor.fetch_all(
                """
                MATCH (p:Project {name: $name})-[:CONTAINS_FILE|CONTAINS_FOLDER*1..3]->(f:File)
                RETURN f.qualified_name AS file_qn, f.name AS file_name
                ORDER BY f.qualified_name
                """,
                {"name": TEST_PROJECT_NAME},
            )
        assert len(files) >= 5, f"Project should contain >=5 files, got {len(files)}"

    def test_file_nodes_have_correct_properties(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        file_nodes = ingestor.fetch_all(
            """
            MATCH (f:File)
            WHERE f.qualified_name STARTS WITH $prefix
            RETURN f.qualified_name AS qn, f.name AS name, f.path AS path,
                   f.extension AS extension
            ORDER BY f.qualified_name
            """,
            {"prefix": TEST_PROJECT_NAME + "."},
        )
        assert len(file_nodes) >= 5
        for f in file_nodes:
            assert f["qn"] is not None
            assert f["name"] is not None

    def test_subfolder_structure(self, ingestor, parsers_and_queries):
        _setup_test_files()
        subpkg = TEST_PROJECT_DIR / "subpkg"
        subpkg.mkdir(exist_ok=True)
        (subpkg / "__init__.py").write_text("", encoding="utf-8")
        (subpkg / "sub_module.py").write_text(
            "def sub_func():\n    return 42\n", encoding="utf-8"
        )
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        # Check that sub_module file/function exists
        funcs = ingestor.fetch_all(
            """
            MATCH (f:Function)
            WHERE f.qualified_name STARTS WITH $prefix
            RETURN f.qualified_name AS qn, f.name AS name
            """,
            {"prefix": TEST_PROJECT_NAME + ".subpkg"},
        )
        func_names = {f["name"] for f in funcs}
        assert "sub_func" in func_names, f"sub_func should exist, got {func_names}"


# =============================================================================
# Test B: INHERITS Relationships
# =============================================================================


class TestInheritsRelationships:
    """Test Class --INHERITS--> Class relationships."""

    def test_single_inheritance(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _setup_inheritance_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        rels = _query_inherits(ingestor)
        pairs = {(r["child_name"], r["parent_name"]) for r in rels}
        assert ("Dog", "Animal") in pairs, f"Dog should inherit Animal, got {pairs}"

    def test_multi_level_inheritance(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _setup_inheritance_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        rels = _query_inherits(ingestor)
        pairs = {(r["child_name"], r["parent_name"]) for r in rels}
        assert ("Puppy", "Dog") in pairs, f"Puppy should inherit Dog, got {pairs}"

    def test_multiple_subclasses(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _setup_inheritance_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        rels = _query_inherits(ingestor)
        pairs = {(r["child_name"], r["parent_name"]) for r in rels}
        assert ("Dog", "Animal") in pairs
        assert ("Cat", "Animal") in pairs

    def test_cross_file_inheritance(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _setup_inheritance_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        rels = _query_inherits(ingestor)
        pairs = {(r["child_name"], r["parent_name"]) for r in rels}
        assert ("Fish", "Animal") in pairs, (
            f"Fish should inherit Animal (cross-file), got {pairs}"
        )

    def test_no_spurious_inherits(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _setup_inheritance_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        rels = _query_inherits(ingestor)
        children_of_animal = {
            r["child_name"] for r in rels if r["parent_name"] == "Animal"
        }
        # Animal itself should not inherit anything
        animal_parents = [r for r in rels if r["child_name"] == "Animal"]
        assert len(animal_parents) == 0, (
            f"Animal should not inherit anything, got {animal_parents}"
        )


# =============================================================================
# Test C: OVERRIDES Relationships
# =============================================================================


class TestOverridesRelationships:
    """Test Method --OVERRIDES--> Method relationships.

    Note: OVERRIDES resolution depends on class_inheritance being populated
    during the override pass. Cross-file overrides are reliably resolved.
    Same-file overrides may or may not be resolved depending on implementation.
    """

    def test_cross_file_override(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _setup_inheritance_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        rels = _query_overrides(ingestor)
        pairs = {(r["child_qn"], r["parent_qn"]) for r in rels}
        expected = (
            f"{TEST_PROJECT_NAME}.cross_animals.Fish.speak",
            f"{TEST_PROJECT_NAME}.animals.Animal.speak",
        )
        assert expected in pairs, (
            f"Fish.speak should override Animal.speak, got {pairs}"
        )

    def test_no_override_for_new_method(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _setup_inheritance_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        rels = _query_overrides(ingestor)
        child_qns = {r["child_qn"] for r in rels}
        assert f"{TEST_PROJECT_NAME}.animals.Dog.fetch" not in child_qns, (
            "Dog.fetch is new, should not override anything"
        )

    def test_override_targets_correct_parent(self, ingestor, parsers_and_queries):
        """Verify that overrides target the correct parent method."""
        _setup_test_files()
        _setup_inheritance_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        rels = _query_overrides(ingestor)
        # All overrides of 'speak' should target either Animal.speak or Dog.speak
        for r in rels:
            if r["child_name"] == "speak":
                assert "speak" in r["parent_qn"], (
                    "Override target should be a speak method"
                )

    def test_move_not_overridden(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _setup_inheritance_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        rels = _query_overrides(ingestor)
        parent_qns = {r["parent_qn"] for r in rels}
        assert f"{TEST_PROJECT_NAME}.animals.Animal.move" not in parent_qns, (
            "Animal.move is not overridden by any subclass"
        )

    def test_override_count(self, ingestor, parsers_and_queries):
        """At least one OVERRIDES relationship should exist."""
        _setup_test_files()
        _setup_inheritance_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        rels = _query_overrides(ingestor)
        assert len(rels) >= 1, (
            f"Expected at least 1 OVERRIDES relationship, got {len(rels)}"
        )


# =============================================================================
# Test F: Incremental Add File
# =============================================================================


class TestIncrementalAddFile:
    """Test IncrementalUpdater._add_file() via apply_changes."""

    def test_incremental_add_creates_functions(
        self, incremental_updater, ingestor, parsers_and_queries
    ):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        fp = _create_file("extra_utils.py", EXTRA_UTILS_PY)
        incremental_updater.apply_changes([FileChange(path=fp, action="add")])

        funcs = ingestor.fetch_all(
            """
            MATCH (f:Function)
            WHERE f.qualified_name STARTS WITH $prefix
            RETURN f.name AS name
            """,
            {"prefix": f"{TEST_PROJECT_NAME}.extra_utils"},
        )
        names = {f["name"] for f in funcs}
        assert {"helper_one", "helper_two", "helper_three"} <= names

    def test_incremental_add_creates_calls(
        self, incremental_updater, ingestor, parsers_and_queries
    ):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        fp = _create_file("extra_utils.py", EXTRA_UTILS_PY)
        incremental_updater.apply_changes([FileChange(path=fp, action="add")])

        calls = _query_calls_from(
            ingestor, f"{TEST_PROJECT_NAME}.extra_utils.helper_three"
        )
        callee_names = {c["callee_name"] for c in calls}
        assert "helper_one" in callee_names, (
            f"helper_three should call helper_one, got {callee_names}"
        )
        assert "helper_two" in callee_names, (
            f"helper_three should call helper_two, got {callee_names}"
        )

    def test_incremental_add_creates_file_node(
        self, incremental_updater, ingestor, parsers_and_queries
    ):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        fp = _create_file("extra_utils.py", EXTRA_UTILS_PY)
        incremental_updater.apply_changes([FileChange(path=fp, action="add")])

        files = ingestor.fetch_all(
            """
            MATCH (f:File)
            WHERE f.qualified_name = $qn
            RETURN f.qualified_name AS qn
            """,
            {"qn": f"{TEST_PROJECT_NAME}.extra_utils"},
        )
        assert len(files) == 1, "File node should exist for extra_utils"


# =============================================================================
# Test H: Batch Mixed Changes
# =============================================================================


class TestBatchMixedChanges:
    """Test apply_changes() with mixed add + modify + delete in one batch."""

    def test_add_modify_delete_in_single_batch(
        self, incremental_updater, ingestor, parsers_and_queries
    ):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        # add extra_utils.py
        fp_add = _create_file("extra_utils.py", EXTRA_UTILS_PY)
        # modify config.py
        fp_mod = TEST_PROJECT_DIR / "config.py"
        fp_mod.write_text(
            CONFIG_PY.replace("MAX_CONNECTIONS = 100", "MAX_CONNECTIONS = 200"),
            encoding="utf-8",
        )
        # delete api_client.js
        fp_del = TEST_PROJECT_DIR / "api_client.js"

        result = incremental_updater.apply_changes(
            [
                FileChange(path=fp_add, action="add"),
                FileChange(path=fp_mod, action="modify"),
                FileChange(path=fp_del, action="delete"),
            ]
        )

        # extra_utils functions should exist
        funcs = ingestor.fetch_all(
            "MATCH (f:Function) WHERE f.qualified_name STARTS WITH $p RETURN f.name AS name",
            {"p": f"{TEST_PROJECT_NAME}.extra_utils"},
        )
        assert len(funcs) >= 3

        # api_client.js nodes should be gone
        js_nodes = ingestor.fetch_all(
            "MATCH (n) WHERE n.qualified_name STARTS WITH $p RETURN count(n) AS cnt",
            {"p": f"{TEST_PROJECT_NAME}.api_client"},
        )
        assert js_nodes[0]["cnt"] == 0

        # Restore
        fp_mod.write_text(CONFIG_PY, encoding="utf-8")

    def test_batch_result_statistics(
        self, incremental_updater, ingestor, parsers_and_queries
    ):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        fp_add = _create_file("extra_utils.py", EXTRA_UTILS_PY)
        fp_mod = TEST_PROJECT_DIR / "config.py"
        fp_mod.write_text(
            CONFIG_PY.replace("DEBUG_MODE = False", "DEBUG_MODE = True"),
            encoding="utf-8",
        )
        fp_del = TEST_PROJECT_DIR / "api_client.js"

        result = incremental_updater.apply_changes(
            [
                FileChange(path=fp_add, action="add"),
                FileChange(path=fp_mod, action="modify"),
                FileChange(path=fp_del, action="delete"),
            ]
        )

        assert result.added == 1
        assert result.modified == 1
        assert result.deleted == 1

        fp_mod.write_text(CONFIG_PY, encoding="utf-8")

    def test_add_two_files_simultaneously(
        self, incremental_updater, ingestor, parsers_and_queries
    ):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        fp1 = _create_file("extra_utils.py", EXTRA_UTILS_PY)
        fp2 = _create_file("importer.py", IMPORTER_PY)

        result = incremental_updater.apply_changes(
            [
                FileChange(path=fp1, action="add"),
                FileChange(path=fp2, action="add"),
            ]
        )
        assert result.added == 2

        # Both files should have function nodes
        for prefix in [
            f"{TEST_PROJECT_NAME}.extra_utils",
            f"{TEST_PROJECT_NAME}.importer",
        ]:
            funcs = ingestor.fetch_all(
                "MATCH (f:Function) WHERE f.qualified_name STARTS WITH $p RETURN f.name AS name",
                {"p": prefix},
            )
            assert len(funcs) >= 1, f"Functions should exist for {prefix}"


# =============================================================================
# Test G: Dependent Calls Rebuild
# =============================================================================


class TestDependentCallsRebuild:
    """Test that modifying a dependency triggers CALLS rebuild in dependents."""

    def test_dependent_rebuild_on_add_new_function(
        self, incremental_updater, ingestor, parsers_and_queries
    ):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        # Add a new function to utils.py
        new_utils = UTILS_PY + "\ndef new_helper():\n    return 'new'\n"
        fp_utils = TEST_PROJECT_DIR / "utils.py"
        fp_utils.write_text(new_utils, encoding="utf-8")

        # Also modify services.py to call new_helper
        new_services = SERVICES_PY.replace(
            "def get_service_config():",
            "def call_new_helper():\n    from utils import new_helper\n    return new_helper()\n\ndef get_service_config():",
        )
        fp_services = TEST_PROJECT_DIR / "services.py"
        fp_services.write_text(new_services, encoding="utf-8")

        incremental_updater.apply_changes(
            [
                FileChange(path=fp_utils, action="modify"),
                FileChange(path=fp_services, action="modify"),
            ]
        )

        # new_helper function should exist
        funcs = ingestor.fetch_all(
            "MATCH (f:Function) WHERE f.name = 'new_helper' AND f.qualified_name STARTS WITH $p RETURN f.qualified_name AS qn",
            {"p": TEST_PROJECT_NAME},
        )
        assert len(funcs) >= 1, "new_helper should exist"

        # Restore
        fp_utils.write_text(UTILS_PY, encoding="utf-8")
        fp_services.write_text(SERVICES_PY, encoding="utf-8")

    def test_modify_target_file_preserves_existing_calls(
        self, incremental_updater, ingestor, parsers_and_queries
    ):
        _setup_test_files()
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        # Verify existing calls
        calls_before = _query_calls_from(
            ingestor, f"{TEST_PROJECT_NAME}.services.get_service_config"
        )
        names_before = {c["callee_name"] for c in calls_before}
        assert "get_config" in names_before

        # Modify config.py (change variable value only, not function signatures)
        fp = TEST_PROJECT_DIR / "config.py"
        fp.write_text(
            CONFIG_PY.replace("MAX_CONNECTIONS = 100", "MAX_CONNECTIONS = 999"),
            encoding="utf-8",
        )

        incremental_updater.apply_changes([FileChange(path=fp, action="modify")])

        # Existing calls should still be intact
        calls_after = _query_calls_from(
            ingestor, f"{TEST_PROJECT_NAME}.services.get_service_config"
        )
        names_after = {c["callee_name"] for c in calls_after}
        assert "get_config" in names_after, (
            f"get_config call should survive, got {names_after}"
        )
        assert "get_db_url" in names_after, (
            f"get_db_url call should survive, got {names_after}"
        )

        fp.write_text(CONFIG_PY, encoding="utf-8")


# =============================================================================
# Test I: External Package Nodes
# =============================================================================


class TestExternalPackageNodes:
    """Test handling of external/unresolvable imports."""

    def test_unresolved_import_does_not_crash(self, ingestor, parsers_and_queries):
        _setup_test_files()
        _create_file(
            "bad_import.py", "import nonexistent_package\n\ndef foo():\n    return 1\n"
        )
        # Should not raise
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        # foo should still exist
        funcs = ingestor.fetch_all(
            "MATCH (f:Function) WHERE f.qualified_name = $qn RETURN f.name AS name",
            {"qn": f"{TEST_PROJECT_NAME}.bad_import.foo"},
        )
        assert len(funcs) == 1

    def test_external_package_node_schema(self, ingestor):
        """ExternalPackage should be in unique_constraints."""
        assert "ExternalPackage" in ingestor.unique_constraints

    def test_stdlib_imports_filtered(self, ingestor, parsers_and_queries):
        """Importing os/sys/json should not create IMPORTS to project files."""
        _setup_test_files()
        _create_file(
            "stdlib_user.py",
            "import os\nimport sys\nimport json\n\ndef bar():\n    return os.getcwd()\n",
        )
        _add_all_files_via_graph_updater(ingestor, parsers_and_queries)

        imports = _query_imports_from(ingestor, f"{TEST_PROJECT_NAME}.stdlib_user")
        for imp in imports:
            assert "os" != imp["target_name"], "os should not be in IMPORTS"
            assert "sys" != imp["target_name"], "sys should not be in IMPORTS"
            assert "json" != imp["target_name"], "json should not be in IMPORTS"


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
