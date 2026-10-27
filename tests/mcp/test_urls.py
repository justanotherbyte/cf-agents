import pytest

from agents.mcp.urls import is_blocked_url, normalize_server_id


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("my-supplied-id", "my-supplied-id"),
        ("GitHub MCP!", "github-mcp"),
        ("42-things", "id-42-things"),
        ("", "id"),
        ("__x__", "x"),
        ("a" * 70, "a" * 64),
        ("a" * 63 + "-b", "a" * 63),
    ],
)
def test_normalize_server_id(value: str, expected: str) -> None:
    assert normalize_server_id(value) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/mcp",
        "http://172.16.5.4/",
        "http://172.31.255.255/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest",
        "http://0.0.0.0/",
        "http://[::]/",
        "http://metadata.google.internal/",
        "http://[fc00::1]/",
        "http://[fd12::1]/",
        "http://[fe80::1]/",
        "http://[febf::1]/",
        "http://[::ffff:10.0.0.1]/",
        "http://[::ffff:a00:1]/",
        "http://167772161/",  # 10.0.0.1 as one number
        "http://0xa.1/",
        "http://012.0.0.1/",  # octal 10
        "ftp://example.com/",
        "not a url",
        "http://[zz::1]/",
    ],
)
def test_blocked(url: str) -> None:
    assert is_blocked_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://mcp.example.com/mcp",
        "http://localhost:8787/mcp",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://172.32.0.1/",
        "http://[fec0::1]/",
        "http://8.8.8.8/",
        "https://10.example.com/",
    ],
)
def test_allowed(url: str) -> None:
    assert not is_blocked_url(url)
