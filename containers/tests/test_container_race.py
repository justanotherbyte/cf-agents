# Reproduction for https://github.com/cloudflare/containers/issues/173
#
# The error "start() cannot be called on a container that is already running."
# is thrown by workerd's Container::start() at src/workerd/api/container.c++:209
# via JSG_REQUIRE(!running, ...). It guards against the JS-visible `running`
# flag being true.
#
# The race happens because the readiness path in
# container_fetch -> start_and_wait_for_ports -> _start_container_if_not_running
# has multiple `await` points BEFORE the synchronous `self._container.start(...)`
# call. Each `await` yields the DO input gate, allowing two concurrent
# fetches to both pass the `if self._container.running: return 0` early
# exit. Whichever calls start() second hits the workerd JSG_REQUIRE.
#
# This test reproduces the race deterministically: it relies on the
# natural task scheduling that `asyncio.gather(fetch_a, fetch_b)` produces,
# with mocked storage that yields to the event loop on every operation (the
# same shape real DO storage has).
#
# Unlike JS, where every `await` yields a microtask, awaiting a Python coroutine
# that never suspends does not yield. The TS race hinges on
# `await this.refreshOutboundInterception()` between the running check and
# `start()`, so this test enables outbound interception to make that await
# really suspend.

import asyncio

from workers import Request


class TestContainerConcurrentStartRace:
    async def test_two_concurrent_container_fetch_calls_do_not_both_invoke_start(
        self, mock_ctx, container
    ):
        # Override the default `start` to mirror workerd's
        # src/workerd/api/container.c++:209 JSG_REQUIRE(!running, ...) guard:
        # calling start() while already running throws.
        def start(*args):
            if mock_ctx.container.running:
                raise RuntimeError(
                    "start() cannot be called on a container that is already running."
                )
            mock_ctx.container.running = True

        mock_ctx.container.start.side_effect = start
        container.allowed_hosts = ["example.com"]
        container._using_interception = True

        req_a = Request("https://example.com/a")
        req_b = Request("https://example.com/b")

        # Fire both concurrently. Both will:
        #   1. await self._state.get_state()  -> yield
        #   2. observe container.running == False
        #   3. enter _start_container_if_not_running
        #   4. await self.schedule_next_alarm() -> yield
        #   5. await self._refresh_outbound_interception() -> yield
        #   6. call self._container.start(...) synchronously
        #
        # Without the coalescing guard, the second caller to reach step 6 sees
        # running == True (set by the first caller) and the workerd guard throws.
        res_a, res_b = await asyncio.gather(
            container.container_fetch(req_a),
            container.container_fetch(req_b),
        )

        start_call_count = mock_ctx.container.start.call_count
        bodies = [await res.text() for res in (res_a, res_b)]

        # Helpful diagnostic dump.
        print(
            "[repro] start call count:",
            start_call_count,
            "statuses:",
            res_a.status,
            res_b.status,
            "bodies:",
            bodies,
        )

        # PRIMARY ASSERTION: container.start() must only ever be invoked once
        # when two requests race the readiness path.
        assert start_call_count == 1

        # SECONDARY ASSERTION: no response should surface the workerd
        # "already running" error string.
        for body in bodies:
            assert (
                "start() cannot be called on a container that is already running."
                not in (body or "")
            )
