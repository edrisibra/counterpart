"""Built-in persona definitions and a name registry.

Personas resolve by name so callers can write ``mock_agent(persona="false_success")``.
Register a custom persona with :func:`register`; look one up with :func:`get_persona`.
Personas are protocol-agnostic (they emit ``core`` directives), so they drive an in-process
mock, the A2A adapter, or any future adapter unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from a2a_sandbox.core.behaviour import Behaviour
from a2a_sandbox.personas.library import (
    Clarifier,
    Cooperative,
    FalseSuccess,
    Flaky,
    OverSharing,
    ResourceAbuse,
)

# A factory takes keyword config and returns a Behaviour.
PersonaFactory = Callable[..., Behaviour]

_REGISTRY: dict[str, PersonaFactory] = {}


def register(name: str, factory: PersonaFactory) -> None:
    """Register a persona factory under ``name`` (overwrites an existing entry)."""
    _REGISTRY[name] = factory


def get_persona(name: str, /, **config: Any) -> Behaviour:
    """Instantiate the persona registered under ``name`` with ``config``."""
    try:
        factory = _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"unknown persona {name!r}; available: {known}") from None
    return factory(**config)


def available() -> tuple[str, ...]:
    """Names of all registered personas, sorted."""
    return tuple(sorted(_REGISTRY))


for _persona in (Cooperative, Clarifier, FalseSuccess, ResourceAbuse, Flaky, OverSharing):
    register(_persona.name, _persona)

__all__ = [
    "Clarifier",
    "Cooperative",
    "FalseSuccess",
    "Flaky",
    "OverSharing",
    "PersonaFactory",
    "ResourceAbuse",
    "available",
    "get_persona",
    "register",
]
