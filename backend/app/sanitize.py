import re

_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_TAG = re.compile(r"<[^>]*>?")
_URL = re.compile(r"(?:\b(?:https?|ftp|javascript|data|vbscript):\S*|\bwww\.\S+)", re.I)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def sanitize_text(text: str, max_len: int) -> str:
    """Make model-written text safe for a PDF, log or UI: no markup, links or control characters, length-capped."""
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _TAG.sub(" ", text)
    text = text.replace("<", " ").replace(">", " ")
    text = _URL.sub(" ", text)
    text = _CONTROL.sub(" ", text)
    text = " ".join(text.split())
    return text[:max_len].rstrip()
