import importlib

import pytest


def need(module: str, *names: str):
    """Skip the whole test module until a planned module, and the public names it must define, exist."""
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        # Only skip when the planned module itself is missing; a missing third-party dependency must fail loudly.
        if exc.name and (module == exc.name or module.startswith(exc.name + ".")):
            pytest.skip(f"{module} is not implemented yet", allow_module_level=True)
        raise
    missing = [n for n in names if not hasattr(mod, n)]
    if missing:
        pytest.skip(f"{module} does not define {', '.join(missing)} yet", allow_module_level=True)
    return mod


def has_field(model, name: str) -> bool:
    return name in getattr(model, "model_fields", {})
