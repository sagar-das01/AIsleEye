import base64
import ssl
from functools import lru_cache
from pathlib import Path

import truststore
from openai import AsyncOpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient, OpenAI

from app.config import get_settings

_MIME_BY_SUFFIX = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _tls_context() -> ssl.SSLContext:
    # OS trust store accepts corporate TLS-inspection CAs that Python 3.13's strict OpenSSL checks reject.
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


@lru_cache
def get_client() -> OpenAI:
    s = get_settings()
    return OpenAI(
        base_url=s.model_api_base_url,
        api_key=s.model_api_key,
        timeout=s.request_timeout_s,
        max_retries=2,
        http_client=DefaultHttpxClient(verify=_tls_context()),
    )


@lru_cache
def get_async_client() -> AsyncOpenAI:
    s = get_settings()
    return AsyncOpenAI(
        base_url=s.model_api_base_url,
        api_key=s.model_api_key,
        timeout=s.request_timeout_s,
        max_retries=2,
        http_client=DefaultAsyncHttpxClient(verify=_tls_context()),
    )


def bytes_to_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def file_to_data_url(path: Path) -> str:
    mime = _MIME_BY_SUFFIX.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"Unsupported image type: {path.suffix}")
    return bytes_to_data_url(path.read_bytes(), mime)
