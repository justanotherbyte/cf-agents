import asyncio

from workers import Request


class TestContainerConcurrentStartRace:
    async def test_two_concurrent_container_fetch_calls_do_not_both_invoke_start(
        self, mock_ctx, container
    ):
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

        res_a, res_b = await asyncio.gather(
            container.container_fetch(req_a),
            container.container_fetch(req_b),
        )

        start_call_count = mock_ctx.container.start.call_count
        bodies = [await res.text() for res in (res_a, res_b)]

        print(
            "[repro] start call count:",
            start_call_count,
            "statuses:",
            res_a.status,
            res_b.status,
            "bodies:",
            bodies,
        )

        assert start_call_count == 1

        for body in bodies:
            assert (
                "start() cannot be called on a container that is already running."
                not in (body or "")
            )


class TestSharedStartCancellation:
    async def test_cancelling_one_caller_does_not_cancel_the_shared_start(
        self, mock_ctx, container
    ):
        release = asyncio.Event()

        async def fetch(*args):
            await release.wait()

        mock_ctx.container.getTcpPort.return_value.fetch.side_effect = fetch

        first = asyncio.ensure_future(container.start_and_wait_for_ports(8080))
        second = asyncio.ensure_future(container.start_and_wait_for_ports(8080))
        await asyncio.sleep(0.01)

        first.cancel()
        await asyncio.sleep(0)
        release.set()

        await second
        assert first.cancelled()
        assert mock_ctx.container.start.call_count == 1
