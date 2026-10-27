import pytest

from workers import asgi, wsgi
from workers._adapter import body_encoding


@pytest.mark.parametrize("adapter", [asgi, wsgi])
def test_options_validate_encode_body(adapter) -> None:
    assert adapter.Options().response_init() == {}
    assert adapter.Options(encode_body="manual").response_init() == {
        "encodeBody": "manual"
    }
    with pytest.raises(ValueError, match="encode_body"):
        adapter.Options(encode_body="gzip")


def test_explicit_options_override_content_encoding_detection() -> None:
    gzipped = [("Content-Encoding", "gzip")]
    assert body_encoding(gzipped, None) == {"encodeBody": "manual"}
    assert body_encoding([], None) == {}
    assert body_encoding(gzipped, asgi.Options()) == {}
    assert body_encoding([], wsgi.Options(encode_body="manual")) == {
        "encodeBody": "manual"
    }
