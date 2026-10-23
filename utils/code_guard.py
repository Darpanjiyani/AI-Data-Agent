"""
Code guard for the pandas code the ETL agent writes (EXP-12).

Two layers, like the SQL side's rule-based guard plus the read-only database user:

1. check_code(code) - BEFORE running: reads the code's syntax tree and rejects
   imports outside a small allow-list and calls that are never needed for data work
   (eval, exec, os.system, os.environ, ...). Returns readable problems, so the agent
   can rewrite its code.

2. runtime_prelude(data_dir) - WHILE running: a few lines placed in front of the code.
   They install a Python audit hook (PEP 578), which sees every file open, network
   connection and process start, even ones the static check can't spot (for example
   names built from strings). The hook allows:
     - writing files only inside data/
     - reading files only inside data/ and Python's own installation (libraries)
     - no network, no other programs, no deleting or renaming outside data/
   pandas' Parquet/Feather/ORC writers are checked too, because pyarrow writes those
   files in C, without going through Python's open().

This is a guard, not a container: it stops the mistakes and misuse the tests cover,
running inside the same user account. Running generated code in a container with no
network and a read-only filesystem is still the stronger, later step.
"""

import ast

# Modules generated ETL code may import. Anything else is rejected before running.
ALLOWED_MODULES = {
    "pandas", "numpy", "json", "re", "datetime", "math", "os", "pathlib", "csv",
    "collections", "itertools", "functools", "decimal", "string", "statistics",
    "zoneinfo", "calendar", "typing", "dateutil",
}

# Calls and attributes data code never needs. Names that are also everyday pandas or list
# methods (rename, replace, remove) are left to the runtime check, which sees the actual
# file operation instead of guessing from the name.
BLOCKED_CALLS = {"eval", "exec", "compile", "__import__", "breakpoint", "input", "globals",
                 "locals", "vars", "getattr", "setattr", "delattr"}
BLOCKED_ATTRIBUTES = {
    "system", "popen", "spawnl", "spawnv", "execv", "execl", "execve", "execvp", "fork", "kill",
    "startfile", "rmtree", "chmod", "chown", "symlink", "environ", "getenv", "putenv", "unsetenv",
    "read_sql", "read_sql_query", "read_sql_table", "to_sql", "read_clipboard", "to_clipboard",
    "read_pickle", "to_pickle",
}


def check_code(code: str) -> list:
    """Problems found in the code before running it; an empty list means it may run."""
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return [f"the code doesn't parse: {error.msg} (line {error.lineno})"]

    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            modules = []
        for module in modules:
            if module.split(".")[0] not in ALLOWED_MODULES:
                problems.append(f"import of '{module}' is not allowed")

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
            problems.append(f"calling {node.func.id}() is not allowed")
        if isinstance(node, ast.Attribute):
            if node.attr in BLOCKED_ATTRIBUTES:
                problems.append(f"using .{node.attr} is not allowed")
            elif node.attr.startswith("__") and node.attr.endswith("__"):
                problems.append(f"using .{node.attr} is not allowed")
        if isinstance(node, ast.Name) and node.id in {"__builtins__", "__loader__", "__spec__"}:
            problems.append(f"using {node.id} is not allowed")

    return list(dict.fromkeys(problems))  # unique, in order


# The runtime part. It's plain source code placed in front of the generated code, so it
# runs inside the child process. DATA_DIR is filled in by runtime_prelude().
_PRELUDE = r'''
import os as _os, sys as _sys, site as _site, sysconfig as _sysconfig

def _real(path):
    return _os.path.normcase(_os.path.realpath(_os.fsdecode(path)))

_DATA = _real(DATA_DIR)
_READABLE = {_DATA}
for _p in [_sys.prefix, _sys.base_prefix, _sys.exec_prefix, _os.path.dirname(_os.__file__),
           *_sysconfig.get_paths().values(), *getattr(_site, "getsitepackages", lambda: [])(),
           "/usr/share/zoneinfo", "/etc/localtime"]:
    if _p:
        _READABLE.add(_real(_p))

def _inside(path, roots):
    return any(path == root or path.startswith(root + _os.sep) for root in roots)

_WRITE_FLAGS = _os.O_WRONLY | _os.O_RDWR | _os.O_CREAT | _os.O_APPEND | _os.O_TRUNC
_NETWORK = {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname",
            "socket.sendto", "urllib.Request", "http.client.connect", "ftplib.connect", "smtplib.connect"}
_PROCESSES = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
              "os.forkpty", "os.startfile", "os.kill"}
_NATIVE = {"ctypes.dlopen", "ctypes.dlsym", "ctypes.dlsym/handle"}  # loading new native code
_PATH_CHANGES = {"os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.symlink",
                 "os.link", "os.truncate", "shutil.rmtree", "shutil.move", "shutil.copyfile"}

def _guard(event, args):
    if event == "open":
        path, mode, flags = args
        if path is None or isinstance(path, int):
            return
        target = _real(path)
        writing = (flags or 0) & _WRITE_FLAGS or (mode and any(c in str(mode) for c in "wax+"))
        if writing and not _inside(target, {_DATA}):
            raise PermissionError(f"code guard: writing outside data/ is not allowed ({path})")
        if not writing and not _inside(target, _READABLE):
            raise PermissionError(f"code guard: reading outside data/ is not allowed ({path})")
    elif event in _NETWORK:
        raise PermissionError("code guard: network access is not allowed")
    elif event in _PROCESSES:
        raise PermissionError("code guard: starting other programs is not allowed")
    elif event in _NATIVE:
        raise PermissionError("code guard: loading native libraries is not allowed")
    elif event in _PATH_CHANGES:
        for value in args[:2]:
            if isinstance(value, (str, bytes)) or hasattr(value, "__fspath__"):
                if not _inside(_real(value), {_DATA}):
                    raise PermissionError(f"code guard: changing files outside data/ is not allowed ({value})")

# Load the data libraries BEFORE the hook is installed. On Windows, importing numpy
# imports ctypes, which loads Python's own DLL; with the hook already on, that counts as
# loading native code and pandas can't even start (found in EXP-12's first Windows run).
# Loaded first, they're cached, and the generated code's own "import pandas" reuses them;
# after the hook is on, loading any NEW native library is blocked.
try:
    import pandas as _pd
    try:
        import pyarrow.parquet as _pq  # Parquet support, also loads native code
    except ImportError:
        pass
    def _checked(method):
        def wrapper(self, path=None, *args, **kwargs):
            if isinstance(path, (str, bytes)) or hasattr(path, "__fspath__"):
                if not _inside(_real(path), {_DATA}):
                    raise PermissionError(f"code guard: writing outside data/ is not allowed ({path})")
            return method(self, path, *args, **kwargs)
        return wrapper
    # pyarrow writes Parquet/Feather/ORC in C, so the open() check never sees those files
    for _name in ("to_parquet", "to_feather", "to_orc"):
        setattr(_pd.DataFrame, _name, _checked(getattr(_pd.DataFrame, _name)))
except ImportError:
    pass

_sys.addaudithook(_guard)   # can't be removed once added


del _p
'''


def runtime_prelude(data_dir) -> str:
    """Source code to place in front of the generated code (see the module docstring)."""
    return f"DATA_DIR = {str(data_dir)!r}\n{_PRELUDE}\ndel DATA_DIR\n"
