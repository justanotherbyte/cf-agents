import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from importlib.metadata import version
from pathlib import Path

from .project import Project, WyndError
from .targets import Target

__all__ = ["select_sdk", "sync"]

SDKS = ("crosswind-workers", "workers-runtime-sdk")
MIN_UV = (0, 12, 3)


def sync(project: Project, target: Target) -> None:
    """Install the project's packages for `target` into python_modules/."""
    token_path = project.wynd_dir / "synced"
    if project.python_modules.is_dir() and _text(token_path) == _token(project, target):
        return
    sdk = select_sdk(project.dependencies)
    _check_uv(project)
    _compile(project, target, sdk)
    # Install beside python_modules/ and swap it in only once uv succeeds, so a
    # failed install never leaves a half-built tree for Wrangler.
    staging = project.wynd_dir / "site-packages"
    shutil.rmtree(staging, ignore_errors=True)
    _install(project, target, staging)
    # --target also writes console scripts, whose shebangs name the host's
    # Pyodide interpreter, and uv's own lock file. Neither belongs in a Worker.
    shutil.rmtree(staging / "bin", ignore_errors=True)
    (staging / ".lock").unlink(missing_ok=True)
    shutil.rmtree(project.python_modules, ignore_errors=True)
    shutil.move(staging, project.python_modules)
    token_path.write_text(_token(project, target))


def select_sdk(dependencies: tuple[str, ...]) -> str:
    """Return the one SDK distribution the project declares directly."""
    names = {_name(requirement) for requirement in dependencies}
    declared = [sdk for sdk in SDKS if sdk in names]
    if not declared:
        raise WyndError(
            "pyproject.toml declares neither crosswind-workers nor "
            "workers-runtime-sdk. Add one to [project] dependencies."
        )
    if len(declared) > 1:
        raise WyndError(
            "pyproject.toml declares both crosswind-workers and "
            "workers-runtime-sdk. Keep one."
        )
    return declared[0]


def _name(requirement: str) -> str:
    # PEP 508 name, normalized as in PEP 503.
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else ""


def _token(project: Project, target: Target) -> str:
    inputs = [
        # Each config has its own python_modules/ beside it.
        str(project.config_path),
        _text(project.root / "pyproject.toml"),
        _text(project.root / "pylock.toml"),
        target.version,
        project.compatibility_date,
        project.compatibility_flags,
        version("wynd"),
    ]
    return hashlib.sha256(json.dumps(inputs).encode()).hexdigest()


def _text(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


def _check_uv(project: Project) -> None:
    output = _uv(project, "--version")
    match = re.match(r"uv (\d+)\.(\d+)\.(\d+)", output)
    if match is None or tuple(map(int, match.groups())) < MIN_UV:
        raise WyndError(
            f"Wynd needs uv {'.'.join(map(str, MIN_UV))} or later, "
            f"found {output.strip()!r}. "
            "Update with `uv self update`."
        )


def _compile(project: Project, target: Target, sdk: str) -> None:
    # A library may depend on the other SDK. Both ship the `workers` package.
    excludes = project.wynd_dir / "excludes.txt"
    excludes.parent.mkdir(exist_ok=True)
    excludes.write_text("".join(f"{name}\n" for name in SDKS if name != sdk))
    args = ["pip", "compile", "pyproject.toml", "--python", target.interpreter]
    args += ["--excludes", str(excludes.relative_to(project.root))]
    args += ["--extra-index-url", target.index, "--index-strategy", "unsafe-best-match"]
    args += ["--no-header", "-o", "pylock.toml"]
    if not project.allow_build:
        args.append("--no-build")
    _uv(project, *args)


def _install(project: Project, target: Target, staging: Path) -> None:
    lock = tomllib.loads(_text(project.root / "pylock.toml"))
    # --target, not a venv: uv ignores a Pyodide venv's interpreter, and with
    # --python <venv> it installs into "/", the prefix Pyodide reports.
    args = ["pip", "install", "--target", str(staging), "--python", target.interpreter]
    if not project.allow_build:
        args += ["--only-binary", ":all:"]
    for package in lock.get("packages", []):
        paths = [
            source["path"]
            for key in ("directory", "sdist", "archive")
            if isinstance(source := package.get(key), dict) and "path" in source
        ]
        if not paths:
            continue
        # A local source has no wheel to fetch unless it is one, and uv caches
        # its build by path, so edits would go unseen without a refresh.
        if not project.allow_build and not all(p.endswith(".whl") for p in paths):
            args += ["--no-binary", package["name"]]
        args += ["--refresh-package", package["name"]]
    args += ["-r", "pylock.toml", "--preview-features", "pylock"]
    _uv(project, *args)


def _uv(project: Project, *args: str) -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise WyndError(
            f"uv not found on PATH. Install uv {'.'.join(map(str, MIN_UV))} or later."
        )
    result = subprocess.run(
        [uv, *args],
        check=False,
        cwd=project.root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WyndError(
            f"uv {' '.join(args[:2])} failed:\n{result.stderr}{result.stdout}"
        )
    return result.stdout
