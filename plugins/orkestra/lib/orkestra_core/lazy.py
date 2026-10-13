"""Lazy module handles: a hook process pays for an import only when the code path that needs it actually runs.

    shutil = LazyModule("shutil")                       # stdlib
    CP = LazyModule("cliparse", __package__)            # a sibling module of this package

The handle imports its module on first attribute access and forwards reads, writes and deletes to it, so call sites
(`shutil.which(...)`) and tests (`mock.patch.object(util.shutil, "which", ...)`) behave as with a real import. An import
error surfaces at the first use, exactly where an eager import would have failed, never silently."""
from __future__ import annotations

import importlib
from typing import Any, Optional


class LazyModule:
    __slots__ = ("_lazy_name", "_lazy_package", "_lazy_module")

    def __init__(self, name: str, package: Optional[str] = None):
        object.__setattr__(self, "_lazy_name", ("." + name) if package else name)
        object.__setattr__(self, "_lazy_package", package)
        object.__setattr__(self, "_lazy_module", None)

    def _lazy_load(self) -> Any:
        mod = object.__getattribute__(self, "_lazy_module")
        if mod is None:
            mod = importlib.import_module(object.__getattribute__(self, "_lazy_name"),
                                          object.__getattribute__(self, "_lazy_package"))
            object.__setattr__(self, "_lazy_module", mod)
        return mod

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._lazy_load(), attr)

    def __setattr__(self, attr: str, value: Any) -> None:
        setattr(self._lazy_load(), attr, value)

    def __delattr__(self, attr: str) -> None:
        delattr(self._lazy_load(), attr)

    def __dir__(self):
        return dir(self._lazy_load())

    def __repr__(self) -> str:
        return f"<lazy module {object.__getattribute__(self, '_lazy_name')!r}>"
