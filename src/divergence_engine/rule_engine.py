"""
Rule Engine — Specification-pattern classifier for the Integrated State Matrix.

Evaluates YAML-defined rules in priority order against a MarketContext.
First matching rule wins. Each rule's conditions are composed internally
as Specification predicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.divergence_engine.state_types import (
    MarketContext,
    StateName,
    Trajectory,
    ValueZone,
)


# ------------------------------------------------------------------
# Specification Predicates
# ------------------------------------------------------------------

@dataclass(frozen=True)
class _TrajectoryIs:
    target: Trajectory
    def ok(self, ctx: MarketContext) -> bool:
        return ctx.trajectory == self.target


@dataclass(frozen=True)
class _ZoneIn:
    zones: frozenset[ValueZone]
    def ok(self, ctx: MarketContext) -> bool:
        return ctx.value_zone in self.zones


@dataclass(frozen=True)
class _Range:
    """Check that a float attribute falls within [lo, hi]."""
    attr: str
    lo: float | None = None
    hi: float | None = None
    def ok(self, ctx: MarketContext) -> bool:
        val = getattr(ctx, self.attr)
        if self.lo is not None and val < self.lo:
            return False
        if self.hi is not None and val > self.hi:
            return False
        return True


# ------------------------------------------------------------------
# Compiled Rule
# ------------------------------------------------------------------

@dataclass
class _CompiledRule:
    name: str
    state: StateName
    priority: int
    predicates: list  # list of predicate objects with .ok(ctx)

    def matches(self, ctx: MarketContext) -> bool:
        return all(p.ok(ctx) for p in self.predicates)


# ------------------------------------------------------------------
# Look-up tables for parsing YAML strings -> enums
# ------------------------------------------------------------------

_TRAJ_MAP: dict[str, Trajectory] = {t.value: t for t in Trajectory}
_ZONE_MAP: dict[str, ValueZone] = {z.value: z for z in ValueZone}
_STATE_MAP: dict[str, StateName] = {s.value: s for s in StateName}


# ------------------------------------------------------------------
# Rule Engine
# ------------------------------------------------------------------

class RuleEngine:
    """Classify a MarketContext using priority-ordered rules from config.

    Usage::

        from src.divergence_engine.rule_engine import RuleEngine
        from src.divergence_engine.config_manager import get_config

        engine = RuleEngine(get_config())
        state = engine.classify(ctx)
    """

    def __init__(self, config: dict) -> None:
        self._rules = self._compile(config.get("rules", []), config.get("thresholds", {}))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, ctx: MarketContext) -> StateName:
        """Return the first matching state, or NEUTRAL_MIXED."""
        for rule in self._rules:
            if rule.matches(ctx):
                return rule.state
        return StateName.NEUTRAL_MIXED

    def explain(self, ctx: MarketContext) -> list[dict]:
        """Return a trace showing which rules matched or didn't.

        Useful for debugging / UI display.
        """
        results = []
        matched = False
        for rule in self._rules:
            is_match = rule.matches(ctx)
            results.append({
                "priority": rule.priority,
                "state": rule.state.label,
                "matched": is_match,
                "winner": is_match and not matched,
            })
            if is_match and not matched:
                matched = True
        return results

    # ------------------------------------------------------------------
    # Internal: compile YAML rules into predicate chains
    # ------------------------------------------------------------------

    @staticmethod
    def _compile(raw_rules: list[dict], thresholds: dict) -> list[_CompiledRule]:
        compiled: list[_CompiledRule] = []

        for r in raw_rules:
            name = r["name"]
            state = _STATE_MAP.get(name)
            if state is None:
                raise ValueError(f"Unknown state name in rules: {name!r}")

            conds = r.get("conditions", {})
            preds: list = []

            # Trajectory
            traj_key = conds.get("trajectory")
            if traj_key:
                traj = _TRAJ_MAP.get(traj_key)
                if traj is None:
                    raise ValueError(f"Unknown trajectory: {traj_key!r}")
                preds.append(_TrajectoryIs(traj))

            # Value zones
            zone_labels = conds.get("value_zones", [])
            if zone_labels:
                zones = frozenset(
                    _ZONE_MAP[lbl] for lbl in zone_labels if lbl in _ZONE_MAP
                )
                if zones:
                    preds.append(_ZoneIn(zones))

            # Numeric range gates — resolve threshold references
            for attr, field in [("mfm", "mfm"), ("rdv_z", "rdv_z"), ("price_z", "price_z")]:
                gate = conds.get(attr)
                if gate:
                    lo = gate.get("min")
                    hi = gate.get("max")
                    # Resolve threshold references (strings starting with $)
                    if isinstance(lo, str) and lo.startswith("$"):
                        lo = thresholds.get(lo[1:], lo)
                    if isinstance(hi, str) and hi.startswith("$"):
                        hi = thresholds.get(hi[1:], hi)
                    preds.append(_Range(attr=field, lo=lo, hi=hi))

            compiled.append(_CompiledRule(
                name=name,
                state=state,
                priority=r.get("priority", 999),
                predicates=preds,
            ))

        # Sort by priority (stable — preserves YAML order for ties)
        compiled.sort(key=lambda r: r.priority)
        return compiled
