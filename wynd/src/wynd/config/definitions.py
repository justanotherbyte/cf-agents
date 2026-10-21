"""The definitions in cloudflare.config.py: the `Config` its module-level
`config` names, one `Worker`, and its `Container`s.

Fields follow @cloudflare/config 0.27.0 in snake_case. The dictionary options
use Wrangler's own keys, so Wynd passes them through unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from .bindings import Spec
    from .context import Input
    from .exports import Export
    from .triggers import Trigger

__all__ = (
    "Access",
    "Assets",
    "AuthorizedKey",
    "Cache",
    "Config",
    "Constraints",
    "Container",
    "ContainerObservability",
    "DockerfileImage",
    "Domain",
    "InstanceType",
    "Limits",
    "Observability",
    "Placement",
    "ReferenceImage",
    "Rollout",
    "Ssh",
    "TailConsumer",
    "Unsafe",
    "Worker",
)


class Assets(TypedDict, total=False):
    # Wynd has no wrangler.config.ts, so the directory goes here.
    directory: str
    html_handling: Literal[
        "auto-trailing-slash", "drop-trailing-slash", "force-trailing-slash", "none"
    ]
    not_found_handling: Literal["single-page-application", "404-page", "none"]
    base_path: str
    run_worker_first: bool | list[str]


class Domain(TypedDict):
    hostname: str
    production: bool
    previews: bool


class TailConsumer(TypedDict):
    worker: str
    streaming: NotRequired[bool]


class Cache(TypedDict):
    enabled: bool
    cross_version_cache: NotRequired[bool]


class Placement(TypedDict, total=False):
    mode: Literal["off", "smart", "targeted"]
    hint: str
    region: str
    host: str
    hostname: str


class Limits(TypedDict, total=False):
    cpu_ms: int
    subrequests: int


class _Logs(TypedDict, total=False):
    enabled: bool
    head_sampling_rate: float
    invocation_logs: bool
    persist: bool
    destinations: list[str]


class _Traces(TypedDict, total=False):
    enabled: bool
    head_sampling_rate: float
    persist: bool
    destinations: list[str]


class _Enabled(TypedDict, total=False):
    enabled: bool


class Observability(TypedDict, total=False):
    enabled: bool
    head_sampling_rate: float
    redact_query_string: bool
    issues: _Enabled
    logs: _Logs
    traces: _Traces


class _AccessDev(TypedDict):
    aud: str
    identity: NotRequired[dict[str, Any]]


class Access(TypedDict, total=False):
    dev: _AccessDev


class _Capnp(TypedDict, total=False):
    base_path: str
    source_schemas: list[str]
    compiled_schema: str


class Unsafe(TypedDict, total=False):
    metadata: dict[str, Any]
    capnp: _Capnp


@dataclass(frozen=True, kw_only=True, eq=False)
class Worker:
    """One Worker. Another config's binding can name it by passing it as
    `worker`, and Wynd then checks the export it binds."""

    name: str
    compatibility_date: str
    compatibility_flags: Sequence[str] | None = None
    # A path from the config's directory. Optional for an assets-only Worker.
    entrypoint: str | None = None
    # Wynd's addition, for a module root other than the entrypoint's directory.
    base_dir: str | None = None
    # An Env class whose attributes are bindings, or a mapping of the same.
    # A binding set to None or False is left out.
    env: type | Mapping[str, Spec[Any] | bool | None] | None = None
    exports: Mapping[str, Export] | None = None
    triggers: Sequence[Trigger] | None = None
    # A bare hostname serves production only.
    domains: Sequence[str | Domain] | None = None
    assets: Assets | None = None
    tail_consumers: Sequence[TailConsumer] | None = None
    cache: Cache | None = None
    placement: Placement | None = None
    limits: Limits | None = None
    logpush: bool | None = None
    observability: Observability | None = None
    access: Access | None = None
    workers_dev: bool | None = None
    preview_urls: bool | None = None
    unsafe: Unsafe | None = None
    first_party_worker: bool | None = None


class DockerfileImage(TypedDict):
    dockerfile: str
    build_context: NotRequired[str]
    build_vars: NotRequired[dict[str, str]]


class ReferenceImage(TypedDict):
    reference: str


class InstanceType(TypedDict, total=False):
    vcpu: float
    memory_mib: int
    disk_mb: int


type _Region = Literal["ENAM", "WNAM", "EEUR", "WEUR", "APAC", "SAM", "ME", "OC", "AFR"]


class Constraints(TypedDict, total=False):
    regions: list[_Region]
    jurisdiction: Literal["eu", "fedramp", "us"]


class Rollout(TypedDict, total=False):
    kind: Literal["full-auto", "none", "full-manual"]
    step_percentage: int | list[int]
    active_grace_period: int


class ContainerObservability(TypedDict, total=False):
    enabled: bool
    logs: _Enabled
    # Not for a Durable Object Container. Set one of the two.
    target_instance_percentage: float
    target_instance_count: int


class Ssh(TypedDict):
    enabled: bool
    port: NotRequired[int]


class AuthorizedKey(TypedDict):
    name: str
    public_key: str


@dataclass(frozen=True, kw_only=True, eq=False)
class Container:
    """A Container application, listed in `Config.containers` and linked to at
    most one Durable Object export.

    A standard Container takes `image`. One with
    `scheduling_policy="durable-object"` takes `images` and none of the
    instance, constraint and rollout fields.

    Raises ValueError for a field its kind doesn't take.
    """

    name: str
    image: DockerfileImage | ReferenceImage | None = None
    images: Mapping[str, DockerfileImage | ReferenceImage] | None = None
    max_instances: int | None = None
    instance_type: (
        Literal["basic", "lite", "standard-1", "standard-2", "standard-3", "standard-4"]
        | InstanceType
        | None
    ) = None
    scheduling_policy: Literal["default", "regional", "durable-object"] | None = None
    constraints: Constraints | None = None
    rollout: Rollout | None = None
    observability: ContainerObservability | None = None
    ssh: Ssh | None = None
    authorized_keys: Sequence[AuthorizedKey] | None = None
    unsafe: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.is_durable_object():
            if self.image is None or self.images is not None:
                raise ValueError(f"Container {self.name!r} takes image, not images")
            return

        standard = {
            "image": self.image,
            "max_instances": self.max_instances,
            "instance_type": self.instance_type,
            "constraints": self.constraints,
            "rollout": self.rollout,
        }
        given = [field for field, value in standard.items() if value is not None]
        if given:
            raise ValueError(
                f"Container {self.name!r} is a durable-object Container, which "
                f"doesn't take {', '.join(given)}"
            )

    def is_durable_object(self) -> bool:
        return self.scheduling_policy == "durable-object"


@dataclass(frozen=True, kw_only=True, eq=False)
class Config:
    """What cloudflare.config.py's `config` holds, directly or from a factory
    of the context."""

    worker: Input[Worker] | None = None
    containers: Sequence[Input[Container]] = ()
    # CLOUDFLARE_ACCOUNT_ID takes priority, as in Wrangler.
    account_id: str | None = None
    compliance_region: Literal["public", "fedramp-high"] | None = None
