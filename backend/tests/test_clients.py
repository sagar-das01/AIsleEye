import base64

import pytest

from app.clients import bytes_to_data_url, file_to_data_url, get_async_client, get_client


def test_bytes_to_data_url_round_trips():
    url = bytes_to_data_url(b"\x89PNG-bytes", "image/png")
    prefix, payload = url.split(",", 1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(payload) == b"\x89PNG-bytes"


@pytest.mark.parametrize(
    ("name", "mime"),
    [("a.jpg", "image/jpeg"), ("a.JPEG", "image/jpeg"), ("a.png", "image/png"), ("a.webp", "image/webp")],
)
def test_file_to_data_url_detects_mime(tmp_path, name, mime):
    path = tmp_path / name
    path.write_bytes(b"img")
    assert file_to_data_url(path).startswith(f"data:{mime};base64,")


def test_file_to_data_url_rejects_unsupported_type(tmp_path):
    path = tmp_path / "doc.gif"
    path.write_bytes(b"gif")
    with pytest.raises(ValueError, match="Unsupported image type"):
        file_to_data_url(path)


def test_clients_use_configured_endpoint_and_are_cached():
    client = get_client()
    assert str(client.base_url).rstrip("/") == "https://api.meta.ai/v1"
    assert client.api_key == "test-key"
    assert get_client() is client
    assert get_async_client() is get_async_client()
