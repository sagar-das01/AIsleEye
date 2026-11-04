"""Optional Langfuse tracing. Everything here is a no-op unless enabled, and nothing in it may fail a scan or an agent run."""

import logging
import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_client: Any = None

_DATA_URL = re.compile(r"data:image/[^\s\"')]*", re.I)
_BLOB = re.compile(r"[A-Za-z0-9+/_=-]{200,}")
IMAGE_REMOVED = "[image removed]"
BLOB_REMOVED = "[blob removed]"


def mask(value: Any) -> Any:
    """Return a copy of the value without image data: data URLs, long base64 runs and raw bytes."""
    if isinstance(value, str):
        return _BLOB.sub(BLOB_REMOVED, _DATA_URL.sub(IMAGE_REMOVED, value))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"[{len(value)} bytes removed]"
    if isinstance(value, dict):
        return {k: mask(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [mask(v) for v in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return mask(dump())
    return value


def _mask_for_langfuse(*, data: Any, **_: Any) -> Any:
    # Backstop only: Langfuse turns data URLs into media uploads before it calls this, so our own spans mask first.
    return mask(data)


def _mask_otel_spans(*, params: Any) -> Any:
    # Catches spans written by instrumentation we do not control, such as the LangChain callback handler.
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span_data in params.spans.items():
        changes = {}
        for key, value in span_data.attributes.items():
            if isinstance(value, str):
                masked = mask(value)
            elif isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
                masked = [mask(v) for v in value]
            else:
                continue
            if masked != value:
                changes[key] = masked
        if changes:
            patches[identifier] = OtelSpanPatch(set_attributes=changes)
    return MaskOtelSpansResult(span_patches=patches)


def init() -> bool:
    """Start the Langfuse client when enabled and both keys are set. Returns False (never raises) otherwise."""
    global _client
    _client = None
    try:
        from app.config import Settings

        settings = Settings()
        secret = settings.langfuse_secret_key.get_secret_value()
        if not (settings.langfuse_enabled and settings.langfuse_public_key and secret):
            return False
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=secret,
            base_url=settings.langfuse_base_url,
            mask=_mask_for_langfuse,
            mask_otel_spans=_mask_otel_spans,
            timeout=5,
        )
        return True
    except Exception as exc:
        logger.warning("Langfuse tracing disabled (%s)", type(exc).__name__)
        _client = None
        return False


def enabled() -> bool:
    return _client is not None


def trace_id_for(scan_id: str) -> str:
    """One trace per scan: the scan, its review and the agent run share this id."""
    try:
        from langfuse import Langfuse

        return Langfuse.create_trace_id(seed=scan_id)
    except Exception:
        import hashlib

        return hashlib.sha256(scan_id.encode()).hexdigest()[:32]


def _open(stack: ExitStack, name: str, attrs: dict) -> Any:
    attrs = dict(attrs)
    scan_id = attrs.pop("scan_id", None)
    as_type = attrs.pop("as_type", "span")
    model = attrs.pop("model", None)
    input_ = attrs.pop("input", None)
    kwargs: dict[str, Any] = {"name": name, "as_type": as_type, "metadata": mask(attrs) or None}
    if input_ is not None:
        kwargs["input"] = mask(input_)
    if model:
        kwargs["model"] = model
    if scan_id:
        from langfuse import propagate_attributes

        kwargs["trace_context"] = {"trace_id": trace_id_for(scan_id)}
        stack.enter_context(propagate_attributes(session_id=scan_id))
    return stack.enter_context(_client.start_as_current_observation(**kwargs))


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    """Trace a block. Pass scan_id on the outermost span of a request to join the scan's trace.

    Tracing errors are swallowed; an exception raised by the traced block itself is not.
    """
    stack = ExitStack()
    observation = None
    if _client is not None:
        try:
            observation = _open(stack, name, attrs)
        except Exception as exc:
            logger.debug("Could not start span %s (%s)", name, type(exc).__name__)
            _close(stack)
            stack = ExitStack()
    try:
        yield observation
    except BaseException as exc:
        _close(stack, exc)
        raise
    else:
        _close(stack)


def _close(stack: ExitStack, exc: BaseException | None = None) -> None:
    try:
        if exc is None:
            stack.close()
        else:
            stack.__exit__(type(exc), exc, exc.__traceback__)
    except Exception as err:
        logger.debug("Could not close span (%s)", type(err).__name__)


def update(observation: Any, **fields: Any) -> None:
    """Add output, metadata or a level to a span returned by span()."""
    if observation is None:
        return
    try:
        observation.update(**{key: mask(value) for key, value in fields.items()})
    except Exception as exc:
        logger.debug("Could not update span (%s)", type(exc).__name__)


def langchain_callbacks() -> list:
    """LangChain callback handlers for the agent run, attached to the current span."""
    if _client is None:
        return []
    try:
        from langfuse.langchain import CallbackHandler

        return [CallbackHandler()]
    except Exception as exc:
        logger.debug("No LangChain callback handler (%s)", type(exc).__name__)
        return []


def create_score(scan_id: str, name: str, value: float | str, score_id: str, comment: str | None = None) -> None:
    """Mirror a score onto the scan's trace. A stable score_id makes a re-sent score overwrite the old one."""
    if _client is None:
        return
    try:
        _client.create_score(
            trace_id=trace_id_for(scan_id),
            name=name,
            value=value,
            score_id=score_id,
            data_type="TEXT" if isinstance(value, str) else "NUMERIC",
            comment=mask(comment) if comment else None,
        )
    except Exception as exc:
        logger.debug("Could not mirror score %s (%s)", name, type(exc).__name__)


def shutdown() -> None:
    global _client
    client, _client = _client, None
    if client is None:
        return
    try:
        client.shutdown()
    except Exception as exc:
        logger.debug("Langfuse shutdown failed (%s)", type(exc).__name__)
