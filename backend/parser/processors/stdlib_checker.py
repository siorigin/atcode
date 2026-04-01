# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import sys
from functools import lru_cache


class StdlibChecker:
    """Unified standard library checker with caching."""

    # Python stdlib modules - fallback for Python < 3.10
    # This covers Python 3.8+ standard library modules
    _PYTHON_STDLIB_FALLBACK: frozenset[str] = frozenset(
        {
            # Built-in types and functions
            "builtins",
            "abc",
            "types",
            "typing",
            # Text processing
            "string",
            "re",
            "difflib",
            "textwrap",
            "unicodedata",
            "stringprep",
            # Binary data
            "struct",
            "codecs",
            # Data types
            "datetime",
            "calendar",
            "collections",
            "heapq",
            "bisect",
            "array",
            "weakref",
            "copy",
            "pprint",
            "reprlib",
            "enum",
            "graphlib",
            # Numeric and math
            "numbers",
            "math",
            "cmath",
            "decimal",
            "fractions",
            "random",
            "statistics",
            # Functional programming
            "itertools",
            "functools",
            "operator",
            # File and directory access
            "pathlib",
            "fileinput",
            "stat",
            "filecmp",
            "tempfile",
            "glob",
            "fnmatch",
            "linecache",
            "shutil",
            # Data persistence
            "pickle",
            "copyreg",
            "shelve",
            "marshal",
            "dbm",
            "sqlite3",
            # Data compression
            "zlib",
            "gzip",
            "bz2",
            "lzma",
            "zipfile",
            "tarfile",
            # File formats
            "csv",
            "configparser",
            "tomllib",
            "netrc",
            "plistlib",
            # Cryptography
            "hashlib",
            "hmac",
            "secrets",
            # OS services
            "os",
            "io",
            "time",
            "argparse",
            "getopt",
            "logging",
            "getpass",
            "curses",
            "platform",
            "errno",
            "ctypes",
            # Concurrent execution
            "threading",
            "multiprocessing",
            "concurrent",
            "subprocess",
            "sched",
            "queue",
            "contextvars",
            "_thread",
            # Networking
            "asyncio",
            "socket",
            "ssl",
            "select",
            "selectors",
            "signal",
            "mmap",
            # Internet protocols
            "email",
            "json",
            "mailbox",
            "mimetypes",
            "base64",
            "binascii",
            "quopri",
            "html",
            "xml",
            "webbrowser",
            "wsgiref",
            "urllib",
            "http",
            "ftplib",
            "poplib",
            "imaplib",
            "smtplib",
            "uuid",
            "socketserver",
            "xmlrpc",
            "ipaddress",
            # Multimedia
            "wave",
            "colorsys",
            # Internationalization
            "gettext",
            "locale",
            # Program frameworks
            "turtle",
            "cmd",
            "shlex",
            # GUI
            "tkinter",
            # Development tools
            "pydoc",
            "doctest",
            "unittest",
            "test",
            # Debugging and profiling
            "bdb",
            "faulthandler",
            "pdb",
            "profile",
            "cProfile",
            "timeit",
            "trace",
            "tracemalloc",
            # Packaging and distribution
            "ensurepip",
            "venv",
            "zipapp",
            # Python runtime
            "sys",
            "sysconfig",
            "warnings",
            "dataclasses",
            "contextlib",
            "atexit",
            "traceback",
            "gc",
            "inspect",
            "site",
            # Importing
            "importlib",
            "pkgutil",
            "modulefinder",
            "runpy",
            "zipimport",
            # Python language services
            "ast",
            "symtable",
            "token",
            "keyword",
            "tokenize",
            "tabnanny",
            "pyclbr",
            "py_compile",
            "compileall",
            "dis",
            "pickletools",
            # MS Windows specific
            "msvcrt",
            "winreg",
            "winsound",
            # Unix specific
            "posix",
            "pwd",
            "grp",
            "fcntl",
            "pipes",
            "resource",
            "nis",
            "termios",
            "tty",
            "pty",
            # Undocumented/internal (commonly seen in imports)
            "_abc",
            "_ast",
            "_bisect",
            "_blake2",
            "_bootsubprocess",
            "_bz2",
            "_codecs",
            "_collections",
            "_collections_abc",
            "_compat_pickle",
            "_compression",
            "_contextvars",
            "_csv",
            "_ctypes",
            "_curses",
            "_curses_panel",
            "_datetime",
            "_dbm",
            "_decimal",
            "_elementtree",
            "_functools",
            "_gdbm",
            "_hashlib",
            "_heapq",
            "_imp",
            "_io",
            "_json",
            "_locale",
            "_lsprof",
            "_lzma",
            "_markupbase",
            "_md5",
            "_multibytecodec",
            "_multiprocessing",
            "_opcode",
            "_operator",
            "_osx_support",
            "_pickle",
            "_posixshmem",
            "_posixsubprocess",
            "_py_abc",
            "_pydecimal",
            "_pyio",
            "_queue",
            "_random",
            "_sha1",
            "_sha256",
            "_sha3",
            "_sha512",
            "_signal",
            "_socket",
            "_sqlite3",
            "_sre",
            "_ssl",
            "_stat",
            "_statistics",
            "_string",
            "_strptime",
            "_struct",
            "_symtable",
            "_thread",
            "_threading_local",
            "_tkinter",
            "_tracemalloc",
            "_uuid",
            "_warnings",
            "_weakref",
            "_weakrefset",
            "_winapi",
            "_xxsubinterpreters",
            "_xxtestfuzz",
            "_zoneinfo",
        }
    )

    # Lua standard library modules
    _LUA_STDLIB: frozenset[str] = frozenset(
        {
            "string",
            "math",
            "table",
            "io",
            "os",
            "debug",
            "coroutine",
            "package",
            "bit32",
            "utf8",
        }
    )

    # JavaScript/Node.js built-in modules
    _JS_STDLIB: frozenset[str] = frozenset(
        {
            # Node.js core modules
            "fs",
            "path",
            "os",
            "http",
            "https",
            "url",
            "util",
            "events",
            "stream",
            "buffer",
            "crypto",
            "zlib",
            "child_process",
            "cluster",
            "net",
            "dns",
            "tls",
            "readline",
            "repl",
            "vm",
            "assert",
            "console",
            "process",
            "timers",
            "querystring",
            "string_decoder",
            "punycode",
            # Browser globals
            "window",
            "document",
            "navigator",
            "location",
            "history",
            "localStorage",
            "sessionStorage",
            "fetch",
            "XMLHttpRequest",
            "console",
            "JSON",
            "Math",
            "Date",
            "Array",
            "Object",
            "String",
            "Number",
            "Boolean",
            "RegExp",
            "Error",
            "Promise",
            "Map",
            "Set",
            "WeakMap",
            "WeakSet",
            "Symbol",
            "Proxy",
            "Reflect",
        }
    )

    # Go standard library packages (top-level)
    _GO_STDLIB: frozenset[str] = frozenset(
        {
            "fmt",
            "io",
            "os",
            "net",
            "http",
            "json",
            "xml",
            "time",
            "sync",
            "context",
            "errors",
            "strings",
            "strconv",
            "bytes",
            "bufio",
            "regexp",
            "sort",
            "math",
            "crypto",
            "encoding",
            "compress",
            "archive",
            "path",
            "filepath",
            "flag",
            "log",
            "testing",
            "reflect",
            "runtime",
            "syscall",
            "unsafe",
            "builtin",
            "debug",
            "go",
            "embed",
        }
    )

    # Java standard library packages (top-level)
    _JAVA_STDLIB: frozenset[str] = frozenset(
        {
            "java",
            "javax",
            "sun",
            "com.sun",
            "org.w3c",
            "org.xml",
            "org.omg",
        }
    )

    # C++ standard library namespaces
    _CPP_STDLIB: frozenset[str] = frozenset(
        {
            "std",
            "boost",
            "__gnu_cxx",
        }
    )

    # Rust standard library crates
    _RUST_STDLIB: frozenset[str] = frozenset(
        {
            "std",
            "core",
            "alloc",
            "proc_macro",
        }
    )

    @classmethod
    @lru_cache(maxsize=1)
    def get_python_stdlib_modules(cls) -> frozenset[str]:
        """Get Python standard library modules (with caching).

        Uses sys.stdlib_module_names for Python 3.10+, falls back to
        hardcoded set for older versions.
        """
        try:
            # Python 3.10+ provides this directly
            return frozenset(sys.stdlib_module_names)
        except AttributeError:
            return cls._PYTHON_STDLIB_FALLBACK

    @classmethod
    def is_python_stdlib(cls, module_name: str) -> bool:
        """Check if a module is part of Python's standard library.

        Args:
            module_name: The root module name (e.g., "os", "collections")

        Returns:
            True if the module is part of Python's standard library
        """
        # Extract root module (handle "collections.abc" -> "collections")
        root_module = module_name.split(".")[0]
        return root_module in cls.get_python_stdlib_modules()

    @classmethod
    def is_lua_stdlib(cls, module_name: str) -> bool:
        """Check if a module is part of Lua's standard library."""
        root_module = module_name.split(".")[0]
        return root_module in cls._LUA_STDLIB

    @classmethod
    def is_js_stdlib(cls, module_name: str) -> bool:
        """Check if a module is part of JavaScript/Node.js standard library."""
        root_module = module_name.split(".")[0]
        return root_module in cls._JS_STDLIB

    @classmethod
    def is_go_stdlib(cls, module_name: str) -> bool:
        """Check if a package is part of Go's standard library."""
        # Go uses "/" for package paths
        root_package = module_name.split("/")[0]
        return root_package in cls._GO_STDLIB

    @classmethod
    def is_java_stdlib(cls, module_name: str) -> bool:
        """Check if a package is part of Java's standard library."""
        # Check if module starts with any stdlib prefix
        for prefix in cls._JAVA_STDLIB:
            if module_name == prefix or module_name.startswith(f"{prefix}."):
                return True
        return False

    @classmethod
    def is_cpp_stdlib(cls, module_name: str) -> bool:
        """Check if a namespace is part of C++ standard library."""
        # C++ uses "::" for namespaces
        root_namespace = module_name.split("::")[0]
        return root_namespace in cls._CPP_STDLIB

    @classmethod
    def is_rust_stdlib(cls, module_name: str) -> bool:
        """Check if a crate is part of Rust's standard library."""
        # Rust uses "::" for module paths
        root_crate = module_name.split("::")[0]
        return root_crate in cls._RUST_STDLIB

    @classmethod
    def is_stdlib(cls, module_name: str, language: str) -> bool:
        """Check if a module is part of the standard library for the given language.

        Args:
            module_name: The module/package name to check
            language: The programming language ("python", "javascript", "go", etc.)

        Returns:
            True if the module is part of the language's standard library
        """
        if language == "python":
            return cls.is_python_stdlib(module_name)
        elif language in ("javascript", "typescript"):
            return cls.is_js_stdlib(module_name)
        elif language == "lua":
            return cls.is_lua_stdlib(module_name)
        elif language == "go":
            return cls.is_go_stdlib(module_name)
        elif language == "java":
            return cls.is_java_stdlib(module_name)
        elif language == "cpp":
            return cls.is_cpp_stdlib(module_name)
        elif language == "rust":
            return cls.is_rust_stdlib(module_name)
        else:
            # Unknown language - assume not stdlib
            return False

    @classmethod
    @lru_cache(maxsize=1)
    def get_python_builtins(cls) -> frozenset[str]:
        """Get Python built-in function names from the builtins module.

        Returns a frozen set containing all built-in functions, types, and constants
        from Python's builtins module. This is more comprehensive than manually
        maintaining a list.

        Returns:
            FrozenSet of built-in names
        """
        import builtins

        # Filter out private/dunder names and modules
        builtin_names = {
            name
            for name in dir(builtins)
            if not name.startswith("_") or name in ("__import__", "__build_class__")
        }
        return frozenset(builtin_names)

    @classmethod
    def is_python_builtin(cls, name: str) -> bool:
        """Check if a name is a Python built-in function or type.

        Args:
            name: The function/variable name to check

        Returns:
            True if the name is a Python built-in
        """
        return name in cls.get_python_builtins()


# Convenience function for direct import
def is_stdlib(module_name: str, language: str = "python") -> bool:
    """Check if a module is part of the standard library.

    This is a convenience wrapper around StdlibChecker.is_stdlib().

    Args:
        module_name: The module/package name to check
        language: The programming language (default: "python")

    Returns:
        True if the module is part of the language's standard library
    """
    return StdlibChecker.is_stdlib(module_name, language)
