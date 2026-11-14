"""Registry of stitch methods.

A *method* is one way of turning a classified piece of a shape into needle
penetrations: tatami fill, satin, running stitch, ... ``planner.classify`` decides
which method sews each piece; the planner then looks the method up here and calls
its ``generate`` function. Adding a stitch method means writing one module that
defines a generator and calls :func:`register` - the planner needs no change.

The flags tell the planner and the shape editor how a method behaves:

* ``directional`` - its stitch direction can be chosen (shape / colour angle)
* ``density``     - the per-shape thickness and extra-width settings apply
* ``outline``     - the per-shape "bold outline" setting applies
* ``multi_piece`` - sewn as several short pieces inside one shape; a short hidden run
  joins them instead of a thread cut
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover
    from ..geometry import Pt
    from ..models import Run, StitchParams
    from .planner import _Item

    Generator = Callable[[_Item, float, StitchParams, Pt], list[Run]]


@dataclass(frozen=True)
class StitchMethod:
    name: str
    generate: "Generator"
    directional: bool = False
    density: bool = False
    outline: bool = False
    multi_piece: bool = False


_METHODS: dict[str, StitchMethod] = {}


def register(method: StitchMethod) -> StitchMethod:
    """Add a method. Registering the same name twice is a programming error."""
    if method.name in _METHODS:
        raise ValueError(f"stitch method {method.name!r} is already registered")
    _METHODS[method.name] = method
    return method


def get(name: str) -> StitchMethod:
    try:
        return _METHODS[name]
    except KeyError:
        raise KeyError(f"unknown stitch method {name!r}") from None


def names() -> tuple[str, ...]:
    """All registered method names, in registration order."""
    return tuple(_METHODS)


def is_directional(name: str) -> bool:
    return get(name).directional


def is_density(name: str) -> bool:
    return get(name).density


def is_outline(name: str) -> bool:
    return get(name).outline


def is_multi_piece(name: str) -> bool:
    return get(name).multi_piece
