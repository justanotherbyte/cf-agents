import copy
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .project import Project, WyndError

# Fields Wrangler resolves from the config directory, by table. A list value
# holds one table per item.
PATH_FIELDS = {
    "assets": ("directory",),
    "build": ("watch_dir",),
    "containers": ("image_build_context",),
    "d1_databases": ("migrations_dir", "migrations_pattern"),
    "site": ("bucket", "entry-point"),
}

# Keys whose relative values must stay relative or aren't config-relative paths.
# main and base_dir resolve into the staged source. build.cwd and build.command
# resolve from the process working directory.
LEFT_ALONE = {"$schema", "main", "base_dir", "build", "rules", "vars", "define"}


def stage(project: Project) -> Path:
    """Copy the Worker into .wynd/build/ and return its staged config's path."""
    config_dir = project.config_path.parent
    config = copy.deepcopy(project.config)
    _rewrite(config, config_dir)
    # shortcut: only "./" and "../" values count as relative paths, so a bare
    # "public" under an unknown key slips through. Upgrade if Wrangler adds one.
    unknown = list(_relative_paths(config, ""))
    if unknown:
        raise WyndError(
            f"{project.config_path.name} has a relative path under "
            f"{', '.join(unknown)}, which Wynd can't point back at the project "
            "from .wynd/build/. Use an absolute path."
        )
    source = _source_dir(project)
    build = project.root / ".wynd" / "build"
    shutil.rmtree(build, ignore_errors=True)
    shutil.copytree(config_dir / source, build / source)
    shutil.copytree(project.python_modules, build / "python_modules")
    staged = build / "wrangler.json"
    # default=str writes TOML dates the way Wrangler reads them.
    staged.write_text(json.dumps(config, indent=2, default=str))
    return staged


def _source_dir(project: Project) -> Path:
    """The module root, relative to the config directory."""
    if project.main is None:
        raise WyndError(f"{project.config_path.name} sets no main")
    config_dir = project.config_path.parent.resolve()
    source = config_dir / (project.base_dir or Path(project.main).parent)
    source = source.resolve()
    # A source holding .wynd/ would be copied into itself.
    if (
        source == config_dir
        or not source.is_relative_to(config_dir)
        or project.root.resolve().is_relative_to(source)
    ):
        raise WyndError(
            f"main and base_dir must point into a subdirectory beside "
            f"{project.config_path.name}, such as src/, so Wynd can stage the "
            "source without the rest of the project."
        )
    return source.relative_to(config_dir)


def _rewrite(section: dict[str, Any], base: Path) -> None:
    """Point a section's known path keys at the project, in place."""
    for key, fields in PATH_FIELDS.items():
        for table in _tables(section.get(key)):
            for field in fields:
                if field in table:
                    table[field] = _absolute(table[field], base)
    for container in _tables(section.get("containers")):
        image = container.get("image")
        # Wrangler builds an image that names a file and pulls anything else. A
        # missing "./Dockerfile" is rewritten too, so Wrangler names the file.
        if isinstance(image, str) and (
            image.startswith(".") or (base / image).is_file()
        ):
            container["image"] = str(base / image)
    if "tsconfig" in section:
        section["tsconfig"] = _absolute(section["tsconfig"], base)
    aliases = section.get("alias")
    if isinstance(aliases, dict):
        for name, target in aliases.items():
            # A target that isn't a relative path names a package.
            if isinstance(target, str) and target.startswith("."):
                aliases[name] = str(base / target)
    envs = section.get("env")
    for env in _tables(list(envs.values()) if isinstance(envs, dict) else None):
        _rewrite(env, base)
    for previews in _tables(section.get("previews")):
        _rewrite(previews, base)


def _tables(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else [value]
    return [item for item in items if isinstance(item, dict)]


def _absolute(value: Any, base: Path) -> Any:
    if isinstance(value, list):
        return [_absolute(item, base) for item in value]
    return str(base / value) if isinstance(value, str) else value


def _relative_paths(node: Any, where: str) -> Iterator[str]:
    if isinstance(node, str) and node.startswith(("./", "../")):
        yield where
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _relative_paths(item, f"{where}[{index}]")
    elif isinstance(node, dict):
        for key, value in node.items():
            if key not in LEFT_ALONE:
                yield from _relative_paths(value, f"{where}.{key}" if where else key)
