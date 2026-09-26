import asyncio
import json
import time
from http import HTTPMethod
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest
from test_fixtures import MockWebSocket, ObjectContaining, wait_for
from workers import Request, Response

from containers import Container, get_random

STATE_KEY = "__CF_CONTAINER_STATE"


def _failed(message: str) -> asyncio.Future[object]:
    future = asyncio.get_running_loop().create_future()
    future.set_exception(Exception(message))
    return future


class TestContainer:
    async def test_should_initialize_with_default_values(self, container):
        assert container.default_port == 8080
        assert container.sleep_after == "10m"

    async def test_should_use_configured_constructor_startup_options(self, mock_ctx):
        container = Container(
            mock_ctx,
            {},
            default_port=8080,
            env_vars={"MESSAGE": "configured"},
            entrypoint=["node", "server.js"],
            enable_internet=False,
        )

        await container.start_and_wait_for_ports()

        mock_ctx.container.start.assert_called_with(
            {
                "enableInternet": False,
                "env": {"MESSAGE": "configured"},
                "entrypoint": ["node", "server.js"],
            }
        )

    async def test_start_and_wait_for_ports_should_start_container_if_not_running_single_port(
        self, mock_ctx, container
    ):
        await container.start_and_wait_for_ports(8080)

        mock_ctx.container.start.assert_called()
        mock_ctx.container.getTcpPort.assert_any_call(8080)

    async def test_start_and_wait_for_ports_should_check_multiple_ports_if_provided(
        self, mock_ctx, container
    ):
        await container.start_and_wait_for_ports([8080, 9090])

        mock_ctx.container.start.assert_called()
        mock_ctx.container.getTcpPort.assert_any_call(8080)
        mock_ctx.container.getTcpPort.assert_any_call(9090)

    async def test_start_and_wait_for_ports_should_use_required_ports_if_defined_and_no_ports_specified(
        self, mock_ctx, container
    ):
        container.required_ports = [3000, 4000]

        await container.start_and_wait_for_ports()

        mock_ctx.container.start.assert_called()
        mock_ctx.container.getTcpPort.assert_any_call(3000)
        mock_ctx.container.getTcpPort.assert_any_call(4000)

    async def test_start_and_wait_for_ports_should_use_default_port_if_no_ports_specified_and_no_required_ports(
        self, mock_ctx, container
    ):
        await container.start_and_wait_for_ports()

        mock_ctx.container.start.assert_called()
        mock_ctx.container.getTcpPort.assert_any_call(8080)

    async def test_start_and_wait_for_ports_should_surface_rate_limited_startup_errors_on_the_final_retry(
        self, mock_ctx, container
    ):
        async def rethrow(error):
            raise error

        mock_ctx.container.monitor.side_effect = None
        mock_ctx.container.monitor.return_value = _failed(
            "you are requesting too many containers per second"
        )
        mock_ctx.container.getTcpPort.return_value = SimpleNamespace(
            fetch=AsyncMock(side_effect=Exception("unexpected startup failure"))
        )

        with patch.object(container, "on_error", side_effect=rethrow) as on_error_spy:
            with pytest.raises(
                Exception, match="you are requesting too many containers per second"
            ):
                await container.start_and_wait_for_ports(
                    8080, instance_get_timeout_ms=1, wait_interval=1
                )
            on_error_spy.assert_called()

        mock_ctx.storage.put.assert_any_call(
            STATE_KEY, ObjectContaining(status="stopped")
        )

    async def test_start_and_wait_for_ports_should_abort_the_durable_object_on_final_network_loss(
        self, mock_ctx, container
    ):
        mock_ctx.container.monitor.side_effect = None
        mock_ctx.container.monitor.return_value = _failed(
            "there is no container instance that can be provided to this durable object"
        )
        mock_ctx.container.getTcpPort.return_value = SimpleNamespace(
            fetch=AsyncMock(side_effect=Exception("Network connection lost"))
        )

        with pytest.raises(
            Exception,
            match="there is no container instance that can be provided to this "
            "durable object",
        ):
            await container.start_and_wait_for_ports(
                8080, instance_get_timeout_ms=1, wait_interval=1
            )

        mock_ctx.abort.assert_called()
        mock_ctx.storage.put.assert_any_call(
            STATE_KEY, ObjectContaining(status="stopped")
        )

    async def test_monitor_should_clear_running_state_when_an_instance_becomes_unavailable(
        self, mock_ctx, container
    ):
        monitor = asyncio.get_running_loop().create_future()
        mock_ctx.container.monitor.side_effect = None
        mock_ctx.container.monitor.return_value = monitor

        await container.start(port_to_check=8080, retries=1, wait_interval=1)
        monitor.set_exception(
            Exception(
                "there is no container instance that can be provided to this durable "
                "object"
            )
        )

        await wait_for(
            lambda: mock_ctx.storage.put.assert_any_call(
                STATE_KEY, ObjectContaining(status="stopped")
            )
        )

    async def test_monitor_should_clear_running_state_before_reporting_a_terminal_error(
        self, mock_ctx, container
    ):
        monitor = asyncio.get_running_loop().create_future()
        mock_ctx.container.monitor.side_effect = None
        mock_ctx.container.monitor.return_value = monitor

        with patch.object(container, "on_error", return_value=None) as on_error_spy:
            await container.start(port_to_check=8080, retries=1, wait_interval=1)
            error = Exception("container supervisor failed")
            monitor.set_exception(error)

            def assertion():
                mock_ctx.storage.put.assert_any_call(
                    STATE_KEY, ObjectContaining(status="stopped")
                )
                on_error_spy.assert_called_with(error)

            await wait_for(assertion)

    async def test_replaced_monitor_should_not_stop_a_newer_container_instance(
        self, mock_ctx, container
    ):
        loop = asyncio.get_running_loop()
        first_monitor = loop.create_future()
        mock_ctx.container.monitor.side_effect = [first_monitor, loop.create_future()]

        await container.start(port_to_check=8080, retries=1, wait_interval=1)
        mock_ctx.container.running = False
        await container.start(port_to_check=8080, retries=1, wait_interval=1)
        first_monitor.set_result(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert (
            call(STATE_KEY, ObjectContaining(status="stopped_with_code"))
            not in mock_ctx.storage.put.call_args_list
        )

    async def test_start_and_wait_for_ports_should_fall_back_to_default_health_check_port(
        self, mock_ctx
    ):
        container_without_port = Container(mock_ctx, {})

        await container_without_port.start_and_wait_for_ports()

        mock_ctx.container.start.assert_called()
        mock_ctx.container.getTcpPort.assert_any_call(33)

    async def test_alarm_should_not_stop_a_container_while_its_start_loop_is_in_flight(
        self, mock_ctx, container
    ):
        start_blocked_before_physical_start = asyncio.Event()
        schedule_next_alarm = container.schedule_next_alarm
        calls = 0

        async def block_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                await start_blocked_before_physical_start.wait()
            else:
                await schedule_next_alarm(*args, **kwargs)

        with (
            patch.object(
                container, "schedule_next_alarm", side_effect=block_once
            ) as schedule_spy,
            patch.object(container, "on_stop") as on_stop_spy,
        ):
            start_task = asyncio.ensure_future(
                container.start(port_to_check=8080, retries=1, wait_interval=1)
            )
            await wait_for(schedule_spy.assert_called)
            mock_ctx.container.start.assert_not_called()

            await container.alarm()
            start_blocked_before_physical_start.set()
            await start_task

            on_stop_spy.assert_not_called()
        assert (await container.get_state())["status"] == "running"

    async def test_sync_pending_stopped_events_should_call_on_stop_for_stopped_container_with_running_state(
        self, mock_ctx, container
    ):
        mock_ctx.storage.get.return_value = {
            "status": "running",
            "last_change": int(time.time() * 1000),
        }
        mock_ctx.container.running = False

        with patch.object(container, "on_stop") as on_stop_spy:
            await container._sync_pending_stopped_events()

            on_stop_spy.assert_called_with(exit_code=0, reason="exit")
        mock_ctx.storage.put.assert_any_call(
            STATE_KEY, ObjectContaining(status="stopped")
        )

    async def test_on_stop_starting_a_new_container_should_not_overwrite_its_running_state(
        self, mock_ctx, container
    ):
        mock_ctx.storage.get.return_value = {
            "status": "running",
            "last_change": int(time.time() * 1000),
        }
        mock_ctx.container.running = False

        async def start_again(**kwargs):
            await container.start(port_to_check=8080, retries=1, wait_interval=1)

        with patch.object(container, "on_stop", side_effect=start_again):
            await container._sync_pending_stopped_events()

        assert mock_ctx.container.running is True
        assert (await container.get_state())["status"] == "running"

    async def test_container_fetch_should_forward_requests_to_container(
        self, mock_ctx, container
    ):
        mock_request = Request(
            "https://example.com/test?query=value",
            method=HTTPMethod.GET,
            headers={"Content-Type": "application/json"},
        )

        mock_ctx.container.running = True
        mock_ctx.storage.get.return_value = {
            "status": "healthy",
            "last_change": int(time.time() * 1000),
        }

        await container.container_fetch(mock_request)

        tcp_port = mock_ctx.container.getTcpPort.return_value
        tcp_port.fetch.assert_called()

        # Just make sure that tcp_port.fetch was called - the exact URL is tested
        # in the container.py implementation
        tcp_port.fetch.assert_called_with(ANY, ANY)
        assert isinstance(tcp_port.fetch.call_args[0][0], str)

    async def test_container_fetch_should_accept_a_relative_url(
        self, mock_ctx, container
    ):
        mock_ctx.container.running = True
        mock_ctx.storage.get.return_value = {
            "status": "healthy",
            "last_change": int(time.time() * 1000),
        }

        await container.container_fetch("/api/data")

        tcp_port = mock_ctx.container.getTcpPort.return_value
        tcp_port.fetch.assert_called_with("http://container/api/data", ANY)
        assert isinstance(tcp_port.fetch.call_args[0][1], Request)

    async def test_container_fetch_should_accept_a_relative_url_with_init_and_an_explicit_port(
        self, mock_ctx, container
    ):
        mock_ctx.container.running = True
        mock_ctx.storage.get.return_value = {
            "status": "healthy",
            "last_change": int(time.time() * 1000),
        }

        await container.container_fetch(
            "/api/data?query=value",
            9090,
            method=HTTPMethod.POST,
            body=json.dumps({"query": "example"}),
        )

        mock_ctx.container.getTcpPort.assert_called_with(9090)

        tcp_port = mock_ctx.container.getTcpPort.return_value
        tcp_port.fetch.assert_called_with("http://container/api/data?query=value", ANY)

        forwarded = tcp_port.fetch.call_args[0][1]
        assert forwarded.method == "POST"
        assert await forwarded.text() == json.dumps({"query": "example"})

    async def test_container_fetch_should_leave_absolute_urls_untouched(
        self, mock_ctx, container
    ):
        mock_ctx.container.running = True
        mock_ctx.storage.get.return_value = {
            "status": "healthy",
            "last_change": int(time.time() * 1000),
        }

        await container.container_fetch(
            "https://example.com/admin", 3000, method=HTTPMethod.GET
        )

        mock_ctx.container.getTcpPort.assert_called_with(3000)

        tcp_port = mock_ctx.container.getTcpPort.return_value
        tcp_port.fetch.assert_called_with("http://example.com/admin", ANY)

    async def test_container_fetch_should_preserve_https_in_query_strings_and_fragments(
        self, mock_ctx, container
    ):
        mock_ctx.container.running = True
        mock_ctx.storage.get.return_value = {
            "status": "healthy",
            "last_change": int(time.time() * 1000),
        }

        await container.container_fetch(
            "/callback?redirect=https://app.example.com#https://fragment"
        )

        tcp_port = mock_ctx.container.getTcpPort.return_value
        tcp_port.fetch.assert_called_with(
            "http://container/callback?redirect=https://app.example.com#https://fragment",
            ANY,
        )

    async def test_container_fetch_should_downgrade_only_the_scheme_of_an_https_url(
        self, mock_ctx, container
    ):
        mock_ctx.container.running = True
        mock_ctx.storage.get.return_value = {
            "status": "healthy",
            "last_change": int(time.time() * 1000),
        }

        await container.container_fetch(
            "https://example.com/callback?redirect=https://app.example.com",
            3000,
            method=HTTPMethod.GET,
        )

        tcp_port = mock_ctx.container.getTcpPort.return_value
        tcp_port.fetch.assert_called_with(
            "http://example.com/callback?redirect=https://app.example.com", ANY
        )

    async def test_container_fetch_should_return_429_when_startup_is_rate_limited(
        self, container
    ):
        mock_request = Request("https://example.com/test", method=HTTPMethod.GET)
        with patch.object(
            container,
            "start_and_wait_for_ports",
            side_effect=Exception("you are requesting too many containers per second"),
        ) as start_spy:
            response = await container.container_fetch(mock_request)

            start_spy.assert_called_with(8080, abort=ANY)
            assert isinstance(start_spy.call_args.kwargs["abort"], asyncio.Event)
        assert response.status == 429
        assert (
            await response.text() == "you are requesting too many containers per second"
        )

    async def test_container_fetch_should_throw_error_when_no_port_is_specified(
        self, mock_ctx
    ):
        mock_request = Request("https://example.com/test", method=HTTPMethod.GET)

        mock_ctx.container.running = True
        mock_ctx.storage.get.return_value = {
            "status": "healthy",
            "last_change": int(time.time() * 1000),
        }

        container_without_port = Container(mock_ctx, {})
        container_without_port.default_port = None

        with pytest.raises(ValueError, match="No port specified for container fetch"):
            await container_without_port.container_fetch(mock_request)

    async def test_stop_should_signal_container_if_running(self, mock_ctx, container):
        mock_ctx.container.running = True

        await container.stop("SIGTERM")

        mock_ctx.container.signal.assert_called_with(15)

    async def test_renew_activity_timeout_should_update_the_activity_deadline(
        self, container
    ):
        before = int(time.time() * 1000)

        container.renew_activity_timeout()

        assert container._sleep_after_ms > before

    async def test_should_renew_activity_timeout_on_fetch(self, mock_ctx, container):
        with patch.object(
            container, "renew_activity_timeout", wraps=container.renew_activity_timeout
        ) as renew_spy:
            mock_request = Request("https://example.com/test")

            mock_ctx.container.running = True
            mock_ctx.storage.get.return_value = {
                "status": "healthy",
                "last_change": int(time.time() * 1000),
            }

            await container.fetch(mock_request)

            renew_spy.assert_called()

    async def test_container_fetch_should_create_a_web_socket_connection_when_requested(
        self, mock_ctx, container, web_socket_pair_spy
    ):
        mock_request = Request(
            "https://example.com/ws",
            headers={"Upgrade": "websocket", "Connection": "Upgrade"},
        )

        mock_ctx.container.running = True
        mock_ctx.storage.get.return_value = {
            "status": "healthy",
            "last_change": int(time.time() * 1000),
        }

        response = await container.container_fetch(mock_request)

        tcp_port = mock_ctx.container.getTcpPort.return_value
        tcp_port.fetch.assert_called()

        forwarded_request = tcp_port.fetch.call_args[0][1]
        assert forwarded_request.headers.get("Upgrade") == "websocket"

        # The WebSocket branch must have been taken. If `WebSocketPair` is missing
        # container_fetch catches the error and returns a 500 — these assertions
        # guard against that silent regression.
        web_socket_pair_spy.assert_called_once()

        # Container-side WebSocket from the tcp_port response must be accepted
        # and wired up with message/close/error handlers.
        container_ws: MockWebSocket = tcp_port.fetch.results[0].webSocket
        container_ws.accept.assert_called_once()
        assert len(container_ws.event_listeners["message"]) == 1
        assert len(container_ws.event_listeners["close"]) == 1
        assert len(container_ws.event_listeners["error"]) == 1

        # The response must carry the upstream status (200 in our mock) and the
        # headers from the tcp_port response.
        assert response.status == 200

    async def test_fetch_should_detect_web_socket_requests_and_forward_them_correctly(
        self, mock_ctx, container
    ):
        with patch.object(
            container, "container_fetch", wraps=container.container_fetch
        ) as proxy_spy:
            mock_request = Request(
                "https://example.com/ws",
                headers={"Upgrade": "websocket", "Connection": "Upgrade"},
            )

            mock_ctx.container.running = True
            mock_ctx.storage.get.return_value = {
                "status": "healthy",
                "last_change": int(time.time() * 1000),
            }

            await container.fetch(mock_request)

            proxy_spy.assert_called_with(mock_request, container.default_port)


class TestHttpsInterceptionRuntimeGuard:
    # Simulates a runtime older than the one that introduced
    # ctx.container.interceptOutboundHttps (workerd ~2026-04-03).
    @staticmethod
    def remove_https_support(mock_ctx: MagicMock) -> None:
        del mock_ctx.container.interceptOutboundHttps

    async def test_throws_an_actionable_error_when_intercept_https_is_enabled(
        self, mock_ctx, container
    ):
        self.remove_https_support(mock_ctx)
        container.intercept_https = True

        with pytest.raises(
            RuntimeError,
            match=r"ctx\.container\.interceptOutboundHttps is not available in this "
            r"runtime",
        ):
            await container.set_allowed_hosts(["example.com"])

    # Without the up-front check, the per-host loop applies HTTP interception for a
    # static host and only then hits the missing HTTPS method, leaving the
    # container partially intercepted.
    async def test_leaves_no_partial_interception_applied_when_the_guard_trips(
        self, mock_ctx
    ):
        async def handler(request, env, ctx):
            return Response("ok")

        class PartialInterceptionContainer(Container):
            intercept_https = True

        PartialInterceptionContainer.outbound_by_host = {"api.example.com": handler}

        container = PartialInterceptionContainer(mock_ctx, {})
        self.remove_https_support(mock_ctx)

        with pytest.raises(RuntimeError):
            await container.set_allowed_hosts(["example.com"])

        mock_ctx.container.interceptOutboundHttp.assert_not_called()
        mock_ctx.container.interceptAllOutboundHttp.assert_not_called()

    async def test_still_intercepts_http_when_intercept_https_is_disabled(
        self, mock_ctx, container
    ):
        self.remove_https_support(mock_ctx)

        await container.set_allowed_hosts(["example.com"])

        mock_ctx.container.interceptAllOutboundHttp.assert_called()

    async def test_applies_https_interception_when_the_runtime_supports_it(
        self, mock_ctx, container
    ):
        container.intercept_https = True

        await container.set_allowed_hosts(["example.com"])

        mock_ctx.container.interceptOutboundHttps.assert_called_with("*", ANY)


class TestGetRandom:
    async def test_should_return_a_container_stub(self):
        mock_binding = MagicMock()
        mock_binding.idFromName = MagicMock(return_value="mock-id")
        mock_binding.get = MagicMock(return_value={"mock_stub": True})

        result = await get_random(mock_binding, 5)

        mock_binding.idFromName.assert_called()
        mock_binding.get.assert_called_with("mock-id")
        assert result == {"mock_stub": True}


class TestRpcKwargs:
    # JS RPC has no keyword arguments: Pyodide sends them as a trailing object.
    async def test_start_accepts_keyword_options_sent_as_a_trailing_dict(
        self, mock_ctx, container
    ):
        await container.start(
            {
                "env_vars": {"MESSAGE": "over rpc"},
                "port_to_check": 8080,
                "retries": 1,
                "wait_interval": 1,
            }
        )

        mock_ctx.container.start.assert_called_with(
            {"enableInternet": True, "env": {"MESSAGE": "over rpc"}}
        )

    async def test_container_fetch_accepts_port_sent_as_a_trailing_dict(
        self, mock_ctx, container
    ):
        mock_ctx.container.running = True
        mock_ctx.storage.get.return_value = {
            "status": "healthy",
            "last_change": int(time.time() * 1000),
        }

        await container.container_fetch(
            Request("https://example.com/test"), {"port": 9090}
        )

        mock_ctx.container.getTcpPort.assert_called_with(9090)
