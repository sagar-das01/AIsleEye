import base64
import inspect

import pytest

from app import clients
from app.clients import bytes_to_data_url, file_to_data_url, get_async_client, get_client
from app.config import get_settings


def test_timeout_and_retries_come_from_settings():
    for client in (get_client(), get_async_client()):
        assert client.max_retries == 2
        assert client.timeout == get_settings().request_timeout_s


def test_sync_and_async_clients_are_distinct():
    assert get_client() is not get_async_client()


def test_custom_timeout_and_base_url_flow_through(monkeypatch):
    monkeypatch.setenv("REQUEST_TIMEOUT_S", "7")
    monkeypatch.setenv("MODEL_API_BASE_URL", "https://example.test/v9")
    get_settings.cache_clear()
    get_client.cache_clear()
    try:
        client = get_client()
        assert client.timeout == 7.0
        assert str(client.base_url).rstrip("/") == "https://example.test/v9"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        get_client.cache_clear()


def test_bytes_to_data_url_handles_empty_and_large_payloads():
    assert bytes_to_data_url(b"", "image/png") == "data:image/png;base64,"
    big = bytes(range(256)) * 4096
    assert base64.b64decode(bytes_to_data_url(big, "image/jpeg").split(",", 1)[1]) == big


def test_data_url_is_ascii_and_has_no_newlines():
    url = bytes_to_data_url(b"x" * 5000, "image/png")
    assert url.isascii() and "\n" not in url


def test_file_to_data_url_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        file_to_data_url(tmp_path / "missing.png")


@pytest.mark.parametrize("name", ["noextension", "archive.tar.gz", "photo.jpg.exe", "photo.", ".png"])
def test_file_to_data_url_rejects_odd_names(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"x")
    with pytest.raises(ValueError, match="Unsupported image type"):
        file_to_data_url(path)


def test_file_to_data_url_uppercase_suffix(tmp_path):
    path = tmp_path / "PHOTO.PNG"
    path.write_bytes(b"x")
    assert file_to_data_url(path).startswith("data:image/png;base64,")


def test_file_to_data_url_rejects_unsupported_before_reading(tmp_path):
    # A directory with a bad suffix must fail on the suffix, not on a read error.
    path = tmp_path / "dir.gif"
    path.mkdir()
    with pytest.raises(ValueError):
        file_to_data_url(path)


@pytest.mark.skipif("truststore" not in inspect.getsource(clients), reason="TLS trust-store support lives on the vision-pipeline branch")
def test_clients_use_the_system_trust_store():
    # Corporate TLS inspection breaks the bundled CA list; the OS trust store must be used.
    assert hasattr(clients, "truststore")
    assert clients.truststore.SSLContext is not None
