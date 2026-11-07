from __future__ import annotations

from importlib import import_module

__all__ = ["run_today"]


def __getattr__(name: str):
    if name == "run_today":
        return import_module("src.pipeline.run_today")
    raise AttributeError(name)

