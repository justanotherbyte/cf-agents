"""Builders for the bindings of an Env class, one per kind in @cloudflare/config
0.27.0's `bindings`.

Each builder returns a `Spec`, a descriptor that type checkers read as the
binding's runtime class on an Env instance, so `env.DB` is a `D1Database` while
`Env.DB` stays the spec Wynd converts. Options take the names of the
TypeScript builders in snake_case, and `dev: {remote}` is the `remote` keyword.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Self, TypedDict, overload

from ._utils import present

if TYPE_CHECKING:
    from workers.bindings import (
        Binding,
        D1Database,
        DurableObjectNamespace,  # noqa: F401 (named in DurableObject's string base)
        Fetcher,
        KVNamespace,
        Queue,
        R2Bucket,
        Workflow,
    )
    from workers.rpc import JsDict

    from .context import Input
    from .definitions import Worker

__all__ = (
    "DurableObject",
    "Outbound",
    "RateLimit",
    "S3Credentials",
    "Spec",
    "agent_memory",
    "ai",
    "ai_search",
    "ai_search_namespace",
    "analytics",
    "analytics_engine_dataset",
    "artifacts",
    "assets",
    "browser",
    "d1",
    "dispatch_namespace",
    "flagship",
    "hyperdrive",
    "images",
    "json",
    "k2",
    "kv",
    "logfwdr",
    "media",
    "mtls_certificate",
    "pipeline",
    "queue",
    "r2",
    "rate_limit",
    "secret",
    "secrets_store_secret",
    "send_email",
    "stream",
    "text",
    "unsafe",
    "vectorize",
    "version_metadata",
    "vpc_network",
    "vpc_service",
    "worker",
    "worker_loader",
    "workflow",
)


class Spec[R]:
    """A binding declared on an Env class, whose runtime type is `R`.

    `section` is the Wrangler config key the binding goes under, dotted when
    nested, and `options` are its Wrangler keys. `target` is the Worker a
    service, Durable Object or Workflow binding points at, or None for this one.
    """

    def __init__(
        self,
        section: str,
        options: dict[str, Any] | None = None,
        *,
        target: str | Input[Worker] | None = None,
    ) -> None:
        self.section = section
        self.options = present(**(options or {}))
        self.target = target

    def __repr__(self) -> str:
        return f"<{type(self).__name__} section={self.section!r}>"

    @overload
    def __get__(self, instance: None, owner: type) -> Self: ...
    @overload
    def __get__(self, instance: object, owner: type) -> R: ...
    def __get__(self, instance: object | None, owner: type) -> Self | R:
        if instance is None:
            return self
        # Worker code reads bindings from the runtime env, never an Env instance.
        raise AttributeError("Env is a declaration. Read bindings from self.env.")


class DurableObject[T](Spec["DurableObjectNamespace[T]"]):
    """A Durable Object namespace. Subscript it with the class name, as in
    `DurableObject["Counter"](export_name="Counter")`, so `get()` returns a
    `Counter`. Without `worker` it binds a class of this Worker."""

    def __init__(
        self, *, export_name: str, worker: str | Input[Worker] | None = None
    ) -> None:
        super().__init__(
            "durable_objects.bindings", {"class_name": export_name}, target=worker
        )


class Outbound(TypedDict, total=False):
    worker: str
    parameters: list[str]


class RateLimit(TypedDict):
    limit: int
    period: Literal[10, 60]


class S3Credentials(TypedDict):
    access_key_id: str
    secret_access_key: str


def agent_memory(*, namespace: str, remote: bool | None = None) -> Spec[Binding]:
    return Spec("agent_memory", {"namespace": namespace, "remote": remote})


def ai(*, remote: bool | None = None) -> Spec[Binding]:
    return Spec("ai", {"remote": remote})


def ai_search(*, name: str, remote: bool | None = None) -> Spec[Binding]:
    return Spec("ai_search", {"instance_name": name, "remote": remote})


def ai_search_namespace(*, namespace: str, remote: bool | None = None) -> Spec[Binding]:
    return Spec("ai_search_namespaces", {"namespace": namespace, "remote": remote})


def analytics(*, remote: bool | None = None) -> Spec[Binding]:
    return Spec("analytics", {"remote": remote})


def analytics_engine_dataset(*, name: str | None = None) -> Spec[Binding]:
    return Spec("analytics_engine_datasets", {"dataset": name})


def artifacts(*, namespace: str, remote: bool | None = None) -> Spec[Binding]:
    return Spec("artifacts", {"namespace": namespace, "remote": remote})


def assets() -> Spec[Fetcher]:
    return Spec("assets")


def browser(*, remote: bool | None = None) -> Spec[Binding]:
    return Spec("browser", {"remote": remote})


def d1(
    *, id: str | None = None, name: str | None = None, remote: bool | None = None
) -> Spec[D1Database]:
    return Spec(
        "d1_databases", {"database_id": id, "database_name": name, "remote": remote}
    )


def dispatch_namespace(
    *,
    namespace: str | None = None,
    outbound: Outbound | None = None,
    remote: bool | None = None,
) -> Spec[Binding]:
    service = None
    if outbound is not None:
        service = present(
            service=outbound.get("worker"), parameters=outbound.get("parameters")
        )
    return Spec(
        "dispatch_namespaces",
        {"namespace": namespace, "outbound": service, "remote": remote},
    )


def flagship(*, id: str | None = None, remote: bool | None = None) -> Spec[Binding]:
    return Spec("flagship", {"app_id": id, "remote": remote})


def hyperdrive(*, id: str, local_connection_string: str | None = None) -> Spec[Binding]:
    return Spec(
        "hyperdrive", {"id": id, "localConnectionString": local_connection_string}
    )


def images(*, remote: bool | None = None) -> Spec[Binding]:
    return Spec("images", {"remote": remote})


def json[T](value: T) -> Spec[T]:
    """A JSON var. Python receives it as its value, typed as written."""
    return Spec("vars", {"value": value})


def k2(*, stream: str, remote: bool | None = None) -> Spec[Binding]:
    return Spec("k2", {"stream": stream, "remote": remote})


def kv(*, id: str | None = None, remote: bool | None = None) -> Spec[KVNamespace]:
    return Spec("kv_namespaces", {"id": id, "remote": remote})


def logfwdr(*, destination: str) -> Spec[Binding]:
    return Spec("logfwdr.bindings", {"destination": destination})


def media(*, remote: bool | None = None) -> Spec[Binding]:
    return Spec("media", {"remote": remote})


def mtls_certificate(*, id: str, remote: bool | None = None) -> Spec[Fetcher]:
    return Spec("mtls_certificates", {"certificate_id": id, "remote": remote})


def pipeline(*, name: str, remote: bool | None = None) -> Spec[Binding]:
    return Spec("pipelines", {"stream": name, "remote": remote})


def queue[T](
    body: type[T] | None = None,
    *,
    name: str | None = None,
    delivery_delay: int | None = None,
    remote: bool | None = None,
) -> Spec[Queue[T]]:
    """A queue producer. `body` is the message type `send` accepts."""
    return Spec(
        "queues.producers",
        {"queue": name, "delivery_delay": delivery_delay, "remote": remote},
    )


def r2(
    *,
    name: str | None = None,
    jurisdiction: str | None = None,
    remote: bool | None = None,
    experimental_s3_credentials: S3Credentials | None = None,
) -> Spec[R2Bucket]:
    local_dev = None
    if experimental_s3_credentials is not None:
        credentials = {
            "accessKeyId": experimental_s3_credentials["access_key_id"],
            "secretAccessKey": experimental_s3_credentials["secret_access_key"],
        }
        local_dev = {"experimental_s3_credentials": credentials}
    return Spec(
        "r2_buckets",
        {
            "bucket_name": name,
            "jurisdiction": jurisdiction,
            "remote": remote,
            "local_dev": local_dev,
        },
    )


def rate_limit(*, namespace: str, simple: RateLimit) -> Spec[Binding]:
    return Spec("ratelimits", {"namespace_id": namespace, "simple": simple})


def secret() -> Spec[str]:
    """A secret Wrangler requires before it deploys or serves the Worker."""
    return Spec("secrets.required")


def secrets_store_secret(*, store_id: str, secret_name: str) -> Spec[Binding]:
    return Spec(
        "secrets_store_secrets", {"store_id": store_id, "secret_name": secret_name}
    )


def send_email(
    *,
    destination_address: str | None = None,
    allowed_destination_addresses: list[str] | None = None,
    allowed_sender_addresses: list[str] | None = None,
    remote: bool | None = None,
) -> Spec[Binding]:
    """Raises ValueError for both destination options."""
    if destination_address is not None and allowed_destination_addresses is not None:
        raise ValueError(
            "send_email takes destination_address or allowed_destination_addresses, "
            "not both"
        )
    return Spec(
        "send_email",
        {
            "destination_address": destination_address,
            "allowed_destination_addresses": allowed_destination_addresses,
            "allowed_sender_addresses": allowed_sender_addresses,
            "remote": remote,
        },
    )


def stream(*, remote: bool | None = None) -> Spec[Binding]:
    return Spec("stream", {"remote": remote})


def text[T: str](value: T) -> Spec[T]:
    return Spec("vars", {"value": value})


def unsafe(type: str, /, **options: Any) -> Spec[Any]:
    """A binding Wrangler passes through as `unsafe.bindings`, such as
    `unsafe("ratelimit", namespace_id="1")`."""
    return Spec("unsafe.bindings", {"type": type, **options})


def vectorize(*, name: str, remote: bool | None = None) -> Spec[Binding]:
    return Spec("vectorize", {"index_name": name, "remote": remote})


def version_metadata() -> Spec[JsDict]:
    # The env wrapper can't tell this plain object from a JSON var.
    return Spec("version_metadata")


def vpc_network(
    *,
    tunnel_id: str | None = None,
    network_id: str | None = None,
    remote: bool | None = None,
) -> Spec[Fetcher]:
    """Raises ValueError unless exactly one of the IDs is given."""
    if (tunnel_id is None) == (network_id is None):
        raise ValueError("vpc_network takes exactly one of tunnel_id or network_id")
    return Spec(
        "vpc_networks",
        {"tunnel_id": tunnel_id, "network_id": network_id, "remote": remote},
    )


def vpc_service(*, id: str, remote: bool | None = None) -> Spec[Fetcher]:
    return Spec("vpc_services", {"service_id": id, "remote": remote})


def worker(
    *,
    worker: str | Input[Worker],
    export_name: str | None = None,
    props: dict[str, Any] | None = None,
    remote: bool | None = None,
) -> Spec[Fetcher]:
    """A service binding to `worker`, a name or another config's Worker."""
    return Spec(
        "services",
        {"entrypoint": export_name, "props": props, "remote": remote},
        target=worker,
    )


def worker_loader() -> Spec[Binding]:
    return Spec("worker_loaders")


def workflow(
    *, name: str, export_name: str, worker: str | Input[Worker] | None = None
) -> Spec[Workflow]:
    """A Workflow. Without `worker` it binds a class of this Worker."""
    return Spec("workflows", {"name": name, "class_name": export_name}, target=worker)
