"""
Scoring Function Registry — decorator-based registry of scoring functions.

Each function maps a raw column value to a continuous score in [0, 1].
Functions are referenced by name in the YAML config.
"""

from __future__ import annotations

import math
from typing import Callable

# Registry: name -> scoring function
_REGISTRY: dict[str, Callable] = {}


def register(name: str):
    """Decorator to register a scoring function by name."""
    def decorator(fn: Callable) -> Callable:
        _REGISTRY[name] = fn
        return fn
    return decorator


def get_scoring_fn(name: str) -> Callable:
    """Look up a registered scoring function by name."""
    fn = _REGISTRY.get(name)
    if fn is None:
        raise ValueError(
            f"Unknown scoring function: {name!r}. "
            f"Available: {sorted(_REGISTRY.keys())}"
        )
    return fn


# ------------------------------------------------------------------
# Built-in scoring functions
#
# Signature: fn(value: float | str, params: dict, direction: str) -> float
#   value     — raw column value from the DataFrame row
#   params    — the `normalize` dict from YAML config
#   direction — "Demand" or "Supply"
#   Returns a score in [0.0, 1.0]
# ------------------------------------------------------------------

@register("higher_is_better")
def _higher(value: float, params: dict, direction: str) -> float:
    """Score increases as value rises toward max_value, shaped by exponent."""
    if math.isnan(value):
        return 0.0
    max_val = params.get("max_value", 1.0)
    exponent = params.get("exponent", 1.0)
    linear = max(0.0, min(value / max_val, 1.0))
    return linear ** exponent


@register("lower_is_better")
def _lower(value: float, params: dict, direction: str) -> float:
    """Score increases as value drops below ref_value toward 0, shaped by exponent."""
    if math.isnan(value):
        return 0.0
    ref = params.get("ref_value", 1.0)
    exponent = params.get("exponent", 1.0)
    if ref == 0:
        return 0.0
    linear = max(0.0, min(1.0 - value / ref, 1.0))
    return linear ** exponent


@register("abs_higher_is_better")
def _abs_higher(value: float, params: dict, direction: str) -> float:
    """Score increases with absolute magnitude, shaped by exponent."""
    if math.isnan(value):
        return 0.0
    max_val = params.get("max_value", 1.0)
    exponent = params.get("exponent", 1.0)
    linear = max(0.0, min(abs(value) / max_val, 1.0))
    return linear ** exponent


@register("directional")
def _directional(value: float, params: dict, direction: str) -> float:
    """Score based on whether value aligns with direction, shaped by exponent.

    For Demand: positive delta = turning up = good.
    For Supply: negative delta = turning down = good.
    The absolute magnitude is scored against max_value.
    Exponent > 1.0 suppresses weak signals, amplifies strong ones.
    """
    if math.isnan(value):
        return 0.0
    max_val = params.get("max_value", 1.0)
    exponent = params.get("exponent", 1.0)
    # Check alignment: Demand wants positive, Supply wants negative
    if direction == "Demand" and value <= 0:
        return 0.0
    if direction == "Supply" and value >= 0:
        return 0.0
    linear = max(0.0, min(abs(value) / max_val, 1.0))
    return linear ** exponent


@register("counter_directional")
def _counter_directional(value: float, params: dict, direction: str) -> float:
    """Score based on whether value is counter to direction (divergence).

    For Demand: negative value = price declining into accumulation = good.
    For Supply: positive value = price rising into distribution = good.
    The absolute magnitude is scored against max_value.
    """
    if math.isnan(value):
        return 0.0
    max_val = params.get("max_value", 1.0)
    # Check counter-alignment: Demand wants negative, Supply wants positive
    if direction == "Demand" and value >= 0:
        return 0.0
    if direction == "Supply" and value <= 0:
        return 0.0
    return max(0.0, min(abs(value) / max_val, 1.0))


@register("count_ratio")
def _count_ratio(value: float, params: dict, direction: str) -> float:
    """Score a count value as a ratio of max_count."""
    if isinstance(value, float) and math.isnan(value):
        return 0.0
    max_count = params.get("max_count", 5)
    return max(0.0, min(float(value) / max_count, 1.0))


@register("categorical_map")
def _categorical_map(value: str | float, params: dict, direction: str) -> float:
    """Score a categorical string by looking it up in a direction-specific map.
    
    params requires:
    - demand_map: dict of string -> float score (0-1)
    - supply_map: dict of string -> float score (0-1)
    """
    if isinstance(value, float) and math.isnan(value):
        return 0.0
    
    val_str = str(value).lower()
    
    if direction == "Demand":
        mapping = params.get("demand_map", {})
    else:
        mapping = params.get("supply_map", {})
        
    return float(mapping.get(val_str, 0.0))
