import runpy
from pathlib import Path
from typing import Any

import pytest
from conftest import Tools
from test_deploy import worker

from wynd.cli import main


def install(tools: Tools, files: dict[str, str | bytes]) -> None:
    """Add files to the site-packages the fake uv installs."""
    for name, content in files.items():
        path = tools.site_packages / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


def staged_modules(root: Path) -> list[str]:
    modules = root / ".wynd" / "build" / "python_modules"
    return sorted(
        path.relative_to(modules).as_posix()
        for path in modules.rglob("*")
        if path.is_file()
    )


def listing(root: Path) -> dict[str, tuple[str, str]]:
    """The shake listing as {path or name: (decision, reason)}."""
    lines = (root / ".wynd" / "shake.txt").read_text().splitlines()
    return {
        name: (decision, reason)
        for decision, name, reason in (line.split("\t") for line in lines)
    }


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = worker(tmp_path / "worker", {})
    monkeypatch.chdir(root)
    return root


def entry(root: Path, source: str) -> None:
    (root / "src" / "entry.py").write_text(source)


def test_deploy_stages_only_modules_the_entry_imports(
    tools: Tools, project: Path
) -> None:
    install(tools, {"used.py": "", "unused.py": ""})
    entry(project, "import workers\nimport used\n")

    assert main(["deploy"]) == 0

    assert staged_modules(project) == ["used.py", "workers/__init__.py"]
    shake = listing(project)
    assert shake["used.py"] == ("kept", "imported by entry")
    assert shake["unused.py"] == ("dropped", "not imported")


def test_a_module_keeps_its_packages_and_what_they_import(
    tools: Tools, project: Path
) -> None:
    install(
        tools,
        {
            "pkg/__init__.py": "from .core import run\n",
            "pkg/core.py": "from ..other import x\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/deep.py": "from . import sibling\nfrom .. import plugin\n",
            "pkg/sub/sibling.py": "",
            "pkg/plugin.py": "",
            "pkg/unused.py": "",
            "lib/__init__.py": "VALUE = 1\n",
            "lib/mod.py": "",
            "lib/rest.py": "",
        },
    )
    entry(project, "import pkg.sub.deep\nfrom lib import mod, VALUE\n")

    assert main(["deploy"]) == 0

    assert staged_modules(project) == [
        "lib/__init__.py",
        "lib/mod.py",
        "pkg/__init__.py",
        "pkg/core.py",
        "pkg/plugin.py",
        "pkg/sub/__init__.py",
        "pkg/sub/deep.py",
        "pkg/sub/sibling.py",
    ]
    shake = listing(project)
    assert shake["pkg/__init__.py"] == ("kept", "package of pkg.sub")
    assert shake["pkg/core.py"] == ("kept", "imported by pkg")
    # A name that isn't a module doesn't show up as unresolved.
    assert "lib.VALUE" not in shake
    # .. above the top package resolves to nothing, as Python's ImportError.
    assert "other" not in shake


def test_the_import_graph_follows_the_edge_rules(tools: Tools, project: Path) -> None:
    install(
        tools,
        {
            "lazy.py": "",
            "optional.py": "",
            "typed_only.py": "",
            "typed_too.py": "",
            "other_spelling.py": "",
            "dynamic.py": "",
            "dunder.py": "",
            # Python 2 code behind a guard, which Python never compiles.
            "legacy.py": "print 'hi'\nimport never_followed\n",
            "never_followed.py": "",
            "lazy_pkg/__init__.py": (
                "from importlib import import_module\n"
                "def __getattr__(name):\n"
                "    return import_module(f'.{name}', package=__name__)\n"
            ),
            "lazy_pkg/a.py": "",
            "lazy_pkg/sub/__init__.py": "",
            "lazy_pkg/sub/b.py": "",
            "backends/__init__.py": "",
            "backends/_asyncio.py": "",
            "backends/_trio.py": "",
            "backends/other.py": "",
            "star/__init__.py": "__all__ = ['listed', 'name']\nname = 1\n",
            "star/listed.py": "",
            "star/unlisted.py": "",
        },
    )
    entry(
        project,
        "import typing\n"
        "from typing import TYPE_CHECKING\n"
        "import importlib\n"
        "if TYPE_CHECKING:\n    import typed_only\n"
        "if typing.TYPE_CHECKING:\n    import typed_too\nelse:\n    import js\n"
        "TC = False\nif TC:\n    import other_spelling\n"
        "try:\n    import optional\nexcept ImportError:\n    optional = None\n"
        "if False:\n    import legacy\n"
        "def handler():\n    import lazy\n"
        "importlib.import_module('dynamic')\n"
        "__import__('dunder')\n"
        "from star import *\n"
        "from lazy_pkg import a_name\n"
        "importlib.import_module('backends._' + 'x')\n"
        "name = 'trio'\n"
        "importlib.import_module(f'backends._{name}')\n"
        "importlib.import_module(name)\n",
    )

    assert main(["deploy"]) == 0

    assert staged_modules(project) == [
        "backends/__init__.py",
        "backends/_asyncio.py",
        "backends/_trio.py",
        "dunder.py",
        "dynamic.py",
        "lazy.py",
        "lazy_pkg/__init__.py",
        "lazy_pkg/a.py",
        "lazy_pkg/sub/__init__.py",
        "lazy_pkg/sub/b.py",
        "legacy.py",
        "optional.py",
        "other_spelling.py",
        "star/__init__.py",
        "star/listed.py",
    ]
    shake = listing(project)
    assert shake["typed_only.py"] == ("dropped", "not imported")
    assert shake["js"] == ("unresolved", "imported by entry")
    assert shake["typing"] == ("unresolved", "imported by entry")
    assert shake["lazy_pkg/a.py"] == ("kept", "imported by lazy_pkg by a computed name")


def test_used_packages_keep_their_data_files_but_not_tests_docs_or_stubs(
    tools: Tools, project: Path
) -> None:
    install(
        tools,
        {
            "jinja/__init__.py": "",
            "jinja/__init__.pyi": "",
            "jinja/py.typed": "",
            "jinja/workflows.js": "export {}",
            "jinja/templates/base.html": "<html>",
            "jinja/__pycache__/__init__.cpython-312.pyc": b"\x00",
            "jinja/tests/__init__.py": "",
            "jinja/tests/data.json": "{}",
            "jinja/docs/index.md": "# docs",
            "jinja/include/jinja.h": "int x;",
            "jinja/_speedups.pxd": "",
            "jinja/ext/__init__.py": "",
            # Loaded by a computed name, as zoneinfo loads tzdata's zone files.
            "jinja/ext/ext.css": "",
            "other/__init__.py": "",
            "other/data.json": "{}",
            "solo.py": "",
            "README.txt": "top level",
        },
    )
    entry(project, "import jinja\nimport solo\n")

    assert main(["deploy"]) == 0

    assert staged_modules(project) == [
        "jinja/__init__.py",
        "jinja/ext/ext.css",
        "jinja/templates/base.html",
        "jinja/workflows.js",
        "solo.py",
    ]
    shake = listing(project)
    assert shake["jinja/templates/base.html"] == ("kept", "data file of jinja")
    assert shake["jinja/__init__.pyi"] == ("dropped", "type stub")
    assert shake["jinja/py.typed"] == ("dropped", "typing marker")
    assert shake["jinja/__pycache__/__init__.cpython-312.pyc"] == (
        "dropped",
        "bytecode cache",
    )
    assert shake["jinja/tests/data.json"] == ("dropped", "tests")
    assert shake["jinja/docs/index.md"] == ("dropped", "docs")
    assert shake["jinja/include/jinja.h"] == ("dropped", "build-time file")
    assert shake["jinja/_speedups.pxd"] == ("dropped", "build-time file")
    assert shake["jinja/ext/ext.css"] == ("kept", "data file of jinja")
    assert shake["other/data.json"] == ("dropped", "no kept module in other")
    assert shake["README.txt"] == ("dropped", "outside any package")


def record(*paths: str) -> str:
    return "".join(f"{path},sha256=x,1\n" for path in paths)


def test_metadata_ships_only_with_a_distribution_that_keeps_a_file(
    tools: Tools, project: Path
) -> None:
    install(
        tools,
        {
            "used/__init__.py": "",
            "used-1.0.dist-info/METADATA": (
                "Metadata-Version: 2.4\nName: used\nVersion: 1.0\n\n"
                "# used\n\nA README.\n"
            ),
            "used-1.0.dist-info/licenses/LICENSE": "MIT",
            "used-1.0.dist-info/RECORD": record(
                "used/__init__.py",
                "used-1.0.dist-info/METADATA",
                "used-1.0.dist-info/RECORD",
                "../../bin/used",
            ),
            "unused/__init__.py": "",
            "unused-2.0.dist-info/METADATA": "Name: unused",
            "unused-2.0.dist-info/RECORD": record(
                "unused/__init__.py", "unused-2.0.dist-info/RECORD"
            ),
            # Without a RECORD, Wynd can't tell whether the distribution is used.
            "bare-3.0.dist-info/METADATA": "Name: bare",
        },
    )
    entry(project, "import used\n")

    assert main(["deploy"]) == 0

    assert staged_modules(project) == [
        "bare-3.0.dist-info/METADATA",
        "used-1.0.dist-info/METADATA",
        "used-1.0.dist-info/RECORD",
        "used-1.0.dist-info/licenses/LICENSE",
        "used/__init__.py",
    ]
    shake = listing(project)
    assert shake["used-1.0.dist-info/METADATA"] == (
        "kept",
        "metadata of used-1.0, which keeps a file",
    )
    assert shake["unused-2.0.dist-info/METADATA"] == (
        "dropped",
        "metadata of unused-2.0, which keeps no file",
    )
    # The description is the README, which nothing reads at runtime.
    staged = project / ".wynd" / "build" / "python_modules"
    assert (staged / "used-1.0.dist-info/METADATA").read_text() == (
        "Metadata-Version: 2.4\nName: used\nVersion: 1.0\n"
    )
    assert (staged / "bare-3.0.dist-info/METADATA").read_text() == "Name: bare"


def test_pth_imports_are_roots(tools: Tools, project: Path) -> None:
    install(
        tools,
        {
            "_loader.pth": "# a comment\n\nimport _loader, _also\n",
            "_loader.py": "import _patches\n",
            "_also.py": "",
            "_patches.py": "",
        },
    )

    assert main(["deploy"]) == 0

    assert staged_modules(project) == [
        "_also.py",
        "_loader.pth",
        "_loader.py",
        "_patches.py",
        "workers/__init__.py",
    ]
    shake = listing(project)
    assert shake["_loader.pth"] == ("kept", "startup hook")
    assert shake["_loader.py"] == ("kept", "imported by _loader.pth")


@pytest.mark.parametrize(
    "line", ["/abs/path", "import os; os.system('x')", "import(", "from x import y"]
)
def test_a_pth_line_that_is_not_a_plain_import_fails_before_wrangler(
    tools: Tools, project: Path, capsys: pytest.CaptureFixture[str], line: str
) -> None:
    install(tools, {"hook.pth": f"import ok\n{line}\n"})

    assert main(["deploy"]) == 1
    assert f"hook.pth line 2 is not a plain import: {line}" in capsys.readouterr().err
    assert tools.wrangler() == []


WASM = b"\x00asm\x01\x00\x00\x00"


def test_webassembly_extensions_ship(tools: Tools, project: Path) -> None:
    install(
        tools,
        {
            "core/__init__.py": "from ._core import *\n",
            "core/_core.cpython-312-wasm32-emscripten.so": WASM,
            "core/libhelper.so": WASM,
            "native/__init__.py": "",
            # A host binary in a package the Worker never imports is dropped.
            "native/_ext.cpython-312-x86_64-linux-gnu.so": b"\x7fELF\x02",
        },
    )
    entry(project, "import core\n")

    assert main(["deploy"]) == 0

    assert staged_modules(project) == [
        "core/__init__.py",
        "core/_core.cpython-312-wasm32-emscripten.so",
        "core/libhelper.so",
    ]
    shake = listing(project)
    assert shake["core/_core.cpython-312-wasm32-emscripten.so"] == (
        "kept",
        "imported by core",
    )


@pytest.mark.parametrize(
    ("header", "kind"),
    [
        (b"\x7fELF\x02\x01", "ELF"),
        (b"\xcf\xfa\xed\xfe", "Mach-O"),
        (b"\xca\xfe\xba\xbe", "Mach-O"),
        (b"MZ\x90\x00", "PE"),
        (b"\x00\x00\x00\x00", "an unknown"),
    ],
)
def test_a_kept_non_webassembly_binary_fails_before_wrangler(
    tools: Tools,
    project: Path,
    capsys: pytest.CaptureFixture[str],
    header: bytes,
    kind: str,
) -> None:
    install(
        tools,
        {
            "fast/__init__.py": "from . import _speed\n",
            "fast/_speed.abi3.so": header + b"\x00" * 16,
        },
    )
    entry(project, "import fast\n")

    assert main(["deploy"]) == 1
    error = capsys.readouterr().err
    assert f"fast/_speed.abi3.so is in {kind} format" in error
    assert tools.wrangler() == []


def test_keep_entries_are_roots(tools: Tools, project: Path) -> None:
    install(tools, {"plugins/__init__.py": "", "plugins/a.py": "", "plugins/b.py": ""})
    with (project / "pyproject.toml").open("a") as pyproject:
        pyproject.write("[tool.wynd]\nkeep = ['plugins.a', 'missing']\n")

    assert main(["deploy"]) == 0

    assert staged_modules(project) == [
        "plugins/__init__.py",
        "plugins/a.py",
        "workers/__init__.py",
    ]
    shake = listing(project)
    assert shake["plugins/a.py"] == ("kept", "keep list")
    assert shake["missing"] == ("unresolved", "keep list")


def test_keep_must_be_a_list_of_module_names(
    tools: Tools, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with (project / "pyproject.toml").open("a") as pyproject:
        pyproject.write("[tool.wynd]\nkeep = 'plugins'\n")

    assert main(["deploy"]) == 1
    assert "[tool.wynd] keep must be a list of module names" in (
        capsys.readouterr().err
    )


def test_a_dry_run_reports_bytes_before_and_after_pruning(
    tools: Tools, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install(tools, {"used.py": "x=1\n" * 25, "unused.py": "x=1\n" * 600})
    entry(project, "import used\n")

    assert main(["deploy", "--dry-run"]) == 0
    assert capsys.readouterr().out == (
        "wynd: python_modules is 2,500 bytes, 100 after pruning (2,400 fewer). "
        "Each file's decision is in .wynd/shake.txt.\n"
    )

    assert main(["deploy"]) == 0
    assert capsys.readouterr().out == ""


def test_namespace_packages_resolve(tools: Tools, project: Path) -> None:
    install(
        tools,
        {
            "ns/trace/__init__.py": "from . import span\n",
            "ns/trace/span.py": "",
            "ns/metrics/__init__.py": "",
            "ns/version.txt": "1",
        },
    )
    entry(project, "import ns.trace\n")

    assert main(["deploy"]) == 0

    assert staged_modules(project) == [
        "ns/trace/__init__.py",
        "ns/trace/span.py",
        "ns/version.txt",
    ]
    assert "ns" not in listing(project)


STRIPPED = (
    "# -*- coding: latin-1 -*-\n"
    '"""Module docstring."""\n'
    "from __future__ import annotations\n"
    "\n"
    "# A comment.\n"
    "NAME = 'caf\xe9'  # trailing\n"
    "\n"
    "\n"
    "def only_doc():\n"
    '    """Nothing but a\n'
    '    docstring."""\n'
    "\n"
    "\n"
    "class Thing:\n"
    '    """A class."""\n'
    "\n"
    "    def method(self):\n"
    "        return '# not a comment'\n"
).encode("latin-1")


def staged_module(root: Path, name: str) -> tuple[bytes, dict[str, Any]]:
    """A staged module's bytes and the globals it defines."""
    path = root / ".wynd" / "build" / "python_modules" / name
    return path.read_bytes(), runpy.run_path(str(path))


def test_vendored_modules_ship_without_comments_or_docstrings(
    tools: Tools, project: Path
) -> None:
    install(tools, {"strip.py": STRIPPED})
    entry(project, "import strip\n")

    assert main(["deploy"]) == 0

    source, namespace = staged_module(project, "strip.py")
    text = source.decode("latin-1")
    # Tracebacks keep pointing at the vendored source's lines.
    assert text.count("\n") == STRIPPED.count(b"\n")
    assert text.startswith("# -*- coding: latin-1 -*-\n")
    for gone in ("docstring", "A comment", "trailing", "A class"):
        assert gone not in text
    assert "'# not a comment'" in text
    assert namespace["NAME"] == "caf\xe9"
    assert namespace.get("__doc__") is None
    assert namespace["only_doc"].__doc__ is None
    assert namespace["only_doc"].__code__.co_firstlineno == 9
    assert namespace["Thing"].__doc__ is None
    # The Worker's own source ships as written.
    staged_entry = project / ".wynd" / "build" / "src" / "entry.py"
    assert staged_entry.read_text() == "import strip\n"


def test_keep_docstrings_ships_vendored_docstrings(tools: Tools, project: Path) -> None:
    install(tools, {"strip.py": STRIPPED})
    entry(project, "import strip\n")
    with (project / "pyproject.toml").open("a") as pyproject:
        pyproject.write("[tool.wynd]\nkeep-docstrings = true\n")

    assert main(["deploy"]) == 0

    source, namespace = staged_module(project, "strip.py")
    assert b"A comment" not in source
    assert namespace["__doc__"] == "Module docstring."
    assert namespace["Thing"].__doc__ == "A class."


def test_keep_docstrings_must_be_a_boolean(
    tools: Tools, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with (project / "pyproject.toml").open("a") as pyproject:
        pyproject.write("[tool.wynd]\nkeep-docstrings = 'yes'\n")

    assert main(["deploy"]) == 1
    assert "[tool.wynd] keep-docstrings must be true or false" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize(
    "source",
    [
        "def broken(:\n    # a comment\n",
        # Blanking the docstring would leave a statement starting with ";".
        'def f():\n    """doc"""; return 1\n',
    ],
)
def test_source_that_wont_strip_ships_unchanged(
    tools: Tools, project: Path, source: str
) -> None:
    install(tools, {"odd.py": source})
    entry(project, "import odd\n")

    assert main(["deploy"]) == 0

    staged = project / ".wynd" / "build" / "python_modules" / "odd.py"
    assert staged.read_text() == source


def test_a_module_whose_top_level_import_is_missing_is_dropped(
    tools: Tools, project: Path
) -> None:
    install(
        tools,
        {
            "pkg/__init__.py": "",
            "pkg/plugin.py": "import os\nimport notinstalled.sub\nimport pkg.helper\n",
            "pkg/helper.py": "",
            "pkg/guarded.py": (
                "try:\n    import notinstalled\nexcept ImportError:\n"
                "    notinstalled = None\n"
            ),
            "pkg/provided.py": (
                "import js\nimport json\nimport annotationlib\n"
                "from pyodide.ffi import to_js\n"
            ),
        },
    )
    entry(project, "import pkg.plugin\nimport pkg.guarded\nimport pkg.provided\n")

    assert main(["deploy"]) == 0

    assert staged_modules(project) == [
        "pkg/__init__.py",
        "pkg/guarded.py",
        "pkg/provided.py",
    ]
    shake = listing(project)
    assert shake["pkg/plugin.py"] == (
        "dropped",
        "imports notinstalled, which isn't installed",
    )
    assert shake["pkg/helper.py"] == ("dropped", "not imported")


LAZY: dict[str, str | bytes] = {
    "lazy/__init__.py": (
        "from importlib import import_module\n"
        "_dynamic_imports = {'A': (__spec__.parent, '.a')}\n"
        "def __getattr__(name):\n"
        "    package, module = _dynamic_imports[name]\n"
        "    if module == '__module__':\n"
        "        return import_module(f'.{name}', package=package)\n"
        "    return getattr(import_module(module, package=package), name)\n"
    ),
    "lazy/a.py": "A = 1\n",
    "lazy/b.py": "B = 2\n",
}


def test_a_lazy_attribute_keeps_only_the_module_it_loads(
    tools: Tools, project: Path
) -> None:
    install(tools, LAZY)
    entry(project, "from lazy import A\n")

    assert main(["deploy"]) == 0

    assert staged_modules(project) == ["lazy/__init__.py", "lazy/a.py"]
    assert listing(project)["lazy/a.py"] == (
        "kept",
        "imported by entry by a lazy import",
    )


def test_importing_a_lazy_package_keeps_every_module_it_can_load(
    tools: Tools, project: Path
) -> None:
    install(tools, LAZY)
    entry(project, "import lazy\n")

    assert main(["deploy"]) == 0

    assert staged_modules(project) == ["lazy/__init__.py", "lazy/a.py", "lazy/b.py"]
