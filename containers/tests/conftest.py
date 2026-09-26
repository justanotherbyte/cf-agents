from test_setup import install

install()

from test_fixtures import (  # noqa: E402, F401
    container,
    mock_ctx,
    web_socket_pair_spy,
)
