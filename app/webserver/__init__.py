from typing import Any

__all__ = ["create_unified_app"]


def __getattr__(name: str) -> Any:
    if name == "create_unified_app":
        from .unified_app import create_unified_app as _create_unified_app

        return _create_unified_app
    raise AttributeError(name)
