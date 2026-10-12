from __future__ import annotations

from .baseline import NaiveGuard
from .riposte import RiposteGuard

__all__ = ["RiposteGuard", "NaiveGuard", "build_guard"]


def build_guard(kind: str = "riposte", provider=None):
    if kind == "naive":
        return NaiveGuard()
    return RiposteGuard(provider=provider)
