import asyncio

import httpx
import openai
import pytest
from openai import _base_client

from app.clients import get_async_client, get_client

OK_BODY = {
    "id": "resp_1",
    "object": "response",
    "created_at": 0,
    "status": "completed",
    "model": "m",
    "output": [],
    "parallel_tool_calls": False,
    "tool_choice": "auto",
    "tools": [],
}


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(_base_client.BaseClient, "_calculate_retry_timeout", lambda *a, **k: 0)


def scripted(statuses):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        status = statuses[min(len(calls) - 1, len(statuses) - 1)]
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, json=OK_BODY if status == 200 else {"error": {"message": "nope", "type": "x"}})

    return calls, handler


def sync_client(handler):
    return get_client().copy(http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def async_client(handler):
    return get_async_client().copy(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("transient", [429, 500, 502, 503, 504])
def test_transient_http_errors_are_retried_then_succeed(transient):
    calls, handler = scripted([transient, transient, 200])
    sync_client(handler).responses.create(model="m", input="hi")
    assert len(calls) == 3


def test_gives_up_after_two_retries():
    calls, handler = scripted([503])
    with pytest.raises(openai.InternalServerError):
        sync_client(handler).responses.create(model="m", input="hi")
    assert len(calls) == 3  # first attempt + max_retries=2


@pytest.mark.parametrize("permanent", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(permanent):
    calls, handler = scripted([permanent])
    with pytest.raises(openai.APIStatusError):
        sync_client(handler).responses.create(model="m", input="hi")
    assert len(calls) == 1


def test_connection_failures_are_retried_and_then_reported():
    calls, handler = scripted([httpx.ConnectError("refused")])
    with pytest.raises(openai.APIConnectionError):
        sync_client(handler).responses.create(model="m", input="hi")
    assert len(calls) == 3


def test_timeouts_are_retried_and_then_reported_as_timeouts():
    calls, handler = scripted([httpx.ReadTimeout("slow")])
    with pytest.raises(openai.APITimeoutError):
        sync_client(handler).responses.create(model="m", input="hi")
    assert len(calls) == 3


def test_async_client_has_the_same_retry_policy():
    calls, handler = scripted([429, 200])
    asyncio.run(async_client(handler).responses.create(model="m", input="hi"))
    assert len(calls) == 2

    calls, handler = scripted([401])
    with pytest.raises(openai.AuthenticationError):
        asyncio.run(async_client(handler).responses.create(model="m", input="hi"))
    assert len(calls) == 1


def test_requests_carry_the_bearer_key_and_target_the_configured_endpoint():
    calls, handler = scripted([200])
    sync_client(handler).responses.create(model="m", input="hi")
    request = calls[0]
    assert request.headers["authorization"] == "Bearer test-key"
    assert str(request.url) == "https://api.meta.ai/v1/responses"


def test_error_messages_raised_to_callers_do_not_include_the_api_key():
    _, handler = scripted([401])
    with pytest.raises(openai.AuthenticationError) as exc:
        sync_client(handler).responses.create(model="m", input="hi")
    assert "test-key" not in str(exc.value)
