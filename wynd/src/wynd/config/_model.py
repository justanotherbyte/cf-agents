from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from ._utils import to_table

if TYPE_CHECKING:
    from .bindings import JSON, Binding
    from .exports import Export
    from .triggers import Trigger

__all__ = (
    "Assets",
    "Config",
    "Container",
    "Limits",
    "Observability",
    "Placement",
    "Rule",
    "TailConsumer",
    "Worker",
)

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True, kw_only=True)
class Container:
    """A container image that one Durable Object export runs.

    Takes exactly one of `dockerfile` and `image`, a prebuilt image reference.
    `build_context` and `build_vars` need `dockerfile`. Paths are relative to
    cloudflare.config.py's directory.

    Raises ValueError when the image fields don't fit those rules.
    """

    name: str
    dockerfile: str | None = None
    image: str | None = None
    build_context: str | None = None
    build_vars: Mapping[str, str] | None = None
    instance_type: str | None = None
    max_instances: int | None = None

    def __post_init__(self) -> None:
        if (self.dockerfile is None) == (self.image is None):
            raise ValueError(
                f"Container {self.name!r} needs exactly one of dockerfile= and image="
            )
        if self.image is not None and (self.build_context or self.build_vars):
            raise ValueError(
                f"Container {self.name!r} sets build_context or build_vars, "
                "which need dockerfile="
            )

    def _entry(self, class_name: str) -> dict[str, Any]:
        """The containers[] entry for the export named `class_name`."""
        keys = {
            "dockerfile": "image",
            "build_context": "image_build_context",
            "build_vars": "image_vars",
        }
        return {"class_name": class_name, **to_table(self, keys)}


@dataclass(frozen=True, kw_only=True)
class Assets:
    """Static assets. Each field is the Wrangler `assets` key of the same name."""

    directory: str
    html_handling: (
        Literal[
            "auto-trailing-slash", "force-trailing-slash", "drop-trailing-slash", "none"
        ]
        | None
    ) = None
    not_found_handling: (
        Literal["single-page-application", "404-page", "none"] | None
    ) = None
    run_worker_first: bool | Sequence[str] | None = None


@dataclass(frozen=True, kw_only=True)
class Observability:
    """Each field is the Wrangler `observability` key of the same name."""

    enabled: bool | None = None
    head_sampling_rate: float | None = None
    redact_query_string: bool | None = None
    logs: Mapping[str, JSON] | None = None
    traces: Mapping[str, JSON] | None = None


@dataclass(frozen=True, kw_only=True)
class Placement:
    mode: Literal["off", "smart"]
    hint: str | None = None


@dataclass(frozen=True, kw_only=True)
class Limits:
    cpu_ms: int | None = None
    subrequests: int | None = None


@dataclass(frozen=True, kw_only=True)
class Rule:
    """Module type for files under base_dir that match `globs`. Each field is
    the Wrangler `rules[]` key of the same name."""

    type: Literal[
        "ESModule",
        "CommonJS",
        "CompiledWasm",
        "Text",
        "Data",
        "PythonModule",
        "PythonRequirement",
    ]
    globs: Sequence[str]
    fallthrough: bool | None = None


@dataclass(frozen=True, kw_only=True)
class TailConsumer:
    worker: str


@dataclass(frozen=True, kw_only=True)
class Worker:
    """The one Worker a config describes, for one mode.

    `entrypoint` is Wrangler's `main`, and None for an assets-only Worker.
    `compatibility_flags` holds only extra flags, because every Python Worker
    gets `python_workers`. `env` maps each binding name to its binding, and None
    or False leaves the name out. `exports` maps each class name in the
    entrypoint to its export. `domains` are custom domains.

    Raises ValueError when `compatibility_date` isn't a YYYY-MM-DD date.
    """

    name: str
    compatibility_date: str
    entrypoint: str | None = None
    base_dir: str | None = None
    compatibility_flags: Sequence[str] = ()
    env: Mapping[str, Binding | None | Literal[False]] = field(default_factory=dict)
    triggers: Sequence[Trigger] = ()
    exports: Mapping[str, Export] = field(default_factory=dict)
    domains: Sequence[str] = ()
    assets: Assets | None = None
    observability: Observability | None = None
    placement: Placement | None = None
    limits: Limits | None = None
    logpush: bool | None = None
    workers_dev: bool | None = None
    preview_urls: bool | None = None
    tail_consumers: Sequence[TailConsumer] = ()
    rules: Sequence[Rule] = ()

    def __post_init__(self) -> None:
        if not _is_date(self.compatibility_date):
            raise ValueError(
                f"compatibility_date {self.compatibility_date!r} isn't a "
                "YYYY-MM-DD date"
            )


@dataclass(frozen=True, kw_only=True)
class Config:
    """What `config` names in cloudflare.config.py, or what `config(mode)`
    returns. Containers sit on the Durable Object exports that run them."""

    worker: Worker
    account_id: str | None = None
    compliance_region: Literal["public", "fedramp-high"] | None = None


def _is_date(text: str) -> bool:
    # fromisoformat alone also takes 20260908 and week dates.
    if not DATE.fullmatch(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True
