import ast
import csv
import io
import re
import shutil
import sys
import tokenize
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

__all__ = ("Kind", "ShakePlan", "shake")

WASM = b"\x00asm"

# Leading bytes of binaries built for a host OS.
HOST_BINARIES = {
    b"\x7fELF": "ELF",
    b"\xfe\xed\xfa\xce": "Mach-O",
    b"\xfe\xed\xfa\xcf": "Mach-O",
    b"\xce\xfa\xed\xfe": "Mach-O",
    b"\xcf\xfa\xed\xfe": "Mach-O",
    b"\xca\xfe\xba\xbe": "Mach-O",
    b"MZ": "PE",
}

# Files packages need only to build against them, never at runtime.
BUILD_TIME = {".h", ".c", ".cpp", ".pxd", ".pyx", ".pxi", ".a", ".pc"}

# shortcut: hand-listed stdlib additions up to 3.14, extend when targets.py adds
# a Python version. Wynd may run on an older Python than the Worker targets.
STDLIB = sys.stdlib_module_names | {
    "annotationlib",
    "compression",
    "_colorize",
    "_pyrepl",
    "_interpreters",
    "_interpqueues",
    "_interpchannels",
    "_zstd",
}

# Modules the Workers runtime provides without a vendored file.
RUNTIME = {
    "js",
    "pyodide",
    "pyodide_js",
    "_pyodide",
    "_pyodide_entrypoint_helper",
    "_cloudflare",
    "_cloudflare_compat_flags",
}

# PEP 263's encoding declaration, which must survive comment stripping.
CODING = re.compile(r"^[ \t\f]*#.*?coding[:=]")


class Kind(Enum):
    MODULE = "module"
    # A `from pkg import name` member, which may not be a module at all.
    MEMBER = "member"
    # `from pkg import *`, named by pkg.
    STAR = "star"
    # A computed name with a literal start, named "package:start".
    PREFIX = "prefix"
    # `import pkg`, which can read any attribute of pkg, lazy ones included.
    WHOLE = "whole"


@dataclass(frozen=True)
class ShakePlan:
    """Kept and dropped files of `site` by POSIX path, unresolved names, and
    build errors."""

    site: Path
    kept: dict[str, str] = field(default_factory=dict)
    dropped: dict[str, str] = field(default_factory=dict)
    # Imported names with no vendored file, and who imported them.
    unresolved: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def size_report(self, target: Path) -> str:
        """Bytes in the full tree and in the copies in `target`, without
        Wrangler's wrapping."""
        before = sum(self._size(name) for name in [*self.kept, *self.dropped])
        after = sum((target / name).stat().st_size for name in self.kept)
        return (
            f"python_modules is {before:,} bytes, {after:,} after pruning "
            f"({before - after:,} fewer). Each file's decision is in .wynd/shake.txt."
        )

    def log(self) -> str:
        """Every decision, one "decision<TAB>name<TAB>reason" line each."""
        decisions = {
            "kept": self.kept,
            "dropped": self.dropped,
            "unresolved": self.unresolved,
        }
        return "".join(
            f"{decision}\t{name}\t{reason}\n"
            for decision, names in decisions.items()
            for name, reason in names.items()
        )

    def record(self, name: str, kept: bool, reason: str) -> None:
        """Keep or drop a file of `site`, with the reason."""
        (self.kept if kept else self.dropped)[name] = reason

    def copy_kept(self, target: Path, docstrings: bool) -> None:
        """Copy the kept files into `target` at the same relative paths, with
        comments, and docstrings if asked, stripped from modules and the
        description stripped from metadata."""
        for name in self.kept:
            source, copy = self.site / name, target / name
            copy.parent.mkdir(parents=True, exist_ok=True)
            if name.endswith(".py"):
                copy.write_bytes(_strip(source.read_bytes(), docstrings))
            elif name.endswith(".dist-info/METADATA"):
                copy.write_bytes(_headers(source.read_bytes()))
            else:
                shutil.copy2(source, copy)

    def _size(self, name: str) -> int:
        return (self.site / name).stat().st_size


def shake(site: Path, source: Path, entry: Path, keep: tuple[str, ...]) -> ShakePlan:
    """Decide which files of `site` the Worker in `source` can import."""
    entry_name = ".".join(entry.relative_to(source).with_suffix("").parts)
    plan = _Shaker(site, source).run(entry_name, keep)

    # Without it nothing the Worker imports is kept, and the upload would fail
    # at import.
    if entry_name in plan.unresolved:
        plan.errors.append(f"entry module {entry_name} has no file in {source}")

    return plan


class _Shaker:
    """One breadth-first walk of the imports, filling in a ShakePlan."""

    def __init__(self, site: Path, source: Path) -> None:
        self.site = site
        self.roots = (source, site)
        self.plan = ShakePlan(site)
        self.queue: deque[tuple[str, str, Kind]] = deque()
        self.seen: set[tuple[str, Kind]] = set()
        self.missing: set[str] = set()
        # Packages that load attributes through a table, by name, with the table.
        self.tables: dict[str, dict[str, str]] = {}
        # Each table package's computed imports, followed only once some module
        # does `import pkg` and can read any attribute.
        self.deferred: dict[str, list[tuple[str, str, Kind]]] = {}
        self.whole: set[str] = set()

    def run(self, entry_name: str, keep: tuple[str, ...]) -> ShakePlan:
        self.queue.append((entry_name, "entry module", Kind.MODULE))
        self.queue += [(name, "keep list", Kind.MODULE) for name in keep]
        self.queue += self._pth_roots()

        while self.queue:
            self._visit(*self.queue.popleft())

        self._decide_files()
        self._check_binaries()
        return self.plan

    def _visit(self, name: str, reason: str, kind: Kind) -> None:
        if (name, kind) in self.seen:
            return

        if kind is Kind.PREFIX:
            self.seen.add((name, kind))
            modules = _prefixed(name, self.roots)
            self.queue += [(module, reason, Kind.MODULE) for module in modules]
            return

        whole = kind in (Kind.STAR, Kind.WHOLE)
        parent = name if whole else name.rpartition(".")[0]
        # Python imports a module's packages first.
        if parent and (parent, Kind.MODULE) not in self.seen:
            self.queue.appendleft((name, reason, kind))
            self.queue.appendleft((parent, f"package of {name}", Kind.MODULE))
            return

        self.seen.add((name, kind))
        if kind is Kind.STAR:
            self._visit_star(name, reason)
        elif kind is Kind.WHOLE:
            self.whole.add(name)
            self.queue += self.deferred.pop(name, [])
            # The computed import only reaches this package's own modules.
            lazy = self.tables.get(name, {}).values()
            self.queue += [(m, f"{reason} by a lazy import", Kind.MODULE) for m in lazy]
        else:
            self._visit_module(name, parent, reason, kind)

    def _visit_star(self, name: str, reason: str) -> None:
        path = None if name in self.missing else _find(name, self.roots)
        for member in _all(_parse(path)) if path else []:
            self.queue.append((f"{name}.{member}", reason, Kind.MEMBER))

    def _visit_module(self, name: str, parent: str, reason: str, kind: Kind) -> None:
        path = None if parent in self.missing else _find(name, self.roots)

        if path is None:
            self.missing.add(name)
            if kind is Kind.MODULE and parent not in self.missing:
                self.plan.unresolved.setdefault(name, reason)
            lazy = self.tables.get(parent, {}).get(name.rpartition(".")[2])
            if kind is Kind.MEMBER and lazy:
                self.queue.append((lazy, f"{reason} by a lazy import", Kind.MODULE))
            return

        tree = _parse(path)
        in_site = path.is_relative_to(self.site)
        absent = _absent_import(tree, self.roots) if in_site else None
        if absent:
            # Python would raise ImportError, as it does for a missing module.
            self.missing.add(name)
            if path.is_file():
                self.plan.dropped[path.relative_to(self.site).as_posix()] = (
                    f"imports {absent}, which isn't installed"
                )
            return

        if path.is_file() and in_site:
            self.plan.kept.setdefault(path.relative_to(self.site).as_posix(), reason)

        package = name if path.name == "__init__.py" or path.is_dir() else parent
        table = _lazy_table(tree, package)
        if table:
            self.tables[name] = table
        for imported, why, imported_kind in _imports(tree, package):
            edge = (imported, f"imported by {name}{why}", imported_kind)
            own = imported_kind is Kind.PREFIX and imported.partition(":")[0] == name
            if table and own and name not in self.whole:
                self.deferred.setdefault(name, []).append(edge)
            else:
                self.queue.append(edge)

    def _pth_roots(self) -> list[tuple[str, str, Kind]]:
        """The modules .pth files import. Other .pth lines are build errors."""
        roots = []
        for pth in sorted(self.site.glob("*.pth")):
            self.plan.kept[pth.name] = "startup hook"
            for number, line in enumerate(pth.read_text().splitlines(), 1):
                imported = _pth_imports(line)
                if imported is None:
                    self.plan.errors.append(
                        f"{pth.name} line {number} is not a plain import: {line}"
                    )
                for name in imported or []:
                    roots.append((name, f"imported by {pth.name}", Kind.MODULE))

        return roots

    def _decide_files(self) -> None:
        """Decide every file that isn't an imported module."""
        site, plan = self.site, self.plan
        files = sorted(p.relative_to(site) for p in site.rglob("*") if p.is_file())
        kept_packages = {Path(name).parts[0] for name in plan.kept}
        metadata = [f for f in files if f.parts[0].endswith(".dist-info")]

        decided = plan.kept.keys() | plan.dropped.keys()
        decisions = [
            (file, *_decide(file, kept_packages))
            for file in files
            if file.as_posix() not in decided and file not in metadata
        ]
        for file, kept, reason in decisions:
            plan.record(file.as_posix(), kept, reason)

        # Metadata goes last, once every other file has its decision.
        dists = {
            d: _metadata(site / d, plan.kept) for d in {f.parts[0] for f in metadata}
        }
        for file in metadata:
            plan.record(file.as_posix(), *dists[file.parts[0]])

    def _check_binaries(self) -> None:
        """Fail the build for every kept .so that isn't WebAssembly."""
        for name in self.plan.kept:
            if not name.endswith(".so"):
                continue
            with (self.site / name).open("rb") as file:
                head = file.read(4)
            if head.startswith(WASM):
                continue

            kind = next(
                (k for m, k in HOST_BINARIES.items() if head.startswith(m)), None
            )
            self.plan.errors.append(
                f"{name} is in {kind or 'an unknown'} format, not WebAssembly built "
                "for Pyodide, so it can't load in a Worker."
            )


def _pth_imports(line: str) -> list[str] | None:
    """The modules a .pth line imports, or None if it does anything else."""
    if not line.strip() or line.startswith("#"):
        return []

    try:
        (statement,) = ast.parse(line).body
    except (SyntaxError, ValueError):
        return None

    if not isinstance(statement, ast.Import):
        return None
    return [alias.name for alias in statement.names]


def _decide(file: Path, kept_packages: set[str]) -> tuple[bool, str]:
    """Whether to keep a file that isn't an imported module, and why."""
    if "__pycache__" in file.parts:
        return False, "bytecode cache"
    if file.suffix == ".pyi":
        return False, "type stub"
    if file.name == "py.typed":
        return False, "typing marker"

    for part in file.parts[:-1]:
        if part in ("tests", "test"):
            return False, "tests"
        if part in ("docs", "doc"):
            return False, "docs"
    if file.suffix in BUILD_TIME:
        return False, "build-time file"

    if file.suffix == ".py":
        return False, "not imported"
    if len(file.parts) == 1:
        return False, "outside any package"

    # Packages load data by computed paths, such as tzdata's zone files, so a
    # kept package keeps the data files anywhere in its tree.
    package = file.parts[0]
    if package in kept_packages:
        return True, f"data file of {package}"
    return False, f"no kept module in {package}"


def _metadata(dist_info: Path, kept: dict[str, str]) -> tuple[bool, str]:
    """Whether to keep a distribution's .dist-info, and why."""
    dist = dist_info.name.removesuffix(".dist-info")
    record = dist_info / "RECORD"
    if not record.is_file():
        return True, f"metadata of {dist}, which has no RECORD"

    with record.open(newline="") as file:
        if any(row and row[0] in kept for row in csv.reader(file)):
            return True, f"metadata of {dist}, which keeps a file"

    return False, f"metadata of {dist}, which keeps no file"


def _strip(source: bytes, docstrings: bool) -> bytes:
    """A module's source with comments, and docstrings if asked, blanked out
    without moving any line. Source that won't parse comes back unchanged."""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
        # Lines as Python counts them. str.splitlines also splits at form feeds
        # and other characters that don't end a line, which shifts every cut.
        lines = io.StringIO(source.decode(encoding), newline=None).readlines()
        text = "".join(lines)
        tree = ast.parse(text)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (SyntaxError, ValueError, UnicodeDecodeError, tokenize.TokenError):
        return source

    # (start row, start column, end row, end column, replacement), columns in
    # characters.
    cuts = [
        (*token.start, *token.end, "")
        for token in tokens
        if token.type == tokenize.COMMENT
        and not (token.start[0] <= 2 and CODING.match(token.line))
    ]
    for node in ast.walk(tree) if docstrings else []:
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        first = node.body[0] if node.body else None
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        assert first.end_lineno is not None and first.end_col_offset is not None
        # ast counts columns in UTF-8 bytes.
        start = len(lines[first.lineno - 1].encode()[: first.col_offset].decode())
        end = len(lines[first.end_lineno - 1].encode()[: first.end_col_offset].decode())
        # A body needs a statement. A module doesn't, and `pass` would come
        # before its `from __future__` imports.
        sole = len(node.body) == 1 and not isinstance(node, ast.Module)
        replacement = "pass" if sole else ""
        cuts.append((first.lineno, start, first.end_lineno, end, replacement))

    # From the end, so each cut leaves the rows and columns before it in place.
    for row, column, end_row, end_column, replacement in sorted(cuts, reverse=True):
        head, tail = lines[row - 1][:column], lines[end_row - 1][end_column:]
        lines[row - 1 : end_row] = [head + replacement + "\n" * (end_row - row) + tail]

    stripped = "".join(lines)
    try:
        # A docstring followed by `;` on its line leaves a stray `;`.
        ast.parse(stripped)
    except SyntaxError:
        return source
    return stripped.encode(encoding)


def _headers(metadata: bytes) -> bytes:
    """A METADATA file without its description, the body after the headers."""
    headers, blank, _ = metadata.partition(b"\n\n")
    return headers + b"\n" if blank else metadata


def _absent_import(tree: ast.Module, roots: tuple[Path, ...]) -> str | None:
    """A package that an unconditional top-level import names and that no root,
    the stdlib or the runtime has, so importing the module always fails."""
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            top = name.partition(".")[0]
            if top not in STDLIB and top not in RUNTIME and _find(top, roots) is None:
                return top
    return None


def _lazy_table(tree: ast.Module, package: str) -> dict[str, str]:
    """The modules a module-level `__getattr__` loads attributes from, by
    attribute, read from the literal dict of (package, ".module") pairs it uses."""
    getattr_ = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "__getattr__"
        ),
        None,
    )
    if getattr_ is None:
        return {}
    used = {node.id for node in ast.walk(getattr_) if isinstance(node, ast.Name)}

    table = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        else:
            continue
        if not (
            isinstance(target, ast.Name)
            and target.id in used
            and isinstance(node.value, ast.Dict)
        ):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            pair = value.elts if isinstance(value, ast.Tuple) else []
            module = pair[1] if len(pair) == 2 else None
            if not (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(module, ast.Constant)
                and isinstance(module.value, str)
            ):
                continue
            # A literal package, as in ('pydantic_core', '.core_schema'), is
            # another one. Anything else, like __spec__.parent, is this one.
            first = pair[0]
            literal = isinstance(first, ast.Constant) and isinstance(first.value, str)
            owner = first.value if literal else package
            if module.value == "__module__":
                table[key.value] = f"{owner}.{key.value}"
                continue
            level = len(module.value) - len(module.value.lstrip("."))
            base = _resolve_relative(module.value[level:], level, owner)
            if base:
                table[key.value] = base

    return table


def _parse(path: Path) -> ast.Module:
    """A module's syntax tree, empty for an extension or code that won't compile."""
    if path.suffix != ".py":
        return ast.Module(body=[], type_ignores=[])

    try:
        return ast.parse(path.read_bytes())
    except (SyntaxError, ValueError):
        # Python raises the same error at import, which the importer may catch.
        return ast.Module(body=[], type_ignores=[])


def _find(name: str, roots: tuple[Path, ...]) -> Path | None:
    """The file of a module, the directory of a namespace package, or None."""
    *parents, last = name.split(".")
    for root in roots:
        directory = root.joinpath(*parents)
        if (package := directory / last / "__init__.py").is_file():
            return package
        # An extension module wins over a source module of the same name.
        extensions = sorted(directory.glob(f"{last}.*.so")) + [directory / f"{last}.so"]
        for candidate in [*extensions, directory / f"{last}.py"]:
            if candidate.is_file():
                return candidate

    # A directory without __init__.py is a namespace package, the last resort.
    return next((d for r in roots if (d := r.joinpath(*parents, last)).is_dir()), None)


def _prefixed(name: str, roots: tuple[Path, ...]) -> list[str]:
    """Every module under a "package:start" name whose next part begins with start."""
    package, _, start = name.partition(":")
    path = _find(package, roots)
    if path is None or (path.is_file() and path.name != "__init__.py"):
        return []

    directory = path if path.is_dir() else path.parent
    modules = []
    for file in sorted(directory.rglob("*")):
        relative = file.relative_to(directory)
        if file.suffix not in (".py", ".so") or "__pycache__" in relative.parts:
            continue
        parts = [*relative.parent.parts, file.name.split(".")[0]]
        if parts[-1] == "__init__":
            parts.pop()
        if parts and parts[0].startswith(start):
            modules.append(".".join([package, *parts]))

    return modules


def _imports(tree: ast.Module, package: str) -> list[tuple[str, str, Kind]]:
    """What a module imports, as (name, reason suffix, kind)."""
    names = []
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            stack += node.orelse
            continue
        stack += ast.iter_child_nodes(node)
        if isinstance(node, ast.Import):
            names += [(alias.name, "", Kind.MODULE) for alias in node.names]
            # shortcut: `import pkg.sub` also binds pkg, but code then reads
            # pkg.sub, so only a bare `import pkg` follows pkg's lazy imports.
            # Upgrade to tracking attribute reads if a table package breaks.
            names += [
                (alias.name, "", Kind.WHOLE)
                for alias in node.names
                if "." not in alias.name
            ]
        elif isinstance(node, ast.ImportFrom):
            names += _from_import(node, package)
        elif isinstance(node, ast.Call) and _is_import_call(node.func) and node.args:
            names += _computed_import(node.args[0], package)

    return names


def _from_import(node: ast.ImportFrom, package: str) -> list[tuple[str, str, Kind]]:
    """The package a `from` import names, and each of its members or its star."""
    base = _resolve_relative(node.module or "", node.level, package)
    if not base:
        return []

    names = [(base, "", Kind.MODULE)]
    for alias in node.names:
        if alias.name == "*":
            names.append((base, "", Kind.STAR))
        else:
            names.append((f"{base}.{alias.name}", "", Kind.MEMBER))

    return names


def _computed_import(argument: ast.expr, package: str) -> list[tuple[str, str, Kind]]:
    """The modules an import_module or __import__ argument can name."""
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        level = len(argument.value) - len(argument.value.lstrip("."))
        base = _resolve_relative(argument.value[level:], level, package)
        return [(base, "", Kind.MODULE)] if base else []

    # f"pkg._backends._{name}" or "pkg." + name can name any module that the
    # literal start allows, as pydantic's lazy f".{name}" imports do.
    start = _literal_start(argument)
    level = len(start) - len(start.lstrip("."))
    head, _, rest = start[level:].rpartition(".")
    base = _resolve_relative(head, level, package)
    if not base:
        return []

    return [(f"{base}:{rest}", " by a computed name", Kind.PREFIX)]


def _literal_start(argument: ast.expr) -> str:
    """The literal text a computed string starts with, or ""."""
    if isinstance(argument, ast.JoinedStr) and argument.values:
        first = argument.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value

    if isinstance(argument, ast.BinOp) and isinstance(argument.op, ast.Add):
        left = argument.left
        if isinstance(left, ast.Constant) and isinstance(left.value, str):
            return left.value
        return _literal_start(left)

    return ""


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    )


def _is_import_call(func: ast.expr) -> bool:
    if isinstance(func, ast.Name):
        return func.id in ("__import__", "import_module")
    return isinstance(func, ast.Attribute) and func.attr == "import_module"


def _all(tree: ast.Module) -> list[str]:
    """A module's literal __all__, or nothing."""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            names = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
            return [name for name in names if isinstance(name, str)]
    return []


def _resolve_relative(module: str, level: int, package: str) -> str | None:
    """The absolute name of a relative import, or None above the top package."""
    if not level:
        return module

    parts = package.split(".") if package else []
    if level - 1 >= len(parts):
        return None
    base = ".".join(parts[: len(parts) - level + 1])
    return f"{base}.{module}" if module else base
