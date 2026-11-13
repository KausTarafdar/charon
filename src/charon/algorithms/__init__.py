"""Evaluation strategies and the registry that resolves them by name."""

from __future__ import annotations

from charon.algorithms.base import Algorithm, Outcome, result_from_reply
from charon.algorithms.leaky_bucket import LeakyBucket
from charon.algorithms.sliding_window import SlidingWindowCounter
from charon.algorithms.token_bucket import TokenBucket
from charon.errors import ConfigurationError
from charon.types import AlgorithmName

__all__ = [
    "Algorithm",
    "LeakyBucket",
    "Outcome",
    "SlidingWindowCounter",
    "TokenBucket",
    "get_algorithm",
    "register_algorithm",
    "result_from_reply",
]

_REGISTRY: dict[AlgorithmName, Algorithm] = {}


def register_algorithm(algorithm: Algorithm) -> None:
    """Register (or replace) a strategy. Custom strategies plug in here."""
    _REGISTRY[algorithm.name] = algorithm


def get_algorithm(name: AlgorithmName) -> Algorithm:
    try:
        return _REGISTRY[AlgorithmName(name)]
    except (KeyError, ValueError) as exc:
        raise ConfigurationError(f"unknown algorithm: {name!r}") from exc


for _strategy in (TokenBucket(), SlidingWindowCounter(), LeakyBucket()):
    register_algorithm(_strategy)
