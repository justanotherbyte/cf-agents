import asyncio
import contextlib
import json
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Literal,
    NotRequired,
    TypedDict,
    Unpack,
)
from urllib.parse import urljoin, urlsplit

from typing_extensions import TypeVar
from workers import (
    DurableObject,
    Request,
    Response,
    WorkerEntrypoint,
    fetch,
    python_from_rpc,
    python_to_rpc,
)

from . import ffi
from .state import ContainerState, State
from .utils import generate_id, parse_time_expression

if TYPE_CHECKING:
    from workers import FetchKwargs

logger = logging.getLogger(__name__)

# ====================
# ====================
#      CONSTANTS
# ====================
# ====================

NO_CONTAINER_INSTANCE_ERROR = (
    "there is no container instance that can be provided to this durable object"
)
RATE_LIMITED_ERROR = "you are requesting too many containers per second"
RUNTIME_SIGNALLED_ERROR = "runtime signalled the container to exit:"
UNEXPECTED_EXIT_ERROR = "container exited with unexpected exit code:"
NOT_LISTENING_ERROR = "the container is not listening"
OUTBOUND_CONFIGURATION_KEY = "OUTBOUND_CONFIGURATION"
CONTAINER_REQUEST_BASE_URL = "http://container"

# maxRetries before scheduling next alarm is purposely set to 3,
# as according to DO docs at https://developers.cloudflare.com/durable-objects/api/alarms/
# the maximum amount for alarm retries is 6.
MAX_ALARM_RETRIES = 3
PING_TIMEOUT_MS = 5000

DEFAULT_SLEEP_AFTER = "10m"  # Default sleep after inactivity time
INSTANCE_POLL_INTERVAL_MS = 300  # Default interval for polling container state

# Timeout for getting container instance and launching a VM
# Time to find an instance, attach a DO, call start, but NOT
# the time for the app the actually start
TIMEOUT_TO_GET_CONTAINER_MS = 8_000

# Timeout for getting a container instance and launching
# the actual application and have it listen for specific ports
# One day might be configurable by the end user in Container class attribute
TIMEOUT_TO_GET_PORTS_MS = 20_000

# If user has specified no ports and we need to check one
# to see if the container is up at all.
FALLBACK_PORT_TO_CHECK = 33

# TypeVars with defaults mirror the TS `<Params = unknown>` and `<T = string>`
# generics. Type parameter defaults need Python 3.13 syntax, so these are
# declared the pre-3.13 way.
ParamsT = TypeVar("ParamsT", default=Any)
PayloadT = TypeVar("PayloadT", default=str)


class OutboundHandlerContext(Generic[ParamsT]):
    """
    Context passed to outbound handlers.

    Parameters
    ----------
    container_id
        The ID of the Durable Object the container belongs to.
    class_name
        The name of the ``Container`` subclass.
    params
        Params given to ``set_outbound_handler`` or ``set_outbound_by_host``.
    """

    def __init__(
        self, container_id: str, class_name: str, params: ParamsT | None = None
    ) -> None:
        self.container_id = container_id
        self.class_name = class_name
        self.params = params


type OutboundHandler = Callable[
    [Request, Any, OutboundHandlerContext[Any]], Awaitable[Response]
]


class OutboundHandlerOverride(TypedDict):
    method: str
    params: NotRequired[Any]


type Signal = Literal["SIGKILL", "SIGINT", "SIGTERM"]

SIGNAL_TO_NUMBERS: dict[Signal, int] = {
    "SIGINT": 2,
    "SIGTERM": 15,
    "SIGKILL": 9,
}


class Schedule(TypedDict, Generic[PayloadT]):
    """
    Represents a scheduled task within a Container.

    ``type`` is ``"scheduled"`` for one-time execution at a specific time, or
    ``"delayed"`` for delayed execution, in which case ``delay_in_seconds`` is set.
    """

    task_id: str
    callback: str
    payload: PayloadT
    type: Literal["scheduled", "delayed"]
    time: int
    delay_in_seconds: NotRequired[float]


class _StartConfigOptions(TypedDict, total=False):
    env_vars: dict[str, str]
    entrypoint: list[str]
    enable_internet: bool
    labels: dict[str, str]


# class name to Container subclass, used by ContainerProxy to find outbound handlers
_container_classes: dict[str, type["Container"]] = {}

# =====================
# =====================
#   HELPER FUNCTIONS
# =====================
# =====================

# ==== Error helpers ====


def _is_error_of_type(e: object, matching_string: str) -> bool:
    return matching_string in str(e).lower()


def _is_no_instance_error(error: object) -> bool:
    return _is_error_of_type(error, NO_CONTAINER_INSTANCE_ERROR)


def _is_rate_limited_error(error: object) -> bool:
    return _is_error_of_type(error, RATE_LIMITED_ERROR)


def _is_runtime_signalled_error(error: object) -> bool:
    return _is_error_of_type(error, RUNTIME_SIGNALLED_ERROR)


def _is_not_listening_error(error: object) -> bool:
    return _is_error_of_type(error, NOT_LISTENING_ERROR)


def _is_container_exit_non_zero_error(error: object) -> bool:
    return _is_error_of_type(error, UNEXPECTED_EXIT_ERROR)


def _get_exit_code_from_error(error: object) -> int | None:
    if not isinstance(error, Exception):
        return None

    message = str(error).lower()
    for marker, matches in (
        (RUNTIME_SIGNALLED_ERROR, _is_runtime_signalled_error),
        (UNEXPECTED_EXIT_ERROR, _is_container_exit_non_zero_error),
    ):
        if matches(error):
            try:
                return int(message[message.index(marker) + len(marker) + 1 :])
            except ValueError:
                return None

    return None


def _add_timeout_signal(existing_signal: asyncio.Event | None, timeout_ms: int) -> Any:
    """
    Combine the user-defined signal with one that aborts after ``timeout_ms``.

    Returns a JavaScript ``AbortSignal``.
    """
    controller = ffi.AbortController.new()

    # Forward existing signal abort
    if existing_signal is not None and existing_signal.is_set():
        controller.abort()
        return controller.signal

    waiter: asyncio.Future[Any] | None = None

    def abort() -> None:
        # Clean up timeout if signal is aborted early
        timeout_handle.cancel()
        if waiter is not None:
            waiter.cancel()
        controller.abort()

    # Add timeout
    timeout_handle = asyncio.get_running_loop().call_later(timeout_ms / 1000, abort)

    if existing_signal is not None:
        waiter = asyncio.ensure_future(existing_signal.wait())
        waiter.add_done_callback(lambda task: task.cancelled() or abort())

    return controller.signal


# ==== Glob helpers ====


def _simple_glob_match(pattern: str, value: str) -> bool:
    """
    Match a value against a simple glob pattern.

    ``*`` matches any sequence of characters, e.g. ``google.*.com``,
    ``*.example.com``, ``goo*gle``.
    """
    parts = pattern.split("*")
    if len(parts) == 1:
        return pattern == value
    if not value.startswith(parts[0]):
        return False
    if not value.endswith(parts[-1]):
        return False
    pos = len(parts[0])
    for part in parts[1:-1]:
        idx = value.find(part, pos)
        if idx == -1:
            return False
        pos = idx + len(part)
    return pos <= len(value) - len(parts[-1])


def _matches_host_list(hostname: str, patterns: list[str]) -> bool:
    return any(_simple_glob_match(pattern, hostname) for pattern in patterns)


def _normalize_hostname(hostname: str) -> str:
    return hostname.rstrip(".")


class ContainerProxy(WorkerEntrypoint):
    """
    Handles outbound HTTP from containers.

    It must be importable from the Worker entry module for outbound interception
    to work.
    """

    async def fetch(self, request: Request) -> Response:
        hostname = _normalize_hostname(urlsplit(request.url).hostname or "")
        props = python_from_rpc(self.ctx.props)
        class_name: str = props["class_name"]
        container_id: str = props["container_id"]
        outbound_by_host_overrides: dict[str, OutboundHandlerOverride] | None = (
            props.get("outbound_by_host_overrides")
        )
        outbound_handler_override: OutboundHandlerOverride | None = props.get(
            "outbound_handler_override"
        )
        enable_internet: bool | None = props.get("enable_internet")
        allowed_hosts: list[str] | None = props.get("allowed_hosts")
        denied_hosts: list[str] | None = props.get("denied_hosts")
        intercept_all: bool | None = props.get("intercept_all")

        container_class = _container_classes.get(class_name)

        # 1. deniedHosts: overrides everything, blocks unconditionally
        if denied_hosts and _matches_host_list(hostname, denied_hosts):
            return Response("Origin is disallowed", status=520)

        # 2. allowedHosts: when set, acts as a whitelist gate — only matching
        #    hosts can proceed. This gates everything below, including outboundByHost.
        #    outboundByHost only maps a handler for a hostname, it does not allow it.
        if allowed_hosts is not None and not _matches_host_list(
            hostname, allowed_hosts
        ):
            return Response("Origin is disallowed", status=520)

        # 3. outboundByHost (runtime override) — exact match then glob
        handlers = container_class.outbound_handlers if container_class else None

        if outbound_by_host_overrides and handlers:
            override = outbound_by_host_overrides.get(hostname) or next(
                (
                    value
                    for pattern, value in outbound_by_host_overrides.items()
                    if pattern != hostname and _simple_glob_match(pattern, hostname)
                ),
                None,
            )
            if override and override["method"] in handlers:
                return await handlers[override["method"]](
                    request,
                    self.env,
                    OutboundHandlerContext(
                        container_id, class_name, override.get("params")
                    ),
                )

        # 4. outboundByHost (static) — exact match then glob
        handlers_by_host = container_class.outbound_by_host if container_class else None
        if handlers_by_host:
            handler = handlers_by_host.get(hostname) or next(
                (
                    value
                    for pattern, value in handlers_by_host.items()
                    if pattern != hostname and _simple_glob_match(pattern, hostname)
                ),
                None,
            )
            if handler:
                return await handler(
                    request, self.env, OutboundHandlerContext(container_id, class_name)
                )

        # In per-host mode, only specific hosts were intercepted.
        # If no handler matched above, fall back to direct internet access only
        # when the container already allows it.
        if not intercept_all:
            if allowed_hosts is not None or enable_internet:
                return await fetch(request)

            return Response("Origin is disallowed", status=520)

        # 5. Runtime catch-all handler override
        if outbound_handler_override and handlers:
            handler = handlers.get(outbound_handler_override["method"])
            if handler:
                return await handler(
                    request,
                    self.env,
                    OutboundHandlerContext(
                        container_id,
                        class_name,
                        outbound_handler_override.get("params"),
                    ),
                )

        # 6. Default catch-all handler (static outbound)
        default_outbound = container_class.outbound if container_class else None
        if default_outbound is not None:
            return await default_outbound(
                request, self.env, OutboundHandlerContext(container_id, class_name)
            )

        # 7. If the host was explicitly allowed and no outbound handled it,
        #    grant internet
        if allowed_hosts is not None:
            return await fetch(request)

        # 8. enableInternet fallback
        if enable_internet:
            return await fetch(request)

        return Response("Origin is disallowed", status=520)


# ===============================
# ===============================
#     MAIN CONTAINER CLASS
# ===============================
# ===============================


class Container(DurableObject):
    """
    A Durable Object that manages a container.

    Configure it by setting class attributes on a subclass, e.g. ``default_port``
    and ``sleep_after``.
    """

    # =========================
    #     Public Attributes
    # =========================

    # Default port for the container (None means no default port)
    default_port: int | None = None

    # Required ports that should be checked for availability during container startup
    # Override this in your subclass to specify ports that must be ready
    required_ports: list[int] | None = None

    # Timeout after which the container will sleep if no activity
    # The signal sent to the container by default is a SIGTERM.
    # The container won't get a SIGKILL if this threshold is triggered.
    sleep_after: str | int = DEFAULT_SLEEP_AFTER

    # Container configuration properties
    # Set these properties directly in your container instance
    env_vars: dict[str, str] = {}
    entrypoint: list[str] | None = None
    enable_internet: bool = True
    labels: dict[str, str] = {}

    # When true, outbound HTTPS traffic from the container will be intercepted.
    # The container must trust /etc/cloudflare/certs/cloudflare-containers-ca.crt
    intercept_https: bool = False

    # Hosts that are allowed to access the internet, even when enable_internet is False.
    # Useful for allowing specific domains on a per-host basis.
    allowed_hosts: list[str] | None = None

    # Hosts that are denied internet access, even when enable_internet is True.
    # Also blocks hosts from being handled by the catch-all outbound handler.
    denied_hosts: list[str] | None = None

    # ping_endpoint is the host and path value that the class will use to send a
    # request to the container and check if the instance is ready.
    #
    # The user does not have to implement this route by any means,
    # but it's still useful if you want to control the path that
    # the Container class uses to send HTTP requests to.
    ping_endpoint: str = "ping"

    # Map of hostname to handler, for requests to specific hosts
    outbound_by_host: dict[str, OutboundHandler] | None = None

    # Catch-all handler for any host not handled by a more specific rule
    outbound: OutboundHandler | None = None

    # Named handlers that can be referenced at runtime via set_outbound_handler()
    # or set_outbound_by_host()
    outbound_handlers: dict[str, OutboundHandler] | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _container_classes[cls.__name__] = cls

    # =========================
    #     PUBLIC INTERFACE
    # =========================

    def __init__(
        self,
        ctx: Any,
        env: Any,
        /,
        *,
        default_port: int | None = None,
        sleep_after: str | int | None = None,
        env_vars: dict[str, str] | None = None,
        entrypoint: list[str] | None = None,
        enable_internet: bool | None = None,
    ) -> None:
        super().__init__(ctx, env)

        if getattr(ctx, "container", None) is None:
            raise RuntimeError(
                "Containers have not been enabled for this Durable Object class. Have "
                "you correctly setup your Wrangler config? More info: "
                "https://developers.cloudflare.com/containers/get-started/#configuration"
            )

        # onStopCalled will be true when we are in the middle of an onStop call
        self._on_stop_called = False
        self._state = ContainerState(self.ctx.storage)
        self._monitor: Any = None

        # Coalesces concurrent calls to _start_container_if_not_running so we never
        # call `self._container.start()` twice. Without this guard, two requests
        # racing the readiness path can both pass the `if self._container.running`
        # early-return (each yielding the DO input gate at storage awaits) and
        # both reach the synchronous workerd `start()`, causing the second to
        # throw "start() cannot be called on a container that is already running."
        # See https://github.com/cloudflare/containers/issues/173.
        self._start_in_flight: asyncio.Future[int] | None = None

        self._monitored_promise: Any = None
        self._sleep_after_ms = 0
        self._inflight_requests = 0
        self._background_tasks: set[asyncio.Future[Any]] = set()

        # Outbound interception runtime overrides (passed through ContainerProxy props)
        self._outbound_by_host_overrides: dict[str, OutboundHandlerOverride] = {}
        self._outbound_handler_override: OutboundHandlerOverride | None = None

        # Only set when the user calls set_allowed_hosts/set_denied_hosts at runtime
        self._allowed_hosts_override: list[str] | None = None
        self._denied_hosts_override: list[str] | None = None

        # The runtime does not expose a way to remove outbound interceptions yet, so
        # once we promote an instance to intercept-all we must keep using it.
        self._has_intercept_all_registration = False
        self._using_interception = False

        self._timeout: asyncio.TimerHandle | None = None
        self._resolve: Callable[[], None] | None = None

        persisted_outbound_configuration = self._restore_outbound_configuration()

        async def initialize() -> None:
            # First thing, schedule the next alarms.
            await self.schedule_next_alarm()
            self.renew_activity_timeout()

            cls = type(self)
            if (
                persisted_outbound_configuration is not None
                or cls.outbound_by_host is not None
                or cls.outbound is not None
                or cls.outbound_handlers is not None
                or self._effective_allowed_hosts is not None
                or self._effective_denied_hosts is not None
            ):
                self._using_interception = True

            if self._container.running:
                task = asyncio.ensure_future(self._apply_outbound_interception())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

        ffi.block_concurrency_while(self.ctx, initialize)

        self._container = ctx.container
        # Apply options if provided
        if default_port is not None:
            self.default_port = default_port
        if sleep_after is not None:
            self.sleep_after = sleep_after
        if env_vars is not None:
            self.env_vars = env_vars
        if entrypoint is not None:
            self.entrypoint = entrypoint
        if enable_internet is not None:
            self.enable_internet = enable_internet

        # Create schedules table if it doesn't exist
        self._sql(
            """
            CREATE TABLE IF NOT EXISTS container_schedules (
              id TEXT PRIMARY KEY NOT NULL DEFAULT (randomblob(9)),
              callback TEXT NOT NULL,
              payload TEXT,
              type TEXT NOT NULL CHECK(type IN ('scheduled', 'delayed')),
              time INTEGER NOT NULL,
              delayInSeconds INTEGER,
              created_at INTEGER DEFAULT (unixepoch())
            )
            """
        )

        if self._container.running:
            self._monitor = self._container.monitor()
            self._setup_monitor_callbacks()

    async def get_state(self) -> State:
        """
        Get the current state of the container.

        Returns
        -------
        State
            A copy of the current state.
        """
        return State(**(await self._state.get_state()))

    # ====================================
    #     OUTBOUND INTERCEPTION CONFIG
    # ====================================

    async def set_outbound_handler(self, method_name: str, params: Any = None) -> None:
        """
        Set the catch-all outbound handler to a handler from ``outbound_handlers``.

        Overrides the default ``outbound`` at runtime via ContainerProxy props.

        Parameters
        ----------
        method_name
            Name of a handler defined in ``outbound_handlers``.
        params
            Optional params passed to the handler as ``ctx.params``.

        Raises
        ------
        ValueError
            If the method name is not found in ``outbound_handlers``.
        """
        self._validate_outbound_handler_method_name(method_name)
        self._outbound_handler_override = (
            {"method": method_name}
            if params is None
            else {"method": method_name, "params": params}
        )
        await self._refresh_outbound_interception()

    async def set_outbound_by_host(
        self, hostname: str, method_name: str, params: Any = None
    ) -> None:
        """
        Add or override a hostname-specific outbound handler at runtime.

        References a named handler from ``outbound_handlers``, and overrides any
        matching entry in ``outbound_by_host`` for this hostname.

        Parameters
        ----------
        hostname
            The hostname or ip:port to intercept (e.g. ``"google.com"``).
        method_name
            Name of a handler defined in ``outbound_handlers``.
        params
            Optional params passed to the handler as ``ctx.params``.

        Raises
        ------
        ValueError
            If the method name is not found in ``outbound_handlers``.
        """
        self._validate_outbound_handler_method_name(method_name)
        self._outbound_by_host_overrides[hostname] = (
            {"method": method_name}
            if params is None
            else {"method": method_name, "params": params}
        )
        await self._refresh_outbound_interception()

    async def remove_outbound_by_host(self, hostname: str) -> None:
        """
        Remove a runtime hostname override added via ``set_outbound_by_host``.

        The default handler from ``outbound_by_host`` (if any) will be used again.

        Parameters
        ----------
        hostname
            The hostname or ip:port to stop overriding.
        """
        self._outbound_by_host_overrides.pop(hostname, None)
        await self._refresh_outbound_interception()

    async def set_outbound_by_hosts(
        self, handlers: dict[str, str | OutboundHandlerOverride]
    ) -> None:
        """
        Replace all runtime hostname overrides at once.

        Parameters
        ----------
        handlers
            Maps hostnames to either a handler name in ``outbound_handlers``, or a
            dict with ``method`` and ``params``.

        Raises
        ------
        ValueError
            If any method name is not found in ``outbound_handlers``.
        """
        for handler in handlers.values():
            method_name = handler if isinstance(handler, str) else handler["method"]
            self._validate_outbound_handler_method_name(method_name)

        self._outbound_by_host_overrides = {
            hostname: {"method": handler} if isinstance(handler, str) else handler
            for hostname, handler in handlers.items()
        }
        await self._refresh_outbound_interception()

    # ====================================
    #     ALLOWED / DENIED HOSTS CONFIG
    # ====================================

    async def set_allowed_hosts(self, hosts: list[str]) -> None:
        """
        Replace all allowed hosts at runtime.

        Allowed hosts get internet access even when ``enable_internet`` is False.

        Parameters
        ----------
        hosts
            Hostnames to allow (e.g. ``["api.stripe.com", "example.com"]``).
        """
        self._allowed_hosts_override = list(hosts)
        self._using_interception = True
        await self._refresh_outbound_interception()

    async def set_denied_hosts(self, hosts: list[str]) -> None:
        """
        Replace all denied hosts at runtime.

        Denied hosts are blocked unconditionally, even when ``enable_internet`` is
        True or a catch-all outbound handler is set.

        Parameters
        ----------
        hosts
            Hostnames to deny (e.g. ``["evil.com", "blocked.org"]``).
        """
        self._denied_hosts_override = list(hosts)
        self._using_interception = True
        await self._refresh_outbound_interception()

    async def allow_host(self, hostname: str) -> None:
        """
        Add a single hostname to the allowed hosts list at runtime.

        Parameters
        ----------
        hostname
            The hostname to allow (e.g. ``"api.stripe.com"``).
        """
        effective = self._effective_allowed_hosts or []
        if hostname not in effective:
            self._allowed_hosts_override = [*effective, hostname]
        self._using_interception = True
        await self._refresh_outbound_interception()

    async def deny_host(self, hostname: str) -> None:
        """
        Add a single hostname to the denied hosts list at runtime.

        Parameters
        ----------
        hostname
            The hostname to deny (e.g. ``"evil.com"``).
        """
        effective = self._effective_denied_hosts or []
        if hostname not in effective:
            self._denied_hosts_override = [*effective, hostname]
        self._using_interception = True
        await self._refresh_outbound_interception()

    async def remove_allowed_host(self, hostname: str) -> None:
        """
        Remove a hostname from the allowed hosts list.

        Parameters
        ----------
        hostname
            The hostname to remove from the allow list.
        """
        self._allowed_hosts_override = [
            host for host in self._effective_allowed_hosts or [] if host != hostname
        ]
        await self._refresh_outbound_interception()

    async def remove_denied_host(self, hostname: str) -> None:
        """
        Remove a hostname from the denied hosts list.

        Parameters
        ----------
        hostname
            The hostname to remove from the deny list.
        """
        self._denied_hosts_override = [
            host for host in self._effective_denied_hosts or [] if host != hostname
        ]
        await self._refresh_outbound_interception()

    # ==========================
    #     CONTAINER STARTING
    # ==========================

    @ffi.rpc_kwargs
    async def start(
        self,
        *,
        env_vars: dict[str, str] | None = None,
        entrypoint: list[str] | None = None,
        enable_internet: bool | None = None,
        labels: dict[str, str] | None = None,
        port_to_check: int | None = None,
        signal: asyncio.Event | None = None,
        retries: int | None = None,
        wait_interval: int | None = None,
    ) -> None:
        """
        Start the container if it's not running, without waiting for ports.

        Sets up monitoring and lifecycle hooks. It will automatically retry if the
        container fails to start.

        Parameters
        ----------
        env_vars, entrypoint, enable_internet, labels
            Override the class attributes of the same name for this start.
        port_to_check
            Port used to check the container is up. Defaults to ``default_port``,
            then the first of ``required_ports``.
        signal
            Set this event to abort waiting for the container to start.
        retries
            Number of attempts. Defaults to about 8s worth of attempts.
        wait_interval
            Time between attempts, in milliseconds.

        Raises
        ------
        RuntimeError
            If all start attempts fail.

        Examples
        --------
        >>> await self.start(
        ...     env_vars={"DEBUG": "true", "NODE_ENV": "development"},
        ...     entrypoint=["npm", "run", "dev"],
        ...     enable_internet=False,
        ...     labels={"tenant": "acme", "env": "prod"},
        ... )
        """
        if port_to_check is None:
            if self.default_port is not None:
                port_to_check = self.default_port
            elif self.required_ports:
                port_to_check = self.required_ports[0]
            else:
                port_to_check = FALLBACK_PORT_TO_CHECK
        poll_interval = (
            wait_interval if wait_interval is not None else INSTANCE_POLL_INTERVAL_MS
        )
        await self._start_container_if_not_running(
            signal=signal,
            wait_interval=poll_interval,
            retries=(
                retries
                if retries is not None
                else math.ceil(TIMEOUT_TO_GET_CONTAINER_MS / poll_interval)
            ),
            port_to_check=port_to_check,
            start_options=_StartConfigOptions(
                **{
                    key: value
                    for key, value in (
                        ("env_vars", env_vars),
                        ("entrypoint", entrypoint),
                        ("enable_internet", enable_internet),
                        ("labels", labels),
                    )
                    if value is not None
                }
            ),
        )

        self._setup_monitor_callbacks()

        # TODO: We should consider an onHealthy callback
        await ffi.block_concurrency_while(self.ctx, self.on_start)

    @ffi.rpc_kwargs
    async def start_and_wait_for_ports(
        self,
        ports: int | list[int] | None = None,
        /,
        *,
        abort: asyncio.Event | None = None,
        instance_get_timeout_ms: int | None = None,
        port_ready_timeout_ms: int | None = None,
        wait_interval: int | None = None,
        env_vars: dict[str, str] | None = None,
        entrypoint: list[str] | None = None,
        enable_internet: bool | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        """
        Start the container and wait for ports to be available.

        For each port, it polls until the port is available or
        ``port_ready_timeout_ms`` is reached.

        Parameters
        ----------
        ports
            The ports to wait for. Defaults to ``required_ports``, then
            ``default_port``.
        abort
            Set this event to abort starting the container.
        instance_get_timeout_ms
            Max time to get a container instance and start it, in milliseconds.
            The application inside may not be ready yet.
        port_ready_timeout_ms
            Max time to wait for the application to be listening on all ports,
            in milliseconds.
        wait_interval
            Time to wait between polling, in milliseconds.
        env_vars, entrypoint, enable_internet, labels
            Override the class attributes of the same name for this start.

        Raises
        ------
        RuntimeError
            If port checks fail after the timeout, or the container fails to start.
        """
        # Determine which ports to check
        ports_to_check = self._get_ports_to_check(ports)

        # trigger all onStop that we didn't do yet
        await self._sync_pending_stopped_events()

        # Prepare to start the container
        container_get_timeout = (
            instance_get_timeout_ms
            if instance_get_timeout_ms is not None
            else TIMEOUT_TO_GET_CONTAINER_MS
        )
        poll_interval = (
            wait_interval if wait_interval is not None else INSTANCE_POLL_INTERVAL_MS
        )
        container_get_retries = math.ceil(container_get_timeout / poll_interval)

        # Start the container if it's not running
        tries_used = await self._start_container_if_not_running(
            signal=abort,
            retries=container_get_retries,
            wait_interval=poll_interval,
            port_to_check=ports_to_check[0],
            start_options=_StartConfigOptions(
                **{
                    key: value
                    for key, value in (
                        ("env_vars", env_vars),
                        ("entrypoint", entrypoint),
                        ("enable_internet", enable_internet),
                        ("labels", labels),
                    )
                    if value is not None
                }
            ),
        )

        # Check each port

        total_port_ready_tries = math.ceil(
            (
                port_ready_timeout_ms
                if port_ready_timeout_ms is not None
                else TIMEOUT_TO_GET_PORTS_MS
            )
            / poll_interval
        )
        tries_left = total_port_ready_tries - tries_used

        for port in ports_to_check:
            tries_left = await self.wait_for_port(
                port,
                signal=abort,
                wait_interval=poll_interval,
                retries=tries_left,
            )

        self._setup_monitor_callbacks()

        async def mark_healthy() -> None:
            # All ports are ready
            await self._state.set_healthy()
            await self.on_start()

        await ffi.block_concurrency_while(self.ctx, mark_healthy)

    @ffi.rpc_kwargs
    async def wait_for_port(
        self,
        port_to_check: int,
        /,
        *,
        signal: asyncio.Event | None = None,
        retries: int | None = None,
        wait_interval: int | None = None,
    ) -> int:
        """
        Wait for a port to be ready.

        Parameters
        ----------
        port_to_check
            The port number to check.
        signal
            Set this event to abort waiting.
        retries
            Number of retries before giving up. Defaults to about 20s worth.
        wait_interval
            Time between retries, in milliseconds.

        Returns
        -------
        int
            The number of tries.

        Raises
        ------
        Exception
            The last connection error, if the port wasn't ready within the retries.
        """
        port = port_to_check
        tcp_port = self._container.getTcpPort(port)
        poll_interval = (
            wait_interval if wait_interval is not None else INSTANCE_POLL_INTERVAL_MS
        )
        tries = (
            retries
            if retries is not None
            else math.ceil(TIMEOUT_TO_GET_PORTS_MS / poll_interval)
        )

        # Try to connect to the port multiple times
        for i in range(tries):
            try:
                combined_signal = _add_timeout_signal(signal, PING_TIMEOUT_MS)
                await tcp_port.fetch(
                    f"http://{self.ping_endpoint}",
                    ffi.to_js_object({"signal": combined_signal}),
                )

                # Successfully connected to this port
                break
            except Exception as e:
                error_message = str(e)

                # If not running, it means the container crashed
                if not self._container.running:
                    # Intentionally ignore errors from the user-supplied on_error
                    # handler; the original error is re-raised below regardless.
                    with contextlib.suppress(Exception):
                        await self.on_error(
                            RuntimeError(
                                "Container crashed while checking for ports, did you "
                                "start the container and setup the entrypoint "
                                "correctly?"
                            )
                        )

                    raise

                # If we're on the last attempt and the port is still not ready, fail
                if i == tries - 1:
                    # Intentionally ignore errors from the user-supplied on_error
                    # handler; the original error is re-raised below regardless.
                    with contextlib.suppress(Exception):
                        await self.on_error(
                            RuntimeError(
                                f"Failed to verify port {port} is available after "
                                f"{(i + 1) * poll_interval}ms, last error: "
                                f"{error_message}"
                            )
                        )
                    raise

                # Wait a bit before trying again
                if signal is None:
                    await asyncio.sleep(poll_interval / 1000)
                else:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(signal.wait(), poll_interval / 1000)
                if signal is not None and signal.is_set():
                    raise RuntimeError("Container request aborted.") from e

        return tries

    # =======================
    #     LIFECYCLE HOOKS
    # =======================

    async def stop(self, signal: Signal | int = "SIGTERM") -> None:
        """
        Send a signal to the container.

        Parameters
        ----------
        signal
            The signal to send, as a name or a number. ``signal.Signals`` members
            work too.
        """
        if self._container.running:
            self._container.signal(
                SIGNAL_TO_NUMBERS[signal] if isinstance(signal, str) else int(signal)
            )

        await self._sync_pending_stopped_events()

    async def destroy(self) -> None:
        """Destroy the container with a SIGKILL. Triggers ``on_stop``."""
        await self._container.destroy()

    async def on_start(self) -> None:
        """
        Lifecycle method called when the container starts successfully.

        Override this method in subclasses to handle container start events.
        """

    async def on_stop(
        self, *, exit_code: int, reason: Literal["exit", "runtime_signal"]
    ) -> None:
        """
        Lifecycle method called when the container shuts down.

        Override this method in subclasses to handle container stopped events.

        Parameters
        ----------
        exit_code
            The exit code of the container.
        reason
            Why the container stopped.
        """

    async def on_activity_expired(self) -> None:
        """
        Lifecycle method called when the activity timeout has been reached.

        Called when the container is running and the activity timeout expiration
        (set by ``sleep_after``) has been reached. If you want to shutdown the
        container, you should call ``self.stop()`` here.

        By default, this method calls ``self.stop()``.
        """
        logger.info("Activity expired, signalling container to stop")
        if not self._container.running:
            return

        await self.stop()

    async def on_error(self, error: Exception) -> None:
        """
        Error handler for container errors.

        Override this method in subclasses to handle container errors.

        Parameters
        ----------
        error
            The error that occurred.

        Raises
        ------
        Exception
            By default, re-raises ``error``.
        """
        logger.error("Container error: %s", error)
        raise error

    def renew_activity_timeout(self) -> None:
        """
        Renew the container's activity timeout.

        Call this method whenever there is activity on the container.
        """
        timeout_in_ms = parse_time_expression(self.sleep_after) * 1000
        self._sleep_after_ms = int(time.time() * 1000) + timeout_in_ms

    def _decrement_inflight(self) -> None:
        """
        Decrement the inflight request counter.

        When the counter transitions to 0, renew the activity timeout so the
        inactivity window starts fresh from the moment the last request completes.
        """
        self._inflight_requests = max(0, self._inflight_requests - 1)
        if self._inflight_requests == 0:
            self.renew_activity_timeout()

    # ==================
    #     SCHEDULING
    # ==================

    async def schedule[T](
        self, when: datetime | float, callback: str, payload: T | None = None
    ) -> Schedule[T | None]:
        """
        Schedule a task to be executed in the future.

        We strongly recommend using this instead of the ``alarm`` handler.

        Parameters
        ----------
        when
            When to execute the task, as a ``datetime`` or a delay in seconds.
        callback
            Name of the method to call. It must be an ``async def`` method and is
            called with ``(payload, schedule)``.
        payload
            Data to pass to the callback. Must be JSON serialisable.

        Returns
        -------
        Schedule
            The scheduled task.

        Raises
        ------
        TypeError
            If ``callback`` is not a string, or ``when`` is of the wrong type.
        ValueError
            If ``callback`` is not a method on this container.
        """
        id = generate_id(9)

        # Ensure the callback is a string (method name)
        if not isinstance(callback, str):
            raise TypeError("Callback must be a string (method name)")

        # Ensure the method exists
        if not callable(getattr(self, callback, None)):
            raise ValueError(f"self.{callback} is not a function")

        serialized_payload = None if payload is None else json.dumps(payload)

        # Schedule based on the type of 'when' parameter
        if isinstance(when, datetime):
            # Schedule for a specific time
            timestamp = math.floor(when.timestamp())

            self._sql(
                """
                INSERT OR REPLACE INTO container_schedules
                  (id, callback, payload, type, time)
                VALUES (?, ?, ?, 'scheduled', ?)
                """,
                id,
                callback,
                serialized_payload,
                timestamp,
            )

            await self.schedule_next_alarm()

            return {
                "task_id": id,
                "callback": callback,
                "payload": payload,
                "time": timestamp,
                "type": "scheduled",
            }

        if isinstance(when, int | float):
            # Schedule for a delay in seconds
            timestamp = math.floor(time.time() + when)

            self._sql(
                """
                INSERT OR REPLACE INTO container_schedules
                  (id, callback, payload, type, delayInSeconds, time)
                VALUES (?, ?, ?, 'delayed', ?, ?)
                """,
                id,
                callback,
                serialized_payload,
                when,
                timestamp,
            )

            await self.schedule_next_alarm()

            return {
                "task_id": id,
                "callback": callback,
                "payload": payload,
                "delay_in_seconds": when,
                "time": timestamp,
                "type": "delayed",
            }

        raise TypeError(
            "Invalid schedule type. 'when' must be a datetime or number of seconds"
        )

    # ============
    #     HTTP
    # ============

    @ffi.rpc_kwargs
    async def container_fetch(
        self,
        request_or_url: Request | str,
        /,
        port: int | None = None,
        **init: Unpack["FetchKwargs"],
    ) -> Response:
        """
        Send a request to the container (HTTP or WebSocket).

        WebSocket requests done outside the DO won't work until
        https://github.com/cloudflare/workerd/issues/2319 is addressed.
        Until then, please use ``switch_port`` + ``fetch()``.

        Supports two forms:

        - ``container_fetch(request, port=None)``
        - ``container_fetch(url, port=None, **init)``, where ``init`` takes the
          same options as ``workers.Request`` (``method``, ``headers``, ``body``...)

        Starts the container if not already running, and waits for the target
        port to be ready.

        Parameters
        ----------
        request_or_url
            A request, or an absolute or relative URL.
        port
            The container port. Defaults to ``default_port``.
        **init
            Request options, only when ``request_or_url`` is a URL.

        Returns
        -------
        Response
            A response from the container.

        Raises
        ------
        ValueError
            If no port is given and ``default_port`` is not set.
        """
        # Parse the arguments based on their types to handle different signatures
        request, port = self._request_and_port_from_container_fetch_args(
            request_or_url, port, init
        )

        state = await self._state.get_state()
        if not self._container.running or state["status"] != "healthy":
            abort = asyncio.Event()
            js_signal = request.js_object.signal
            if js_signal.aborted:
                abort.set()
            listener = ffi.add_event_listener(js_signal, "abort", lambda _: abort.set())
            try:
                await self.start_and_wait_for_ports(port, abort=abort)
            except Exception as e:
                if _is_no_instance_error(e):
                    return Response(
                        "There is no Container instance available at this time.\n"
                        "This is likely because you have reached your max concurrent "
                        "instance count (set in wrangler config) or are you currently "
                        "provisioning the Container.\n"
                        "If you are deploying your Container for the first time, "
                        "check your dashboard to see provisioning status, this may "
                        "take a few minutes.",
                        status=503,
                    )

                if _is_rate_limited_error(e):
                    return Response(str(e), status=429)

                return Response(f"Failed to start container: {e}", status=500)
            finally:
                js_signal.removeEventListener("abort", listener)

        tcp_port = self._container.getTcpPort(port)

        # Create URL for the container request. `tcp_port.fetch` opens a raw TCP
        # connection to the container, which does not terminate TLS, so an https
        # scheme has to be downgraded. The match is anchored on purpose: an
        # unanchored string replace rewrites the first `https:` anywhere in the URL,
        # which corrupts query strings and fragments that carry an absolute URL (for
        # example `/callback?redirect=https://app.example.com`) whenever the scheme
        # is already http.
        container_url = re.sub(r"^https:", "http:", request.url)

        self._inflight_requests += 1

        try:
            # Renew the activity timeout whenever a request is proxied
            self.renew_activity_timeout()
            res = await tcp_port.fetch(container_url, request.js_object)

            container_ws = res.webSocket
            if container_ws is not None and container_ws is not ffi.jsnull:
                # WebSocket response: proxy by accepting both sides and forwarding
                # messages
                client, server = ffi.Object.values(ffi.WebSocketPair.new())

                # Guard to ensure we only decrement inflight once per WebSocket,
                # since both close and error events can fire.
                settled = False

                def settle_inflight() -> None:
                    nonlocal settled
                    if not settled:
                        settled = True
                        self._decrement_inflight()

                # Accept both WebSocket ends
                container_ws.accept()
                server.accept()

                async def forward_to_container(event: Any) -> None:
                    self.renew_activity_timeout()
                    try:
                        data = event.data
                        if (
                            getattr(getattr(data, "constructor", None), "name", None)
                            == "Blob"
                        ):
                            data = await data.arrayBuffer()
                        container_ws.send(data)
                    except Exception:
                        server.close(1011, "Failed to forward message to container")

                async def forward_to_client(event: Any) -> None:
                    self.renew_activity_timeout()
                    try:
                        data = event.data
                        if (
                            getattr(getattr(data, "constructor", None), "name", None)
                            == "Blob"
                        ):
                            data = await data.arrayBuffer()
                        server.send(data)
                    except Exception:
                        container_ws.close(1011, "Failed to forward message to client")

                def close_container(event: Any) -> None:
                    settle_inflight()
                    # Codes 1005 (No Status Received) and 1006 (Abnormal Closure) are
                    # reserved and cannot be sent in a close frame — fall back to 1000.
                    code = 1000 if event.code in (1005, 1006) else event.code
                    container_ws.close(code, event.reason)

                def close_client(event: Any) -> None:
                    settle_inflight()
                    code = 1000 if event.code in (1005, 1006) else event.code
                    server.close(code, event.reason)

                def client_error(_: Any) -> None:
                    settle_inflight()
                    container_ws.close(1011, "Client WebSocket error")

                def container_error(_: Any) -> None:
                    settle_inflight()
                    server.close(1011, "Container WebSocket error")

                # Forward messages from client to container
                ffi.add_event_listener(server, "message", forward_to_container)
                # Forward messages from container to client
                ffi.add_event_listener(container_ws, "message", forward_to_client)
                # Forward close from client to container
                ffi.add_event_listener(server, "close", close_container)
                # Forward close from container to client
                ffi.add_event_listener(container_ws, "close", close_client)
                # Forward errors
                ffi.add_event_listener(server, "error", client_error)
                ffi.add_event_listener(container_ws, "error", container_error)

                return Response(
                    None, status=res.status, headers=res.headers, web_socket=client
                )

            response = Response(res)
            if response.body is not None:
                stream = ffi.IdentityTransformStream.new()
                pipe = response.body.pipeTo(stream.writable)

                async def track_body() -> None:
                    try:
                        await pipe
                    finally:
                        self._decrement_inflight()

                task = asyncio.ensure_future(track_body())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

                return Response(
                    stream.readable,
                    status=res.status,
                    status_text=res.statusText,
                    headers=res.headers,
                )

            self._decrement_inflight()
            return response
        except BaseException as e:
            self._decrement_inflight()

            if not isinstance(e, Exception):
                raise

            # This error means that the container might've just restarted
            if "Network connection lost." in str(e):
                return Response(
                    "Container suddenly disconnected, try again", status=500
                )

            logger.error(
                "Error proxying request to container %s: %s", self.ctx.id.toString(), e
            )
            return Response(f"Error proxying request to container: {e}", status=500)

    async def fetch(self, request: Request) -> Response:
        """
        Fetch handler on the Container class.

        By default this forwards all requests to the container by calling
        ``container_fetch``. Use ``switch_port`` to specify which port on the
        container to target, or this will use ``default_port``.

        Parameters
        ----------
        request
            The request to handle.

        Returns
        -------
        Response
            A response from the container.

        Raises
        ------
        ValueError
            If no port is configured, or the port set by ``switch_port`` is not a
            number.
        """
        target_port = request.headers.get("cf-container-target-port")
        if self.default_port is None and target_port is None:
            raise ValueError(
                "No port configured for this container. Set the `default_port` in your "
                "Container subclass, or specify a port with "
                "`container.fetch(switch_port(request, port))`."
            )

        port_value = self.default_port

        if target_port is not None:
            try:
                port_value = int(target_port)
            except ValueError:
                raise ValueError(
                    "port value from switch_port is not a number"
                ) from None

        # Forward all requests (HTTP and WebSocket) to the container
        return await self.container_fetch(request, port_value)

    # ==========================
    #     GENERAL HELPERS
    # ==========================

    def _validate_outbound_handler_method_name(self, method_name: str) -> None:
        """
        Validate that a method name exists in ``outbound_handlers`` for this class.

        Raises
        ------
        ValueError
            If the method name is not found.
        """
        handlers = type(self).outbound_handlers
        if not handlers or method_name not in handlers:
            raise ValueError(
                f"Outbound handler method '{method_name}' not found in "
                f"outbound_handlers for {type(self).__name__}"
            )

    @property
    def _effective_allowed_hosts(self) -> list[str] | None:
        if self._allowed_hosts_override is not None:
            return self._allowed_hosts_override
        return self.allowed_hosts

    @property
    def _effective_denied_hosts(self) -> list[str] | None:
        if self._denied_hosts_override is not None:
            return self._denied_hosts_override
        return self.denied_hosts

    def _get_outbound_configuration(self) -> dict[str, Any]:
        return {
            "outbound_by_host_overrides": self._outbound_by_host_overrides or None,
            "outbound_handler_override": self._outbound_handler_override,
            "allowed_hosts": self._effective_allowed_hosts,
            "denied_hosts": self._effective_denied_hosts,
            "has_intercept_all_registration": (
                self._has_intercept_all_registration or None
            ),
        }

    def _persist_outbound_configuration(self, configuration: dict[str, Any]) -> None:
        self.ctx.storage.kv.put(
            OUTBOUND_CONFIGURATION_KEY,
            {
                **configuration,
                "allowed_hosts": self._allowed_hosts_override,
                "denied_hosts": self._denied_hosts_override,
            },
        )

    def _restore_outbound_configuration(self) -> dict[str, Any] | None:
        configuration = self.ctx.storage.kv.get(OUTBOUND_CONFIGURATION_KEY)

        if not configuration:
            return None

        self._outbound_handler_override = None
        outbound_handler_override = configuration.get("outbound_handler_override")
        if outbound_handler_override is not None:
            try:
                self._validate_outbound_handler_method_name(
                    outbound_handler_override["method"]
                )
                self._outbound_handler_override = outbound_handler_override
            except ValueError as error:
                logger.warning(
                    "Ignoring invalid persisted outbound handler override: %s", error
                )

        self._outbound_by_host_overrides = {}
        for hostname, override in (
            configuration.get("outbound_by_host_overrides") or {}
        ).items():
            try:
                self._validate_outbound_handler_method_name(override["method"])
                self._outbound_by_host_overrides[hostname] = override
            except ValueError as error:
                logger.warning(
                    "Ignoring invalid persisted outbound override for %s: %s",
                    hostname,
                    error,
                )

        self._has_intercept_all_registration = (
            configuration.get("has_intercept_all_registration") is True
        )

        if configuration.get("allowed_hosts"):
            self._allowed_hosts_override = configuration["allowed_hosts"]

        if configuration.get("denied_hosts"):
            self._denied_hosts_override = configuration["denied_hosts"]

        return self._get_outbound_configuration()

    def _needs_catch_all_interception(self) -> bool:
        """
        Return True if a catch-all outbound HTTP interception is needed.

        This is the case when a static ``outbound`` handler or a runtime
        ``outbound_handler_override`` (catch-all) is configured.
        When False, we only intercept specific hosts to avoid overhead.
        """
        return (
            type(self).outbound is not None
            or self._outbound_handler_override is not None
        )

    def _has_mutable_outbound_configuration(self) -> bool:
        return (
            len(self._outbound_by_host_overrides) > 0
            or self._allowed_hosts_override is not None
            or self._denied_hosts_override is not None
        )

    def _should_intercept_all_outbound(self) -> bool:
        return (
            self._has_intercept_all_registration
            or self._needs_catch_all_interception()
            or self._effective_allowed_hosts is not None
            or self._effective_denied_hosts is not None
            or self._has_mutable_outbound_configuration()
        )

    def _get_static_outbound_by_host_keys(self) -> list[str]:
        return list(type(self).outbound_by_host or {})

    def _get_hosts_to_intercept(self) -> list[str]:
        """
        Collect all hostnames that need per-host outbound interception.

        This path is only used for the narrow optimized case where outbound
        handling is static and host-specific.
        """
        hosts = dict.fromkeys(type(self).outbound_by_host or {})
        hosts.update(dict.fromkeys(self._outbound_by_host_overrides))
        return list(hosts)

    async def _refresh_outbound_interception(self) -> None:
        if not self._using_interception:
            return

        await self._apply_outbound_interception()

    async def _apply_outbound_interception(self) -> None:
        """
        Apply (or re-apply) outbound HTTP interception.

        Uses the class-level outbound config plus runtime overrides, passed
        through ContainerProxy props.

        Uses per-host interception only for static host-specific outbound
        handlers. As soon as the config needs to evaluate all hosts (catch-all
        outbound, allow/deny lists, or runtime-mutated outbound config), we
        promote the container to intercept-all and keep it there until the
        instance restarts.

        When ``intercept_https`` is enabled, also applies HTTPS interception:

        - Intercept-all mode: ``interceptOutboundHttps('*', ...)`` for all HTTPS
        - Per-host mode: ``interceptOutboundHttps(host, ...)`` for each known host
        """
        exports = getattr(self.ctx, "exports", None)
        if exports is None:
            raise RuntimeError(
                "ctx.exports is undefined, please try to update your compatibility "
                "date or import ContainerProxy from the containers package in your "
                "worker entrypoint"
            )

        container_proxy = getattr(exports, "ContainerProxy", None)
        if container_proxy is None:
            raise RuntimeError(
                "ctx.exports.ContainerProxy is undefined, import ContainerProxy from "
                "the containers package in your worker entrypoint"
            )

        # Checked up front so an unsupported runtime fails before any interception
        # is applied, rather than part-way through and leaving HTTP hosts
        # intercepted.
        if (
            self.intercept_https
            and getattr(self._container, "interceptOutboundHttps", None) is None
        ):
            raise RuntimeError(
                "intercept_https is enabled, but ctx.container.interceptOutboundHttps "
                "is not available in this runtime. HTTPS interception requires a "
                "runtime dated 2026-04-02 or later, please update wrangler and your "
                "compatibility date"
            )

        intercept_all = self._should_intercept_all_outbound()

        if intercept_all:
            self._has_intercept_all_registration = intercept_all

        outbound_configuration = self._get_outbound_configuration()
        self._persist_outbound_configuration(outbound_configuration)

        hosts = self._get_hosts_to_intercept()

        props = {
            "enable_internet": self.enable_internet,
            "container_id": self.ctx.id.toString(),
            "class_name": type(self).__name__,
            "outbound_by_host_overrides": outbound_configuration[
                "outbound_by_host_overrides"
            ],
            "outbound_handler_override": outbound_configuration[
                "outbound_handler_override"
            ],
            "allowed_hosts": outbound_configuration["allowed_hosts"],
            "denied_hosts": outbound_configuration["denied_hosts"],
            "intercept_all": intercept_all,
        }

        fetcher = container_proxy(python_to_rpc({"props": props}))

        if intercept_all:
            # If we previously installed static per-host interceptors, refresh them
            # with the current fetcher so they follow the latest config too.
            for host in self._get_static_outbound_by_host_keys():
                await self._container.interceptOutboundHttp(host, fetcher)

                if self.intercept_https:
                    await self._container.interceptOutboundHttps(host, fetcher)

            # If HTTPS interception is enabled, intercept all HTTPS traffic too
            if self.intercept_https:
                await self._container.interceptOutboundHttps("*", fetcher)

            # Intercept-all: intercept all outbound HTTP traffic
            await self._container.interceptAllOutboundHttp(fetcher)
        else:
            # Per-host: only intercept traffic for known hosts
            for host in hosts:
                await self._container.interceptOutboundHttp(host, fetcher)

                if self.intercept_https:
                    await self._container.interceptOutboundHttps(host, fetcher)

    def _sql(self, query: str, *values: Any) -> list[dict[str, Any]]:
        """Execute SQL queries against the Container's database."""
        return list(self.ctx.storage.sql.exec(query, *values))

    def _request_and_port_from_container_fetch_args(
        self,
        request_or_url: Request | str,
        port: int | None,
        init: "FetchKwargs",
    ) -> tuple[Request, int]:
        if isinstance(request_or_url, str):
            # URL-based: container_fetch(url, port=None, **init)
            request = Request(
                urljoin(CONTAINER_REQUEST_BASE_URL, request_or_url), **init
            )
        else:
            # Request-based: container_fetch(request, port=None)
            if init:
                raise TypeError(
                    "Request options can only be given when container_fetch is "
                    "called with a URL"
                )
            request = request_or_url

        if port is None:
            port = self.default_port
        # Require a port to be specified, either as a parameter or as a
        # default_port attribute
        if port is None:
            raise ValueError(
                "No port specified for container fetch. Set default_port or specify "
                "a port parameter."
            )

        return request, port

    def _get_ports_to_check(self, override_ports: int | list[int] | None) -> list[int]:
        """
        Get the ports to check when starting the container.

        The method prioritizes port sources in this order:

        1. Ports specified directly in the method call
        2. ``required_ports`` class attribute (if set)
        3. ``default_port`` (if neither of the above is specified)
        4. Falls back to port 33 if none of the above are set
        """
        if override_ports is not None:
            # Use explicitly provided ports (single port or list)
            return (
                list(override_ports)
                if isinstance(override_ports, list)
                else [override_ports]
            )

        if self.required_ports:
            # Use required_ports class attribute if available
            return list(self.required_ports)

        # Fall back to default_port if available
        return [
            self.default_port
            if self.default_port is not None
            else FALLBACK_PORT_TO_CHECK
        ]

    # ===========================================
    #     CONTAINER INTERACTION & MONITORING
    # ===========================================

    async def _start_container_if_not_running(
        self,
        *,
        port_to_check: int,
        retries: int,
        wait_interval: int,
        signal: asyncio.Event | None,
        start_options: _StartConfigOptions,
    ) -> int:
        """
        Try to start a container if it's not already running.

        Returns the number of tries used.
        """
        # Coalesce concurrent starts: if another caller is already in the start
        # path, join their outcome instead of racing a second `start()`. Checked
        # before the running fast path so callers also join during the
        # post-start()/pre-port-ready window.
        if self._start_in_flight is not None:
            return await self._start_in_flight

        # Fast path: container is already running and no start is in flight.
        if self._container.running:
            if self._monitor is None:
                self._monitor = self._container.monitor()

            return 0

        start_task = asyncio.ensure_future(
            self._do_start_container(
                port_to_check=port_to_check,
                retries=retries,
                wait_interval=wait_interval,
                signal=signal,
                start_options=start_options,
            )
        )
        self._start_in_flight = start_task
        try:
            return await start_task
        finally:
            # Clear the in-flight marker once this attempt resolves so future
            # start attempts (e.g. after the container has stopped) can proceed.
            # Use identity check in case a later attempt has already replaced it.
            if self._start_in_flight is start_task:
                self._start_in_flight = None

    async def _do_start_container(
        self,
        *,
        port_to_check: int,
        retries: int,
        wait_interval: int,
        signal: asyncio.Event | None,
        start_options: _StartConfigOptions,
    ) -> int:
        poll_interval = wait_interval
        total_tries = retries

        async def handle_error() -> None:
            err: object
            try:
                err = await self._monitor
            except Exception as e:
                err = e

            if isinstance(err, int | float) and not isinstance(err, bool):
                to_raise = RuntimeError(
                    "Container exited before we could determine the container "
                    f"health, exit code: {err}"
                )

                await self._state.set_stopped_with_code(int(err))
                self._monitor = None

                # Intentionally ignore errors from the user-supplied on_error
                # handler; the original error is raised below regardless.
                with contextlib.suppress(Exception):
                    await self.on_error(to_raise)

                raise to_raise
            elif not _is_no_instance_error(err):
                await self._state.set_stopped()
                self._monitor = None

                error = err if isinstance(err, Exception) else RuntimeError(str(err))

                # Intentionally ignore errors from the user-supplied on_error
                # handler; the original error is raised below regardless.
                with contextlib.suppress(Exception):
                    await self.on_error(error)

                raise error

        for tries in range(total_tries):
            # Use provided options or fall back to instance attributes
            env_vars = start_options.get("env_vars", self.env_vars)
            entrypoint = start_options.get("entrypoint", self.entrypoint)
            enable_internet = start_options.get("enable_internet", self.enable_internet)
            labels = start_options.get("labels", self.labels)
            # TODO: hopefully, enableInternet can be false in a future where we
            # enable DNS and TLS paths.

            # Only include properties that are defined
            start_config: dict[str, Any] = {"enableInternet": enable_internet}

            if env_vars:
                start_config["env"] = env_vars
            if entrypoint:
                start_config["entrypoint"] = entrypoint
            if labels:
                start_config["labels"] = labels

            self.renew_activity_timeout()

            if tries > 0 and not self._container.running:
                await handle_error()

            await self.schedule_next_alarm()

            if not self._container.running:
                await self._refresh_outbound_interception()
                self._container.start(ffi.to_js_object(start_config))
                self._monitor = self._container.monitor()
                await self._state.set_running()
            else:
                await self.schedule_next_alarm()

            self.renew_activity_timeout()

            # TODO: Make this the port I'm trying to get!
            port = self._container.getTcpPort(port_to_check)
            try:
                combined_signal = _add_timeout_signal(signal, PING_TIMEOUT_MS)
                await port.fetch(
                    "http://containerstarthealthcheck",
                    ffi.to_js_object({"signal": combined_signal}),
                )
                return tries
            except Exception as error:
                if _is_not_listening_error(error) and self._container.running:
                    return tries

                if not self._container.running and _is_not_listening_error(error):
                    await handle_error()

                if signal is None:
                    await asyncio.sleep(poll_interval / 1000)
                else:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(signal.wait(), poll_interval / 1000)

                if signal is not None and signal.is_set():
                    raise RuntimeError(
                        "Aborted waiting for container to start as we received a "
                        "cancellation signal"
                    ) from error

                # TODO: Make this error specific to this, but then catch it above
                # w something else
                if total_tries == tries + 1:
                    if "Network connection lost" in str(error):
                        # We have to abort here, the reasoning is that we might've
                        # found ourselves in an internal error where the Worker is
                        # stuck with a failed connection to the container services.
                        #
                        # Until we address this issue on the back-end CF side, we
                        # will need to abort the durable object so it retries to
                        # reconnect from scratch.
                        self.ctx.abort()

                    await handle_error()
                    await self._state.set_stopped()
                    self._monitor = None

                    raise RuntimeError(NO_CONTAINER_INSTANCE_ERROR) from error

                continue

        raise RuntimeError(
            f"Container did not start after {total_tries * poll_interval}ms"
        )

    def _setup_monitor_callbacks(self) -> None:
        monitor = self._monitor
        if monitor is None or self._monitored_promise is monitor:
            return

        self._monitored_promise = monitor
        task = asyncio.ensure_future(self._watch_monitor(monitor))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _watch_monitor(self, monitor: Any) -> None:
        try:
            try:
                await monitor

                async def on_exit() -> None:
                    if self._monitor is monitor:
                        await self._state.set_stopped_with_code(0)

                await ffi.block_concurrency_while(self.ctx, on_exit)
            except Exception as error:
                if self._monitor is not monitor:
                    return

                if _is_no_instance_error(error):

                    async def on_no_instance() -> None:
                        if self._monitor is monitor:
                            await self._state.set_stopped()

                    await ffi.block_concurrency_while(self.ctx, on_no_instance)
                    return

                exit_code = _get_exit_code_from_error(error)
                if exit_code is not None:

                    async def on_exit_code() -> None:
                        if self._monitor is monitor:
                            await self._state.set_stopped_with_code(exit_code)

                    await ffi.block_concurrency_while(self.ctx, on_exit_code)
                    return

                async def on_failure() -> None:
                    if self._monitor is monitor:
                        await self._state.set_stopped()

                await ffi.block_concurrency_while(self.ctx, on_failure)

                if self._monitor is not monitor:
                    return

                # TODO: Be able to retrigger onError
                # Intentionally ignore errors from the user-supplied on_error handler.
                with contextlib.suppress(Exception):
                    await self.on_error(error)
        finally:
            if self._monitor is monitor:
                self._monitored_promise = None
                self._monitor = None
                if self._timeout is not None:
                    if self._resolve is not None:
                        self._resolve()
                    self._timeout.cancel()

    def delete_schedules(self, name: str) -> None:
        """
        Delete all scheduled tasks for a callback.

        Parameters
        ----------
        name
            The callback method name.
        """
        self._sql("DELETE FROM container_schedules WHERE callback = ?", name)

    # ============================
    #     ALARMS AND SCHEDULES
    # ============================

    async def alarm(self, alarm_info: Any = None) -> None:
        """
        Method called when an alarm fires.

        Executes any scheduled tasks that are due.
        """
        if (
            alarm_info is not None
            and alarm_info.isRetry
            and alarm_info.retryCount > MAX_ALARM_RETRIES
        ):
            rows = self._sql("SELECT COUNT(*) as count FROM container_schedules")
            schedule_count = int(rows[0]["count"] or 0) if rows else 0
            has_scheduled_tasks = schedule_count > 0
            if has_scheduled_tasks or self._container.running:
                await self.schedule_next_alarm()
            return

        # do not remove this, container DOs ALWAYS need an alarm right now.
        # The only way for this DO to stop having alarms is:
        #  1. The container is not running anymore.
        #  2. Activity expired and it exits.
        prev_alarm = int(time.time() * 1000)
        await self.ctx.storage.setAlarm(prev_alarm)
        await self.ctx.storage.sync()

        # Get all schedules that should be executed now
        result = self._sql("SELECT * FROM container_schedules;")
        min_time: float = int(time.time() * 1000) + 3 * 60 * 1000

        now = time.time()
        # Process each due schedule
        for row in result:
            # check if we need to run it
            if row["time"] > now:
                continue

            callback = getattr(self, row["callback"], None)
            if not callable(callback):
                logger.error(
                    "Callback %s not found or is not a function", row["callback"]
                )
                continue

            # Create a schedule object for context
            schedule = await self.get_schedule(row["id"])

            try:
                # Parse the payload and execute the callback
                payload = json.loads(row["payload"]) if row["payload"] else None

                await callback(payload, schedule)
            except Exception:
                logger.exception(
                    'Error executing scheduled callback "%s"', row["callback"]
                )

            # Delete the schedule after execution (one-time schedules)
            self._sql("DELETE FROM container_schedules WHERE id = ?", row["id"])

        result_for_min_time = self._sql("SELECT * FROM container_schedules;")
        min_time_from_schedules = min(
            (row["time"] * 1000 for row in result_for_min_time), default=math.inf
        )

        # if not running and nothing to do, stop
        if not self._container.running:
            await self._sync_pending_stopped_events()

            if len(result_for_min_time) == 0:
                await self.ctx.storage.deleteAlarm()
            else:
                await self.ctx.storage.setAlarm(min_time_from_schedules)

            return

        if self._is_activity_expired():
            await self.on_activity_expired()
            # renew_activity_timeout makes sure we don't spam calls here
            self.renew_activity_timeout()
            return

        # min(3m or maxTime, sleepTimeout)
        min_time = min(min_time_from_schedules, min_time, self._sleep_after_ms)
        timeout = max(0, min_time - int(time.time() * 1000))

        # await a sleep for maxTime to keep the DO alive for
        # at least this long
        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()

        def resolve() -> None:
            if not done.done():
                done.set_result(None)

        self._resolve = resolve
        if not self._container.running:
            resolve()
        else:
            self._timeout = loop.call_later(timeout / 1000, resolve)

        await done

        await self.ctx.storage.setAlarm(int(time.time() * 1000))

        # we exit and we have another alarm,
        # the next alarm is the one that decides if it should stop the loop.

    async def _sync_pending_stopped_events(self) -> None:
        """Synchronise container state with the container source of truth."""
        state = await self._state.get_state()
        if not self._container.running and state["status"] in ("healthy", "running"):
            await self._call_on_stop(exit_code=0, reason="exit", state=state)
            return

        if not self._container.running and state["status"] == "stopped_with_code":
            await self._call_on_stop(
                exit_code=state.get("exit_code", 0), reason="exit", state=state
            )
            return

    async def _call_on_stop(
        self,
        *,
        exit_code: int,
        reason: Literal["exit", "runtime_signal"],
        state: State,
    ) -> None:
        if self._on_stop_called:
            return

        self._on_stop_called = True
        try:
            await self.on_stop(exit_code=exit_code, reason=reason)
        finally:
            self._on_stop_called = False

        await self._state.set_stopped_if_unchanged(state)

    async def schedule_next_alarm(self, ms: int = 1000) -> None:
        """
        Schedule the next alarm based on upcoming tasks.

        Parameters
        ----------
        ms
            Delay until the next alarm, in milliseconds.
        """
        next_time = ms + int(time.time() * 1000)

        # if not already set
        if self._timeout is not None:
            if self._resolve is not None:
                self._resolve()
            self._timeout.cancel()

        await self.ctx.storage.setAlarm(next_time)
        await self.ctx.storage.sync()

    async def list_schedules(self, name: str) -> list[Schedule[PayloadT]]:
        """
        List scheduled tasks for a callback.

        Parameters
        ----------
        name
            The callback method name.

        Returns
        -------
        list of Schedule
            The scheduled tasks.
        """
        result = self._sql(
            "SELECT * FROM container_schedules WHERE callback = ? LIMIT 1", name
        )

        if not result:
            return []

        return [self._to_schedule(row) for row in result]

    def _to_schedule(self, schedule: dict[str, Any]) -> Schedule[Any]:
        payload: Any
        try:
            payload = (
                json.loads(schedule["payload"])
                if schedule["payload"] is not None
                else None
            )
        except ValueError:
            logger.exception("Error parsing payload for schedule %s", schedule["id"])
            payload = None

        if schedule["type"] == "delayed":
            return {
                "task_id": schedule["id"],
                "callback": schedule["callback"],
                "payload": payload,
                "type": "delayed",
                "time": schedule["time"],
                "delay_in_seconds": schedule["delayInSeconds"],
            }

        return {
            "task_id": schedule["id"],
            "callback": schedule["callback"],
            "payload": payload,
            "type": "scheduled",
            "time": schedule["time"],
        }

    async def get_schedule(self, id: str) -> Schedule[PayloadT] | None:
        """
        Get a scheduled task by ID.

        Parameters
        ----------
        id
            ID of the scheduled task.

        Returns
        -------
        Schedule or None
            The scheduled task, or None if not found.
        """
        result = self._sql("SELECT * FROM container_schedules WHERE id = ? LIMIT 1", id)

        if not result:
            return None

        return self._to_schedule(result[0])

    def _is_activity_expired(self) -> bool:
        if self._inflight_requests > 0:
            self.renew_activity_timeout()
            return False

        return self._sleep_after_ms <= int(time.time() * 1000)
