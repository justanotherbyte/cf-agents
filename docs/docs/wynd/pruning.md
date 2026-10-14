---
title: "Prune a Python Worker's upload with Wynd"
description: "How Wynd decides which vendored files a Python Worker uploads, how to read .wynd/shake.txt, and how to keep modules imported by computed names."
---

Your Worker uploads every file in `python_modules/`, though it imports only
part of each package. Before every upload, Wynd works out which files your
code can import and uploads only those. This page explains what it keeps, how
to check its decisions, and how to keep a module it can't see.

- `wynd deploy`, `wynd versions upload` and `wynd preview` prune. `wynd dev`
  runs the full `python_modules/`, so a new import works without a rebuild.
- Wynd never prunes your own source. It copies the source directory whole.
- `.wynd/shake.txt` records each decision, and `.wynd/build/` holds the tree
  that Wrangler uploaded.
- `[tool.wynd] keep` adds modules Wynd can't find by reading imports.

> **Warning**
>
> The pruner is in early alpha. Its rules will change often between releases,
> and an upgrade can change which files your Worker uploads. Run
> `uvx wynd deploy --dry-run` after you upgrade Wynd, and test the deployed
> Worker before you rely on it.
>
> It works best today for Workers whose dependencies are mostly pure Python
> and ship tests, docs, type stubs or submodules you don't import. A minimal
> crosswind-workers Worker uploads 27% fewer bytes. It saves little when
> compiled extensions make up most of the bytes, because it keeps each
> extension whole. A FastAPI Worker, where `pydantic_core` is most of the
> upload, saves about 2%.
>
> These improvements are planned:
>
> - Dropping unused functions and classes from the modules it keeps. Today it
>   removes only whole files.
> - Minifying the Python files it keeps.
> - Tracing which imports run, to find modules imported by names built at
>   runtime that it now drops unless you list them in `keep`.
> - Running the pruned tree under `wynd dev`, so you can test it before you
>   deploy.

## See what pruning removes

```sh
uvx wynd deploy --dry-run
```

```text
wynd: python_modules is 127,013 bytes, 92,668 after pruning (34,345 fewer). Each file's decision is in .wynd/shake.txt.
```

The totals count file sizes on disk, without the few bytes Wrangler adds per
module.

`.wynd/shake.txt` has one line per file, as `decision`, `path` and `reason`
separated by tabs:

```text
kept	crosswind_workers_patches.pth	startup hook
kept	_crosswind_patches/__init__.py	imported by crosswind_workers_patches.pth
kept	workers/__init__.py	imported by entry
dropped	workers/asgi.py	not imported
dropped	workers/py.typed	typing marker
unresolved	typing	imported by workers
```

To find out why Wynd dropped a file, search for its path:

```sh
grep 'workers/asgi.py' .wynd/shake.txt
```

An `unresolved` line names an import with no file in `python_modules/`, such
as a standard library module. Wynd lists it and carries on.

After an upload, `.wynd/build/` holds exactly what Wrangler sent. Wynd
deletes it and rebuilds it at the start of the next upload.

## What Wynd keeps

Wynd starts from these modules, called roots:

- Your entry module, from `main` in the Wrangler config.
- Every module in `[tool.wynd] keep`.
- Every module that a `.pth` file in `python_modules/` imports.

It then follows each `import` and `from ... import` statement, including
relative ones, and calls to `importlib.import_module` or `__import__` with a
literal name. For a name built at runtime that starts with literal text, such
as `import_module(f"anyio._backends._{name}")`, it keeps every module whose
name starts with that text.

From the modules it reaches, Wynd keeps:

| Kept                                                        | Why                                                  |
| ----------------------------------------------------------- | ---------------------------------------------------- |
| Each reached `.py` file and its parent packages             | Your code can import it                              |
| Every non-`.py` file under a top-level package with a kept module, including `.so` extensions | Packages load templates and data by computed paths, such as Jinja2 templates or `tzdata` zones |
| `.dist-info` of each distribution with a kept file          | `importlib.metadata.version()` reads it              |
| `.pth` files                                                | Python runs them at startup                          |

It drops:

| Dropped                                         | Reason in `shake.txt`  |
| ----------------------------------------------- | ---------------------- |
| `.py` files nothing imports                     | `not imported`         |
| Unimported files under a `tests`, `test`, `docs` or `doc` directory | `tests`, `docs` |
| Files directly in `python_modules/`, except `.pth` files | `outside any package` |
| `__pycache__`                                   | `bytecode cache`       |
| `.pyi` stubs and `py.typed` markers             | `type stub`, `typing marker` |
| Data files of a package with no kept module     | `no kept module in <package>` |
| A distribution's `.dist-info` when none of its files are kept | `metadata of <dist>, which keeps no file` |

Wynd follows imports inside `try` blocks and inside functions, because they
may run. It skips imports under `if TYPE_CHECKING:`, because they never run.

## Keep modules Wynd can't see

A plugin system that builds module names from config or user input hides
those imports from Wynd. The upload then raises `ModuleNotFoundError` at
runtime for a module that works under `wynd dev`.

List those modules under `[tool.wynd]` in `pyproject.toml`:

```toml title="pyproject.toml"
[tool.wynd]
keep = ["myplugins.csv_export", "babel.locale_data"]
```

Wynd treats each one as a root, so it also keeps everything that module
imports. Run `uvx wynd deploy --dry-run` and check that `shake.txt` lists
them as `kept` with the reason `keep list`.

## Native extensions

A compiled extension in a Worker must be WebAssembly built for Pyodide. If a
kept `.so` file isn't WebAssembly, Wynd stops before uploading and names the
format it found, ELF, Mach-O or PE:

```text
wynd: error: mypkg/_speedups.cpython-313-x86_64-linux-gnu.so is in ELF format, not WebAssembly built for Pyodide, so it can't load in a Worker.
```

This happens when a package came from a source build on your machine.
Remove `allow-build` from `[tool.wynd]` so uv installs only Pyodide wheels, or
drop the package. See [Commands and configuration](commands.md#toolwynd).

## Troubleshooting

### Why does the deployed Worker raise `ModuleNotFoundError` when `wynd dev` works?

`wynd dev` runs the full `python_modules/`, and the upload commands prune it.
Something imports the missing module by a computed name. Find it in `shake.txt`, where
it shows `dropped` with `not imported`, and add it to `[tool.wynd] keep`.

### Why did pruning save so little?

Pruning removes whole files only. It doesn't remove unused functions from a
kept module, and it keeps compiled extensions whole. In a FastAPI Worker,
`pydantic_core`'s extension is most of the bytes, and Wynd saves about 2%.

### `wynd: error: <name>.pth line 2 is not a plain import`

Wynd reads `.pth` files to find startup imports, without running them. A
line that does anything other than `import a, b` stops the build. Find the
package that installed the file and check whether the Worker needs it.
